# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Refresh ISTL comps against Zillow — area search + per-pin lookup.

The pooled `CRM Comp` index is ISTL's RentCast-derived last *asks*, frozen when
the marketplace lead was scraped. It ages. This module is the freshness layer:

  A. Circle search for every RecentlySold in the last 2 years AND every
     ForSale (`coordinates=lon lat,diameter`, diameter = 2× radius miles).
     Pages through `totalPages`; a window over RapidAPI's 800-result ceiling
     binary-splits on price (devproppy `fetch_all_zillow_properties`). Cached 7 days per rounded
     center so two leads on the same block share the spend.
     ForRent is a SEPARATE inventory (the rental map toggle), never mixed into
     this circle and never prewarmed — same billed key, different question.
  B. `/property` on the nearest stale ISTL pins (capped) so a house ISTL still
     has as an 8-month-old ask can pick up the sale Zillow recorded last week.

Street-address `/search` returns `{zpid}` only. ZIP is the wrong geography.
`polygon` is a box (corners sit at 2.83 mi). `coordinates` is the true circle
(measured: `d=4` → solds out to 1.99 mi). `dateSold` is epoch-ms.

Every path degrades softly: a Zillow outage or a quota-reserve hit leaves the
ISTL set untouched. BatchData stays the last resort for a truly empty map.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone

import frappe

from crm.api import sale_history
from crm.api.comp_provenance import is_adc, mark_verified, qualified_address
from crm.api import zillow as zillow_api

#: How long a COMPLETE circle is served before it is re-bought.
#:
#: Two clocks, because the two halves age differently and cost differently. An
#: ask moves weekly, so ForSale stays at 7. A closed sale is still evidence a
#: month later, and the RecentlySold circle is the bigger half (two years of
#: solds pages further than the live inventory does) -- so 14 days there halves
#: the weekly re-buy of the whole board's most expensive query. Measured on the
#: cycle this was tuned for: ~500 calls a night of scheduled re-warming, most of
#: it solds that had not changed. A sale recorded inside the window still shows
#: up within two weeks, which is well inside how long a lead sits on the board.
AREA_HIT_DAYS = 7
AREA_SOLD_HIT_DAYS = 14
AREA_MISS_DAYS = 1
PIN_CACHE_DAYS = 30
#: DELIBERATELY UNCHANGED at 12, even though these are no longer sequential.
#: Running them together took this phase from 21.3s to 0.6s, so time is no longer
#: what limits it -- but the cap was never about time. Each one is a BILLED call
#: on a key shared with istl-buyer, so raising it doubles the cost of opening a
#: lead we have not seen before. It is a one-line dial if more photo coverage
#: turns out to be worth the spend; it should be turned deliberately, not as a
#: side-effect of making things faster.
PIN_REFRESH_CAP = 12
#: Don't spend a /property call on an ISTL pin that already looks current.
PIN_STALE_DAYS = 90
SOLD_IN_LAST = "24m"
#: RapidAPI's own ceiling per query — same number devproppy stops at. Past this
#: the API silently repeats page 1, so we binary-split the price range instead
#: (devproppy `fetch_all_zillow_properties`). Each half recurses if still >800.
MAX_SEARCH_RESULTS = 800
MAX_SPLIT_DEPTH = 6
#: Hard stop on RapidAPI calls for one circle+status so a dense metro cannot
#: spend the shared quota on a single comps open. 40 pages ≈ 1,600 rows.
MAX_SEARCH_CALLS = 40
#: v7 asks for pending/under-contract listings too, so a v6 entry answers a
#: DIFFERENT (smaller) question and must not be served for this one.
# v8 stops writing days-since-sale into `days_on_market` on sold rows, so a v7
# entry answers the question WRONG rather than merely differently -- it must not
# be served. NOTE these bumps are now the ONLY way a cached circle is
# invalidated: since gw365 the caches survive `bench clear-cache`, so a deploy no
# longer quietly does it for you (crm/hooks.py, persistent_cache_keys).
AREA_CACHE_VERSION = 8  # v5 price-splits past 800; v6 imgSrc; v7 pending; v8 DOM fix
PIN_CACHE_VERSION = 5  # v2 cover_photo; v3 parsed history (dropped); v4 raw price_history; v5 is_for_auction


#: A version bump used to mean re-buying every cached circle -- v7->v8 alone was
#: ~300 circles at ~3 calls each on a shared key that ran dry a week later. Most
#: bumps are a READ-TIME fix in disguise (v8 was "blank one field on sold rows"),
#: so before spending, an older generation of the same key is carried forward
#: through this ladder and re-written under the current version with its
#: ORIGINAL timestamp, so migrating never extends a TTL. Bump the version AND add
#: an entry here; leave the entry out only when the old blob genuinely cannot
#: answer the new question (a field the old fetch never kept).
#:
#: {from_version: fn(data) -> data | None}. Returning None refuses the migration
#: and the circle is fetched as before.
def _migrate_area_v7(data):
	"""v8 stopped writing days-since-sale into `days_on_market` on sold rows."""
	rows = data.get("rows") if isinstance(data, dict) else data
	for row in rows or []:
		if isinstance(row, dict) and row.get("status") != "Active":
			row["days_on_market"] = None
	return data


AREA_MIGRATIONS = {7: _migrate_area_v7}


def _migrate_pin_v4(data):
	"""v5 adds is_for_auction from /property listingSubType.

	A live listing whose cached blob predates the field cannot answer auction vs
	for-sale (1623 Nevada stayed red For sale after gw481). Sold/empty blobs do
	not need a re-buy — they were never auctions on this cache.
	"""
	if not isinstance(data, dict):
		return data
	if "is_for_auction" in data:
		return data
	home = str(data.get("home_status") or "").strip().upper()
	if home in {"FOR_SALE", "PENDING", "CONTINGENT", "COMING_SOON", "FOR_RENT"}:
		return None
	data = dict(data)
	data["is_for_auction"] = False
	return data


#: v3 dropped the raw priceHistory that v4 is built on; nothing to carry forward.
PIN_MIGRATIONS = {4: _migrate_pin_v4}

#: Zillow's own words for a home that is spoken for but has not closed. We have
#: to ask for these explicitly (`isPendingUnderContract=1`) because the default
#: ForSale search hides them: measured on prod, Davenport 97 -> 156 listings and
#: Indianapolis 281 -> 359 once they are included.
#:
#: They are worth the extra pages. A pending sale is a price two parties have
#: AGREED, which an ask is not, and it is happening now, which a closed sale is
#: not -- so in a moving market it is the most honest read available. It is still
#: not a completed transaction, which is why it gets its own label everywhere
#: rather than being quietly counted as either a listing or a sale.
PENDING_STATUSES = {"PENDING", "UNDER_CONTRACT", "ACCEPTING_BACKUP_OFFERS", "CONTINGENT"}
RENT_STATUSES = {"FOR_RENT", "RENTAL", "FOR_RENT_BY_OWNER"}

_SUFFIXES = {
	"street": "st",
	"avenue": "ave",
	"boulevard": "blvd",
	"drive": "dr",
	"road": "rd",
	"lane": "ln",
	"court": "ct",
	"place": "pl",
	"terrace": "ter",
	"circle": "cir",
	"highway": "hwy",
	"parkway": "pkwy",
	"trail": "trl",
}


def _cache_rec(key):
	"""Raw `(t, data)` for a `{t, data}` blob, or None. No TTL judgement here."""
	try:
		rec = frappe.cache().get_value(key)
	except Exception:
		return None
	if not isinstance(rec, dict) or "data" not in rec:
		return None
	try:
		return float(rec.get("t") or 0), rec["data"]
	except (TypeError, ValueError):
		return None


def _cache_get(key, ttl_days):
	"""Read a `{t, data}` blob. No Redis TTL — see zillow.py's cache GOTCHA."""
	rec = _cache_rec(key)
	if rec is None:
		return None
	t, data = rec
	if time.time() - t > float(ttl_days) * 86400:
		return None
	return data


def _cache_get_migrated(key_for, version, migrations, ttl_days):
	"""`_cache_get` for the current version, else an older generation carried forward.

	`key_for(version)` names the key for any generation. Walks down one version at
	a time while a migrator exists for the step, applies the chain oldest-first, and
	re-writes the result under the current key with the OLD entry's timestamp --
	so a migrated circle expires exactly when the original would have.
	"""
	data = _cache_get(key_for(version), ttl_days)
	if data is not None:
		return data
	steps = []
	v = version
	while (v - 1) in migrations:
		v -= 1
		steps.append(migrations[v])
		rec = _cache_rec(key_for(v))
		if rec is None:
			continue
		t, data = rec
		if time.time() - t > float(ttl_days) * 86400:
			# Anything older still is older still.
			return None
		for step in reversed(steps):
			data = step(data)
			if data is None:
				return None
		try:
			frappe.cache().set_value(key_for(version), {"t": t, "data": data})
		except Exception:
			pass
		return data
	return None


def _cache_set(key, data):
	try:
		frappe.cache().set_value(key, {"t": time.time(), "data": data})
	except Exception:
		pass


def _ymd(value):
	"""Zillow dateSold is epoch-ms; priceHistory dates are YYYY-MM-DD. One shape."""
	if value in (None, ""):
		return None
	if isinstance(value, str):
		s = value.strip()
		if len(s) >= 10 and s[4] == "-" and s[7] == "-":
			return s[:10]
		try:
			value = float(s)
		except (TypeError, ValueError):
			return None
	try:
		n = float(value)
	except (TypeError, ValueError):
		return None
	if n > 1e12:
		n /= 1000.0
	if n <= 0:
		return None
	try:
		return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d")
	except (OverflowError, OSError, ValueError):
		return None


def _newer(a, b):
	"""True when date string `a` is strictly after `b`. Missing `b` → yes."""
	if not a:
		return False
	if not b:
		return True
	return str(a)[:10] > str(b)[:10]


def _comps():
	# Late import: comps.py calls this module from get_lead_comps, so a top-level
	# import of address_key / _haversine_mi would cycle.
	from crm.api import comps as comps_mod

	return comps_mod


def merge_key(address: str) -> str:
	"""address_key after collapsing Street/Avenue/… so ISTL and Zillow collide.

	ISTL writes `3362 N 22nd St`; Zillow search writes `3362 N 22nd STREET`.
	The CRM Comp docname is the ISTL form, so a raw address_key miss would
	duplicate the pin instead of refreshing it.
	"""
	norm = re.sub(r"\s+", " ", (address or "").strip().lower())
	parts = []
	for word in re.split(r"([^a-z0-9]+)", norm):
		parts.append(_SUFFIXES.get(word, word))
	return _comps().address_key("".join(parts))


def _ll_key(lat, lng):
	if lat is None or lng is None:
		return None
	try:
		return (round(float(lat), 4), round(float(lng), 4))
	except (TypeError, ValueError):
		return None


def _coordinates(lat, lng, radius_mi) -> str:
	"""RapidAPI `/search` circle: `lon lat,diameter` (miles, 0–99).

	Diameter is 2× the comps radius so a 2-mile map is `d=4`. Measured: `d=4`
	around a St Paul point returned RecentlySold pins out to 1.99 mi. Rounded
	to 4 decimals (~11 m) so two leads on the same block share the cache.
	"""
	diameter = max(1, min(99, int(round(float(radius_mi) * 2))))
	return f"{round(float(lng), 4)} {round(float(lat), 4)},{diameter}"


def _listing_sub_type(prop):
	sub = (prop or {}).get("listingSubType") or (prop or {}).get("listing_sub_type") or {}
	return sub if isinstance(sub, dict) else {}


def _is_auction(prop):
	"""Zillow's own auction flag. Search and /property both send listingSubType.

	Measured 2026-09-08 against Auction.com (foreclosure / redemption / bank-owned)
	and ServiceLink: every live auction had `is_forAuction: true` and
	`homeStatus: FOR_SALE`. `is_foreclosure` / `is_bankOwned` are NOT reliable —
	most Auction.com rows left both false. Do not infer auction from provider
	name or a Public Record sale.
	"""
	sub = _listing_sub_type(prop)
	return bool(sub.get("is_forAuction") or sub.get("is_for_auction"))


def listing_state(prop, kind):
	"""What Zillow says this row IS: auction / sold / pending / for sale / for rent.

	Read off the row's own `listingStatus` rather than inferred from which query
	it arrived in, because the two disagree: a RecentlySold page came back holding
	a PENDING row on the very first sample. `contingentListingType` is the second
	signal -- an UNDER_CONTRACT home is still listed as FOR_SALE while accepting
	backups, so reading only `listingStatus` would call it a plain listing.

	Auction is first: RecentlySold search also returns houses that are still a
	live auction on Zillow (1623 Nevada: listingStatus RECENTLY_SOLD, /property
	homeStatus FOR_SALE + is_forAuction). The live flag wins.
	"""
	if _is_auction(prop):
		return "auction"
	status = str(prop.get("listingStatus") or "").strip().upper()
	contingent = str(prop.get("contingentListingType") or "").strip().upper()
	if kind == "rent" or status in RENT_STATUSES:
		return "for_rent"
	if status == "RECENTLY_SOLD":
		return "sold"
	if status in PENDING_STATUSES or contingent in PENDING_STATUSES:
		return "pending"
	if status == "FOR_SALE":
		return "for_sale"
	# No usable status on the row: fall back to the query it came from, which is
	# what this did before the field was read at all.
	if kind == "sale":
		return "for_sale"
	if kind == "rent":
		return "for_rent"
	return "sold"


def _shape_search(prop, kind):
	"""One RapidAPI search prop → the row shape `get_lead_comps` already emits."""
	if not isinstance(prop, dict):
		return None
	addr = (prop.get("address") or "").strip()
	lat = zillow_api._num(prop.get("latitude"))
	lng = zillow_api._num(prop.get("longitude"))
	price = zillow_api._num(prop.get("price"))
	zpid = prop.get("zpid")
	if not addr or lat is None or lng is None or not zpid:
		return None

	sold = _ymd(prop.get("dateSold"))
	# GOTCHA -- Zillow says `daysOnZillow: -1` for "we don't know", not "zero days".
	# Passing it through rendered "Under contract - listed -1 days" on a real card,
	# and would have put "-1d on market" in the popup of any listing it hit.
	# Unknown is None, which every consumer here already omits rather than prints.
	dom = zillow_api._num(prop.get("daysOnZillow"))
	if dom is not None and dom < 0:
		dom = None
	home = str(prop.get("propertyType") or "").strip().upper()
	state = listing_state(prop, kind)
	# Auctions list at $0 (no ask). Dropping those rows is how they only ever
	# arrived via RecentlySold, as a sold pin, while Zillow still showed Auction.
	if not price and state in ("sold", "off_market"):
		return None
	# A pending/auction home has NOT sold, so it stays "Active" in the status field
	# every filter, colour and count in this app already keys on. What makes it
	# pending/auction rides alongside in `listing_state`, which is additive --
	# nothing that predates it has to learn a third status to keep working.
	# Rentals use a different name prefix so hide/select/type on a sale pin cannot
	# collide with the same zpid listed for rent (or the reverse).
	active = state not in ("sold", "off_market")
	prefix = "zillow-rent" if state == "for_rent" else "zillow"
	return {
		"name": f"{prefix}::{zpid}",
		"address": addr,
		"city": "",
		"state": "",
		"zip": "",
		"lat": lat,
		"lng": lng,
		"price": price,
		"status": "Active" if active else "Inactive",
		"listing_state": state,
		"listed_date": None if not active else None,
		"removed_date": None if active else sold,
		# ONLY on a live listing. `daysOnZillow` means two different things depending
		# on the row, and this field is rendered as "N days on market" everywhere:
		#
		#   ForSale row       -> days the listing has been up. Correct.
		#   RecentlySold row  -> days SINCE THE SALE. Nothing to do with time on
		#                        market at all.
		#
		# Measured on 1128 Pleasant St, Indianapolis: `daysOnZillow` = 4, while the
		# house was listed 2026-07-16 and sold 2026-08-21 -- 36 days. Passing it
		# through put "Listed  · 4d on market" in the map popup (with a blank date,
		# since a sold search row carries no `listed_date` either).
		#
		# For a sold row the number is redundant anyway: it IS the recency, which
		# `_recency_days` already derives from `removed_date`. Real time-to-sell now
		# comes from the listing chain in `sale_history`.
		"days_on_market": int(dom) if (dom is not None and active) else None,
		"days_old": None,
		"bedrooms": zillow_api._num(prop.get("bedrooms")),
		"bathrooms": zillow_api._num(prop.get("bathrooms")),
		"square_footage": zillow_api._num(prop.get("livingArea")),
		# Search usually omits this (the card doesn't show a year). `/property`
		# fills it later in attach_sale_history. Read it here anyway so a row that
		# happens to carry yearBuilt isn't stripped for nothing.
		"year_built": zillow_api._num(prop.get("yearBuilt")),
		"lot_size": zillow_api.lot_size_label(prop),
		"property_type": zillow_api.HOME_TYPES.get(home) or (home.title().replace("_", " ") or None),
		"source": "zillow",
		"zpid": str(zpid),
		# The search response already carries a listing thumbnail. Keeping it is the
		# whole reason the tray can show a photo per comp for NOTHING: the alternative
		# is `/property?address=` per house, which is one billed call each. Measured
		# on a St Paul 2-mile RecentlySold page: imgSrc present on 41 of 41 rows.
		"photo": (prop.get("imgSrc") or "").strip(),
	}


def _search_params(coordinates, status_type, sold_in_last=None, min_price=None, max_price=None, sort="Newest", page=1):
	params = {
		"coordinates": coordinates,
		"status_type": status_type,
		"sort": sort,
		"page": page,
	}
	if status_type == "ForSale":
		# Pending / under-contract homes are excluded by default and are the best
		# evidence on the board -- an agreed price, on a sale that is happening now.
		# Must be a NUMBER: `true` returns {"errors": ["Is Pending Under Contract
		# must be a number."]} with an HTTP 200, i.e. it fails silently as an empty
		# result rather than as an error.
		params["isPendingUnderContract"] = 1
	if sold_in_last:
		params["soldInLast"] = sold_in_last
	if min_price is not None:
		params["minPrice"] = int(min_price)
	if max_price is not None:
		params["maxPrice"] = int(max_price)
	return params


def _usable(body):
	"""RapidAPI answers an unusable query with a 200 and a non-`props` body."""
	return body if isinstance(body, dict) and "props" in body else None


def _get(coordinates, status_type, sold_in_last=None, min_price=None, max_price=None, sort="Newest", page=1):
	"""One RapidAPI /search. None on quota-reserve or a bad body."""
	left = zillow_api.quota_remaining()
	if left is not None and left <= zillow_api.QUOTA_RESERVE:
		return None
	params = _search_params(
		coordinates, status_type, sold_in_last, min_price, max_price, sort, page
	)
	body = zillow_api._request(
		"/search", params, f"Zillow: {status_type} {sort} p{page} failed"
	)
	return _usable(body)


def _get_pages(coordinates, status_type, sold_in_last, min_price, max_price, pages):
	"""Several pages of one window AT ONCE. -> [body|None] in the order asked.

	Page 1 has already told us `totalPages`, so pages 2..N have no dependency on
	each other and there is nothing to be gained by waiting between them. This is
	the single change that took a 2-mile circle from 40.5s to seconds: the work was
	never computation, it was thirty consecutive round trips to RapidAPI.
	"""
	specs = [
		(
			"/search",
			_search_params(coordinates, status_type, sold_in_last, min_price, max_price, page=p),
		)
		for p in pages
	]
	bodies = zillow_api.fetch_many(specs, f"Zillow: {status_type} paging failed")
	return [_usable(b) for b in bodies]


def _price_edge(coordinates, status_type, sold_in_last, min_price, max_price, high):
	"""Cheapest or dearest price in this window. One extra call, like devproppy."""
	sort = "Price_High_Low" if high else "Price_Low_High"
	body = _get(
		coordinates, status_type, sold_in_last, min_price, max_price, sort=sort, page=1
	)
	if not body:
		return None
	for prop in body.get("props") or []:
		n = zillow_api._num(prop.get("price"))
		if n:
			return int(n)
	return None


def _collect_pages(coordinates, status_type, sold_in_last, min_price, max_price, first_body, budget):
	"""Page through one price window that is already known to be ≤800."""
	if status_type == "ForRent":
		kind = "rent"
	elif status_type == "ForSale":
		kind = "sale"
	else:
		kind = "sold"
	rows = []
	seen = set()
	try:
		total_pages = max(1, int(first_body.get("totalPages") or 1))
	except (TypeError, ValueError):
		total_pages = 1

	# Everything left in this window, requested together. The call budget is spent
	# up front rather than checked between calls, because there is no longer a
	# "between" -- so the ceiling is enforced by how many pages we ASK for.
	allowance = max(0, MAX_SEARCH_CALLS - budget["n"])
	wanted = list(range(2, total_pages + 1))[:allowance]
	budget["n"] += len(wanted)
	bodies = [first_body]
	failed = False
	if wanted:
		for body in _get_pages(coordinates, status_type, sold_in_last, min_price, max_price, wanted):
			if body is None:
				# Keep the pages that did arrive, but remember the hole: `complete`
				# is what decides whether this circle may be cached for a week, and
				# caching a partial answer would hide the rest of it until it expires.
				failed = True
				continue
			bodies.append(body)
	for body in bodies:
		for prop in body.get("props") or []:
			shaped = _shape_search(prop, kind)
			if not shaped or shaped.get("zpid") in seen:
				continue
			seen.add(shaped["zpid"])
			rows.append(shaped)
	complete = not failed and len(wanted) == max(0, total_pages - 1)
	return rows, complete


def _search_window(coordinates, status_type, sold_in_last=None, min_price=None, max_price=None, depth=0, budget=None):
	"""One circle (optionally price-sliced). Splits when totalResultCount > 800."""
	if budget is None:
		budget = {"n": 0}
	if budget["n"] >= MAX_SEARCH_CALLS or depth > MAX_SPLIT_DEPTH:
		return [], False

	body = _get(coordinates, status_type, sold_in_last, min_price, max_price, page=1)
	budget["n"] += 1
	if body is None:
		return None, False

	try:
		count = int(body.get("totalResultCount") or 0)
	except (TypeError, ValueError):
		count = 0

	if count > MAX_SEARCH_RESULTS and depth < MAX_SPLIT_DEPTH:
		lo = min_price if min_price is not None else _price_edge(
			coordinates, status_type, sold_in_last, min_price, max_price, high=False
		)
		hi = max_price if max_price is not None else _price_edge(
			coordinates, status_type, sold_in_last, min_price, max_price, high=True
		)
		budget["n"] += int(min_price is None) + int(max_price is None)
		# No prices → cannot split (non-disclosure solds). Page this window's 800.
		if lo and hi and int(lo) < int(hi):
			mid = (int(lo) + int(hi)) // 2
			low_rows, low_ok = _search_window(
				coordinates, status_type, sold_in_last, int(lo), mid, depth + 1, budget
			)
			high_rows, high_ok = _search_window(
				coordinates, status_type, sold_in_last, mid, int(hi), depth + 1, budget
			)
			seen, out = set(), []
			for row in (low_rows or []) + (high_rows or []):
				zpid = row.get("zpid")
				if not zpid or zpid in seen:
					continue
				seen.add(zpid)
				out.append(row)
			return out, bool(low_ok and high_ok)

	return _collect_pages(
		coordinates, status_type, sold_in_last, min_price, max_price, body, budget
	)


def _search(coordinates, status_type, sold_in_last=None):
	"""All solds/listings in the circle. Price-splits when a window exceeds 800.

	`complete` is False when we stopped early (quota, call budget, a mid-run
	error). Those must not be cached for a week or we hide the rest of the circle.
	"""
	rows, complete = _search_window(coordinates, status_type, sold_in_last)
	if rows is None:
		return None, False
	return rows, complete


#: The two searches that together ARE one circle. Kept as one list so the fetch
#: and the free "is this circle already warm?" probe cannot drift apart — a warmer
#: that checked a different pair of keys than the reader populates would report
#: everything warm and prewarm nothing.
#: (out key, RapidAPI status_type, soldInLast, days a complete circle is served).
AREA_QUERIES = (
	("sold", "RecentlySold", SOLD_IN_LAST, AREA_SOLD_HIT_DAYS),
	("for_sale", "ForSale", None, AREA_HIT_DAYS),
)
#: Not in AREA_QUERIES on purpose. Prewarm and the sale map must not buy a
#: ForRent circle; the rental toggle is the only thing that spends this.
RENTAL_AREA_QUERIES = (("for_rent", "ForRent", None, AREA_HIT_DAYS),)


def _area_key(coordinates, status_type, sold_in_last=None, version=None):
	digest = hashlib.md5(
		f"{coordinates}|{status_type}|{sold_in_last or ''}".encode()
	).hexdigest()[:12]
	return f"zillow_area:v{version or AREA_CACHE_VERSION}:{digest}"


def _area_get(coordinates, status_type, sold_in_last, ttl_days):
	"""Current-version circle, or an older generation migrated forward."""
	return _cache_get_migrated(
		lambda v: _area_key(coordinates, status_type, sold_in_last, v),
		AREA_CACHE_VERSION,
		AREA_MIGRATIONS,
		ttl_days,
	)


def area_is_cached(lat, lng, radius_mi):
	"""Is this circle already in cache? FREE — Redis only, never an HTTP call.

	This is what makes prewarming cheap enough to run every five minutes: the sweep
	can ask about every lead on the board and pay only for the ones that would
	actually have made someone wait.
	"""
	if lat is None or lng is None:
		return False
	try:
		coords = _coordinates(float(lat), float(lng), float(radius_mi))
	except (TypeError, ValueError):
		return False
	return all(
		_area_get(coords, status_type, sold_in_last, hit_days) is not None
		for _, status_type, sold_in_last, hit_days in AREA_QUERIES
	)


def _area_cached(coordinates, status_type, sold_in_last=None, hit_days=AREA_HIT_DAYS):
	"""One circle, from cache when we can. -> (rows, complete).

	A PARTIAL answer is cached too, just not for as long. It used to be thrown
	away, which meant a single throttled page in a dense circle re-charged the full
	search on every open forever: measured at 2 miles around a Davenport lead,
	~27 calls and 13s, repeated every time anyone looked. A partial set is still
	most of the market and is worth showing today; what it must not do is masquerade
	as the finished answer for a week, so it expires overnight and is re-tried.
	"""
	key = _area_key(coordinates, status_type, sold_in_last)

	def unpack(rec):
		# Stored as {rows, complete}; tolerate a bare list so a blob written by an
		# older build in the same cache generation still reads.
		if isinstance(rec, dict) and "rows" in rec:
			return rec["rows"] or [], bool(rec.get("complete"))
		return rec or [], True

	hit = _area_get(coordinates, status_type, sold_in_last, hit_days)
	if hit is not None:
		rows, complete = unpack(hit)
		# A complete circle is good for the full week. A partial one is only served
		# from the short window, so past that it falls through and is re-fetched.
		if complete or _cache_get(key, AREA_MISS_DAYS) is not None:
			return rows, complete

	rows, complete = _search(coordinates, status_type, sold_in_last)
	if rows is None:
		# Nothing at all came back (quota floor, or a total outage). Remember
		# nothing: an empty cache entry here would hide a circle that is fine.
		return [], False
	_cache_set(key, {"rows": rows, "complete": complete})
	return rows, complete


def area_comps(lat, lng, radius_mi):
	"""RecentlySold + ForSale inside a distance circle. Empty list on any failure."""
	if lat is None or lng is None or not zillow_api._api_key():
		return {"sold": [], "for_sale": [], "location": "", "cached": True}
	coords = _coordinates(float(lat), float(lng), float(radius_mi))
	# Driven off AREA_QUERIES so this and `area_is_cached` are the same question.
	out = {"location": f"{radius_mi:.2f}mi", "cached": True}
	for key, status_type, sold_in_last, hit_days in AREA_QUERIES:
		rows, complete = _area_cached(coords, status_type, sold_in_last, hit_days)
		out[key] = rows or []
		out["cached"] = out["cached"] and bool(complete)
	return out


def area_rentals(lat, lng, radius_mi):
	"""ForRent inside a distance circle. Never mixed into the sale cache."""
	if lat is None or lng is None or not zillow_api._api_key():
		return {"for_rent": [], "location": "", "cached": True}
	coords = _coordinates(float(lat), float(lng), float(radius_mi))
	out = {"location": f"{radius_mi:.2f}mi", "cached": True}
	for key, status_type, sold_in_last, hit_days in RENTAL_AREA_QUERIES:
		rows, complete = _area_cached(coords, status_type, sold_in_last, hit_days)
		out[key] = rows or []
		out["cached"] = out["cached"] and bool(complete)
	return out


def _pin_key(address, version=None):
	return f"zillow_pin:v{version or PIN_CACHE_VERSION}:{_comps().address_key(address)}"


def _pin_facts(address):
	"""Cached `/property` normalize for one ISTL pin. None on miss/failure."""
	if not address:
		return None
	return _pin_facts_many([address]).get(address)


def _pin_facts_many(addresses):
	"""Cached `/property` facts for many pins at once. -> {address: facts|None}.

	Three phases, and the split is the point: read every cache entry HERE, fetch
	only the misses on worker threads (which have no Frappe at all), then write the
	results back HERE. Doing the lookups one at a time measured 21.3s of a 27.8s
	comps load for twelve addresses.
	"""
	out = {}
	misses = []
	for address in addresses:
		if not address or address in out or address in misses:
			continue
		hit = _cache_get_migrated(
			lambda v, a=address: _pin_key(a, v), PIN_CACHE_VERSION, PIN_MIGRATIONS, PIN_CACHE_DAYS
		)
		if hit is not None:
			# `{}` is a remembered negative and stays cheap; None means never asked.
			out[address] = hit or None
		else:
			misses.append(address)
	if not misses:
		return out

	bodies = zillow_api.fetch_many(
		[("/property", {"address": a}) for a in misses], "Zillow: pin lookup failed"
	)
	for address, raw in zip(misses, bodies):
		facts = zillow_api._normalize(raw) if raw else None
		if raw is None:
			# A failed CALL is not a negative ANSWER. Caching it would lock the pin
			# out for 30 days over one timeout; leaving it uncached retries next open.
			out[address] = None
			continue
		# Cache the empty answer too — same lesson as subject facts: an address
		# Zillow cannot resolve would otherwise be re-billed on every open.
		_cache_set(_pin_key(address), facts or {})
		out[address] = facts
	return out


def _apply_facts(existing, incoming):
	"""Zillow's shape wins over ISTL's. ISTL sqft is the listing/tax figure and is
	what Dennis caught disagreeing with the Zillow page — we used to keep it
	whenever it was already set, so a merge that updated the SALE left the
	wrong living area on the pin.

	EXCEPT AGAINST REDFIN, which outranks both. Measured over the benchmark's
	matched houses, the odd one out on sqft is Zillow 52% of the time against
	Redfin's 9%, and on price Zillow 42% against Redfin's 12%. Once
	`comp_merge` has stamped a value onto a row, this must not quietly undo it
	— and it would, because `attach_sale_history` calls this on the final board
	AFTER the merge has run, with a billed `/property` payload in hand.

	Filling a BLANK is still allowed and still wanted: Redfin returning no sqft
	is Redfin declining to answer, not an assertion that the house has none, so
	Zillow's number is strictly better than nothing.
	"""
	redfin_owned = existing.get("field_authority") == "redfin"
	for key in ("square_footage", "bedrooms", "bathrooms", "year_built", "lot_size"):
		val = incoming.get(key)
		if not val:
			continue
		if redfin_owned and existing.get(key):
			continue
		existing[key] = val


def _apply_sale(row, price, date):
	row["price"] = price or row.get("price")
	row["removed_date"] = date
	row["status"] = "Inactive"
	# A confirmed sale overwrites any earlier "pending" read: that is what pending
	# was always going to become, and leaving the old label would keep calling a
	# closed transaction an open one.
	row["listing_state"] = "sold"
	mark_verified(row, "sold", "zillow_sale")
	row["zillow_refreshed"] = True


def _state_from_facts(facts):
	"""listing_state from a /property facts blob. None if it is not on the market."""
	if not facts:
		return None
	if facts.get("is_for_auction"):
		return "auction"
	home = str(facts.get("home_status") or "").strip().upper()
	if home in RENT_STATUSES:
		return "for_rent"
	if home in PENDING_STATUSES:
		return "pending"
	if home in {"FOR_SALE", "COMING_SOON"}:
		return "for_sale"
	return None


def _apply_listing(row, price, days_on_market, state="for_sale"):
	state = state or "for_sale"
	# Auctions often have no ask ($0). `price or row.get("price")` would keep a
	# public-record transfer on the pin — 1623 Nevada stayed $190k sold that way.
	if state == "auction":
		row["price"] = price or 0
	elif price:
		row["price"] = price
	row["status"] = "Active"
	row["listing_state"] = state
	row["removed_date"] = None
	if days_on_market is not None:
		row["days_on_market"] = int(days_on_market)
	mark_verified(row, state, "current_ask")
	row["zillow_refreshed"] = True


def _index(rows):
	by_key, by_ll = {}, {}
	for row in rows:
		by_key[merge_key(row.get("address") or "")] = row
		ll = _ll_key(row.get("lat"), row.get("lng"))
		if ll:
			by_ll.setdefault(ll, row)
	return by_key, by_ll


def _find(row, by_key, by_ll):
	hit = by_key.get(merge_key(row.get("address") or ""))
	if hit:
		return hit
	ll = _ll_key(row.get("lat"), row.get("lng"))
	return by_ll.get(ll) if ll else None


def _merge_one(existing, incoming, today):
	"""Update `existing` from a Zillow row when Zillow is actually newer."""
	# A photo is not "newer" data, it is data the pooled index simply never had, so
	# it rides along on ANY match rather than waiting for a price/date to change.
	# This is what gives ISTL-origin pins a thumbnail without a per-address call.
	if incoming.get("photo") and not existing.get("photo"):
		existing["photo"] = incoming["photo"]
	if incoming.get("zpid") and not existing.get("zpid"):
		existing["zpid"] = incoming["zpid"]
	# Facts ride on ANY match, same as the photo: Zillow livingArea is the
	# number on the page Dennis is looking at. Leaving ISTL's figure in place
	# because a price didn't change is how sold comps showed the wrong sqft.
	_apply_facts(existing, incoming)
	if incoming.get("status") == "Active":
		# A live Zillow listing is more current than an ISTL ask, even if ISTL
		# also thought it was active — the ask may have moved.
		if (
			existing.get("status") != "Active"
			or existing.get("source") != "zillow"
			# "It went under contract" is news even when we already knew it was
			# listed at this price, and it is the most decision-relevant news on
			# the board — so it counts as an update rather than being skipped.
			or existing.get("listing_state") != incoming.get("listing_state")
		):
			_apply_listing(
				existing,
				incoming.get("price"),
				incoming.get("days_on_market"),
				incoming.get("listing_state"),
			)
			existing["recency_days"] = _comps()._recency_days(existing, today)
			return "updated"
		return None
	if _newer(incoming.get("removed_date"), existing.get("removed_date")):
		_apply_sale(existing, incoming.get("price"), incoming.get("removed_date"))
		existing["recency_days"] = _comps()._recency_days(existing, today)
		return "updated"
	return None


def refresh_pins(rows, cap=PIN_REFRESH_CAP):
	"""B: `/property` the nearest ISTL-origin pins. Mutates `rows`. Returns counts."""
	today = frappe.utils.today()
	checked = updated = 0

	def _needs_zillow_shape(row):
		# Any pin still wearing ISTL facts — those are the listing/tax numbers
		# that disagree with the Zillow page. Zillow-origin rows already carry
		# livingArea from /search.
		return bool(row.get("address") and row.get("source") != "zillow"
			and (not is_adc(row) or qualified_address(row)))

	candidates = [r for r in rows if _needs_zillow_shape(r)]
	candidates.sort(key=lambda r: r.get("distance_mi") or 99)
	candidates = candidates[: max(0, int(cap))]
	# One batch for the whole set, so the nearest 24 pins cost about what two used
	# to. Resolved up front rather than inside the loop for exactly that reason.
	facts_by_address = _pin_facts_many([r["address"] for r in candidates])
	for row in candidates:
		facts = facts_by_address.get(row["address"])
		checked += 1
		if not facts:
			continue
		sale = facts.get("last_sale") or {}
		sale_date = _ymd(sale.get("date"))
		changed = False
		# A picture is not "newer" data, it is data the pooled index never had, so it
		# rides along on ANY hit. We have already paid for this response; the tray
		# showing "No photo" next to it would be throwing away something we bought.
		if facts.get("cover_photo") and not row.get("photo"):
			row["photo"] = facts["cover_photo"]
			changed = True
		if facts.get("zpid") and not row.get("zpid"):
			row["zpid"] = str(facts["zpid"])
		# Live homeStatus wins over a later public-record Sold in priceHistory.
		live = _state_from_facts(facts)
		if live:
			listing = facts.get("last_listing") or {}
			ask = None if live == "auction" else (listing.get("price") or facts.get("zestimate"))
			_apply_listing(row, ask, None, live)
			changed = True
		elif sale_date and _newer(sale_date, row.get("removed_date")):
			_apply_sale(row, sale.get("price"), sale_date)
			changed = True
		# Shape from /property, even when the sale date did not move — same
		# reason as _merge_one. Blank-only used to preserve ISTL's listing sqft.
		if facts.get("sqft"):
			row["square_footage"] = facts["sqft"]
			changed = True
		if facts.get("beds"):
			row["bedrooms"] = facts["beds"]
			changed = True
		if facts.get("baths"):
			row["bathrooms"] = facts["baths"]
			changed = True
		if facts.get("year_built"):
			row["year_built"] = facts["year_built"]
			changed = True
		if facts.get("lot_size"):
			row["lot_size"] = facts["lot_size"]
			changed = True
		if changed:
			row["recency_days"] = _comps()._recency_days(row, today)
			updated += 1
	return {"checked": checked, "updated": updated}


def attach_sale_history(rows, today=None):
	"""Give every row in `rows` its sale history. Mutates them. -> status dict.

	Called with the FINAL, already-filtered and already-capped board, because that
	is the first moment we know which comps a person is actually going to look at.
	The promise it exists to keep is that a pin on the map has complete
	information: a board where some pins carry a flip warning and others silently
	lack one is worse than a board with no warnings at all, because a missing
	warning reads as "this one is clean".

	That is also why a row whose lookup FAILED is marked `sale_history_missing`
	rather than left blank -- the UI says "unknown", never nothing.

	One billed `/property` per row, so the caller's cap is the bill. Shares the
	30-day `zillow_pin` cache with `refresh_pins`, so the pins that were already
	refreshed cost nothing here, and a lead reopened within the month is free.
	"""
	today = today or frappe.utils.today()
	info = {"checked": 0, "with_history": 0, "flips": 0, "missing": 0}
	rows = [r for r in (rows or []) if r.get("address")]
	if not rows or not zillow_api._api_key():
		for row in rows:
			row["sale_history"] = None
			row["sale_history_missing"] = True
		info["missing"] = len(rows)
		return info

	facts_by_address = _pin_facts_many([r["address"] for r in rows])
	for row in rows:
		info["checked"] += 1
		facts = facts_by_address.get(row["address"])
		# Year/beds/baths/sqft live on this same `/property` payload. Search almost
		# never carries yearBuilt, so dropping them here is how a board of
		# Zillow-origin pins rendered with no year even though we had already paid
		# for it. Measured on a live board: 0 of 50 Broken Arrow comps had a year;
		# the subject, from the same endpoint, had 2005.
		if facts:
			_apply_facts(
				row,
				{
					"square_footage": facts.get("sqft"),
					"bedrooms": facts.get("beds"),
					"bathrooms": facts.get("baths"),
					"year_built": facts.get("year_built"),
					"lot_size": facts.get("lot_size"),
				},
			)
			# Same billed /property payload. If Zillow still has the house on the
			# market, the pin is live even when RecentlySold search called it sold.
			live = _state_from_facts(facts)
			if live:
				# Don't copy last_listing.price — on an auction that is often a years-old
			# MLS ask. Auction pins get $0 (no ask); other live pins keep the number
			# they already had so a ForSale row's ask is not replaced with history.
				_apply_listing(row, 0 if live == "auction" else None, None, live)
		# Parsed HERE, from the raw events, on every read. The cache holds Zillow's
		# priceHistory, not our reading of it, so a parser fix costs nothing and the
		# ages inside are always computed against today rather than against whenever
		# the entry happened to be written.
		history = sale_history.parse(
			(facts or {}).get("price_history"), today, (facts or {}).get("home_status")
		)
		if not history:
			# Two different nothings, deliberately collapsed to one for the UI: an
			# address Zillow cannot resolve, and a house with no recorded history.
			# Neither lets us vouch for the comp, which is all the rep needs to know.
			row["sale_history"] = None
			row["sale_history_missing"] = True
			info["missing"] += 1
			continue
		row["sale_history"] = history
		row["sale_history_missing"] = False
		info["with_history"] += 1
		if row["sale_history"].get("flip"):
			info["flips"] += 1
		# A confirmed sale date from the history is better evidence than whatever the
		# pooled index had, but only ever used to FILL a gap here -- re-dating a comp
		# at this point would silently contradict the filter that already selected it.
		last = row["sale_history"].get("last_sale") or {}
		if last.get("date") and not row.get("removed_date") and row.get("status") != "Active":
			row["removed_date"] = last["date"]
		# Pending / listed often arrive from /search with daysOnZillow = -1 (unknown),
		# which we drop, so the pill had no DOM. The listing chain we just paid for
		# has the real clock -- fill the gap, never overwrite a number search already
		# gave us.
		if history.get("first_listed") and not row.get("listed_date"):
			row["listed_date"] = history["first_listed"]
		if history.get("days_on_market") is not None and row.get("days_on_market") is None:
			row["days_on_market"] = int(history["days_on_market"])
		row["recency_days"] = _comps()._recency_days(row, today)
	return info


def apply(doc, out, lat, lng, radius):
	"""A then B. Mutates `out`. Returns a small status dict for the UI."""
	info = {
		"used": False,
		"added": 0,
		"updated": 0,
		"sold": 0,
		"for_sale": 0,
		"pending": 0,
		"pins_checked": 0,
		"location": "",
		"cached": True,
		"reason": None,
		# The subject's OWN listing is usually inside its own search radius. We throw
		# the row away (a house is not a comp for itself) but its thumbnail is the
		# subject photo, free — the alternative is a billed `/property` lookup for a
		# picture we were already handed.
		"subject_photo": "",
	}
	if lat is None or lng is None:
		info["reason"] = "no_subject"
		return info
	if not zillow_api._api_key():
		info["reason"] = "not_configured"
		return info

	today = frappe.utils.today()
	self_keys = {merge_key(doc.get("property_address") or ""), merge_key(_comps()._full_address(doc))}
	self_keys.discard(merge_key(""))
	area = area_comps(lat, lng, radius)
	info["location"] = area.get("location") or ""
	info["cached"] = bool(area.get("cached"))
	incoming = []
	for row in (area.get("sold") or []) + (area.get("for_sale") or []):
		dist = _comps()._haversine_mi(lat, lng, row["lat"], row["lng"])
		if dist > radius:
			continue
		if merge_key(row.get("address") or "") in self_keys:
			if row.get("photo") and not info["subject_photo"]:
				info["subject_photo"] = row["photo"]
			continue
		row = dict(row)
		row["distance_mi"] = round(dist, 2)
		row["selected"] = False
		row["hidden"] = False
		row["recency_days"] = _comps()._recency_days(row, today)
		incoming.append(row)
		state = row.get("listing_state")
		if state == "pending":
			info["pending"] += 1
		elif row["status"] == "Active":
			info["for_sale"] += 1
		else:
			info["sold"] += 1

	by_key, by_ll = _index(out)
	for row in incoming:
		existing = _find(row, by_key, by_ll)
		if existing:
			if _merge_one(existing, row, today) == "updated":
				info["updated"] += 1
			continue
		out.append(row)
		by_key[merge_key(row.get("address") or "")] = row
		ll = _ll_key(row.get("lat"), row.get("lng"))
		if ll:
			by_ll[ll] = row
		info["added"] += 1

	pins = refresh_pins(out)
	info["pins_checked"] = pins["checked"]
	info["updated"] += pins["updated"]
	info["used"] = bool(info["added"] or info["updated"] or info["sold"] or info["for_sale"] or info["pins_checked"])
	return info


def apply_rentals(doc, out, lat, lng, radius):
	"""Fill `out` with current ForRent listings. No ISTL merge, no pin refresh.

	Sale history / BatchData / the pooled index are last *asks* and last *sales*.
	A rent is a different number; mixing the two on one map is how $1,450 reads
	as a $1k sale. This path is Zillow ForRent only, cached on its own key.
	"""
	info = {
		"used": False,
		"added": 0,
		"updated": 0,
		"sold": 0,
		"for_sale": 0,
		"for_rent": 0,
		"pending": 0,
		"pins_checked": 0,
		"location": "",
		"cached": True,
		"inventory": "rentals",
		"reason": "",
		"subject_photo": "",
	}
	if lat is None or lng is None:
		info["reason"] = "no_subject"
		return info
	if not zillow_api._api_key():
		info["reason"] = "not_configured"
		return info

	today = frappe.utils.today()
	self_keys = {merge_key(doc.get("property_address") or ""), merge_key(_comps()._full_address(doc))}
	self_keys.discard(merge_key(""))
	area = area_rentals(lat, lng, radius)
	info["location"] = area.get("location") or ""
	info["cached"] = bool(area.get("cached"))
	for row in area.get("for_rent") or []:
		dist = _comps()._haversine_mi(lat, lng, row["lat"], row["lng"])
		if dist > radius:
			continue
		if merge_key(row.get("address") or "") in self_keys:
			if row.get("photo") and not info["subject_photo"]:
				info["subject_photo"] = row["photo"]
			continue
		row = dict(row)
		row["distance_mi"] = round(dist, 2)
		row["selected"] = False
		row["hidden"] = False
		row["recency_days"] = _comps()._recency_days(row, today)
		out.append(row)
		info["added"] += 1
		info["for_rent"] += 1
	info["used"] = bool(info["added"])
	return info

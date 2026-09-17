# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Subject-property facts from Zillow, for the comps map.

Why this exists
---------------
The comps map compares a lead against nearby sales, but until now it could barely
describe the lead itself. The two sources it had are both weak:

  * the lead's own iSpeedToLead fields are pick-list LABELS, not numbers
    ("3 Bedroom", "1000 - 2000", "1900-1950"), so a "similar" filter built from
    them is a wide guess;
  * the property's own row in our comp inventory carries real numbers, but only
    ~5% of leads have one, and its price is the last ASK, not a sale.

Zillow answers both. `/property?address=` resolves our ordinary address strings
and returns real beds / baths / living area / year built / type / coordinates,
plus `priceHistory` — which contains genuine `event: "Sold"` rows sourced from
Public Record. That is an actual transaction with a date, which is what a rep
means by "what did it last sell for".

Cost, and the SHARED key
------------------------
OUR spend here is small: one lookup per lead, cached 30 days, so ~764 leads is a
few hundred requests a month even if every lead is opened.

The plan itself is a RapidAPI "Ultra" tier, 57,000 requests per billing cycle,
renewing on the 14th (anniversary-billed, not the 1st). That ceiling is NOT our
budget though — **the key is shared**. `istl-buyer/src/zillow_api.py` runs a
background ZIP-market job against the identical key (Infisical exposes it twice,
as `RAPIDAPI_ZILLOW_API_KEY` and `ZILLOW_RAPIDAPI_KEY` — verified byte-identical),
and it is the heavy consumer: 8,414 of 57,000 were already spent the day this
shipped, ~350/day, against ~10 from the CRM.

That job stops at `QUOTA_RESERVE = 5_000` remaining, commented "leave headroom
for the other Zillow-backed app that shares this RapidAPI key" — that app is now
us. So the CRM may use the band istl-buyer deliberately refuses to touch, but it
keeps its own smaller floor so a runaway loop here can never zero the account for
both apps.

One lookup per lead is plenty, so the normalized result is cached on the lead and
only refetched past CACHE_DAYS or when explicitly forced. Everything is
`has_field`-guarded, so the app is safe to deploy before the ops script adds the
cache fields — it just refetches each time until they exist.

Every failure path is soft. A Zillow outage, a quota exhaustion or an address
Zillow cannot resolve must degrade the popup back to the older sources, never
break the comps map.
"""

import json
import time

import frappe
from frappe import _

HOST = "us-property-market1.p.rapidapi.com"
BASE = f"https://{HOST}"

#: Property facts barely move; a sale a week old is still news next month.
CACHE_DAYS = 30
TIMEOUT = 20

#: Stop fetching once the SHARED plan is this close to empty. Deliberately far
#: below istl-buyer's 5,000 reserve: that reserve exists to leave room for THIS
#: app, so matching it would strand 5,000 requests neither app is willing to
#: spend. Below this floor the comps map degrades to its older fact sources
#: rather than taking the last of a budget the ZIP-market job may still need.
QUOTA_RESERVE = 500
#: RapidAPI reports remaining quota on every response, so caching it briefly
#: makes the guard free — and it reflects the OTHER app's spending too.
#:
#: GOTCHA — this is stored WITHOUT `expires_in_sec`, and freshness is judged from
#: a timestamp we store alongside it. `frappe.cache().get_value()` memoizes a MISS
#: as None into the per-request `frappe.local.cache`, while
#: `set_value(..., expires_in_sec=...)` writes only to Redis — so the poisoned
#: local None then shadows the value that is demonstrably sitting in Redis, and
#: the guard silently never fires. (`get_value(..., expires=True)` does NOT help;
#: it checks the local dict first regardless.) Storing with no TTL populates the
#: local cache too, which overwrites the poisoned entry.
_QUOTA_KEY = "zillow_quota_remaining"
_QUOTA_TTL = 900

#: Zillow's enum -> the vocabulary our comp inventory uses, so the subject's type
#: can be fed straight into the comps "Type" filter and actually match.
#: (Measured comp values: Single Family, Townhouse, Condo, Multi-Family,
#: Manufactured, Land, Apartment.)
HOME_TYPES = {
	"SINGLE_FAMILY": "Single Family",
	"TOWNHOUSE": "Townhouse",
	"CONDO": "Condo",
	"CONDOMINIUM": "Condo",
	"MULTI_FAMILY": "Multi-Family",
	"MANUFACTURED": "Manufactured",
	"MOBILE": "Manufactured",
	"LOT": "Land",
	"LAND": "Land",
	"VACANT_LAND": "Land",
	"APARTMENT": "Apartment",
}

CACHE_FIELDS = ("zillow_facts", "zillow_fetched_at", "zillow_zpid")


def _num(v):
	"""Zillow uses null for unknown; it also hands back strings like '1,438 sqft'."""
	if v is None or v == "":
		return None
	if isinstance(v, (int, float)):
		return float(v)
	try:
		import re

		m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v))
		return float(m.group().replace(",", "")) if m else None
	except Exception:
		return None


def _is_for_auction(raw):
	"""Zillow listingSubType.is_forAuction — search and /property share the flag."""
	sub = (raw or {}).get("listingSubType") or (raw or {}).get("listing_sub_type") or {}
	if not isinstance(sub, dict):
		return False
	return bool(sub.get("is_forAuction") or sub.get("is_for_auction"))


def lot_size_label(src, reso=None):
	"""A display string for lot size, from a /search row or a /property blob.

	resoFacts.lotSize is the one we want when it is there ("4,530 sqft"). Search
	rows often skip it and only carry lotAreaValue + lotAreaUnit; without this
	fallback the tray had nothing to print even though Zillow had already told us.
	"""
	if not isinstance(src, dict):
		src = {}
	if reso is None:
		reso = src.get("resoFacts") if isinstance(src.get("resoFacts"), dict) else {}
	for bag in (reso or {}, src):
		raw = bag.get("lotSize") or bag.get("lot_size")
		if isinstance(raw, str) and raw.strip():
			return raw.strip()
	val = _num(
		(reso or {}).get("lotAreaValue")
		or (reso or {}).get("lotSizeValue")
		or src.get("lotAreaValue")
		or src.get("lotSizeValue")
	)
	if not val or val <= 0:
		return None
	unit = str(
		(reso or {}).get("lotAreaUnit")
		or src.get("lotAreaUnit")
		or src.get("lotSizeUnit")
		or "sqft"
	).lower()
	if "acre" in unit:
		n = int(val) if float(val) == int(val) else round(val, 2)
		return f"{n} acre" if n == 1 else f"{n} acres"
	return f"{int(round(val)):,} sqft"


def _has_cache() -> bool:
	return all(frappe.db.has_column("CRM Lead", f) for f in CACHE_FIELDS)


def _api_key():
	return frappe.conf.get("rapidapi_zillow_key") or ""


def quota_remaining():
	"""Last known remaining requests on the SHARED plan, or None if unknown.

	Taken from the response headers of our most recent call, so it also reflects
	whatever istl-buyer's ZIP job has spent since — which is the point.
	"""
	try:
		rec = frappe.cache().get_value(_QUOTA_KEY)
		if not isinstance(rec, dict):
			return None
		# Expiry enforced here rather than by Redis — see the GOTCHA on _QUOTA_KEY.
		if time.time() - float(rec.get("t") or 0) > _QUOTA_TTL:
			return None
		return int(rec["n"])
	except Exception:
		return None


def _store_quota(n):
	"""Remember what RapidAPI last said was left. Called only on a real thread."""
	if n is None:
		return
	try:
		frappe.cache().set_value(_QUOTA_KEY, {"n": int(n), "t": time.time()})
	except Exception:
		pass


#: A 403 from RapidAPI is "You are not subscribed to this API" — a dead
#: subscription or a rotated key, and nothing about it is transient. Once seen,
#: every Zillow call for the next `_OUTAGE_HOLD` seconds is skipped WITHOUT a
#: request. Measured 2026-09-16/17, when the subscription lapsed: ~275 failed
#: calls an hour overnight from the Today-board prewarm alone, 5,000 Error Log
#: rows a day, and every rep's comps open waited on 30 calls that were all going
#: to fail. Same no-TTL storage rule as `_QUOTA_KEY` (see the GOTCHA there).
_OUTAGE_KEY = "zillow_outage"
_OUTAGE_HOLD = 600


def outage_reason():
	"""Why Zillow is being skipped right now, or None. Redis only — never HTTP."""
	try:
		rec = frappe.cache().get_value(_OUTAGE_KEY)
		if not isinstance(rec, dict):
			return None
		if time.time() - float(rec.get("t") or 0) > _OUTAGE_HOLD:
			return None
		return rec.get("reason") or "outage"
	except Exception:
		return None


def _mark_unavailable(reason):
	"""Remember, for THIS request, that Zillow gave no answer.

	`frappe.flags` is per-request, so the comps response can say "Zillow is down"
	out loud instead of letting a failed lookup read as "Zillow does not know
	this address" — which is what sent a rep asking the seller to re-confirm a
	perfectly good address during the 2026-09-16 outage.
	"""
	try:
		if not getattr(frappe.flags, "zillow_unavailable", None):
			frappe.flags.zillow_unavailable = reason
	except Exception:
		pass


def unavailable_reason():
	"""Did Zillow fail to answer in this request (or is it held off)? -> reason|None."""
	try:
		return getattr(frappe.flags, "zillow_unavailable", None) or outage_reason()
	except Exception:
		return None


def _note_failures(errors):
	"""Inspect the tracebacks `_raw_get` handed back. Request thread only.

	Only a 403 opens the hold: it is unambiguous. A 429 is left alone because one
	throttled page in a batch is ordinary (the key is shared with the ZIP job)
	and `_raw_get` already retried it once.
	"""
	errors = [e for e in (errors or []) if e]
	if not errors:
		return
	if any("HTTP Error 403" in e for e in errors):
		reason = "not_subscribed"
		try:
			if outage_reason() is None:
				frappe.log_error(
					"RapidAPI answered 403 (not subscribed / bad key). Skipping every "
					f"Zillow call for {_OUTAGE_HOLD // 60} minutes. Check the RapidAPI "
					"subscription for us-property-market1.",
					"Zillow: subscription unavailable",
				)
			frappe.cache().set_value(_OUTAGE_KEY, {"reason": reason, "t": time.time()})
		except Exception:
			pass
	elif all("HTTP Error 429" in e for e in errors):
		reason = "throttled"
	else:
		reason = "error"
	_mark_unavailable(reason)


#: A throttled call is a TRANSIENT refusal, not an answer, and dropping one is
#: expensive twice over: the page's ~40 comps vanish from the map, and the circle
#: is then marked incomplete so the week-long cache is never written and the next
#: open re-pays for everything. One patient retry is far cheaper than either.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_RETRY_BACKOFF = 1.5


def _raw_get(key: str, path: str, params: dict, retries: int = 1):
	"""One RapidAPI GET with NO Frappe involvement. -> (body, remaining, error).

	Deliberately pure: this is the only thing `fetch_many` runs on a worker thread,
	and `frappe.local` is a thread-local proxy — a worker has no site, no database
	connection and no cache, so touching `frappe.conf`, `frappe.cache()` or
	`frappe.log_error` from one raises instead of degrading. The key, the quota
	guard, the quota update and the error logging all stay on the calling thread.
	"""
	import urllib.error
	import urllib.parse
	import urllib.request

	url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
	req = urllib.request.Request(
		url, headers={"x-rapidapi-key": key, "x-rapidapi-host": HOST}
	)
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
			body = json.loads(resp.read().decode("utf-8", "replace"))
			try:
				remaining = int(resp.headers.get("X-RateLimit-Requests-Remaining"))
			except (TypeError, ValueError):
				remaining = None
			return body, remaining, None
	except urllib.error.HTTPError as e:
		if retries > 0 and e.code in _RETRY_STATUS:
			# Plain `time.sleep` is safe here precisely because this function touches
			# nothing but the socket — it parks one worker thread, not the request.
			time.sleep(_RETRY_BACKOFF)
			return _raw_get(key, path, params, retries - 1)
		import traceback

		return None, None, traceback.format_exc()
	except Exception:
		import traceback

		return None, None, traceback.format_exc()


def _quota_blocked(path: str):
	"""True when the SHARED plan is too close to empty to spend anything here."""
	held = outage_reason()
	if held:
		# Deliberately no log line: the hold exists to STOP the flood, and the one
		# entry that matters was written when the 403 was first seen.
		_mark_unavailable(held)
		return True
	left = quota_remaining()
	if left is None or left > QUOTA_RESERVE:
		return False
	_mark_unavailable("quota")
	frappe.log_error(
		f"Zillow quota reserve reached ({left} left <= {QUOTA_RESERVE}); skipping "
		f"{path}. Key is shared with istl-buyer's ZIP-market job.",
		"Zillow: quota reserve",
	)
	return True


def _request(path: str, params: dict, error_title: str):
	"""One guarded RapidAPI GET, or None. Every caller degrades softly."""
	key = _api_key()
	if not key:
		_mark_unavailable("not_configured")
		return None
	if not params:
		return None

	# Yield the last of a shared budget rather than spend it: both subject facts and
	# an on-demand comp gallery are optional, whereas istl-buyer's batch job cannot
	# degrade at all. This check runs before EACH request, so a property lookup that
	# lands on the reserve does not spend one more request fetching its photos.
	if _quota_blocked(path):
		return None

	body, remaining, error = _raw_get(key, path, params)
	_store_quota(remaining)
	if error:
		_note_failures([error])
		frappe.log_error(error, error_title)
		return None
	return body


#: How many RapidAPI calls may be in flight at once for one page load.
#:
#: Every call here is ~1.35s of pure network wait, and they were being made one
#: after another: a 2-mile comps circle is ~30 calls, which measured 40.5s on
#: production while the CPU did nothing. Threads are the right tool precisely
#: because the work is I/O — each one blocks in `urlopen` with the GIL released.
#:
#: FOUR, and that number is measured, not guessed. Nineteen pages fetched eight
#: at a time came back with THIRTEEN failures — `HTTP 429: Too Many Requests` —
#: which is worse than being slow, because each dropped page silently removes ~40
#: comps from the map. Sweeping the same eight calls across worker counts:
#:
#:   1 -> 8/8 in 9.5s    3 -> 8/8 in 3.3s    6 -> 8/8 in 2.2s
#:   2 -> 8/8 in 5.0s    4 -> 8/8 in 2.2s    8 -> 7/8 in 1.6s + a 429
#:
#: Four is where the curve flattens: it is the full 4.3x speedup, six buys
#: literally nothing on top of it, and eight starts losing data. The key is also
#: SHARED with istl-buyer's ZIP job, so the limit we are near is not ours alone.
FETCH_WORKERS = 4


def fetch_many(specs, error_title="Zillow: batch request failed", workers=FETCH_WORKERS):
	"""Run several independent RapidAPI GETs at once. [(path, params)] -> [body|None].

	Results come back in the order asked for, with `None` in the slot of anything
	that failed, so a caller can tell a partial answer from a complete one and
	decide whether it is safe to cache.

	The quota is checked ONCE, before the batch, rather than before each call as the
	serial path does: the whole point is that these are in flight together, so there
	is no "before" to check in between. The reserve is 500 and a batch is at most a
	few dozen, so the floor still cannot be crossed by more than one batch.
	"""
	from concurrent.futures import ThreadPoolExecutor

	specs = list(specs or [])
	if not specs:
		return []
	key = _api_key()
	if not key:
		_mark_unavailable("not_configured")
		return [None] * len(specs)
	if _quota_blocked(specs[0][0]):
		return [None] * len(specs)

	with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(specs)))) as pool:
		results = list(pool.map(lambda s: _raw_get(key, s[0], s[1]), specs))

	# Back on the request thread, where Frappe is usable again.
	remaining = [r for _, r, _ in results if r is not None]
	if remaining:
		# The LOWEST reading is the truthful one: the responses raced, so the
		# smallest number is the furthest the plan actually got drawn down.
		_store_quota(min(remaining))
	errors = [e for _, _, e in results if e]
	if errors:
		_note_failures(errors)
		# One log line for the batch, not one per call: a Zillow outage would
		# otherwise write 30 identical tracebacks per page load.
		frappe.log_error(
			f"{len(errors)} of {len(specs)} calls failed.\n\n{errors[0]}", error_title
		)
	return [body for body, _, _ in results]


def _fetch(address: str):
	"""One address -> Zillow's raw property blob, or None. Never raises."""
	if not address:
		return None
	return _request("/property", {"address": address}, "Zillow: property lookup failed")


def property_details(address: str):
	"""Raw details for an explicitly opened comp. Kept separate from lead caching."""
	return _fetch(address)


def property_photos(zpid):
	"""Raw `/photos` response for a Zillow property id, or None on any failure."""
	if not zpid:
		return None
	body = _request("/photos", {"zpid": zpid}, "Zillow: photo lookup failed")
	# RapidAPI can report endpoint errors inside an HTTP-200 JSON body. Treat only
	# the documented list shape as success; `{status:'error', errors:[…]}` must get
	# the short retry cache rather than hiding photos for 30 days.
	return body if isinstance(body, dict) and isinstance(body.get("photos"), list) else None


def _price_history(raw):
	"""Split priceHistory into the last real SALE and the last LISTING event.

	GOTCHA — the top-level `dateSold` / `lastSoldPrice` are null even on homes
	whose priceHistory plainly contains Sold rows (verified on prod addresses), so
	the history is the only reliable source. `event` is a display string, hence
	the lowercase compare rather than an equality test on a code.
	"""
	events = raw.get("priceHistory") or []
	sale = listing = None
	for e in events:  # newest first as returned
		if not isinstance(e, dict):
			continue
		kind = str(e.get("event") or "").strip().lower()
		price, date = _num(e.get("price")), e.get("date")
		if not date:
			continue
		if sale is None and kind == "sold":
			sale = {"price": price, "date": date, "source": e.get("source")}
		elif listing is None and "list" in kind:
			# "Listed for sale" / "Listing removed" both describe the ask.
			listing = {
				"price": price,
				"date": date,
				"event": e.get("event"),
				"source": e.get("source"),
			}
		if sale and listing:
			break
	return sale, listing


#: Kept per priceHistory event, and no more. This is CACHED for 30 days, so it is
#: the raw material every later question is answered from.
_HISTORY_FIELDS = ("date", "event", "price", "source", "postingIsRental")


def _slim_history(raw):
	"""The part of `priceHistory` worth keeping. Cached INSTEAD of a parse of it.

	Caching the raw events rather than `sale_history.parse()`'s output is a
	deliberate trade, and it was made after getting it the other way round first:

	  * a parser fix is then FREE. The first version cached the parse, so
	    discovering that a chain must not span a rental (15256 Edmore Dr was
	    reporting 1,115 days to sell) meant every cached property had to be
	    re-fetched -- one billed call each -- to correct a pure arithmetic bug.
	  * ages cannot go stale. Two of the parse's numbers are relative to today, so
	    a cached parse quietly drifts by up to a month. Parsing on read means the
	    answer is always computed against the date being asked about.

	The cost is a few hundred bytes per property, against a payload we already keep
	photos and coordinates in.
	"""
	out = []
	for e in (raw or {}).get("priceHistory") or []:
		if not isinstance(e, dict) or not e.get("date"):
			continue
		out.append({k: e.get(k) for k in _HISTORY_FIELDS})
	return out


def _normalize(raw):
	"""Zillow's blob -> the small, flat shape the comps map actually consumes."""
	if not isinstance(raw, dict) or not raw.get("zpid"):
		return None
	reso = raw.get("resoFacts") or {}
	sale, listing = _price_history(raw)

	home_type = str(raw.get("homeType") or "").strip().upper()
	# GOTCHA — on an off-market home `price` mirrors taxAssessedValue rather than
	# any list price (verified: Macon price 6005 == taxAssessedValue 6005). It is
	# deliberately NOT surfaced as a price.
	return {
		"zpid": raw.get("zpid"),
		"beds": _num(raw.get("bedrooms")),
		"baths": _num(raw.get("bathrooms")),
		"sqft": _num(raw.get("livingArea") or raw.get("livingAreaValue")),
		"year_built": _num(raw.get("yearBuilt")),
		"property_type": HOME_TYPES.get(home_type) or (home_type.title().replace("_", " ") or None),
		"lot_size": lot_size_label(raw, reso),
		"lat": _num(raw.get("latitude")),
		"lng": _num(raw.get("longitude")),
		"zestimate": _num(raw.get("zestimate")),
		"rent_zestimate": _num(raw.get("rentZestimate")),
		"tax_assessed_value": _num(reso.get("taxAssessedValue") or raw.get("taxAssessedValue")),
		"last_sale": sale,
		"last_listing": listing,
			"home_status": raw.get("homeStatus"),
		"is_for_auction": _is_for_auction(raw),
		"address": raw.get("streetAddress"),
		# Carried so a lead whose facts we already paid for can show its own photo
		# without a second call. Leads cached before this shipped simply have no key
		# and fall back to the area search's self-match, which is also free.
		"cover_photo": raw.get("imgSrc") or "",
		# The SAME `priceHistory` we already pay for, kept so time-on-market and flips
		# can be derived later for free. Parsed on READ, never here -- see _slim_history.
		"price_history": _slim_history(raw),
	}


def normalize_detail(raw):
	"""Zillow's large property blob -> the useful facts in a comp detail panel."""
	if not isinstance(raw, dict) or not raw.get("zpid"):
		return None
	reso = raw.get("resoFacts") or {}
	sale, listing = _price_history(raw)
	home_type = str(raw.get("homeType") or "").strip().upper()
	status = str(raw.get("homeStatus") or "").strip().upper()
	# Zillow's top-level `price` is an assessed value on many off-market homes.
	# Only call it an asking price while the property is actually marketed.
	asking_statuses = {"FOR_SALE", "FOR_RENT", "PENDING", "CONTINGENT", "COMING_SOON"}
	url = raw.get("url") or ""
	if url.startswith("/"):
		url = "https://www.zillow.com" + url
	address = raw.get("address") if isinstance(raw.get("address"), dict) else {}

	def text_list(value):
		if isinstance(value, list):
			return ", ".join(str(v) for v in value if v)
		return str(value or "")

	return {
		"zpid": raw.get("zpid"),
		"address": raw.get("streetAddress") or address.get("streetAddress"),
		"city": raw.get("city") or address.get("city"),
		"state": raw.get("state") or address.get("state"),
		"zip": raw.get("zipcode") or address.get("zipcode"),
		"beds": _num(raw.get("bedrooms")),
		"baths": _num(raw.get("bathrooms")),
		"sqft": _num(raw.get("livingArea") or raw.get("livingAreaValue")),
		"year_built": _num(raw.get("yearBuilt")),
		"property_type": HOME_TYPES.get(home_type) or (home_type.title().replace("_", " ") or None),
		"lot_size": lot_size_label(raw, reso),
		"home_status": raw.get("homeStatus"),
		"is_for_auction": _is_for_auction(raw),
		"asking_price": _num(raw.get("price")) if status in asking_statuses else None,
		"zestimate": _num(raw.get("zestimate")),
		"rent_zestimate": _num(raw.get("rentZestimate")),
		"hoa_fee": _num(raw.get("monthlyHoaFee") or reso.get("hoaFee")),
		"parking": text_list(reso.get("parkingFeatures")),
		"heating": text_list(reso.get("heating")),
		"cooling": text_list(reso.get("cooling")),
		"description": raw.get("description") or "",
		"last_sale": sale,
		"last_listing": listing,
		# A rep who has opened the photos is the one most likely to be asking "why did
		# this sell for that?", so the history rides along here too -- raw, and parsed
		# on read for the same two reasons `_slim_history` explains.
		"price_history": _slim_history(raw),
		"zillow_url": url,
		"cover_photo": raw.get("imgSrc") or "",
		"photo_count": int(_num(raw.get("photoCount")) or 0),
	}


def photo_urls(raw, limit=60):
	"""Choose one useful Zillow CDN URL per photo, preferring ~1152px JPEGs."""
	if not isinstance(raw, dict):
		return []
	out = []
	for photo in raw.get("photos") or []:
		sources = (photo or {}).get("mixedSources") or {}
		options = sources.get("jpeg") or sources.get("webp") or []
		options = [o for o in options if isinstance(o, dict) and o.get("url")]
		if not options:
			continue
		# Big enough to inspect without pulling the largest 1536px image into a
		# modal. If all variants are smaller/larger, take the nearest useful one.
		under = [o for o in options if float(o.get("width") or 0) <= 1152]
		chosen = max(under or options, key=lambda o: float(o.get("width") or 0))
		if chosen["url"] not in out:
			out.append(chosen["url"])
		if len(out) >= int(limit):
			break
	return out


# Bumped when `_normalize` starts carrying a field worth re-fetching a lead for.
# A cached blob without every key here is treated as stale, which spends ONE
# lookup per lead and then rides the normal 30-day cache. Cheaper than the
# alternative it replaces (a per-comp `/property` call just to get a picture),
# and self-limiting because the refetch rewrites the cache with the key present.
REQUIRED_FACT_KEYS = ("cover_photo", "price_history")


def _cached(doc):
	if not _has_cache() or not doc.get("zillow_facts"):
		return None
	fetched = doc.get("zillow_fetched_at")
	if fetched:
		try:
			if frappe.utils.date_diff(frappe.utils.nowdate(), str(fetched)[:10]) > CACHE_DAYS:
				return None
		except Exception:
			pass
	try:
		hit = json.loads(doc.get("zillow_facts"))
	except Exception:
		return None
	# A remembered negative is `{}` (maybe with `_queried_address`) and must stay
	# cheap. Only a REAL hit missing a later-added key is stale — treating a miss
	# as stale would re-bill every unresolvable address on every open.
	if isinstance(hit, dict) and hit.get("zpid") and any(
		k not in hit for k in REQUIRED_FACT_KEYS
	):
		return None
	return hit


def _store(doc, facts):
	if not _has_cache():
		return
	payload = dict(facts or {})
	try:
		from crm.api.comps import _full_address

		payload["_queried_address"] = _full_address(doc)
	except Exception:
		pass
	try:
		frappe.db.set_value(
			doc.doctype, doc.name,
			{
				"zillow_facts": json.dumps(payload),
				"zillow_fetched_at": frappe.utils.now(),
				"zillow_zpid": str(payload.get("zpid") or ""),
			},
			# A cached lookup is not a human edit; same rule as the geocode cache.
			update_modified=False,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Zillow: caching facts failed")


def facts_for_lead(doc, force=False):
	"""Normalized Zillow facts for a lead, cached. Returns None when unavailable.

	`doc` is a CRM Lead document. Safe to call on every comps-map open: past the
	first fetch this is a JSON parse off a column. A miss is `{}` (plus the
	address we asked about), never None, once Zillow has ANSWERED — so the UI can
	tell "Zillow does not know this house" from "we have not asked yet".

	None means Zillow gave no answer at all (no key, quota floor, outage hold, an
	HTTP error). That is deliberately NOT cached: a genuine miss is an HTTP 200
	with no zpid (measured — 16 historic negatives, 7 lookup failures ever), so a
	None body is never "no such house". Caching it as one stamped 28 leads with
	"Zillow doesn't recognize this address" for 30 days during the 2026-09-16
	subscription outage, and sent a rep asking the seller to re-confirm an
	address that was fine.
	"""
	if not force:
		hit = _cached(doc)
		if hit is not None:
			return hit
	if not _api_key():
		_mark_unavailable("not_configured")
		return None

	from crm.api.comps import _full_address

	raw = _fetch(_full_address(doc))
	if raw is None:
		_mark_unavailable(unavailable_reason() or "error")
		return None
	# Zillow answered. No zpid in the answer is a genuine miss: `{}`, cached, so
	# an address Zillow cannot resolve is not re-fetched (and re-billed) on every
	# single modal open.
	facts = _normalize(raw) or {}
	_store(doc, facts)
	return facts


def clear_failed_negatives(since, dry_run=1):
	"""Repair: drop the negatives an outage wrote as "address not found".

	Bench-executable. Clears the facts cache on every lead whose stamp is at/after
	`since` and carries no zpid, so the next comps open asks Zillow again (one
	billed call each). A genuine miss caught in the window is re-asked once too —
	that is the whole cost, and it is a fraction of one rep's wrong phone call.

	    bench execute crm.api.zillow.clear_failed_negatives \\
	      --kwargs '{"since": "2026-09-16 10:00:00", "dry_run": 0}'
	"""
	if not _has_cache():
		return {"cleared": 0, "dry_run": bool(int(dry_run)), "leads": []}
	rows = frappe.get_all(
		"CRM Lead",
		filters=[
			["zillow_fetched_at", ">=", since],
			["zillow_zpid", "in", ["", None]],
		],
		fields=["name", "property_address", "zillow_fetched_at"],
		order_by="zillow_fetched_at asc",
	)
	if not int(dry_run):
		for r in rows:
			frappe.db.set_value(
				"CRM Lead", r["name"],
				{"zillow_facts": "", "zillow_fetched_at": None, "zillow_zpid": ""},
				update_modified=False,
			)
		frappe.db.commit()
	return {
		"cleared": len(rows),
		"dry_run": bool(int(dry_run)),
		"leads": [f"{r['name']} · {r['property_address']}" for r in rows],
	}


def _clear_location_caches(lead):
	"""Drop the geo + BatchData answers so a new address is looked up fresh."""
	updates = {}
	if frappe.db.has_column("CRM Lead", "property_lat"):
		updates["property_lat"] = None
		updates["property_lng"] = None
	if frappe.db.has_column("CRM Lead", "batchdata_comps"):
		updates["batchdata_comps"] = ""
	if frappe.db.has_column("CRM Lead", "batchdata_comps_fetched_at"):
		updates["batchdata_comps_fetched_at"] = None
	if updates:
		from crm.api.comps import subject_doctype

		frappe.db.set_value(subject_doctype(lead), lead, updates, update_modified=False)


@frappe.whitelist()
def refresh_lead_facts(lead):
	"""Force a re-fetch for one lead (whitelisted so a button can offer it).

	Clears the geocode and BatchData caches first: the button exists because the
	address just changed, and leaving those in place would re-center the map on
	the old parcel and skip the sale fallback for a miss we no longer mean.
	"""
	from crm.api.comps import _full_address, _guard, _load_subject

	_guard()
	doc = _load_subject(lead)
	_clear_location_caches(lead)
	doc.reload()
	facts = facts_for_lead(doc, force=True)
	unavailable = unavailable_reason() if facts is None else None
	facts = facts or {}
	return {
		"ok": True,
		"matched": bool(facts.get("zpid")),
		"zpid": str(facts.get("zpid") or ""),
		"address": _full_address(doc),
		"queried_address": facts.get("_queried_address") or _full_address(doc),
		# Zillow gave no answer (outage / quota / key) — the address was NOT judged.
		"unavailable": unavailable,
	}

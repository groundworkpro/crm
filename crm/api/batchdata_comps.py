# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""BatchData comps — the fallback for leads our pooled index cannot cover.

Why this exists
---------------
`crm.api.comps` serves a POOLED AREA INDEX of iSpeedToLead/RentCast comparables.
It covers most of the book, but not all of it: sampling the 45 most recent leads,
**8 (18%) returned zero comps** — Albany/Brooklyn/Rochester NY, High Point and
Glade Valley NC, Avondale AZ, Newnan GA, Warsaw MO. Those reps open the map and
get nothing at all, which is the one outcome `comps.py` set out to avoid.

It also fills the non-disclosure hole: Zillow RecentlySold in LA (and the other
ND states) returns solds with `price: null`, which `_shape_search` drops, so the
map can be full of ISTL last-asks and still have **zero Sold pins**. ISTL asks
do not suppress this path — they are not sales.

What it costs
-------------
Billing is PER ROW RETURNED at the sum of the datasets enabled on the token. The
`comps` token (Basic Property Data + Comparable Properties) measured at
**$0.03/row**, verified by wallet-balance deltas. `take=5` is **$0.15** per lead
that has no priced Zillow solds. Cached, so each lead is paid for once.

The sale-date window is applied SERVER-SIDE (`sale.lastSaleDate.minDate/maxDate`),
so we only pay for rows already inside it rather than buying 25 and discarding the
stale ones. Measured: 24% cheaper for the same answer.

Caching, including the empty answer
-----------------------------------
Same rule the Zillow cache learned the hard way: **a miss is cached too.** An
address BatchData cannot match would otherwise be re-billed on every modal open.

WHERE: PropWarehouse (`GET /batchdata/comps`), keyed on the ADDRESS, since
2026-09-29. It used to live on the CRM Lead (`batchdata_comps`), which made a
purchase belong to one record in one system — Knock, Radar, LeadMarket and a
CRM Property for the same house could not see it and would buy it again.

The lead field is now READ-ONLY: a lead that paid before the move keeps its
comps until that answer ages out, and nothing writes it again. The one
exception is the old direct path below, used only when the warehouse cannot
answer at all (unreachable, or it has no BatchData key) — the same
`payload_or_fallback` contract as the Zillow and Realtor lookups.
"""

import json
import time
import urllib.error
import urllib.request

import frappe
from frappe import _

API_BASE = "https://api.batchdata.com/api/v1"

#: Cheap, purpose-built token: Basic Property Data + Comparable Properties ONLY.
#: Do NOT point this at the general BatchData key — that one carries all 13
#: datasets and bills $0.64/row, 21x more, for data this feature never reads.
CONF_KEY = "batchdata_comps_api_key"

#: Billed per row returned. This now fires on every lead whose Zillow solds have
#: no prices (ND states), not only empty maps, so five is the spend dial — $0.15
#: rather than $0.30. take=25 was tried once and capped back.
DEFAULT_TAKE = 5

#: Hard ceiling on how far a "comp" may be. `compAddress` has NO radius control
#: and has been observed matching out to ~3mi, so this is enforced by us or not at
#: all. It DROPS rather than pads: four honest comps beat ten with three from
#: across town.
MAX_MILES = 2.0

#: How many survive ranking and reach the map. Matches take so we do not buy a
#: row and then throw it away.
KEEP = 5

#: Two years, not one. Measured on a real San Antonio subject: a 12-month window
#: left 3 usable comps and read ~4% low, while 2 years gave 9-11 and converged.
#: "No limit" is also wrong — decade-old sales drag the median down.
SALE_WINDOW_DAYS = 730

#: Shown to the rep in the provenance banner. Says only the WINDOW — both callers
#: already say "recorded sales", and repeating it read as
#: "recorded sales from BatchData (recorded sales, last 2 years)".
WINDOW_LABEL = "last 2 years"

#: Cache fields on CRM Lead. Absent until the ops script adds them, in which case
#: the whole feature degrades quietly — same contract as `comps._state_supported`.
CACHE_FIELD = "batchdata_comps"
CACHE_STAMP_FIELD = "batchdata_comps_fetched_at"

#: A found answer is stable — comps do not un-sell. A MISS is re-checked sooner,
#: because "no comparable sales yet" is a statement about time, not about the
#: property, and a new subdivision gets its first sales eventually.
HIT_TTL_DAYS = 90
MISS_TTL_DAYS = 14

HTTP_TIMEOUT = 20


def _api_key() -> str:
	return frappe.conf.get(CONF_KEY) or ""


def _cache_supported() -> bool:
	return frappe.db.has_column("CRM Lead", CACHE_FIELD) and frappe.db.has_column(
		"CRM Lead", CACHE_STAMP_FIELD
	)


def available() -> bool:
	"""True when this fallback can actually run. Callers degrade quietly."""
	if _api_key():
		return True
	try:
		from crm.api import vendor_facts

		return bool(vendor_facts._base_url())
	except Exception:
		return False


def _query(doc):
	"""(street, city, state, zip) for a subject, or None when it cannot be comped."""
	street = (doc.get("property_address") or "").split(",")[0].strip()
	city = (doc.get("property_city") or "").strip()
	state = (doc.get("property_state") or "").strip()
	zipc = str(doc.get("property_zip") or "").strip()
	if not street or not (zipc or (city and state)):
		return None
	return street, city, state, zipc


def _warehouse(doc, spend, force=False):
	"""The warehouse envelope for this subject's address, memoized per request.

	`cached_at` then `fetch_for_lead` is the normal pair on one map open; the
	memo keeps that to one store read plus, at most, one purchase.
	"""
	q = _query(doc)
	if not q:
		return None
	memo = getattr(frappe.local, "batchdata_warehouse", None)
	if memo is None:
		memo = {}
		frappe.local.batchdata_warehouse = memo
	k = tuple(p.lower() for p in q)
	bought = memo.get(k + (True,))
	if bought is not None and not force:
		return bought
	if not spend and k + (False,) in memo:
		return memo[k + (False,)]
	from crm.api import vendor_facts

	env = vendor_facts.batchdata_comps(*q, take=DEFAULT_TAKE, spend=spend, force=force, caller="crm")
	if env is not None:
		memo[k + (bool(spend),)] = env
	return env


def _epoch(iso):
	try:
		return frappe.utils.get_datetime(iso).timestamp()
	except Exception:
		return None


def _shape_all(rows):
	comps = [_shape(r, i) for i, r in enumerate(rows or []) if isinstance(r, dict)]
	# Only rows we can actually place on a map and price are worth showing; the
	# rest still cost us, which is why the window is applied server-side.
	return [c for c in comps if c["lat"] is not None and c["lng"] is not None and c["price"]]


def _warehouse_comps(env):
	"""Shaped comps from an owned envelope; [] for a miss or a failure."""
	if not env or not env.get("ok") or not env.get("matched"):
		return []
	return _shape_all((env.get("payload") or {}).get("properties"))


# ---------------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------------
def _cached(doc):
	"""Prior answer for this lead, or None when there is nothing usable on file."""
	if not _cache_supported() or not doc.get(CACHE_FIELD):
		return None
	try:
		payload = json.loads(doc.get(CACHE_FIELD))
	except Exception:
		return None
	if not isinstance(payload, dict):
		return None

	age_days = (time.time() - float(payload.get("t") or 0)) / 86400.0
	ttl = HIT_TTL_DAYS if payload.get("comps") else MISS_TTL_DAYS
	if age_days > ttl:
		return None
	return payload


def cached_comps(doc):
	"""Comps already bought for this address, or []. Never calls the API.

	For boards that do not NEED the fallback: once a house has paid for recorded
	sales they stay on it, even after a vendor starts returning its own priced
	solds. Without this, the paid rows silently vanished the moment one Redfin
	sale appeared (Myesha Moore, Wichita KS, 2026-09-28). Bought by ANY system:
	the warehouse row is shared.
	"""
	hit = _cached(doc)
	if hit is not None:
		return hit.get("comps") or []
	return _warehouse_comps(_warehouse(doc, spend=False))


def cached_at(doc):
	"""Epoch the saved answer was bought, or None. Free; never calls the API."""
	hit = _cached(doc)
	if hit is not None:
		try:
			return float(hit["t"]) if hit.get("t") else None
		except (TypeError, ValueError):
			return None
	env = _warehouse(doc, spend=False)
	if env and env.get("ok") and env.get("source") == "store" and env.get("fetched_at"):
		return _epoch(env["fetched_at"])
	return None


def _store(doc, comps):
	if not _cache_supported():
		return
	payload = {"t": time.time(), "comps": comps}
	try:
		frappe.db.set_value(
			doc.doctype,
			doc.name,
			{
				CACHE_FIELD: json.dumps(payload),
				CACHE_STAMP_FIELD: frappe.utils.now(),
			},
			# A cached lookup is not a human edit — same rule as the geocode and
			# Zillow caches, so `modified` keeps meaning "a person touched this".
			update_modified=False,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData comps: cache write failed")


# ---------------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------------
def _post(path, body):
	req = urllib.request.Request(
		API_BASE + path,
		data=json.dumps(body).encode(),
		headers={
			"Authorization": "Bearer {0}".format(_api_key()),
			"Content-Type": "application/json",
		},
	)
	with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
		return json.loads(resp.read().decode())


def _window():
	today = frappe.utils.getdate()
	since = frappe.utils.add_days(today, -SALE_WINDOW_DAYS)
	return str(since), str(today)


def _num(v):
	try:
		n = float(v)
	except (TypeError, ValueError):
		return None
	return n if n == n else None  # NaN guard


def _shape(row, idx):
	"""Normalize one BatchData property into the row shape `comps.py` already emits.

	Returning the SAME keys means the map, the table and the pill grammar all work
	untouched — this is a data source swap, not a UI feature.
	"""
	addr = row.get("address") or {}
	bld = row.get("building") or {}
	sale = ((row.get("sale") or {}).get("lastSale")) or {}
	price = _num(sale.get("price"))
	sold = str(sale.get("saleDate") or "")[:10] or None

	street = addr.get("street") or ""
	city = addr.get("city") or ""
	state = addr.get("state") or ""
	zipc = addr.get("zip") or ""
	label = ", ".join([p for p in (street, city, "{0} {1}".format(state, zipc).strip()) if p])

	return {
		# Namespaced so it can never collide with a real CRM Comp docname, and so
		# `set_comp_state` (which writes docnames) visibly does not apply to these.
		"name": "batchdata::{0}".format(row.get("_id") or idx),
		"address": label,
		"lat": _num(addr.get("latitude")),
		"lng": _num(addr.get("longitude")),
		"price": price,
		# BatchData returns recorded/closed sales, so these are never live listings.
		"status": "Inactive",
		# And unlike the pooled ISTL index, these genuinely ARE closed transactions,
		# so they earn the word "sold" rather than the weaker "off-market".
		"listing_state": "sold",
		"listed_date": None,
		"removed_date": sold,
		"days_on_market": None,
		"days_old": None,
		"bedrooms": _num(bld.get("bedroomCount")),
		"bathrooms": _num(bld.get("bathroomCount")),
		"square_footage": _num(bld.get("livingAreaSquareFeet")),
		"year_built": _num(bld.get("yearBuilt")),
		"property_type": (row.get("general") or {}).get("propertyTypeDetail"),
		# Provenance. The UI can badge these as bought-in rather than pooled, and
		# anyone reading the payload can tell where a number came from.
		"source": "batchdata",
	}


def fetch_for_lead(doc, take=DEFAULT_TAKE, force=False):
	"""Comps for a lead from BatchData, cached. Returns [] when unavailable.

	Never raises: a comps map that renders without the fallback is a far better
	outcome than a 500 on a lead detail page.
	"""
	if not available():
		return []

	if not force:
		hit = _cached(doc)
		if hit is not None:
			return hit.get("comps") or []

	from crm.api import vendor_facts

	owned, env = vendor_facts.payload_or_fallback(_warehouse(doc, spend=True, force=force))
	if owned:
		# Only on a live answer: the warehouse remembers the error for 15 minutes,
		# and every map open in that window would otherwise alert again.
		if env.get("error") == "insufficient_balance" and env.get("source") == "live":
			_report_wallet_empty(env.get("error"))
		return _warehouse_comps(env)
	if not _api_key():
		return []
	return _fetch_direct(doc, take)


def _report_wallet_empty(detail):
	frappe.log_error(str(detail or ""), "BatchData comps: WALLET EMPTY - top up to re-enable")
	# The Error Log is where this went to die: 24 of these accumulated over a
	# week while reps' tax pulls failed and nobody knew. Tell a person.
	try:
		from crm.api import batchdata_wallet

		batchdata_wallet.report_wallet_empty("comps fallback")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData comps: alert failed")


def _fetch_direct(doc, take=DEFAULT_TAKE):
	"""The pre-warehouse path: BatchData straight from the CRM, cached on the lead.

	Only when the warehouse cannot answer at all. Kept so an outage there does not
	blank ND-state maps, not as a second home for the data.
	"""
	street = (doc.get("property_address") or "").split(",")[0].strip()
	city = (doc.get("property_city") or "").strip()
	state = (doc.get("property_state") or "").strip()
	zipc = str(doc.get("property_zip") or "").strip()
	if not street or not (zipc or (city and state)):
		return []

	since, until = _window()
	body = {
		"searchCriteria": {
			"compAddress": {"street": street, "city": city, "state": state, "zip": zipc},
			# minDate/maxDate is the ONLY accepted shape here. min/max, start/end,
			# from/to, gte/lte and ISO datetimes all fail with "Invalid Date", and
			# an unrecognised key is SILENTLY IGNORED — which would mean paying for
			# stale rows and never being told.
			"sale": {"lastSaleDate": {"minDate": since, "maxDate": until}},
		},
		"options": {"take": int(take), "skip": 0},
	}

	try:
		raw = _post("/property/search", body)
	except urllib.error.HTTPError as e:
		detail = ""
		try:
			detail = e.read().decode()[:200]
		except Exception:
			pass
		# 403 means two very different things on this API and they need different
		# human responses: an empty wallet is an ops problem, a scope problem is a
		# token problem. Say which.
		if e.code == 403 and "insufficient balance" in detail.lower():
			_report_wallet_empty(detail)
		else:
			frappe.log_error(detail, "BatchData comps: HTTP {0}".format(e.code))
		return []
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData comps: request failed")
		return []

	rows = ((raw.get("results") or {}).get("properties")) or []
	comps = _shape_all(rows)

	# Cached even when empty — otherwise an unmatched address is re-billed on every
	# single modal open.
	_store(doc, comps)
	return comps

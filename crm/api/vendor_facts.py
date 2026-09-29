"""Read-through client for PropWarehouse Zillow / Realtor facts.

The facts API (redfin-scraper-api) is the only process that talks to RapidAPI
or Apivex. This module is HTTP only: store-first, never a second billed call
from the CRM. Unreachable / old scraper (404) returns None so the existing
direct path can still degrade rather than blank a gallery.

Do not open PropWarehouse Postgres from here.
"""

from __future__ import annotations

TIMEOUT = 25


def _base_url():
	"""Where the vendor read-through lives.

	The Zillow/Realtor endpoints are moving out of redfin-scraper-api into a
	dedicated propwarehouse-api (see `propwarehouse/EGRESS.md`): the scraper is
	named, unit-named and database-named for Redfin, and it should not keep
	growing vendor surface. Only the LOOKUP path moves — `crm.api.geo` keeps
	pointing at the scraper for /properties, /parcels, /facts, /photos, /url.

	Resolution order, first non-empty wins:

	  1. site_config `propwarehouse_url`
	  2. env `PROPWAREHOUSE_URL`
	  3. the scraper's own base — today's behaviour

	So this is inert until the config is set, and setting it is the whole
	cutover. Unsetting it is the whole rollback.
	"""
	from crm.api.geo import _base_url as geo_base

	try:
		import frappe

		configured = frappe.conf.get("propwarehouse_url") or ""
	except Exception:
		# No frappe (bench-free unit tests) or no site context: fall through to
		# the env var, then to the scraper. Never raise from a URL lookup.
		configured = ""
	if not configured:
		import os

		configured = os.environ.get("PROPWAREHOUSE_URL") or ""
	configured = str(configured).strip().rstrip("/")
	return configured or geo_base()


#: A whole-market search can be ~40 vendor calls behind the warehouse's 7/s
#: limiter, so it gets longer than a single-house lookup.
SEARCH_TIMEOUT = 90


def _get(path, params, timeout=TIMEOUT, base=None):
	"""Envelope dict, or None if the warehouse is unreachable / does not have the route.

	Pass `base` from a worker thread: `_base_url` reads `frappe.conf`, which a
	thread with no site cannot do. Resolve it once on the calling thread.
	"""
	base = base if base is not None else _base_url()
	if not base:
		return None
	import requests

	try:
		r = requests.get(f"{base}{path}", params=params, timeout=timeout)
	except Exception:
		return None
	if r.status_code == 404:
		return None
	if r.status_code >= 400:
		return {"ok": False, "matched": False, "error": f"http_{r.status_code}", "payload": None}
	try:
		body = r.json()
	except ValueError:
		return None
	return body if isinstance(body, dict) else None


def zillow_property(address=None, zpid=None, photos=False):
	"""Warehouse envelope for one house, or None if we should fall back."""
	params = {}
	if address:
		params["address"] = address
	if zpid:
		params["zpid"] = zpid
	if photos:
		params["photos"] = "true"
	if not params:
		return None
	return _get("/zillow/property", params)


def zillow_property_many(addresses, workers=4):
	"""`/zillow/property` for many addresses at once -> [envelope|None], same order.

	The warehouse answers from its 30-day store first, so a repeat address
	across leads is never billed twice. Threads do HTTP only; the base URL is
	resolved here, on the calling thread (see `_get`).
	"""
	addresses = list(addresses or [])
	if not addresses:
		return []
	base = _base_url()
	if not base:
		return [None] * len(addresses)
	from concurrent.futures import ThreadPoolExecutor

	def one(a):
		return _get("/zillow/property", {"address": a}, base=base) if a else None

	with ThreadPoolExecutor(max_workers=max(1, min(workers, len(addresses)))) as pool:
		return list(pool.map(one, addresses))


def zillow_search(lat, lng, radius_mi, status_type, sold_in_last=None, include_pending=False):
	"""Whole-market Zillow circle search through the warehouse. Envelope, or None.

	`full=true` is what makes this the same question the CRM used to ask
	RapidAPI directly: every page, and a price split past Zillow's 800-row
	ceiling. `radius_mi` is a RADIUS; the warehouse doubles it for Zillow.
	"""
	if lat is None or lng is None or not radius_mi:
		return None
	params = {
		"lat": lat,
		"lng": lng,
		"radius_mi": radius_mi,
		"status_type": status_type,
		"full": "true",
	}
	if sold_in_last:
		params["sold_in_last"] = sold_in_last
	if include_pending:
		params["include_pending"] = "true"
	return _get("/zillow/search", params, timeout=SEARCH_TIMEOUT)


def zillow_photos(zpid):
	if not zpid:
		return None
	return _get("/zillow/photos", {"zpid": zpid})


def realtor_photos(address):
	addr = (address or "").strip()
	if not addr:
		return None
	return _get("/realtor/photos", {"address": addr})


def realtor_search(lat=None, lng=None, radius_mi=None, zip_code=None,
				   kind="forsold", sold_date_min=None):
	"""Apivex circle+ZIP search through the warehouse. Envelope, or None.

	PASS `zip_code` WHENEVER THE LEAD HAS ONE. Apivex's `/coordinates` search
	under-returns badly and the ZIP search is the better half -- measured over
	the same circles and the same 12-month window:

	    coordinates alone   33% recall
	    ZIP alone           68%
	    both, merged        73%

	Spokane alone: 32 homes from the coordinate search against 81 from the ZIP
	search, same vendor, same day. A coordinates-only answer therefore arrives
	as `ok=True` with a plausible row count and is a third of the market.

	THE QUERY PARAMETER IS `zip`, NOT `zip_code`. FastAPI binds by parameter
	name, so `zip_code=` is silently ignored and the ZIP half never runs -- the
	failure is a quiet two-thirds loss of comps, not an error. The keyword here
	is `zip_code` only because `zip` is a Python builtin.
	"""
	params = {"kind": kind}
	if lat is not None and lng is not None and radius_mi:
		params.update(lat=lat, lng=lng, radius_mi=radius_mi)
	zc = str(zip_code or "").strip()[:10]
	if zc:
		params["zip"] = zc
	if sold_date_min:
		params["sold_date_min"] = sold_date_min
	if "lat" not in params and "zip" not in params:
		return None
	return _get("/realtor/search", params)


def payload_or_fallback(envelope):
	"""(use_warehouse, payload).

	None envelope -> caller falls back.
	ok=False not_configured -> fall back (scraper has no key yet).
	anything else -> warehouse owns the answer, even a miss.
	"""
	if envelope is None:
		return False, None
	if envelope.get("ok") is False and envelope.get("error") == "not_configured":
		return False, None
	return True, envelope

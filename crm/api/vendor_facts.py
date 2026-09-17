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
	from crm.api.geo import _base_url as geo_base

	return geo_base()


def _get(path, params):
	"""Envelope dict, or None if the warehouse is unreachable / does not have the route."""
	base = _base_url()
	if not base:
		return None
	import requests

	try:
		r = requests.get(f"{base}{path}", params=params, timeout=TIMEOUT)
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


def zillow_photos(zpid):
	if not zpid:
		return None
	return _get("/zillow/photos", {"zpid": zpid})


def realtor_photos(address):
	addr = (address or "").strip()
	if not addr:
		return None
	return _get("/realtor/photos", {"address": addr})


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

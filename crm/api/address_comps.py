# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Comps for an ADDRESS, with no CRM record behind it.

Knock (and anything else that is not the CRM) needs the CRM's comps board —
the Redfin → Zillow → Realtor → BatchData cascade, the preset tiers, the
self-comp and unpriced-pin rules, the Sources card — for houses that are not,
and may never be, CRM Leads. Before this, the only way in was a CRM Lead or a
scratch CRM Property, so a caller had to create a record just to look.

HOW: a TRANSIENT CRM Lead — built in memory, never inserted — carries the
address and whatever facts the caller already knows, and `get_lead_comps`
runs against it unchanged. `comps._load_subject` returns it for its `ADDR-`
name for the length of this request only. Nothing is duplicated: the filters
and fallbacks are the ones the CRM map uses, by construction.

WHAT IS NOT KEPT HERE, on purpose:

* Paid data lives in PropWarehouse, keyed on the address (BatchData comps,
  Zillow and Realtor lookups), so a second open of the same house is free
  whichever system opens it. That is what made a record unnecessary.
* Ticked / hidden comps belong to the caller's own review of the deal. They
  arrive as `comp_state` ({"hidden": [...], "selected": [...]}) and are never
  written anywhere by this module.
* The per-lead caches (`property_lat`, `zillow_facts`, …) write to a row that
  does not exist, which is a no-op. Pass `lat`/`lng` to skip the geocode.

The `ADDR-` name is a hash of the normalized address, so Redis caches keyed on
the subject name (the Redfin subject record, the Realtor estimate) are shared
by every open of the same house.
"""

import hashlib
import json
import re

import frappe
from frappe import _

PREFIX = "ADDR-"


def is_address_subject(name) -> bool:
	return str(name or "").startswith(PREFIX)


def _norm(*parts) -> str:
	line = ", ".join(str(p or "").strip() for p in parts if str(p or "").strip())
	return re.sub(r"\s+", " ", line).lower()


def subject_name(address, city="", state="", zip_code="") -> str:
	"""Stable `ADDR-<hash>` for one house. Street cut at its first comma so a
	whole-address street line names the same subject as split fields."""
	street = str(address or "").split(",")[0]
	digest = hashlib.sha1(_norm(street, city, state, zip_code).encode()).hexdigest()[:16]
	return PREFIX + digest


def transient_subject(name):
	"""The in-memory subject registered for this request, or None."""
	return (getattr(frappe.local, "address_subjects", None) or {}).get(name)


def _num(v):
	try:
		n = float(v)
	except (TypeError, ValueError):
		return None
	return n if n == n and n > 0 else None


def build_subject(address, city="", state="", zip_code="", lat=None, lng=None,
				  beds=None, baths=None, sqft=None, year_built=None):
	"""An unsaved CRM Lead standing in for the house. Never inserted."""
	street = str(address or "").split(",")[0].strip()
	doc = frappe.get_doc({
		"doctype": "CRM Lead",
		"property_address": street,
		"property_city": str(city or "").strip(),
		"property_state": str(state or "").strip(),
		"property_zip": str(zip_code or "").strip(),
	})
	doc.name = subject_name(street, city, state, zip_code)
	try:
		la, ln = float(lat), float(lng)
	except (TypeError, ValueError):
		la = ln = None
	# 0.0 is how an ungeocoded lead reads, so truthiness, same as _subject_point.
	if la and ln:
		doc.property_lat, doc.property_lng = la, ln
	# The caller's own facts fill the lowest rung of `_subject_facts` — the same
	# rung iSpeedToLead's pick-list answers use — so Redfin and Zillow still win
	# wherever they know the house.
	for field, value in (("bedrooms", beds), ("bathrooms", baths),
						 ("square_footage", sqft), ("year_built", year_built)):
		n = _num(value)
		if n is not None:
			doc.set(field, str(int(n)) if field != "bathrooms" else str(n))
	return doc


def point_for(name):
	"""(lat, lng) of a registered subject, or (None, None)."""
	doc = transient_subject(name)
	if not doc:
		return None, None
	try:
		return float(doc.property_lat), float(doc.property_lng)
	except (TypeError, ValueError, AttributeError):
		return None, None


@frappe.whitelist()
def get_address_comps(
	address, city=None, state=None, zip=None, lat=None, lng=None,
	beds=None, baths=None, sqft=None, year_built=None,
	radius_mi=None, limit=None, filters=None, auto=1, comp_state=None,
	inventory=None, settle=0,
):
	"""`get_lead_comps` for an address. Same response, `lead` is the `ADDR-` name.

	`comp_state` is the caller's own picks, `{"hidden": [...], "selected": [...]}`
	(JSON or dict). It is named so because `state` is the US state here.
	"""
	from crm.api import comps

	comps._guard()
	if not str(address or "").strip() or not (str(zip or "").strip() or (city and state)):
		frappe.throw(_("Street address plus a ZIP, or a city and state, is required."))
	doc = build_subject(address, city, state, zip, lat, lng, beds, baths, sqft, year_built)
	subjects = getattr(frappe.local, "address_subjects", None)
	if subjects is None:
		subjects = {}
		frappe.local.address_subjects = subjects
	subjects[doc.name] = doc
	if comp_state in (None, ""):
		comp_state = {"hidden": [], "selected": []}
	if isinstance(comp_state, dict):
		comp_state = json.dumps(comp_state)
	try:
		return comps.get_lead_comps(
			doc.name, radius_mi=radius_mi, limit=limit, filters=filters, auto=auto,
			state=comp_state, inventory=inventory, settle=settle,
		)
	finally:
		subjects.pop(doc.name, None)

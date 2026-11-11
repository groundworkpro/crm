"""A lead's address was edited: drop the old house's location and relocate.

German (#bugs, 2026-11-11): a rep fixed a wrong subject address on the lead,
but the comps map and the comping tool stayed on the old house. Every location
answer on a CRM Lead is cached against the lead, not the address -- the comp
circle centre (`property_lat/lng`), the exact parcel (`parcel_*`), Zillow's
facts, BatchData's comps, Redfin's subject record and the subject gallery -- and
only the Zillow banner's "Rerun comps" and "Use this address" cleared them. A
plain edit on the lead page left every one in place.

CRM Property already does this in `properties.update_property`; this is the
CRM Lead half, as doc hooks so every edit path gets it (side panel, the Zillow
banner's "Save address", imports, the API):

* `before_save` -- when the address changed in substance (suffix-, case- and
  punctuation-blind tokens, so "Street" -> "St" or a comma is not a change), blank the cached fields on the
  doc itself so they are written in the same save.
* `on_update` -- drop the Redis caches keyed on the lead and enqueue
  `relocate`, after commit, on the long queue.
* `relocate` -- resolve the new address's exact point, move the comp circle
  there (`resolve_after_address_correction`: the human edit is the proof the old
  centre is wrong, the same rule as accepting a suggestion), and warm the new
  neighbourhood. Nothing billed is pre-fetched: Zillow is asked once, on the
  next comps open, because its cache is now empty.

The suggestion-accept path saves the address itself and then relocates inline;
it sets `doc.flags.address_change_handled` so this does not enqueue a second
relocate for it.
"""

from __future__ import annotations

import frappe

#: Lead fields that hold an answer about the house at the OLD address. Each is
#: blanked only when the column exists, so app code can deploy ahead of setup.
LOCATION_FIELDS = {
	"property_lat": None,
	"property_lng": None,
	"zillow_facts": "",
	"zillow_fetched_at": None,
	"zillow_zpid": "",
	"batchdata_comps": "",
	"batchdata_comps_fetched_at": None,
	"parcel_lat": None,
	"parcel_lng": None,
	"parcel_source": "",
	"parcel_address_key": "",
	"parcel_checked_at": None,
	"address_suggested": "",
	"address_suggestion_key": "",
	"address_suggestion_state": "",
}


def _key(doc) -> tuple:
	"""The address as suffix/case/punctuation-blind tokens (address_resolve's)."""
	from crm.api.address_resolve import _tokens
	from crm.api.comps import _full_address

	return tuple(_tokens(_full_address(doc)))


def address_changed(doc) -> bool:
	"""True when an existing lead's address now names a different house string."""
	if doc.is_new():
		return False
	before = doc.get_doc_before_save()
	if not before:
		return False
	return _key(before) != _key(doc)


def before_save(doc, method=None):
	"""CRM Lead before_save: forget the old house's location answers."""
	try:
		if not address_changed(doc):
			return
	except Exception:
		frappe.log_error(frappe.get_traceback(), "address_change: compare failed")
		return
	doc.flags.address_changed = True
	for field, blank in LOCATION_FIELDS.items():
		if frappe.db.has_column(doc.doctype, field):
			doc.set(field, blank)


def _redis_keys(lead):
	from crm.api import comps, redfin

	keys = [redfin._check_cache_key(lead)]
	for v in range(1, comps.DETAIL_CACHE_VERSION + 1):
		keys.append(comps._detail_cache_key(f"subject::nz::{lead}", v))
	keys.append(
		f"crm:comp-detail:zillow:v{comps.DETAIL_ZILLOW_CACHE_VERSION}:subject::{lead}|"
	)
	return keys


def clear_caches(lead):
	"""Redis answers keyed on the lead (not the address) for the old house."""
	cache = frappe.cache()
	for key in _redis_keys(lead):
		try:
			cache.delete_value(key)
		except Exception:
			pass
	# House facts a rep typed into Ask AI's low-confidence box were about the
	# old house; every later Ask AI job would otherwise replace the new house's
	# facts with them.
	try:
		from crm.api.ai_pricer import FACTS_KEY

		if frappe.db.get_default(FACTS_KEY + lead):
			frappe.db.set_default(FACTS_KEY + lead, "")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "address_change: clear Ask AI facts failed")


def on_update(doc, method=None):
	"""CRM Lead on_update: after an address change, clear caches and relocate."""
	if not doc.flags.get("address_changed"):
		return
	doc.flags.address_changed = False
	clear_caches(doc.name)
	if doc.flags.get("address_change_handled"):
		return
	try:
		frappe.enqueue(
			"crm.api.address_change.relocate",
			queue="long",
			job_name=f"address-change-{doc.name}",
			enqueue_after_commit=True,
			lead=doc.name,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "address_change: enqueue relocate failed")


def relocate(lead):
	"""Background: exact point for the new address, circle moved there, area warmed.

	Without an exact point the circle centre stays the free Census geocode of the
	NEW address (`_subject_point` writes it while resolving), never the old house.
	"""
	from crm.api.address_resolve import resolve_after_address_correction

	resolved = resolve_after_address_correction(lead)
	out = {"lead": lead, "resolved": resolved, "warmed": None}
	if resolved.get("ok") and resolved.get("exact"):
		try:
			from crm.api.geo import _enabled, warm_lead

			if _enabled():
				out["warmed"] = warm_lead(lead)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "address_change: warm failed")
	return out

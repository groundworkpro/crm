# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Scratch properties — comps and calcs on a house that is NOT a lead.

A rep wants to price a house they have not bought a lead for: a deal being
scouted, a neighbour of a live lead, a buyer's ask, a "what would we pay for
this" on a drive-by. Until now the only way to open the comps map was to
create a CRM Lead, which put a fake seller on the Kanban, the Today board, the
round robin tally and the dashboard cohort.

`CRM Property` (ops `setup_properties.py`) is a subject WITHOUT any of that. It
carries the same property / cache / comps columns a CRM Lead does, so the
whole comps stack — `get_lead_comps`, hides/picks/types, the sqft override,
Zillow / Redfin / Realtor facts, the BatchData fallback, the offer calculator —
runs against it unchanged. An optional `lead` Link can hang a property off a
real seller (a neighbour, a second house, a buyer's ask) without turning the
property into a lead — blank is the default. The dispatch is `crm.api.comps.subject_doctype`,
keyed on the `PROP-` name prefix. Saved calcs live on the property itself
(`offer_calcs`, newest first) because there is no activity timeline to post
them to; the latest one seeds the calculator on the next open.

Team-visible on purpose, like practice sets: a comp someone already ran is
worth more to the next person than a private scratchpad would be.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import now

from crm.api.comps import SCRATCH_DOCTYPE, SCRATCH_PREFIX, _guard
from crm.api.listing_url import looks_like_url, parse_listing_url

#: The board columns, in order. Stored as free text on `status` (not a Select)
#: so renaming a stage is a one-line edit here with no schema change; anything
#: not in this list renders in the first column and is rewritten on next move.
STAGES = ("New", "Comped", "Offer Sent", "Follow Up", "Dead")
DEFAULT_STAGE = STAGES[0]

#: How many saved calcs a property keeps. Each is a few KB; nobody re-prices a
#: house twenty-five times, and if they do the early ones are noise.
MAX_OFFERS = 25


def _available() -> bool:
	return bool(frappe.db.exists("DocType", SCRATCH_DOCTYPE))


def _need() -> None:
	if not _available():
		frappe.throw(_("Properties are not set up on this site yet."))


def _get(name: str):
	if not name or not str(name).startswith(SCRATCH_PREFIX) or not frappe.db.exists(
		SCRATCH_DOCTYPE, name
	):
		frappe.throw(_("Property {0} does not exist.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(SCRATCH_DOCTYPE, name)


def _offers(doc) -> list:
	raw = doc.get("offer_calcs")
	if not raw:
		return []
	try:
		val = json.loads(raw)
	except Exception:
		return []
	return val if isinstance(val, list) else []


def latest_offer(doc):
	"""The most recent saved calc's payload (the calculator's `seed`), or None."""
	offers = _offers(doc)
	return (offers[0].get("offer") or None) if offers else None


def record_offer(name: str, payload: dict, html: str) -> None:
	"""Prepend one saved calc. Called by `cash_offer.save_cash_offer`."""
	doc = _get(name)
	offers = _offers(doc)
	offers.insert(
		0,
		{
			"at": now(),
			"by": frappe.session.user,
			"offer": payload,
			"html": html,
		},
	)
	del offers[MAX_OFFERS:]
	# db.set_value so a saved calc is not a "modified" edit of the address.
	frappe.db.set_value(
		SCRATCH_DOCTYPE, name, "offer_calcs", json.dumps(offers), update_modified=False
	)


def _status_supported() -> bool:
	return frappe.db.has_column(SCRATCH_DOCTYPE, "status")


def _listing_url_supported() -> bool:
	return frappe.db.has_column(SCRATCH_DOCTYPE, "listing_url")


def _lead_supported() -> bool:
	return frappe.db.has_column(SCRATCH_DOCTYPE, "lead")


def _assign_lead(doc, lead) -> None:
	"""Blank unlinks. A missing lead is refused rather than stored dangling."""
	lead = _clean(lead)
	if not lead:
		doc.lead = ""
		return
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	doc.lead = lead


def _lead_names_for(docs) -> dict:
	ids = []
	for doc in docs:
		n = (doc.get("lead") or "").strip()
		if n and n not in ids:
			ids.append(n)
	if not ids:
		return {}
	rows = frappe.get_all(
		"CRM Lead",
		filters={"name": ["in", ids]},
		fields=["name", "lead_name"],
		limit_page_length=len(ids),
	)
	return {r["name"]: (r.get("lead_name") or r["name"]) for r in rows}


def _resolve_address(address: str) -> tuple[dict, str]:
	"""Typed text → ({address, city, state, zip}, listing_url).

	A pasted Zillow / Redfin / Realtor / Auction.com link is read off its slug
	(`crm.api.listing_url`); anything else is taken as the address itself.
	"""
	address = _clean(address)
	if looks_like_url(address):
		parsed = parse_listing_url(address)
		if not parsed:
			frappe.throw(
				_(
					"Could not read an address from that link. Paste a Zillow, Redfin, "
					"Realtor or Auction.com PROPERTY page, or type the address."
				)
			)
		return (
			{
				"address": parsed["address"],
				"city": parsed.get("city") or "",
				"state": parsed.get("state") or "",
				"zip": parsed.get("zip") or "",
			},
			parsed["url"],
		)
	return {"address": address, "city": "", "state": "", "zip": ""}, ""


def _stage(doc) -> str:
	val = (doc.get("status") or "").strip()
	return val if val in STAGES else DEFAULT_STAGE


def _user_label(user: str) -> str:
	return frappe.db.get_value("User", user, "full_name") or user or ""


def _offer_summary(offer: dict) -> dict:
	"""Kind + headline number for the list page."""
	if not isinstance(offer, dict):
		return {}
	scenes = offer.get("scenarios") or []
	first = scenes[0] if scenes else {}
	return {
		"kind": offer.get("kind") or "cash",
		"offer": first.get("offer"),
		"arv": first.get("arv"),
	}


def _shape(doc, *, with_offers: bool = False, lead_names: dict | None = None) -> dict:
	offers = _offers(doc)
	latest = offers[0] if offers else None
	try:
		selected = json.loads(doc.get("comps_selected") or "[]")
	except Exception:
		selected = []
	lead = (doc.get("lead") or "").strip() if _lead_supported() else ""
	lead_name = ""
	if lead:
		if lead_names is not None:
			lead_name = lead_names.get(lead) or lead
		else:
			lead_name = frappe.db.get_value("CRM Lead", lead, "lead_name") or lead
	out = {
		"name": doc.name,
		"property_address": doc.get("property_address") or "",
		"property_city": doc.get("property_city") or "",
		"property_state": doc.get("property_state") or "",
		"property_zip": doc.get("property_zip") or "",
		"notes": doc.get("notes") or "",
		"status": _stage(doc),
		"listing_url": doc.get("listing_url") or "",
		"source": doc.get("source") or "",
		"lead": lead,
		"lead_name": lead_name,
		"lead_supported": _lead_supported(),
		"owner": doc.owner,
		"owner_name": _user_label(doc.owner),
		"creation": str(doc.creation),
		"modified": str(doc.modified),
		"picked_count": len(selected) if isinstance(selected, list) else 0,
		"offer_count": len(offers),
		"latest_offer": _offer_summary(latest.get("offer")) if latest else None,
		"latest_offer_at": latest.get("at") if latest else None,
		"latest_offer_by": _user_label(latest.get("by")) if latest else None,
	}
	if with_offers:
		out["stages"] = list(STAGES)
		out["offers"] = [
			{
				"at": o.get("at"),
				"by": o.get("by"),
				"by_name": _user_label(o.get("by")),
				"html": o.get("html") or "",
				"offer": o.get("offer") or {},
			}
			for o in offers
		]
	return out


def _clean(val) -> str:
	return " ".join(str(val or "").split())


# ---------------------------------------------------------------------------------
# Whitelisted
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def list_properties(q: str = "", mine: int = 0, lead: str = "") -> dict:
	"""Every scratch property, newest first. `q` filters on the address.
	`lead` (a CRM Lead name) returns only properties linked to that seller."""
	_guard()
	if not _available():
		return {"available": False, "lead_supported": False, "properties": []}
	filters = {}
	if int(mine or 0):
		filters["owner"] = frappe.session.user
	lead = _clean(lead)
	if lead:
		if not _lead_supported():
			return {
				"available": True,
				"stages": list(STAGES),
				"status_supported": _status_supported(),
				"lead_supported": False,
				"properties": [],
			}
		filters["lead"] = lead
	or_filters = None
	q = _clean(q)
	if q:
		like = f"%{q}%"
		or_filters = {
			"property_address": ["like", like],
			"property_city": ["like", like],
			"property_zip": ["like", like],
			"notes": ["like", like],
		}
	names = frappe.get_all(
		SCRATCH_DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		pluck="name",
		order_by="modified desc",
		limit=500,
	)
	docs = [frappe.get_doc(SCRATCH_DOCTYPE, n) for n in names]
	lead_names = _lead_names_for(docs) if _lead_supported() else {}
	return {
		"available": True,
		"stages": list(STAGES),
		"status_supported": _status_supported(),
		"lead_supported": _lead_supported(),
		"properties": [_shape(d, lead_names=lead_names) for d in docs],
	}


@frappe.whitelist()
def get_property(name: str) -> dict:
	_guard()
	_need()
	return _shape(_get(name), with_offers=True)


@frappe.whitelist()
def preview_address(text: str) -> dict:
	"""What `create_property` would store for this text — so the add dialog can
	show the address read off a pasted link before anything is created."""
	_guard()
	text = _clean(text)
	if not text:
		return {"address": "", "from_url": False}
	if looks_like_url(text):
		parsed = parse_listing_url(text)
		if not parsed:
			return {"address": "", "from_url": True, "error": _("Not a property page we can read.")}
		return {"address": parsed["address"], "from_url": True, "source": parsed["source"]}
	return {"address": text, "from_url": False}


@frappe.whitelist()
def create_property(
	address: str, city: str = "", state: str = "", zip_code: str = "", notes: str = "",
	lead: str = "",
) -> dict:
	"""Add a house to comp. `address` is either the address itself or a
	Zillow / Redfin / Realtor / Auction.com listing URL.

	One full address line is fine — `_full_address` skips the city/state/zip
	parts already inside it, so "412 Maple Ave, Aurora, MN 55705" with the
	rest blank geocodes and resolves on Zillow exactly like a webhook lead's
	address does. A link is read off its slug and kept as `listing_url`.
	"""
	_guard()
	_need()
	if not _clean(address):
		frappe.throw(_("Type the property address or paste a listing link first."))
	parts, listing_url = _resolve_address(address)
	doc = frappe.get_doc(
		{
			"doctype": SCRATCH_DOCTYPE,
			"property_address": parts["address"],
			"property_city": _clean(city) or parts["city"],
			"property_state": (_clean(state) or parts["state"]).upper()[:2],
			"property_zip": _clean(zip_code) or parts["zip"],
			"notes": (notes or "").strip(),
		}
	)
	if _status_supported():
		doc.status = DEFAULT_STAGE
	if listing_url and _listing_url_supported():
		doc.listing_url = listing_url
	if lead and _lead_supported():
		_assign_lead(doc, lead)
	doc.insert()
	return _shape(doc)


@frappe.whitelist()
def update_property(
	name: str, address=None, city=None, state=None, zip_code=None, notes=None,
	lead=None,
) -> dict:
	"""Edit the address or note. An address change drops every location cache
	(geocode, Zillow facts, BatchData) so the next open looks the new house up
	fresh instead of re-centering on the old parcel."""
	_guard()
	_need()
	doc = _get(name)
	before = (
		doc.get("property_address"),
		doc.get("property_city"),
		doc.get("property_state"),
		doc.get("property_zip"),
	)
	if address is not None:
		if not _clean(address):
			frappe.throw(_("The address cannot be blank."))
		parts, listing_url = _resolve_address(address)
		doc.property_address = parts["address"]
		if listing_url:
			# A pasted link names the whole address, so its parts win over
			# whatever city/state/zip were on file for the old house.
			doc.property_city = parts["city"]
			doc.property_state = parts["state"]
			doc.property_zip = parts["zip"]
			if _listing_url_supported():
				doc.listing_url = listing_url
	if city is not None:
		doc.property_city = _clean(city)
	if state is not None:
		doc.property_state = _clean(state).upper()[:2]
	if zip_code is not None:
		doc.property_zip = _clean(zip_code)
	if notes is not None:
		doc.notes = (notes or "").strip()
	if lead is not None:
		if not _lead_supported():
			frappe.throw(_("The lead column is missing — run setup_properties.py."))
		_assign_lead(doc, lead)
	after = (
		doc.get("property_address"),
		doc.get("property_city"),
		doc.get("property_state"),
		doc.get("property_zip"),
	)
	if before != after:
		doc.property_lat = None
		doc.property_lng = None
		doc.zillow_facts = ""
		doc.zillow_fetched_at = None
		doc.zillow_zpid = ""
		doc.batchdata_comps = ""
		doc.batchdata_comps_fetched_at = None
		# Parcel resolution and a suggestion decision belong to the exact source
		# address they were made against. A later edit must not keep the old house's
		# point or suppress a new correction. Guarded so app code can deploy before
		# setup_geocode.py adds the custom fields.
		parcel_reset = {
			"parcel_lat": None, "parcel_lng": None, "parcel_source": "",
			"parcel_address_key": "", "parcel_checked_at": None,
			"address_suggested": "", "address_suggestion_key": "",
			"address_suggestion_state": "",
		}
		for field, value in parcel_reset.items():
			if doc.meta.has_field(field):
				doc.set(field, value)
	doc.save()
	return _shape(doc, with_offers=True)


@frappe.whitelist()
def set_property_lead(name: str, lead: str = "") -> dict:
	"""Link or unlink a lead without touching the address. Blank unlinks.
	`db.set_value` so a chip click is not an edit of the house."""
	_guard()
	_need()
	if not _lead_supported():
		frappe.throw(_("The lead column is missing — run setup_properties.py."))
	doc = _get(name)
	_assign_lead(doc, lead)
	frappe.db.set_value(
		SCRATCH_DOCTYPE, doc.name, "lead", doc.get("lead") or "", update_modified=False
	)
	return _shape(doc, with_offers=True)


@frappe.whitelist()
def search_leads(q: str = "") -> list:
	"""Leads for the property picker — name, seller, or address."""
	_guard()
	q = _clean(q)
	or_filters = None
	if q:
		like = f"%{q}%"
		or_filters = {
			"lead_name": ["like", like],
			"property_address": ["like", like],
			"name": ["like", like],
		}
	return frappe.get_all(
		"CRM Lead",
		filters={},
		or_filters=or_filters,
		fields=["name", "lead_name", "property_address", "status"],
		order_by="modified desc",
		limit_page_length=20,
	)


@frappe.whitelist()
def search_properties(q: str = "") -> list:
	"""Scratch properties for linking onto a lead from the lead page."""
	_guard()
	if not _available():
		return []
	q = _clean(q)
	or_filters = None
	if q:
		like = f"%{q}%"
		or_filters = {
			"property_address": ["like", like],
			"property_city": ["like", like],
			"name": ["like", like],
		}
	fields = ["name", "property_address", "status"]
	if _lead_supported():
		fields.append("lead")
	return frappe.get_all(
		SCRATCH_DOCTYPE,
		filters={},
		or_filters=or_filters,
		fields=fields,
		order_by="modified desc",
		limit_page_length=20,
	)


@frappe.whitelist()
def set_property_status(name: str, status: str) -> dict:
	"""Move a property between board columns. `db.set_value` so a drag is not an
	edit of the address (`modified` keeps meaning a person changed the record)."""
	_guard()
	_need()
	status = (status or "").strip()
	if status not in STAGES:
		frappe.throw(_("Unknown stage {0}").format(status))
	if not _status_supported():
		frappe.throw(_("The status column is missing — run setup_properties.py."))
	doc = _get(name)
	frappe.db.set_value(SCRATCH_DOCTYPE, doc.name, "status", status, update_modified=False)
	return {"ok": True, "name": doc.name, "status": status}


@frappe.whitelist()
def delete_property(name: str) -> dict:
	_guard()
	_need()
	doc = _get(name)
	doc.delete()
	return {"ok": True}


# ---------------------------------------------------------------------------------
# Auction.com import — a PROPERTY, never a CRM Lead
# ---------------------------------------------------------------------------------
# LeadMarket's Auction tab hands a courthouse/REO listing here. Unlike the iSTL
# and Zolo sources there is no seller to work, so the record is a scratch
# CRM Property (comps + calcs), keyed by a namespaced external id so a second
# click on the same listing returns the same PROP- instead of a duplicate.
# Identity is `external_id`; the ADC URL is kept separately and never parsed
# for identity. ADC comp evidence lands in the shared CRM Comp pool (by the
# caller) and is picked up by geography like every other comp.

AUCTION_SOURCE = "Auction.com"
_EXTERNAL_ID_RE = re.compile(r"^auction:\d{1,12}$")
#: Cap on the evidence blob we accept; ADC sends ~30 comps, not thousands.
_MAX_EVIDENCE_BYTES = 200_000


def _external_id_supported() -> bool:
	return frappe.db.has_column(SCRATCH_DOCTYPE, "external_id")


def _num_or_none(v):
	try:
		f = float(v)
	except (TypeError, ValueError):
		return None
	return f if f == f and abs(f) != float("inf") else None


def _by_external_id(external_id: str):
	name = frappe.db.get_value(SCRATCH_DOCTYPE, {"external_id": external_id}, "name")
	return frappe.get_doc(SCRATCH_DOCTYPE, name) if name else None


@frappe.whitelist()
def import_auction_property(
	listing_id, address: str, city: str = "", state: str = "", zip_code: str = "",
	lat=None, lng=None, listing_url: str = "", evidence=None,
) -> dict:
	"""Idempotently create the CRM Property for one Auction.com listing.

	Returns the existing property when `auction:<listing_id>` is already on
	file (`created: False`) so repeated pushes never duplicate. Never touches
	CRM Lead. Coordinates are ADC's own geocode for the listing and are stored
	as the subject point so the comps map does not have to re-geocode a
	street-only address into the wrong state.
	"""
	_guard()
	_need()
	if not _external_id_supported():
		frappe.throw(_("The external_id column is missing — run setup_properties.py."))
	listing_id = str(listing_id or "").strip()
	external_id = f"auction:{listing_id}"
	if not _EXTERNAL_ID_RE.match(external_id):
		frappe.throw(_("Auction.com listing id must be numeric."))
	if not _clean(address):
		frappe.throw(_("The property address is required."))
	url = _clean(listing_url)
	if url and not re.match(r"^https://(www\.)?auction\.com/", url):
		frappe.throw(_("listing_url must be an auction.com page."))
	point_lat, point_lng = _num_or_none(lat), _num_or_none(lng)
	if (point_lat is None) != (point_lng is None) or (
		point_lat is not None and not (-90 <= point_lat <= 90 and -180 <= point_lng <= 180)
	):
		frappe.throw(_("Invalid property coordinates."))
	if evidence is not None and not isinstance(evidence, (dict, list)):
		evidence = frappe.parse_json(evidence)
	blob = json.dumps(evidence, default=str) if evidence is not None else ""
	if len(blob) > _MAX_EVIDENCE_BYTES:
		frappe.throw(_("Auction evidence payload is too large."))

	existing = _by_external_id(external_id)
	if existing:
		return {**_shape(existing), "created": False, "external_id": external_id}

	values = {
		"doctype": SCRATCH_DOCTYPE,
		"property_address": _clean(address),
		"property_city": _clean(city),
		"property_state": (_clean(state) or "").upper()[:2],
		"property_zip": _clean(zip_code),
		"external_id": external_id,
		"notes": "",
	}
	if frappe.db.has_column(SCRATCH_DOCTYPE, "source"):
		values["source"] = AUCTION_SOURCE
	if url and _listing_url_supported():
		values["listing_url"] = url
	if point_lat is not None:
		values["property_lat"] = point_lat
		values["property_lng"] = point_lng
	if blob and frappe.db.has_column(SCRATCH_DOCTYPE, "adc_evidence"):
		values["adc_evidence"] = blob
	doc = frappe.get_doc(values)
	if _status_supported():
		doc.status = DEFAULT_STAGE
	try:
		doc.insert()
	except Exception as exc:  # unique external_id lost a race: return the winner
		dup = tuple(getattr(frappe, n) for n in ("DuplicateEntryError", "UniqueValidationError") if hasattr(frappe, n))
		if dup and isinstance(exc, dup):
			frappe.db.rollback()
			winner = _by_external_id(external_id)
			if winner:
				return {**_shape(winner), "created": False, "external_id": external_id}
		raise
	return {**_shape(doc), "created": True, "external_id": external_id}


_ADC_COMP_FIELDS = (
	"address_key", "address", "city", "state", "zip",
	"lat", "lng", "price", "status", "removed_date",
	"bedrooms", "bathrooms", "square_footage", "year_built", "source_lead",
)


@frappe.whitelist()
def import_adc_comps(rows=None) -> dict:
	"""Insert-only CRM Comp rows from an Auction.com push, in one request.

	LeadMarket reverse-geocodes first; we never overwrite an existing address_key.
	One bench round-trip instead of N docker-exec inserts (which hung the desk).
	"""
	_guard()
	if isinstance(rows, str):
		rows = frappe.parse_json(rows)
	if not isinstance(rows, list):
		frappe.throw(_("rows must be a list"))
	inserted = existing = failed = 0
	for rec in rows[:200]:
		if not isinstance(rec, dict):
			failed += 1
			continue
		key = str(rec.get("address_key") or "").strip()
		if not key:
			failed += 1
			continue
		try:
			if frappe.db.exists("CRM Comp", {"address_key": key}):
				existing += 1
				continue
			values = {"doctype": "CRM Comp"}
			for field in _ADC_COMP_FIELDS:
				if field in rec:
					values[field] = rec[field]
			frappe.get_doc(values).insert(ignore_permissions=True)
			inserted += 1
		except Exception as exc:
			dup = tuple(
				getattr(frappe, n) for n in ("DuplicateEntryError", "UniqueValidationError")
				if hasattr(frappe, n)
			)
			if dup and isinstance(exc, dup):
				existing += 1
			else:
				failed += 1
	return {
		"inserted": inserted,
		"existing_untouched": existing,
		"failed": failed,
	}

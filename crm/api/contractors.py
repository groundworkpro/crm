"""Contractors -- people we hire per property for photos and lockbox installs.

Exe's ask (Mattermost #bugs, 2026-09-28): keep the contacts of reliable
contractors with the areas they work, and flag cards in "Photos & Lockbox In
Progress" when someone on file covers that property. Lance: area = metro area.

  * **CRM Contractor** (ops doctype, `../frappe-crm-deploy/scripts/
    setup_contractors.py`): name, company, phone, email, does_photos,
    does_lockbox, `metro_areas` (JSON list of CRM Metro Area names, the same
    shape as CRM Buyer.metro_areas -- metro names contain commas), notes, active.
  * A lead has no metro field. Leads carry property_county + property_state
    (~99.7% filled), so `lead_metro` looks the county up in
    `data/metro_counties.json` -- the Census 2023 delineation, the same vintage
    the 393-row CRM Metro Area list was seeded from. Counties outside every
    metro (rural / micropolitan, ~2% of leads) resolve to None: no badge.

The Kanban calls `card_badges` once per board page, and only for cards in the
Photos & Lockbox column, so every other card ships nothing.
"""

from __future__ import annotations

import json
import os

import frappe
from frappe import _
from frappe.utils import cint

from crm.api.dispo_buyers import _county_keys

DOCTYPE = "CRM Contractor"
PHOTOS_LOCKBOX = "Photos & Lockbox In Progress"
SALES_ROLES = ("System Manager", "Sales Manager", "Sales User")

EDITABLE = (
	"contractor_name", "company", "phone", "email", "does_photos", "does_lockbox",
	"metro_areas", "notes", "active",
)
CHECKS = {"does_photos", "does_lockbox", "active"}
LIST_FIELDS = ["name", *EDITABLE, "modified"]

_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "metro_counties.json")
_COUNTIES = None


def _guard():
	if not any(r in SALES_ROLES for r in frappe.get_roles()):
		frappe.throw(_("Only sales users can manage contractors."), frappe.PermissionError)


def _ready() -> bool:
	"""The doctype is created by an ops script; before it runs, degrade to
	'no contractors' rather than breaking the board."""
	return bool(frappe.db.exists("DocType", DOCTYPE))


def _counties() -> dict:
	global _COUNTIES
	if _COUNTIES is None:
		try:
			with open(_DATA_FILE) as fh:
				_COUNTIES = json.load(fh)["counties"]
		except Exception:
			frappe.log_error(title="contractors: metro_counties.json unavailable")
			_COUNTIES = {}
	return _COUNTIES


def lead_metro(county, state) -> str | None:
	"""(county, state) -> CRM Metro Area name, or None if outside every metro."""
	table = _counties()
	for key in _county_keys(county, state):
		if key in table:
			return table[key]
	return None


def _metro_list(raw) -> list[str]:
	if isinstance(raw, str):
		try:
			raw = json.loads(raw) if raw.strip() else []
		except ValueError:
			raw = [raw]
	out = []
	for m in raw or []:
		if isinstance(m, str) and m.strip() and m.strip() not in out:
			out.append(m.strip())
	return out


def _shape(row) -> dict:
	row = dict(row)
	row["metros"] = _metro_list(row.get("metro_areas"))
	services = []
	if row.get("does_photos"):
		services.append("Photos")
	if row.get("does_lockbox"):
		services.append("Lockbox")
	row["services"] = services
	return row


def _active_contractors() -> list[dict]:
	if not _ready():
		return []
	rows = frappe.get_all(
		DOCTYPE, filters={"active": 1}, fields=LIST_FIELDS, limit_page_length=0,
		order_by="contractor_name asc",
	)
	return [_shape(r) for r in rows]


def match(metro, contractors) -> list[dict]:
	"""Contractors covering `metro`, trimmed to what a card popover needs."""
	if not metro:
		return []
	return [
		{
			"name": c["name"],
			"contractor_name": c.get("contractor_name"),
			"company": c.get("company"),
			"phone": c.get("phone"),
			"services": c.get("services") or [],
		}
		for c in contractors
		if metro in c["metros"]
	]


def card_badges(leads) -> dict:
	"""{lead name: {"metro": str, "contractors": [...]}} for Photos & Lockbox
	cards. `leads` rows need name, status, property_county, property_state.

	A card whose metro nobody covers still gets an entry with an empty list, so
	the board can say "no contractor on file for <metro>" -- that is exactly the
	card someone needs to go find a contractor for.
	"""
	wanted = [l for l in leads if l.get("status") == PHOTOS_LOCKBOX]
	if not wanted:
		return {}
	contractors = _active_contractors()
	out = {}
	for lead in wanted:
		metro = lead_metro(lead.get("property_county"), lead.get("property_state"))
		out[lead["name"]] = {"metro": metro, "contractors": match(metro, contractors)}
	return out


# --- API -----------------------------------------------------------------


@frappe.whitelist()
def get_contractors(search=None, metro=None, include_inactive=0):
	"""The directory: every contractor, optionally filtered."""
	_guard()
	if not _ready():
		return []
	filters = []
	if not cint(include_inactive):
		filters.append(["active", "=", 1])
	if metro:
		# metro_areas is a JSON array of names -- match the quoted element
		filters.append(["metro_areas", "like", f"%{json.dumps(metro)}%"])
	or_filters = None
	if search:
		q = f"%{search.strip()}%"
		or_filters = [
			["contractor_name", "like", q],
			["company", "like", q],
			["phone", "like", q],
			["email", "like", q],
		]
	rows = frappe.get_all(
		DOCTYPE, filters=filters, or_filters=or_filters, fields=LIST_FIELDS,
		order_by="active desc, contractor_name asc", limit_page_length=0,
	)
	return [_shape(r) for r in rows]


@frappe.whitelist()
def get_lead_contractors(lead):
	"""Contractors covering one lead's metro (the lead page / card popover)."""
	_guard()
	county, state = frappe.db.get_value(
		"CRM Lead", lead, ["property_county", "property_state"]
	) or (None, None)
	metro = lead_metro(county, state)
	return {"metro": metro, "contractors": match(metro, _active_contractors())}


def _clean(values: dict) -> dict:
	vals = {}
	for k, v in (values or {}).items():
		if k not in EDITABLE:
			continue
		if k == "metro_areas":
			vals[k] = json.dumps(_metro_list(v), ensure_ascii=False)
		elif k in CHECKS:
			vals[k] = 1 if cint(v) else 0
		else:
			v = (v or "").strip() if isinstance(v, str) else v
			vals[k] = v or None
	if vals.get("email"):
		vals["email"] = vals["email"].lower()
	return vals


@frappe.whitelist()
def save_contractor(values, contractor=None):
	"""Create (no `contractor`) or update a contractor. Returns its name."""
	_guard()
	if not _ready():
		frappe.throw(_("Contractors are not set up on this site yet."))
	if isinstance(values, str):
		values = json.loads(values)
	vals = _clean(values)
	if contractor:
		if not frappe.db.exists(DOCTYPE, contractor):
			frappe.throw(_("Contractor not found"), frappe.DoesNotExistError)
		if "contractor_name" in vals and not vals["contractor_name"]:
			frappe.throw(_("Name is required."))
		doc = frappe.get_doc(DOCTYPE, contractor)
		doc.update(vals)
		doc.save()
		return {"name": doc.name}
	if not vals.get("contractor_name"):
		frappe.throw(_("Name is required."))
	vals.setdefault("active", 1)
	doc = frappe.get_doc({"doctype": DOCTYPE, **vals})
	doc.insert()
	return {"name": doc.name}


@frappe.whitelist()
def delete_contractor(contractor):
	_guard()
	frappe.delete_doc(DOCTYPE, contractor)
	return {"ok": True}

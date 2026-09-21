"""Lead status lifecycle: real deletion + a guard for statuses code depends on.

Two problems this closes (Lance, 2026-09-22):

1. Deleting a kanban column only set ``delete: true`` on the saved view — the
   CRM Lead Status row survived, so the status stayed assignable and any lead
   given it vanished from the board. ``delete_status`` removes the status
   EVERYWHERE: the status row, every saved view (all users), and it refuses
   while any lead still sits in the status.

2. Several status NAMES are load-bearing for app logic (Today board phases,
   dashboard acq/dispo scopes, the dispo gate). Renaming one in the UI used to
   break that logic silently — "Needs Listing"/"Marketing to Buyer" were
   renamed to "Submit to Dispo"/"Dispo Accepted" and the code kept matching
   the old names, so e.g. Dispo Accepted leads got daily Today nudge cards.
   ``watched_statuses()`` is derived from the code constants themselves (never
   a hand-maintained copy), and hooks on CRM Lead Status alert Lance the
   moment a watched status is renamed or deleted, plus a daily integrity
   sweep in case a change happens outside the hooks.
"""

from __future__ import annotations

import importlib
import json

import frappe
from frappe import _

#: Status doctype -> (label field, the doctype whose records reference it).
STATUS_DOCTYPES = {
	"CRM Lead Status": ("lead_status", "CRM Lead"),
	"CRM Deal Status": ("deal_status", "CRM Deal"),
}

#: (module, constant, where it is used). The watched set is DERIVED from these
#: constants so the guard can never drift from the logic it protects.
WATCH_SOURCES = (
	("crm.api.daily_standup", "CHASE_STATUSES", "Today board cadence / standup grouping"),
	("crm.api.daily_standup", "CLOSER_STATUSES", "Today board 'deal in flight' phase"),
	("crm.api.daily_standup", "POST_CONTRACT_STATUSES", "Today board / standup post-contract set"),
	("crm.api.leads_dashboard", "ACQ_STATUSES", "Dashboard acquisition scope"),
	("crm.api.leads_dashboard", "DISPO_STATUSES", "Dashboard dispo scope"),
	("crm.api.investorlift_ingest", "DISPO_LEAD_STATUSES", "Dispo board gate / buyer-import property picker"),
)

DELETE_ROLES = ("System Manager", "Sales Manager")
MAX_MOVE = 300


def watched_statuses():
	"""{status name: [usage sites]} for every status app logic matches by name.

	Imports are lazy and per-source: a module that cannot import in some
	context (unit-test shim, mid-deploy) drops its entries rather than taking
	the hook down — an incomplete guard that still fires beats no guard.
	"""
	watched = {}
	for module_path, constant, site in WATCH_SOURCES:
		try:
			module = importlib.import_module(module_path)
			names = getattr(module, constant, ()) or ()
		except Exception:
			continue
		for name in names:
			watched.setdefault(name, [])
			if site not in watched[name]:
				watched[name].append(site)
	return watched


def missing_watched_statuses():
	existing = set(frappe.get_all("CRM Lead Status", pluck="lead_status"))
	return [name for name in watched_statuses() if name not in existing]


def _notify_recipient():
	return frappe.conf.get("status_guard_notify") or "lance.johnson@groundworkpro.com"


def _notify(subject, message):
	try:
		frappe.sendmail(
			recipients=[_notify_recipient()],
			subject=subject,
			message=message.replace("\n", "<br>"),
		)
	except Exception:
		frappe.log_error(message, subject)


def _sites_text(name, watched):
	sites = watched.get(name) or []
	return "\n".join(f"  - {s}" for s in sites) or "  - (registry changed — re-check)"


def guard_on_update(doc, method=None):
	"""CRM Lead Status on_update: alert when a watched status is renamed."""
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	if not before:
		return
	old, new = (before.get("lead_status") or ""), (doc.get("lead_status") or "")
	if old == new:
		return
	watched = watched_statuses()
	if old not in watched:
		return
	_notify(
		f"CRM status renamed: {old} → {new}",
		f"The lead status <b>{old}</b> was renamed to <b>{new}</b> by {frappe.session.user}.\n\n"
		f"App code matches the OLD name in:\n{_sites_text(old, watched)}\n\n"
		"Update those constants (and any mirrored frontend lists like ACQ_STAGES) "
		"or that logic silently stops matching.",
	)


def guard_on_trash(doc, method=None):
	"""CRM Lead Status on_trash: alert when a watched status is deleted."""
	name = doc.get("lead_status") or doc.name
	watched = watched_statuses()
	if name not in watched:
		return
	leads = frappe.db.count("CRM Lead", {"status": name})
	_notify(
		f"CRM status deleted: {name}",
		f"The lead status <b>{name}</b> was deleted by {frappe.session.user} "
		f"({leads} lead(s) still reference it).\n\n"
		f"App code matches this name in:\n{_sites_text(name, watched)}\n\n"
		"Update those constants (and any mirrored frontend lists) if the deletion is permanent.",
	)


def daily_integrity_check():
	"""Daily sweep: a watched status missing from the table means logic is
	silently matching nothing (rename/delete happened outside the hooks)."""
	missing = missing_watched_statuses()
	if not missing:
		return
	watched = watched_statuses()
	detail = "\n".join(f"- {n}\n{_sites_text(n, watched)}" for n in missing)
	_notify(
		f"CRM status guard: {len(missing)} code-referenced status(es) missing",
		"These statuses are referenced by name in app code but do not exist in "
		f"CRM Lead Status — the matching logic is silently doing nothing:\n\n{detail}",
	)


@frappe.whitelist()
def check_status_integrity():
	"""Read-only probe: which watched statuses exist, which are missing."""
	watched = watched_statuses()
	existing = set(frappe.get_all("CRM Lead Status", pluck="lead_status"))
	return {
		"watched": sorted(watched),
		"missing": [n for n in watched if n not in existing],
	}


def _clean_view_json(raw, status):
	"""Drop every entry named `status` from a view's columns/kanban_columns
	JSON (incl. soft-deleted ones). Pure; returns (new_raw, changed)."""
	if not raw:
		return raw, False
	try:
		entries = json.loads(raw)
	except Exception:
		return raw, False
	kept = [c for c in entries if c.get("name") != status]
	if len(kept) == len(entries):
		return raw, False
	return json.dumps(kept), True


def _clean_saved_views(parent_doctype, status):
	cleaned = 0
	for view in frappe.get_all(
		"CRM View Settings",
		filters={"dt": parent_doctype},
		fields=["name", "columns", "kanban_columns"],
	):
		changed = False
		updates = {}
		for field in ("columns", "kanban_columns"):
			new_raw, did = _clean_view_json(view.get(field), status)
			if did:
				updates[field] = new_raw
				changed = True
		if changed:
			frappe.db.set_value("CRM View Settings", view.name, updates, update_modified=False)
			cleaned += 1
	return cleaned


@frappe.whitelist()
def delete_status(doctype, status, move_to=None):
	"""Delete a status EVERYWHERE — the status row, every user's saved kanban
	view, and (with move_to) the records still sitting in it.

	Refuses while records reference the status unless move_to names the status
	they should take; deleting a column off one board must never orphan a lead.
	"""
	if doctype not in STATUS_DOCTYPES:
		frappe.throw(_("Unknown status doctype: {0}").format(doctype))
	roles = set(frappe.get_roles())
	if not (set(DELETE_ROLES) & roles):
		frappe.throw(_("Only a Sales Manager or System Manager can delete a status."))

	label_field, parent_doctype = STATUS_DOCTYPES[doctype]
	row_name = frappe.db.get_value(doctype, {label_field: status}, "name")
	if not row_name:
		frappe.throw(_('No {0} named "{1}".').format(doctype, status))

	records = frappe.get_all(parent_doctype, filters={"status": status}, pluck="name")
	moved = 0
	if records and not move_to:
		frappe.throw(
			_('{0} record(s) are in "{1}" — move them to another status first.').format(
				len(records), status
			)
		)
	if records:
		if not frappe.db.get_value(doctype, {label_field: move_to}, "name"):
			frappe.throw(_('Cannot move to "{0}" — no such status.').format(move_to))
		if len(records) > MAX_MOVE:
			frappe.throw(
				_("Refusing to move {0} records in one go (cap {1}).").format(len(records), MAX_MOVE)
			)
		for name in records:
			# doc.save() on purpose: the status change log, task hygiene and
			# sequence-status hooks all hang off a real save.
			doc = frappe.get_doc(parent_doctype, name)
			doc.status = move_to
			doc.save()
			moved += 1

	frappe.delete_doc(doctype, row_name)
	views_cleaned = _clean_saved_views(parent_doctype, status)
	return {"deleted": status, "moved": moved, "views_cleaned": views_cleaned}

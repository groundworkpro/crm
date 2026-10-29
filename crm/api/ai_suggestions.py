"""Suggested pricer fixes: a list of the corrections the team gives Ask AI, for the
pricer's owner to review before anything goes into the comping skill.

Exe, 2026-10-29: "when a team member disagrees or makes a correction on the Ask AI
chat it saves on a list of suggested updates that I can later pull and look through
to see which ones we will actually push to the skill."

Rows in **CRM AI Suggestion** come from two places:
  - Disagree: the rep's "Disagree?" form on a price card (arv_feedback.send). The
    Talk DM to Exe still goes out; this is the reviewable copy.
  - Chat: a follow-up in the Ask AI chat that corrects the AI. The agentbox worker
    tells the model to end such replies with a `CORRECTION:` line, strips it, and
    returns it as `correction`. ai_pricer._log_reply files it here.

Nothing here changes the skill. Exe pulls the list (comping-computers:
`python3 -m comping.suggestions`) and marks each one Accepted, Rejected or Added to
skill. The doctype is made by `ensure_doctype()` (run once with bench execute).
Until it exists nothing is written, and a failed write never breaks the chat.
"""

from __future__ import annotations

import json

import frappe
from frappe import _

DOCTYPE = "CRM AI Suggestion"
CHAT_DOCTYPE = "CRM AI Chat Message"
STATUSES = ("New", "Accepted", "Rejected", "Added to skill")
SOURCES = ("Disagree", "Chat")
MAX_TEXT = 8000

FIELDS = [
	{"fieldname": "status", "label": "Status", "fieldtype": "Select", "options": "\n".join(STATUSES),
	 "default": "New", "in_list_view": 1, "in_standard_filter": 1, "search_index": 1},
	{"fieldname": "source", "label": "Source", "fieldtype": "Select", "options": "\n".join(SOURCES),
	 "in_list_view": 1, "in_standard_filter": 1, "read_only": 1},
	{"fieldname": "lead", "label": "Lead", "fieldtype": "Link", "options": "CRM Lead",
	 "in_standard_filter": 1, "read_only": 1},
	{"fieldname": "address", "label": "Address", "fieldtype": "Data", "in_list_view": 1, "read_only": 1},
	{"fieldname": "user", "label": "From", "fieldtype": "Link", "options": "User",
	 "in_list_view": 1, "in_standard_filter": 1, "read_only": 1},
	{"fieldname": "col_meta", "fieldtype": "Column Break"},
	{"fieldname": "ai_arv", "label": "AI ARV", "fieldtype": "Currency", "read_only": 1},
	{"fieldname": "rep_arv", "label": "Rep ARV", "fieldtype": "Currency", "read_only": 1},
	{"fieldname": "chat", "label": "Chat", "fieldtype": "Data", "read_only": 1},
	{"fieldname": "job", "label": "Job", "fieldtype": "Data", "read_only": 1},
	{"fieldname": "sec_text", "fieldtype": "Section Break"},
	{"fieldname": "correction", "label": "What the rep said", "fieldtype": "Long Text", "read_only": 1},
	{"fieldname": "lesson", "label": "Suggested lesson (AI's summary)", "fieldtype": "Small Text",
	 "in_list_view": 1, "read_only": 1},
	{"fieldname": "context", "label": "AI's math / reply", "fieldtype": "Long Text", "read_only": 1},
	{"fieldname": "review_note", "label": "Review note", "fieldtype": "Small Text"},
]


def supported() -> bool:
	return bool(frappe.db.exists("DocType", DOCTYPE))


def _num(v):
	try:
		return float(v) if v not in (None, "") else None
	except (TypeError, ValueError):
		return None


def build_row(source: str, user: str, correction: str, lead=None, address="", lesson="", ai_arv=None,
              rep_arv=None, context="", chat=None, job=None) -> dict:
	if source not in SOURCES:
		raise ValueError(source)
	return {
		"doctype": DOCTYPE,
		"status": "New",
		"source": source,
		"user": user,
		"lead": lead or None,
		"address": (address or "")[:140],
		"correction": (correction or "").strip()[:MAX_TEXT],
		"lesson": (lesson or "").strip()[:1000],
		"ai_arv": _num(ai_arv),
		"rep_arv": _num(rep_arv),
		"context": (context or "").strip()[:MAX_TEXT],
		"chat": chat,
		"job": job,
	}


def record(source: str, user: str, correction: str, **kw):
	"""File one suggestion; returns its name (truthy) or None. Never raises: it must not break the chat or the DM."""
	try:
		if not supported() or not (correction or "").strip():
			return None
		doc = frappe.get_doc(build_row(source, user, correction, **kw))
		doc.insert(ignore_permissions=True)
		return getattr(doc, "name", None) or True
	except Exception:
		frappe.log_error(title="Ask AI: suggestion write failed")
		return None


def _check_reviewer():
	from crm.api.arv_feedback import recipient

	user = frappe.session.user
	if user == "Administrator" or user.lower() == recipient() or "System Manager" in frappe.get_roles(user):
		return
	frappe.throw(_("Only the pricer's owner reviews suggestions."), frappe.PermissionError)


def _transcript(chat: str, job: str | None) -> list[dict]:
	"""The chat up to and including this job's reply, oldest first."""
	if not chat or not frappe.db.exists("DocType", CHAT_DOCTYPE):
		return []
	rows = frappe.get_all(
		CHAT_DOCTYPE,
		filters={"chat": chat},
		fields=["role", "text", "job", "creation"],
		order_by="creation asc",
		limit=100,
	)
	out = []
	for r in rows:
		out.append({"role": r.role, "text": r.text or ""})
		if job and r.job == job and r.role == "ai":
			break
	return out


@frappe.whitelist()
def export(status: str = "New", limit: int = 200, transcripts: int = 1):
	"""Suggestions for review, oldest first. status "" or "all" = every status."""
	_check_reviewer()
	if not supported():
		return []
	filters = {} if status in ("", "all", None) else {"status": status}
	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=["name", "creation", "status", "source", "lead", "address", "user", "ai_arv", "rep_arv",
		        "correction", "lesson", "context", "chat", "job", "review_note"],
		order_by="creation asc",
		limit=min(int(limit or 200), 1000),
	)
	out = []
	for r in rows:
		r = dict(r)
		r["creation"] = str(r["creation"])
		if int(transcripts or 0) and r.get("chat"):
			r["transcript"] = _transcript(r["chat"], r.get("job"))
		out.append(r)
	return out


@frappe.whitelist(methods=["POST"])
def set_status(names, status: str, note: str = ""):
	"""Mark suggestions reviewed. names: one name or a JSON list."""
	_check_reviewer()
	if status not in STATUSES:
		frappe.throw(_("Status must be one of: {0}").format(", ".join(STATUSES)))
	if isinstance(names, str):
		names = json.loads(names) if names.strip().startswith("[") else [names]
	done = []
	for name in names:
		if not frappe.db.exists(DOCTYPE, name):
			continue
		values = {"status": status}
		if note:
			values["review_note"] = note[:1000]
		frappe.db.set_value(DOCTYPE, name, values)
		done.append(name)
	frappe.db.commit()
	return {"updated": done}


def ensure_doctype():
	"""Create or extend the doctype (idempotent). bench execute crm.api.ai_suggestions.ensure_doctype"""
	if frappe.session.user != "Administrator":
		frappe.throw(_("Run this with bench execute."), frappe.PermissionError)
	if supported():
		doc = frappe.get_doc("DocType", DOCTYPE)
		have = {f.fieldname for f in doc.fields}
		missing = [f for f in FIELDS if f["fieldname"] not in have]
		for f in missing:
			doc.append("fields", f)
		if missing:
			doc.save()
		return {"ok": True, "added": [f["fieldname"] for f in missing]}
	frappe.get_doc(
		{
			"doctype": "DocType",
			"name": DOCTYPE,
			"custom": 1,
			"module": "FCRM",
			"autoname": "hash",
			"sort_field": "creation",
			"sort_order": "DESC",
			"title_field": "address",
			"search_fields": "address,user,status",
			"fields": FIELDS,
			"permissions": [
				{"role": "System Manager", "read": 1, "write": 1, "report": 1, "export": 1},
				{"role": "Sales Manager", "read": 1, "report": 1, "export": 1},
			],
		}
	).insert()
	return {"ok": True, "created": DOCTYPE}

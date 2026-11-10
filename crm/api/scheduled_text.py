"""Schedule a text to a lead for later ("send Saturday 9am").

Why: the New Lead 10-Day sequence hands the rep a "Text <name> — day N" task
every day, and some of those days are Saturdays. Rather than texting on a
weekend by hand, the rep writes it Friday and schedules it (Lance, 2026-09-09).

Storage is a `Quo Message` row, so the scheduled text sits IN the lead's
thread at its future time with a "Scheduled" badge and a cancel button — no
new doctype. `id` is the docname and must be unique, so a scheduled row gets
a synthetic `sched-…` id and `status = "scheduled"`. When it is due, the
1-minute scheduler (`send_due`, on the same cron as `drain_due` — needs
`sync_jobs` on prod) POSTs it to OpenPhone, inserts the REAL row under the
Quo message id (the same dedupe the `send-text` script and the webhook use, so
the later `message.delivered` event does not double it) and deletes the
placeholder. The real row's after_insert hook fires the `quo_message`
realtime, so the thread swaps the placeholder for the sent bubble live.

A lead that went Dead/Lost before the send time is not texted: the row is
marked `canceled` with the reason in `content` untouched, visible in the thread.
"""

import secrets

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, now_datetime

from crm.api.bulk_text import OPENPHONE_MESSAGES_API, _e164, _guard, _resolve_from_number
from crm.api.sms import validate_access
from crm.api.text_delivery import text_block_reason

STATUS_SCHEDULED = "scheduled"
STATUS_CANCELED = "canceled"
STATUS_FAILED = "failed"
ID_PREFIX = "sched-"
MANAGER_ROLES = ("Sales Manager", "System Manager")


def _lead_primary_number(lead):
	return frappe.db.get_value("CRM Lead", lead, "mobile_no") or ""


def _publish(reference_doctype, reference_docname):
	frappe.publish_realtime(
		"quo_message",
		{"reference_doctype": reference_doctype, "reference_docname": reference_docname},
		after_commit=True,
	)


@frappe.whitelist()
def schedule_text(reference_doctype, reference_name, content, send_at, to=None, from_number=None):
	"""Queue one text to a lead for `send_at` (site-local datetime string)."""
	_guard()
	if reference_doctype != "CRM Lead":
		frappe.throw(_("Texts can only be scheduled on a lead."))
	validate_access(reference_doctype, reference_name, "write")
	content = (content or "").strip()
	if not content:
		frappe.throw(_("Write the message first."))
	when = get_datetime(send_at)
	if not when or when <= now_datetime():
		frappe.throw(_("Pick a time in the future."))
	e164 = _e164(to or _lead_primary_number(reference_name))
	if not e164:
		frappe.throw(_("The lead has no valid mobile number."))
	reason = text_block_reason(e164)
	if reason:
		frappe.throw(reason)
	from_num = _resolve_from_number(from_number)

	doc = frappe.get_doc(
		{
			"doctype": "Quo Message",
			"id": ID_PREFIX + secrets.token_hex(8),
			"provider": "quo",
			"direction": "Outgoing",
			"from": from_num,
			"to": e164,
			"content": content,
			"status": STATUS_SCHEDULED,
			"message_date": when,
			"sent_by": frappe.session.user,
			"activity_source": "Manual",
			"reference_doctype": reference_doctype,
			"reference_docname": reference_name,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "send_at": str(when), "to": e164}


@frappe.whitelist()
def cancel_scheduled_text(name):
	"""Delete a not-yet-sent scheduled text. The person who scheduled it, or a
	manager. Anything already sent is not touchable here."""
	_guard()
	row = frappe.db.get_value(
		"Quo Message",
		name,
		["status", "sent_by", "reference_doctype", "reference_docname"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Not found."))
	if (row.status or "").lower() != STATUS_SCHEDULED:
		frappe.throw(_("This text has already been sent."))
	if row.sent_by != frappe.session.user and not any(
		r in MANAGER_ROLES for r in frappe.get_roles()
	):
		frappe.throw(_("Only the person who scheduled this text can cancel it."), frappe.PermissionError)
	frappe.delete_doc("Quo Message", name, ignore_permissions=True, force=1)
	_publish(row.reference_doctype, row.reference_docname)
	return {"ok": True}


def send_due():
	"""1-min scheduler: send every scheduled text whose time has come."""
	rows = frappe.get_all(
		"Quo Message",
		filters={"status": STATUS_SCHEDULED, "message_date": ["<=", now_datetime()]},
		fields=["name"],
		order_by="message_date asc",
	)
	for r in rows:
		try:
			_dispatch(r.name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "scheduled_text: send failed " + r.name)
			try:
				_mark(r.name, STATUS_FAILED)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()


def _mark(name, status):
	row = frappe.db.get_value(
		"Quo Message", name, ["reference_doctype", "reference_docname"], as_dict=True
	)
	frappe.db.set_value("Quo Message", name, "status", status, update_modified=False)
	if row:
		_publish(row.reference_doctype, row.reference_docname)


def _lead_is_terminal(lead):
	status = frappe.db.get_value("CRM Lead", lead, "status")
	if not status:
		return True
	return frappe.db.get_value("CRM Lead Status", status, "type") in ("Lost", "Won")


def _dispatch(name):
	row = frappe.get_doc("Quo Message", name)
	if (row.status or "").lower() != STATUS_SCHEDULED:
		return
	if row.reference_doctype == "CRM Lead" and _lead_is_terminal(row.reference_docname):
		_mark(name, STATUS_CANCELED)
		return
	if text_block_reason(row.to):
		_mark(name, STATUS_CANCELED)
		return
	token = (frappe.conf.get("quo_api_key") or "").strip()
	if not token:
		frappe.throw("quo_api_key is not configured")
	resp = requests.post(
		OPENPHONE_MESSAGES_API,
		headers={"Authorization": token, "User-Agent": "curl/8.1.0"},
		json={"content": row.content, "from": row.get("from"), "to": [row.to]},
		timeout=30,
	)
	resp.raise_for_status()
	data = (resp.json() or {}).get("data") or {}
	msg_id = str(data.get("id") or "")
	status = data.get("status") or "sent"
	if msg_id and not frappe.db.exists("Quo Message", {"id": msg_id}):
		frappe.get_doc(
			{
				"doctype": "Quo Message",
				"id": msg_id,
				"provider": "quo",
				"direction": "Outgoing",
				"from": row.get("from"),
				"to": row.to,
				"content": row.content,
				"status": status,
				"message_date": now_datetime(),
				"sent_by": row.sent_by,
				"activity_source": "Manual",
				"reference_doctype": row.reference_doctype,
				"reference_docname": row.reference_docname,
			}
		).insert(ignore_permissions=True)
	frappe.delete_doc("Quo Message", name, ignore_permissions=True, force=1)

"""Notify the lead owner when a DocuSeal agreement is viewed / started / signed.

Driven from `crm.api.agreement.docuseal_webhook`: every DocuSeal event lands
there, and after the CRM Esign Agreement row is updated we call `notify_event`
with the raw `event_type` + payload `data`. We resolve the lead owner, read
their per-user preferences (`crm.api.notification_prefs`), and — per their
channel + per-event toggles — send a **text** (OpenPhone/Quo, from the dedicated
"Notifications" line) and/or an **email**.

Notifications must never break the webhook: every send is wrapped, failures are
logged and swallowed so DocuSeal still gets its 200.
"""

import frappe
import requests
from frappe import _

from crm.api.notification_prefs import get_prefs

OPENPHONE_MESSAGES_API = "https://api.openphone.com/v1/messages"

# The dedicated "Notifications" Quo/OpenPhone line, (952) 395-3833. Overridable
# via site_config `notifications_quo_number`.
DEFAULT_NOTIFICATIONS_NUMBER = "+19523953833"

# DocuSeal event_type → our preference category. Anything not here (declined,
# expired, archived, …) is not notified.
EVENT_CATEGORY = {
	"form.viewed": "viewed",
	"form.started": "started",
	"form.completed": "signed",
	"submission.completed": "signed",
}

# Human verb per DocuSeal event (the per-signer form.completed reads differently
# from the whole-envelope submission.completed).
_EVENT_VERB = {
	"form.viewed": "viewed",
	"form.started": "started filling out",
	"form.completed": "signed",
	"submission.completed": "fully signed",
}


def _e164(raw: str) -> str:
	"""US phone → E.164: 10 digits → +1XXXXXXXXXX, 11 w/ leading 1 → +1…, else ""."""
	digits = "".join(ch for ch in (raw or "") if ch.isdigit())
	if len(digits) == 10:
		return "+1" + digits
	if len(digits) == 11 and digits[0] == "1":
		return "+" + digits
	if (raw or "").strip().startswith("+"):
		return "+" + digits
	return ""


def _actor_name(data) -> str:
	"""Best-effort name of who triggered the event (the DocuSeal submitter)."""
	if not isinstance(data, dict):
		return "Someone"
	sub = data.get("submitter") if isinstance(data.get("submitter"), dict) else {}
	return data.get("name") or sub.get("name") or data.get("role") or sub.get("role") or "Someone"


def notify_event(agr, event, data):
	"""Text/email the lead owner about a DocuSeal `event` on agreement `agr`.

	`agr` is the saved CRM Esign Agreement doc; `event` the DocuSeal event_type;
	`data` the webhook payload's `data` (the submitter for form.* events).
	"""
	category = EVENT_CATEGORY.get(event)
	if not category:
		return

	lead = agr.get("lead")
	owner = frappe.db.get_value("CRM Lead", lead, "lead_owner") if lead else None
	owner = owner or agr.get("owner")
	if not owner or owner in ("Administrator", "Guest"):
		return

	prefs = get_prefs(owner)
	if not prefs.get(category):
		return

	actor = _actor_name(data)
	verb = _EVENT_VERB.get(event, category)
	title = (agr.get("template_title") or "agreement").strip()
	address = (frappe.db.get_value("CRM Lead", lead, "property_address") or "").strip() if lead else ""
	# A second-house agreement names that house, not the seller's main one.
	from crm.api.agreement import PROPERTY_DOCTYPE, agreement_property

	prop = agreement_property(agr)
	if prop:
		address = (frappe.db.get_value(PROPERTY_DOCTYPE, prop, "property_address") or "").strip() or address

	if prefs.get("text"):
		_safe(_send_text, owner, prefs, actor, verb, title, address)
	if prefs.get("email"):
		_safe(_send_email, owner, lead, actor, verb, title, address)


def _safe(fn, *args):
	try:
		fn(*args)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "agreement notification failed")


# --------------------------------------------------------------------------- #
# text (OpenPhone / Quo)
# --------------------------------------------------------------------------- #
def _send_text(owner, prefs, actor, verb, title, address):
	number = prefs.get("text_number") or frappe.db.get_value("User", owner, "custom_quo_number")
	to = _e164(number)
	if not to:
		return
	token = (frappe.conf.get("quo_api_key") or "").strip()
	if not token:
		frappe.log_error("quo_api_key not set in site_config", "agreement notification failed")
		return
	from_num = (frappe.conf.get("notifications_quo_number") or DEFAULT_NOTIFICATIONS_NUMBER).strip()

	body = f"{actor} {verb} the {title}"
	if address:
		body += f" for {address}"
	body += "."

	requests.post(
		OPENPHONE_MESSAGES_API,
		headers={"Authorization": token, "User-Agent": "curl/8.1.0"},
		json={"content": body, "from": from_num, "to": [to]},
		timeout=20,
	)


# --------------------------------------------------------------------------- #
# email
# --------------------------------------------------------------------------- #
def _send_email(owner, lead, actor, verb, title, address):
	email = frappe.db.get_value("User", owner, "email") or owner
	if not email or "@" not in email:
		return

	label = address or title
	link = f"{frappe.utils.get_url()}/crm/leads/{lead}" if lead else frappe.utils.get_url("/crm")

	frappe.sendmail(
		recipients=[email],
		subject=f"{actor} {verb} the {title}" + (f" — {address}" if address else ""),
		template="crm_agreement_notification",
		args={
			"actor": actor,
			"verb": verb,
			"title": title,
			"label": label,
			"link": link,
		},
		reference_doctype="CRM Lead",
		reference_name=lead,
		now=True,
	)

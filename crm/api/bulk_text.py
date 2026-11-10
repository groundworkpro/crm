"""Bulk-text buyers — text a picked group of CRM Buyers, one confirmed send at a time.

Entry points (frontend): the Dispo board's "Text buyers" button (seeded with that
deal's buyers) and a select-mode on the /buyers directory. The compose step lets
the user pick recipients and write a template with a `{{first_name}}` token; the
review step then walks the recipients ONE AT A TIME, showing each buyer's fully
rendered message so the user can eyeball the variable substitution (and edit it)
before clicking Send. Hence the send here is a **single** synchronous text per
click — there is deliberately no background blast.

Like `agreement_notify.py`, this runs in app code and POSTs OpenPhone directly
with the site_config `quo_api_key`, so it can reference **CRM Buyer** (buyer texts
are stored/threaded on CRM Buyer per gw177) without going through the lead-scoped
`send-text` sandbox server script. The stored Quo Message's after_insert hook
emits the `quo_message` realtime event, so the buyer's Conversation tab updates
live.
"""

import frappe
import requests
from frappe import _

from crm.api.do_not_contact import is_blocked, is_blocked_number
from crm.api.text_delivery import text_block_reason
from crm.api.investorlift_ingest import BUYER_DOCTYPE

OPENPHONE_MESSAGES_API = "https://api.openphone.com/v1/messages"
SALES_ROLES = ("System Manager", "Sales Manager", "Sales User")


def _guard():
	if not any(r in SALES_ROLES for r in frappe.get_roles()):
		frappe.throw(_("Only sales users can send texts."), frappe.PermissionError)


def _e164(raw):
	"""US phone → E.164: 10 digits → +1XXXXXXXXXX, 11 w/ leading 1 → +1…, else ""."""
	digits = "".join(ch for ch in (raw or "") if ch.isdigit())
	if len(digits) == 10:
		return "+1" + digits
	if len(digits) == 11 and digits[0] == "1":
		return "+" + digits
	if (raw or "").strip().startswith("+") and digits:
		return "+" + digits
	return ""


def _resolve_from_number(from_number):
	"""The sender's Quo line: an explicit pick this send, else their linked number.
	A first-time pick is saved to their profile (mirrors the send-text script)."""
	chosen = (from_number or "").strip()
	linked = frappe.db.get_value("User", frappe.session.user, "custom_quo_number")
	from_num = chosen or linked
	if not from_num:
		frappe.throw(_("No Quo number is set for you. Pick your Quo number before texting."))
	if chosen and not linked:
		frappe.db.set_value(
			"User", frappe.session.user, "custom_quo_number", chosen, update_modified=False
		)
	return from_num


@frappe.whitelist()
def send_buyer_text(buyer, content, from_number=None):
	"""Send one text to one CRM Buyer and mirror it into Quo Message (referenced to
	the buyer, so it threads on the buyer's Conversation tab).

	`content` is sent verbatim — the frontend renders `{{first_name}}` and lets the
	user confirm/edit each message, so whatever they approved is exactly what goes.
	The buyer's phone is resolved server-side (authoritative)."""
	_guard()
	content = (content or "").strip()
	if not content:
		frappe.throw(_("Message is empty."))

	b = frappe.db.get_value(BUYER_DOCTYPE, buyer, ["name", "buyer_name", "phone"], as_dict=True)
	if not b:
		frappe.throw(_("Buyer not found."), frappe.DoesNotExistError)

	# Hard stop, server-side and last (gw296). The UI also filters these out, but a
	# buyer who asked to be removed must not depend on a client-side filter staying
	# correct: the Danny Stoica text went out precisely because the signal the UI
	# filtered on (the Dispo column) had been quietly changed by another system.
	if is_blocked(b.name):
		frappe.throw(
			_("{0} asked not to be contacted. Clear Do Not Contact on the buyer first.")
			.format(b.buyer_name or buyer)
		)

	e164 = _e164(b.phone)
	if not e164:
		frappe.throw(_("{0} has no valid phone number.").format(b.buyer_name or buyer))

	# Second stop, keyed on the NUMBER rather than the record. Once Quo and Telnyx
	# run side by side the same person can exist as more than one row -- a duplicate
	# created by the IL webhook race, or a buyer reached on a Telnyx line whose
	# opt-out was recorded against a different record. The flag is a statement about
	# a person, so the last thing checked before we hand a number to a carrier is
	# that number itself.
	if is_blocked_number(e164):
		frappe.throw(
			_("{0} is on the do-not-contact list under another record. Clear it first.")
			.format(e164)
		)
	reason = text_block_reason(e164)
	if reason:
		frappe.throw(reason)

	from_num = _resolve_from_number(from_number)

	token = (frappe.conf.get("quo_api_key") or "").strip()
	if not token:
		frappe.throw(_("Texting is not configured (missing Quo API key)."))

	resp = requests.post(
		OPENPHONE_MESSAGES_API,
		headers={"Authorization": token, "User-Agent": "curl/8.1.0"},
		json={"content": content, "from": from_num, "to": [e164]},
		timeout=20,
	)
	resp.raise_for_status()
	data = (resp.json() or {}).get("data") or {}
	msg_id = str(data.get("id") or "")
	status = data.get("status") or "sent"
	name = _store_message(msg_id, from_num, e164, content, status, b.name)
	return {"ok": True, "id": msg_id, "name": name, "to": e164}


def _store_message(msg_id, from_num, e164, content, status, buyer):
	"""Mirror the sent text into Quo Message (dedup on the Quo message id, like the
	send-text script). Referenced to CRM Buyer so it lands on the buyer's thread;
	the Quo Message after_insert app hook emits the `quo_message` realtime event."""
	if not msg_id or frappe.db.exists("Quo Message", {"id": msg_id}):
		return ""
	values = {
		"doctype": "Quo Message",
		"id": msg_id,
		"direction": "Outgoing",
		"from": from_num,
		"to": e164,
		"content": content,
		"status": status,
		"message_date": frappe.utils.now_datetime(),
		"reference_doctype": BUYER_DOCTYPE,
		"reference_docname": buyer,
	}
	meta = frappe.get_meta("Quo Message")
	if meta.has_field("sent_by"):
		values["sent_by"] = frappe.session.user
	if meta.has_field("activity_source"):
		values["activity_source"] = "Manual"
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	return doc.name

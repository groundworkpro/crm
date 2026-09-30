"""Read-only API for the native SMS (text) feature.

Texts are stored in the custom **Quo Message** doctype (managed from the ops repo,
`../frappe-crm-deploy`): inbound mirrored by the `sequence-events` webhook on
`message.received`, outbound by `message.delivered` and by the `send-text` server
script (the Send Text action / compose box). Both directions are linked to a
CRM Lead by last-10 phone-digit matching.

This module only *reads* those messages for the UI — it touches no secrets. The
actual send lives in the `send-text` server script (it needs the Quo API key and
the per-user from-number map). Shapes mirror `crm/api/whatsapp.py` so the frontend
reuses the same thread components.
"""

import json

import frappe

from crm.api import telephony
from frappe import _

ALLOWED_SMS_ROLES = ["System Manager", "Sales Manager", "Sales User"]


def _media(raw):
	"""Parse the Quo Message `media` field (a JSON array of {url, type}) into the
	shape the thread renders. MMS attachments arrive only in the Quo webhook
	payload (the REST API omits them), so historical texts have no media."""
	if not raw:
		return []
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except (ValueError, TypeError):
			return []
	out = []
	for it in raw or []:
		if isinstance(it, dict) and it.get("url"):
			out.append({"url": it.get("url"), "type": it.get("type") or ""})
	return out


def validate_access(reference_doctype=None, reference_name=None, permtype="read"):
	if not any(role in ALLOWED_SMS_ROLES for role in frappe.get_roles()):
		frappe.throw(_("Only sales users can access text messages."), frappe.PermissionError)

	if reference_doctype and reference_name:
		if not frappe.db.exists(reference_doctype, reference_name):
			frappe.throw(
				_("Reference document {0} {1} does not exist.").format(reference_doctype, reference_name),
				frappe.DoesNotExistError,
			)
		reference_doc = frappe.get_doc(reference_doctype, reference_name)
		if not reference_doc.has_permission(permtype):
			frappe.throw(
				_("Not permitted to access reference document {0} {1}.").format(
					reference_doctype, reference_name
				),
				frappe.PermissionError,
			)
		return reference_doc

	return None


def _last10(number):
	return telephony.last10(number)


def _sender_map():
	"""Last-10-digit line -> user, for attributing an outbound text to the
	teammate whose line sent it (a stored message carries only phone numbers).

	Shared with the Team Activity report and the intraday pulse, and provider-wide:
	a rep who sends from Telnyx must still be named on their own texts, or the
	thread shows an unattributed message and the activity report under-counts them.
	"""
	owners = telephony.line_owners()
	if not owners:
		return {}
	names = {
		row.name: row
		for row in frappe.get_all(
			"User", filters={"name": ["in", list(set(owners.values()))]},
			fields=["name", "full_name"],
		)
	}
	return {digits: names[user] for digits, user in owners.items() if user in names}


def _shape(rows):
	"""Map raw Quo Message rows to the shape the thread component expects
	(it speaks the WhatsApp message vocabulary: type / message / creation)."""
	senders = _sender_map()
	out = []
	users = _user_names({m.get("sent_by") for m in rows if m.get("sent_by")})
	for m in rows:
		outgoing = m.direction == "Outgoing"
		sender = senders.get(_last10(m.get("from"))) if outgoing else None
		knock_url = m.get("knock_url") or None
		# Knock's numbers aren't Quo lines: name the teammate who sent it, or say
		# it was Knock's AI (an automated text with no teammate behind it).
		sender_id = sender.name if sender else (m.get("sent_by") if outgoing else None)
		if sender:
			sender_name = sender.full_name
		elif outgoing and m.get("sent_by"):
			sender_name = users.get(m.get("sent_by")) or m.get("sent_by")
		elif outgoing and knock_url:
			sender_name = "Knock AI" if m.get("activity_source") == "Sequence" else "Knock"
		else:
			sender_name = None
		out.append(
			{
				"name": m.name,
				"type": m.direction,
				"from": m.get("from"),
				"to": m.get("to"),
				"message": m.content,
				"media": _media(m.get("media")),
				"status": (m.status or "").lower(),
				"creation": m.message_date or m.creation,
				"reference_doctype": m.reference_doctype,
				"reference_name": m.reference_docname,
				"sender": sender_id,
				"sender_name": sender_name,
				"knock_url": knock_url,
			}
		)
	return out


def _user_names(users):
	if not users:
		return {}
	return {
		u.name: u.full_name
		for u in frappe.get_all("User", filters={"name": ["in", list(users)]}, fields=["name", "full_name"])
	}


def _message_fields():
	"""Columns to read. The Knock ones exist only once the ops setup script ran."""
	fields = [
		"name", "direction", "from", "to", "content", "media", "status",
		"message_date", "creation", "reference_doctype", "reference_docname",
	]
	meta = frappe.get_meta("Quo Message")
	return fields + [f for f in ("sent_by", "activity_source", "knock_url") if meta.has_field(f)]


def on_quo_message_insert(doc, method=None):
	"""after_insert hook on Quo Message — emit the realtime event the SMS thread
	and inbox listen for. Lives in app code because the server scripts that insert
	these (send-text, the Quo webhook) run in a sandbox without publish_realtime."""
	if doc.get("reference_doctype") and doc.get("reference_docname"):
		# No room/user → broadcast to the site room ("all"): every logged-in System
		# User gets it (site-wide). after_commit=True defers the emit until the insert
		# commits, so a listener's reload can't race ahead and miss the new message.
		frappe.publish_realtime(
			"quo_message",
			{
				"reference_doctype": doc.reference_doctype,
				"reference_docname": doc.reference_docname,
			},
			after_commit=True,
		)


@frappe.whitelist()
def is_sms_enabled():
	# The Quo Message doctype is provisioned on the site by the ops repo; its
	# presence is what gates the SMS tab / inbox in the UI.
	return bool(frappe.db.exists("DocType", "Quo Message"))


@frappe.whitelist()
def get_sms_messages(reference_doctype: str, reference_name: str):
	reference_doc = validate_access(reference_doctype, reference_name)
	if not frappe.db.exists("DocType", "Quo Message"):
		return []

	# (doctype, name) pairs to pull messages for. Texts are lead-linked, so on a
	# deal we also surface the linked lead's thread (same as get_whatsapp_messages).
	targets = [(reference_doctype, reference_name)]
	if reference_doctype == "CRM Deal":
		lead = reference_doc.get("lead")
		if lead:
			validate_access("CRM Lead", lead)
			targets.append(("CRM Lead", lead))

	fields = _message_fields()
	rows = []
	for rd, rn in targets:
		rows += frappe.get_all(
			"Quo Message",
			filters={"reference_doctype": rd, "reference_docname": rn},
			fields=fields,
			order_by="message_date asc",
			limit_page_length=0,
		)
	return _shape(rows)


@frappe.whitelist()
def set_user_quo_number(user: str, number: str = ""):
	"""Link a user to their Quo sending number. Managers/admins set it for anyone
	(Settings -> Users); a user may set their own (the send picker does this)."""
	roles = frappe.get_roles()
	is_manager = "System Manager" in roles or "Sales Manager" in roles
	if not is_manager and user != frappe.session.user:
		frappe.throw(_("Not permitted to set another user's Quo number."), frappe.PermissionError)
	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} does not exist.").format(user), frappe.DoesNotExistError)
	frappe.db.set_value("User", user, "custom_quo_number", (number or "").strip(), update_modified=False)
	return True


@frappe.whitelist()
def get_sms_conversations():
	"""One row per lead that has texts, most-recent first — feeds the inbox list."""
	validate_access()
	if not frappe.db.exists("DocType", "Quo Message"):
		return []

	rows = frappe.get_all(
		"Quo Message",
		filters={"reference_doctype": "CRM Lead"},
		fields=["reference_docname", "direction", "content", "media", "status", "message_date", "creation"],
		order_by="message_date desc, creation desc",
		limit_page_length=0,
	)

	convos = {}
	order = []
	for m in rows:
		lead = m.reference_docname
		if not lead:
			continue
		if lead not in convos:
			# rows are newest-first, so the first one seen per lead is the latest.
			# A media-only text (empty content) would show a blank preview — label it.
			preview = m.content or ("📷 Attachment" if _media(m.get("media")) else "")
			convos[lead] = {
				"lead": lead,
				"last_message": preview,
				"last_direction": m.direction,
				"last_date": m.message_date or m.creation,
				"count": 0,
			}
			order.append(lead)
		convos[lead]["count"] += 1

	result = []
	for lead in order:
		c = convos[lead]
		info = (
			frappe.db.get_value(
				"CRM Lead",
				lead,
				["lead_name", "first_name", "last_name", "mobile_no", "phone", "image"],
				as_dict=True,
			)
			or {}
		)
		display = (
			info.get("lead_name")
			or " ".join(x for x in [info.get("first_name"), info.get("last_name")] if x)
			or lead
		)
		c["lead_name"] = display
		c["mobile_no"] = info.get("mobile_no") or info.get("phone") or ""
		c["image"] = info.get("image") or ""
		result.append(c)

	return result

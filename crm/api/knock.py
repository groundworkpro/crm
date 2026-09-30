"""One-way, idempotent Knock text mirroring. This endpoint never sends a text.

POST crm.api.knock.sync(payload): serializes by Knock thread, matches a CRM lead,
adds SMS Communications with deterministic names, and updates lead_owner only.
Knock's global Postgres revision rejects old snapshots during overlapping deploys.
A private Info Comment anchors the association; seller/rep notes are untouched.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import re
from zoneinfo import ZoneInfo

import frappe


@dataclass(frozen=True)
class Text:
	id: str
	body: str
	speaker: str
	received: bool
	unix: int
	at: str


@dataclass(frozen=True)
class Thread:
	id: str
	revision: int
	phone: str
	owner: str
	crm_id: str
	first: str
	last: str
	address: str
	city: str
	state: str
	zip: str
	texts: tuple[Text, ...]


def stable_name(kind, thread, message=""):
	return "knock-" + kind + "-" + sha256((thread + "\0" + message).encode()).hexdigest()[:40]


def phone_digits(value):
	digits = re.sub(r"\D", "", str(value or ""))
	return digits[-10:] if len(digits) == 10 or (len(digits) == 11 and digits.startswith("1")) else ""


def parse_payload(payload):
	p = json.loads(payload) if isinstance(payload, str) else payload
	if not isinstance(p, dict):
		raise ValueError("Expected a Knock thread object")
	def string(key, default=""):
		v = p.get(key, default)
		if not isinstance(v, str):
			raise ValueError(f"{key} must be text")
		return v.strip()
	revision = p.get("revision")
	if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
		raise ValueError("revision must be a positive integer")
	id, phone, owner = string("knock_id"), string("phone"), string("owner_email")
	if not id or len(id) > 200 or not re.fullmatch(r"\+1\d{10}", phone):
		raise ValueError("Need a stable Knock id and E.164 US seller number")
	if not owner.endswith("@groundworkpro.com"):
		raise ValueError("Owner must be a Groundwork teammate")
	rows = p.get("messages", [])
	if not isinstance(rows, list) or len(rows) > 10000:
		raise ValueError("messages must be an array of at most 10000 texts")
	texts, seen = [], set()
	for m in rows:
		if not isinstance(m, dict) or any(not isinstance(m.get(k), str) for k in ("id", "body", "speaker", "at")):
			raise ValueError("A text needs id, body, speaker and at strings")
		if not m["id"] or m["id"] in seen or len(m["body"]) > 64000:
			raise ValueError("Text ids must be unique and nonempty")
		seen.add(m["id"])
		stamp, received = m.get("unix", 0), m.get("received")
		if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0 or not isinstance(received, bool):
			raise ValueError("Invalid text timestamp or direction")
		texts.append(Text(m["id"], m["body"], m["speaker"], received, stamp, m["at"]))
	return Thread(id, revision, phone, owner, string("crm_id"), string("first_name"), string("last_name"), string("address"), string("city"), string("state"), string("zip"), tuple(texts))


def message_content(t):
	# Legacy messages only have a wall-clock label. Never invent their date.
	when = "" if t.unix else f"<p><small>Original Knock time: {escape(t.at)} (date unavailable)</small></p>"
	return f"<p><strong>{escape(t.speaker)} · {'Received' if t.received else 'Sent'} text</strong></p><p>{escape(t.body).replace(chr(10), '<br>')}</p>{when}"


def _address(value):
	return re.sub(r"\s+", " ", str(value or "").split(",")[0]).strip().lower()


def _lead(thread, anchor):
	if anchor:
		name = anchor.reference_name
		if not frappe.db.exists("CRM Lead", name):
			frappe.throw("The CRM lead linked to this Knock thread was deleted; relink it before syncing.")
		return frappe.get_doc("CRM Lead", name)
	if thread.crm_id:
		doc = frappe.get_doc("CRM Lead", thread.crm_id)
		if phone_digits(doc.get("phone")) != phone_digits(thread.phone) and phone_digits(doc.get("mobile_no")) != phone_digits(thread.phone):
			frappe.throw("The selected CRM lead has a different seller number.")
		return doc
	# Frappe filters can't normalize formatted phone strings. Use bound SQL.
	digits = phone_digits(thread.phone)
	matches = frappe.db.sql("""
		select name, property_address from `tabCRM Lead`
		where regexp_replace(coalesce(phone, ''), '[^0-9]', '') in (%s, %s)
		   or regexp_replace(coalesce(mobile_no, ''), '[^0-9]', '') in (%s, %s)
		limit 50
	""", (digits, "1" + digits, digits, "1" + digits), as_dict=True)
	if thread.address:
		exact = [r for r in matches if _address(r.property_address) == _address(thread.address)]
		if len(exact) == 1:
			return frappe.get_doc("CRM Lead", exact[0].name)
	if len(matches) == 1 and (not thread.address or not matches[0].property_address):
		return frappe.get_doc("CRM Lead", matches[0].name)
	if matches:
		frappe.throw("Seller number matches ambiguous or different CRM properties. Link the correct lead explicitly; no duplicate was created.")
	if not frappe.has_permission("CRM Lead", "create"):
		frappe.throw("Not permitted to create a CRM lead", frappe.PermissionError)
	return frappe.get_doc({
		"doctype": "CRM Lead", "first_name": thread.first or thread.phone,
		"last_name": thread.last, "phone": thread.phone, "mobile_no": thread.phone,
		"property_address": thread.address, "property_city": thread.city,
		"property_state": thread.state, "property_zip": thread.zip,
		"source": "Knock", "lead_owner": thread.owner,
	}).insert()


@frappe.whitelist(methods=["POST"])
def sync(payload):
	if frappe.session.user == "Guest":
		frappe.throw("Sign in required", frappe.PermissionError)
	try:
		thread = parse_payload(payload)
	except (ValueError, TypeError, json.JSONDecodeError) as e:
		frappe.throw(str(e), frappe.ValidationError)
	if not frappe.db.get_value("User", thread.owner, "enabled"):
		frappe.throw("The assigned CRM teammate is not enabled")
	anchor_name = stable_name("thread", thread.id)
	# Commit inside the lock: releasing before Frappe's end-of-request commit
	# would let another worker create the same lead against an invisible anchor.
	with frappe.cache().lock("knock-sync:" + anchor_name, timeout=120, blocking_timeout=15):
		try:
			anchor = frappe.get_doc("Comment", anchor_name) if frappe.db.exists("Comment", anchor_name) else None
			meta = json.loads(anchor.content) if anchor else {}
			doc = _lead(thread, anchor)
			doc.check_permission("write")
			if anchor and int(meta.get("revision", 0)) >= thread.revision:
				return {"id": doc.name, "owner": doc.lead_owner, "stale": True, "added": 0}
			if doc.lead_owner != thread.owner:
				doc.lead_owner = thread.owner
				doc.save()  # never _assign, statuses, unrelated notes or property facts
			added = 0
			for t in thread.texts:
				name = stable_name("text", thread.id, t.id)
				if frappe.db.exists("Communication", name):
					existing = frappe.get_doc("Communication", name)
					if existing.reference_doctype != "CRM Lead" or existing.reference_name != doc.name:
						frappe.throw("A mirrored message belongs to another CRM lead")
					continue
				fields = {
					"doctype": "Communication", "communication_type": "Communication",
					"communication_medium": "SMS", "sent_or_received": "Received" if t.received else "Sent",
					"reference_doctype": "CRM Lead", "reference_name": doc.name,
					"subject": "Knock text · " + t.speaker, "content": message_content(t),
					"sender_full_name": t.speaker, "sender": thread.phone if t.received else thread.owner,
					"recipients": thread.owner if t.received else thread.phone,
				}
				if t.unix:
					fields["communication_date"] = datetime.fromtimestamp(t.unix, timezone.utc).astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None)
				frappe.get_doc(fields).insert(ignore_permissions=True, set_name=name)
				added += 1
			meta = json.dumps({"revision": thread.revision, "knock_id": thread.id})
			if anchor:
				anchor.content = meta
				anchor.save(ignore_permissions=True)
			else:
				frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "CRM Lead", "reference_name": doc.name, "content": meta}).insert(ignore_permissions=True, set_name=anchor_name)
			frappe.db.commit()
			return {"id": doc.name, "owner": doc.lead_owner, "stale": False, "added": added}
		except Exception:
			frappe.db.rollback()
			raise

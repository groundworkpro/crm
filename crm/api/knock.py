"""One-way, idempotent Knock text mirror. This endpoint never sends a text.

POST crm.api.knock.sync(payload). Knock (knock.groundworkpro.com) texts sellers
from its own numbers; this files every one of those texts on the CRM lead as a
**Quo Message**, the doctype the lead's Text Messages tab, the kanban contact
counts and the Today board's "last contact" already read. Knock rows are told
apart by `knock_url` (the link back to the Knock conversation) and `id`
("knock-text-…", never a Quo id).

* One row per Knock text, named deterministically, so a retry adds nothing.
* Sender: a teammate's text is `activity_source` Manual + `sent_by` them; the
  AI's is Sequence (automated, so team activity counts only human texts).
* The CRM owner follows Knock only when Knock sends one (a person has the
  thread). Nothing else on the lead changes: no status, notes or `_assign`.
* A CRM lead is created only when Knock says `create` (an explicit "Send to
  CRM"). Otherwise the thread must already be a CRM lead, or it's refused.
* Serialized per Knock thread; Knock's global revision rejects older snapshots.
  A private Info Comment anchors the thread to its lead.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

import frappe

MESSAGE = "Quo Message"


@dataclass(frozen=True)
class Text:
	id: str
	body: str
	speaker: str
	received: bool
	unix: int
	at: str
	failed: bool = False
	sender_email: str = ""
	automated: bool = False


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
	create: bool = False
	knock_url: str = ""
	line: str = ""


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

	def flag(obj, key):
		v = obj.get(key, False)
		if not isinstance(v, bool):
			raise ValueError(f"{key} must be true or false")
		return v

	revision = p.get("revision")
	if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
		raise ValueError("revision must be a positive integer")
	id, phone, owner = string("knock_id"), string("phone"), string("owner_email")
	if not id or len(id) > 200 or not re.fullmatch(r"\+1\d{10}", phone):
		raise ValueError("Need a stable Knock id and E.164 US seller number")
	if owner and not owner.endswith("@groundworkpro.com"):
		raise ValueError("Owner must be a Groundwork teammate")
	create = flag(p, "create")
	if create and not owner:
		raise ValueError("Creating a CRM lead needs an owner")
	knock_url = string("knock_url")
	if knock_url and not knock_url.startswith("https://"):
		raise ValueError("knock_url must be an https link")
	line = string("line")
	if line and not re.fullmatch(r"\+1\d{10}", line):
		raise ValueError("line must be an E.164 US number")
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
		sender = m.get("sender_email", "")
		if not isinstance(sender, str) or (sender and not sender.endswith("@groundworkpro.com")):
			raise ValueError("sender_email must be a Groundwork teammate or empty")
		texts.append(Text(m["id"], m["body"], m["speaker"], received, stamp, m["at"],
			flag(m, "failed"), sender, flag(m, "automated")))
	return Thread(id, revision, phone, owner, string("crm_id"), string("first_name"), string("last_name"),
		string("address"), string("city"), string("state"), string("zip"), tuple(texts),
		create, knock_url, line)


def message_row(thread, lead, t, has_link, tz):
	"""The Quo Message fields for one Knock text. Pure apart from the timezone."""
	incoming = t.received
	row = {
		"doctype": MESSAGE,
		"id": stable_name("text", thread.id, t.id),
		"direction": "Incoming" if incoming else "Outgoing",
		"from": thread.phone if incoming else thread.line,
		"to": thread.line if incoming else thread.phone,
		"content": t.body,
		"status": "received" if incoming else ("undelivered" if t.failed else "delivered"),
		"reference_doctype": "CRM Lead",
		"reference_docname": lead,
	}
	if not incoming:
		row["activity_source"] = "Sequence" if t.automated else "Manual"
		if t.sender_email:
			row["sent_by"] = t.sender_email
	if t.unix:
		row["message_date"] = datetime.fromtimestamp(t.unix, timezone.utc).astimezone(tz).replace(tzinfo=None)
	if has_link and thread.knock_url:
		row["knock_url"] = thread.knock_url
	return row


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
	if not thread.create:
		frappe.throw("No CRM lead for this Knock thread; only Send to CRM creates one.")
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
	if thread.owner and not frappe.db.get_value("User", thread.owner, "enabled"):
		frappe.throw("The assigned CRM teammate is not enabled")
	if not frappe.db.exists("DocType", MESSAGE):
		frappe.throw("This site has no Quo Message doctype to file texts in")
	has_link = frappe.get_meta(MESSAGE).has_field("knock_url")
	tz = ZoneInfo(frappe.utils.get_system_timezone())
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
			if thread.owner and doc.lead_owner != thread.owner:
				doc.lead_owner = thread.owner
				doc.save()  # never _assign, statuses, unrelated notes or property facts
			senders = {t.sender_email for t in thread.texts if t.sender_email}
			enabled = {u for u in senders if frappe.db.get_value("User", u, "enabled")}
			added = dated = 0
			for t in thread.texts:
				row = message_row(thread, doc.name, t, has_link, tz)
				if row.get("sent_by") not in (None, *enabled):
					row.pop("sent_by")
				if frappe.db.exists(MESSAGE, row["id"]):
					existing = frappe.get_doc(MESSAGE, row["id"])
					if existing.reference_doctype != "CRM Lead" or existing.reference_docname != doc.name:
						frappe.throw("A mirrored text belongs to another CRM lead")
					# A date recovered later fills a gap; nothing else is rewritten.
					if not existing.get("message_date") and row.get("message_date"):
						existing.db_set("message_date", row["message_date"], update_modified=False)
						dated += 1
					continue
				frappe.get_doc(row).insert(ignore_permissions=True)
				added += 1
			meta = json.dumps({"revision": thread.revision, "knock_id": thread.id, "knock_url": thread.knock_url})
			if anchor:
				anchor.content = meta
				anchor.save(ignore_permissions=True)
			else:
				frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "CRM Lead", "reference_name": doc.name, "content": meta}).insert(ignore_permissions=True, set_name=anchor_name)
			frappe.db.commit()
			return {"id": doc.name, "owner": doc.lead_owner, "stale": False, "added": added, "dated": dated}
		except Exception:
			frappe.db.rollback()
			raise

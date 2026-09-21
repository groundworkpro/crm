"""Ingest InvestorLift buyer-board rows into CRM Buyer + CRM Lead Buyer.

The buyer board lives at `investorlift.ai/deals/{property_id}` — a Next.js RSC app
with no REST API and a reCAPTCHA login, so it can't be crcommed like the admin API
(Tier 1). Instead an ops Playwright worker (`../frappe-crm-deploy`,
`scripts/investorlift_scrape.py`) loads a human-seeded session, reads the rendered
DOM per deal, and POSTs the parsed buyer rows here.

This endpoint is the CRM side: for each scraped buyer it upserts a global
**CRM Buyer** (deduped by email → phone) and a per-property **CRM Lead Buyer**
relationship (the Dispo-board row), resolving the lead from `il_property_id`. The
board's column becomes the `interest_stage`; type tags, verified, deal history,
direction, and last-active are mirrored on. Buyer↔us texts/calls are NOT ingested
here — once a buyer's phone is on a CRM Buyer, their Quo conversation already shows
in our own timeline.
"""

import json
import re
import time
import uuid

import requests

import frappe
from frappe import _
from frappe.utils import now_datetime

BUYER_DOCTYPE = "CRM Buyer"
LEAD_BUYER_DOCTYPE = "CRM Lead Buyer"

# A property is in disposition from the moment we're under contract on it — that
# is when it needs a buyer board, whether or not it was ever posted to
# InvestorLift. Confirmed with Lance (2026-07-27): "Contract Sent" isn't ours
# yet and "Won" is closed. Single source of truth: crm.api.buyer_import imports
# this for its property picker (importing the other way round would cycle).
DISPO_LEAD_STATUSES = (
	"Photos & Lockbox In Progress",
	"Submit to Dispo",
	"Dispo Accepted",
	"Buyer Assigned",
)

# board column label -> CRM Lead Buyer.interest_stage option
COLUMN_TO_STAGE = {
	"new leads": "New",
	"new lead": "New",
	"attempted to contact": "Attempted to Contact",
	"not interested": "Not Interested",
	"interested": "Interested",
	"offer made": "Offer Made",
}

# interest_stage -> the column id InvestorLift's own board uses. This is the
# write-back direction, and it is NOT symmetric with COLUMN_TO_STAGE:
#
#   IL's transition matrix makes `new` unreachable from every other column and
#   `offer_made` terminal. So a CRM move back to "New" can never be pushed --
#   it is deliberately absent here rather than mapped to something lossy, and
#   such rows are reported as unpushable instead of being silently mangled.
STAGE_TO_COLUMN = {
	"Attempted to Contact": "attempted_to_contact",
	"Not Interested": "not_interested",
	"Interested": "interested",
	"Offer Made": "offer_made",
}
UNPUSHABLE_STAGES = ("New",)

# How many times we'll try to make InvestorLift accept a stage before giving up
# and reporting it. IL's move handler no-ops silently on a transition it doesn't
# like, so "tried and nothing happened" is indistinguishable from "tried and it
# worked" at push time -- only a later scrape settles it. Without a cap, a
# transition IL refuses is re-attempted every cycle forever.
MAX_STAGE_PUSH_ATTEMPTS = 5

MANAGER_ROLES = ("System Manager", "Sales Manager")
MANUAL_SYNC_TTL = 30 * 60
MANUAL_SYNC_LEASE = 5 * 60


def _guard():
	if not any(r in MANAGER_ROLES for r in frappe.get_roles()):
		frappe.throw(_("Not permitted to ingest InvestorLift buyers."), frappe.PermissionError)


def _last10(number):
	return "".join(ch for ch in (number or "") if ch.isdigit())[-10:]


def _find_buyer(email, phone, name=None):
	"""Dedupe key: email first (stable per buyer), then last-10 phone digits.
	Last resort: exact name match against a buyer that has NO email and NO phone —
	those rows only come from the address-request webhook when the contact lookup
	failed (e.g. Marcel Cohen), so an enrichment pass should merge into them
	rather than create a duplicate."""
	if email:
		buyer = frappe.db.get_value(BUYER_DOCTYPE, {"email": email}, "name")
		if buyer:
			return buyer
	if phone:
		last10 = _last10(phone)
		if last10:
			# no SQL LIKE on a computed suffix — scan the small buyer set by digits
			for b in frappe.get_all(BUYER_DOCTYPE, filters={"phone": ("is", "set")}, fields=["name", "phone"]):
				if _last10(b.phone) == last10:
					return b.name
	if name:
		want = name.strip().lower()
		if want:
			for b in frappe.get_all(
				BUYER_DOCTYPE,
				filters={"email": ("is", "not set"), "phone": ("is", "not set")},
				fields=["name", "buyer_name"],
			):
				if (b.buyer_name or "").strip().lower() == want:
					return b.name
	return None


def _upsert_buyer(row):
	"""Create/update a CRM Buyer from a scraped row; return its name."""
	email = (row.get("email") or "").strip().lower() or None
	phone = (row.get("phone") or "").strip() or None
	full_name = (row.get("name") or "").strip() or (email or phone or "Unknown")
	tags = row.get("tags") or []
	buyer_type = ", ".join(t for t in tags if t) if tags else None

	fields = {
		"buyer_name": full_name,
		"first_name": row.get("first_name"),
		"last_name": row.get("last_name"),
		"phone": phone,
		"email": email,
		"verified": 1 if row.get("verified") else 0,
		"buyer_type": buyer_type,
		"deal_history": row.get("deal_history"),
		"last_active": row.get("last_active_at"),  # optional ISO; may be None
	}
	if row.get("il_buyer_id"):
		fields["il_buyer_id"] = str(row["il_buyer_id"])

	name = _find_buyer(email, phone, full_name)
	if name:
		# only overwrite with non-empty values (never blank out on a sparse scrape)
		update = {k: v for k, v in fields.items() if v not in (None, "")}
		# The team's habit is to append a marker to the name of someone who asked to
		# be removed ("Danny Stoica (REMOVE)"). IL rewrites buyer_name from its own
		# scrape every cycle, silently and without bumping `modified`, so that marker
		# used to survive about five minutes. do_not_contact is the real record now,
		# but for exactly those buyers we also stop overwriting the name, so what the
		# team wrote on the card stays written (gw296).
		if frappe.get_meta(BUYER_DOCTYPE).has_field("do_not_contact") and \
				frappe.db.get_value(BUYER_DOCTYPE, name, "do_not_contact"):
			update.pop("buyer_name", None)
			update.pop("first_name", None)
			update.pop("last_name", None)
		if update:
			# db.set_value fires no doc events — push identity changes to Quo,
			# but only when a value ACTUALLY changed (the scraper re-upserts
			# every buyer each run; blind enqueues flooded the short queue)
			identity = {"buyer_name", "first_name", "last_name", "phone", "email"} & set(update)
			changed = False
			if identity:
				current = frappe.db.get_value(BUYER_DOCTYPE, name, list(identity), as_dict=True) or {}
				changed = any((current.get(k) or "") != update[k] for k in identity)
			frappe.db.set_value(BUYER_DOCTYPE, name, update, update_modified=False)
			if changed:
				from crm.api.quo_contacts import enqueue_push

				enqueue_push(name)
		return name

	doc = frappe.get_doc({"doctype": BUYER_DOCTYPE, **{k: v for k, v in fields.items() if v is not None}})
	doc.insert(ignore_permissions=True)
	return doc.name


def _is_human_removal(current):
	"""Did a person deliberately park this card in Not Interested?

	`not_interested_by` is stamped only by crm.api.buyers.move_buyer_stage, i.e.
	only by a human acting on the Dispo board. An IL-origin move into that column
	leaves it empty, so this cannot mistake InvestorLift's own bookkeeping for a
	decision one of ours made.
	"""
	return bool(current.get("not_interested_by")) and \
		current.get("interest_stage") == "Not Interested"


def _upsert_relationship(lead, buyer, row, stage_is_authoritative=True):
	"""Create/update the per-property CRM Lead Buyer row.

	`interest_stage` has two writers: this sync and a human on the Dispo board
	(drag / "Move to" -> crm.api.buyers.move_buyer_stage). It used to be
	overwritten from IL every cycle, so a CRM-side move survived ~10 minutes.

	The rule now: **a human's move wins until InvestorLift's own column moves.**
	`il_stage` snapshots the column IL last reported, so we can tell a genuine
	IL move from IL merely repeating itself:

	  incoming == il_stage  -> IL hasn't moved the card; leave interest_stage
	                           alone (it may hold a human's decision).
	  incoming != il_stage  -> the buyer actually moved on IL's board; that's
	                           new information, so it wins and re-syncs the row.

	This deliberately isn't a permanent human lock: that would let the CRM go
	stale forever against real buyer activity. Only `interest_stage` is
	protected — direction/message_count/last_active are pure IL telemetry that
	nobody edits by hand, so they still overwrite freely.

	TWO EXCEPTIONS, both learned the hard way in gw296 (the Danny Stoica incident):

	`stage_is_authoritative=False` — the caller invented the column instead of
	reading it off the board. `_handle_address_request` and `pull_new_inquiries`
	both hardcode "NEW LEADS", which is a fine default for a card that does not
	exist yet and a lie about one that does: feeding it to the comparison above
	makes it look like IL moved the card to New, which re-snapshots `il_stage`
	and hands the next real scrape a false "IL moved it" signal.

	A human's explicit Not Interested is **never** undone by an IL-origin move.
	That column is where the team records "this person asked us to stop", and
	InvestorLift moving a card back is not new information about our own
	decision. `il_stage` still records what IL says, so the push queue keeps
	trying to make IL agree — we just don't let IL win in the meantime. (The
	durable opt-out lives on CRM Buyer.do_not_contact; this only stops the board
	silently disagreeing with the person who worked it.)
	"""
	stage = COLUMN_TO_STAGE.get((row.get("column") or "").strip().lower())
	direction = (row.get("direction") or "").strip().capitalize()
	if direction not in ("Inbound", "Outbound"):
		direction = None

	# Guard the field lookup: this doctype is provisioned per-site by the ops repo
	# (frappe-crm-deploy scripts/setup_investorlift.py), so a site that hasn't run
	# scripts/setup_il_stage_field.py yet must still ingest -- it just falls back
	# to the old overwrite-always behaviour until the field exists.
	meta = frappe.get_meta(LEAD_BUYER_DOCTYPE)
	track_il_stage = meta.has_field("il_stage")
	track_il_lead = meta.has_field("il_lead_id")

	lookup = ["name", "interest_stage"]
	if track_il_stage:
		lookup.append("il_stage")
	if meta.has_field("not_interested_by"):
		lookup.append("not_interested_by")
	current = frappe.db.get_value(
		LEAD_BUYER_DOCTYPE, {"lead": lead, "buyer": buyer}, lookup, as_dict=True
	)

	vals = {
		"interest_stage": stage,
		"direction": direction,
		"message_count": row.get("note_count") or 0,
		"last_active": row.get("last_active_at"),
	}
	# InvestorLift's per-(property,buyer) id, scraped off the card's React fiber.
	# This is the handle write-back needs; without it a row can never be pushed.
	if track_il_lead and row.get("il_lead_id"):
		vals["il_lead_id"] = str(row["il_lead_id"])
	vals = {k: v for k, v in vals.items() if v not in (None, "")}

	if current:
		if not stage_is_authoritative:
			# a synthesised column says nothing about where the card sits now
			vals.pop("interest_stage", None)
		elif stage and track_il_stage:
			if current.get("il_stage") == stage:
				vals.pop("interest_stage", None)  # IL unchanged -> don't touch the CRM's value
			else:
				vals["il_stage"] = stage  # IL moved -> take it, and re-snapshot
				if meta.has_field("il_push_attempts"):
					# the board actually changed, so whatever we were retrying is moot
					vals["il_push_attempts"] = 0
				if _is_human_removal(current):
					vals.pop("interest_stage", None)
		if vals:
			frappe.db.set_value(LEAD_BUYER_DOCTYPE, current["name"], vals, update_modified=False)
		return current["name"], False

	if stage and track_il_stage:
		vals["il_stage"] = stage
	doc = frappe.get_doc({"doctype": LEAD_BUYER_DOCTYPE, "lead": lead, "buyer": buyer, **vals})
	doc.insert(ignore_permissions=True)
	return doc.name, True


@frappe.whitelist()
def get_pending_stage_pushes(il_property_id):
	"""Rows on this property whose CRM stage a human changed and IL doesn't know.

	`interest_stage != il_stage` means exactly one thing after ingest has run: a
	human moved the card in the CRM and InvestorLift's board still shows the old
	column. (When IL is the one that moved, ingest sets both together, so they
	match and nothing is pending.)

	Returns pushable rows plus, separately, the ones that can never be pushed --
	IL makes `new` unreachable, so a CRM move back to New has nowhere to go and is
	surfaced rather than silently dropped.
	"""
	_guard()
	il_property_id = str(il_property_id).strip()
	lead = frappe.db.get_value("CRM Lead", {"il_property_id": il_property_id}, "name")
	if not lead:
		return {"ok": False, "error": f"no CRM Lead linked to il_property_id {il_property_id}"}

	meta = frappe.get_meta(LEAD_BUYER_DOCTYPE)
	if not (meta.has_field("il_stage") and meta.has_field("il_lead_id")):
		return {"ok": True, "lead": lead, "pushes": [], "unpushable": [],
		        "note": "il_stage/il_lead_id not provisioned on this site"}

	fields = ["name", "buyer", "interest_stage", "il_stage", "il_lead_id"]
	track_attempts = meta.has_field("il_push_attempts")
	if track_attempts:
		fields.append("il_push_attempts")
	rows = frappe.get_all(
		LEAD_BUYER_DOCTYPE,
		filters={"lead": lead},
		fields=fields,
		limit_page_length=0,
	)

	pushes, unpushable = [], []
	for r in rows:
		if not r.interest_stage or r.interest_stage == r.il_stage:
			continue  # nothing pending
		if track_attempts and int(r.get("il_push_attempts") or 0) >= MAX_STAGE_PUSH_ATTEMPTS:
			# InvestorLift has ignored this move MAX times. Keep the CRM's value --
			# it's a human's decision -- but stop pretending the push will work.
			unpushable.append({"relationship": r.name, "stage": r.interest_stage,
			                   "reason": f"InvestorLift did not accept this move after "
			                             f"{MAX_STAGE_PUSH_ATTEMPTS} attempts; move it on IL by hand"})
			continue
		if not r.il_lead_id:
			unpushable.append({"relationship": r.name, "stage": r.interest_stage,
			                   "reason": "no il_lead_id captured for this card yet"})
			continue
		if r.interest_stage in UNPUSHABLE_STAGES:
			unpushable.append({"relationship": r.name, "stage": r.interest_stage,
			                   "reason": "InvestorLift has no transition back to New"})
			continue
		target = STAGE_TO_COLUMN.get(r.interest_stage)
		if not target:
			unpushable.append({"relationship": r.name, "stage": r.interest_stage,
			                   "reason": "no InvestorLift column for this stage"})
			continue
		pushes.append({"relationship": r.name, "lead_id": str(r.il_lead_id),
		               "target": target, "stage": r.interest_stage})

	return {"ok": True, "lead": lead, "pushes": pushes, "unpushable": unpushable}


@frappe.whitelist()
def record_stage_push_attempt(relationship, pushed=0):
	"""Note that the daemon tried to push this row's stage onto InvestorLift.

	This REPLACES the old `confirm_stage_push`, which let the daemon declare that
	IL agreed and wrote `il_stage` on the strength of it. That is what broke the
	Danny Stoica card (gw296): the daemon "verified" a push by re-reading the
	board's React store 3.5s after clicking, which is the client's own optimistic
	state, not InvestorLift's. IL never persisted the move, `il_stage` was set to
	Not Interested anyway, and the next scrape read the real column, concluded IL
	had moved the card, and overwrote the human's decision. Three buyers marked
	Not Interested were silently returned to a textable column that way; one of
	them then received a bulk text after asking to be removed.

	So the invariant is now absolute and worth stating plainly:

	    `il_stage` is ONLY ever written from an observed scrape of the board.

	Nothing else may claim to know what InvestorLift thinks. A push that really
	landed needs no confirmation -- the next ingest sees the new column and
	reconciles on its own, which is the same self-healing path the daemon already
	relied on whenever a confirm call failed.

	All this records is how many times we have tried, so a transition IL keeps
	refusing stops being retried forever and gets surfaced instead.
	"""
	_guard()
	if not frappe.db.exists(LEAD_BUYER_DOCTYPE, relationship):
		return {"ok": False, "error": "relationship not found"}
	meta = frappe.get_meta(LEAD_BUYER_DOCTYPE)
	if not meta.has_field("il_push_attempts"):
		return {"ok": True, "note": "il_push_attempts not provisioned; nothing recorded"}

	attempts = int(frappe.db.get_value(LEAD_BUYER_DOCTYPE, relationship, "il_push_attempts") or 0) + 1
	vals = {"il_push_attempts": attempts}
	if meta.has_field("il_push_last_at"):
		vals["il_push_last_at"] = now_datetime()
	frappe.db.set_value(LEAD_BUYER_DOCTYPE, relationship, vals, update_modified=False)
	frappe.db.commit()
	return {
		"ok": True, "relationship": relationship, "attempts": attempts,
		"exhausted": attempts >= MAX_STAGE_PUSH_ATTEMPTS,
	}


@frappe.whitelist()
def ingest_deal_buyers(il_property_id, buyers):
	"""Upsert a deal's scraped buyer rows. `buyers` = JSON list of row dicts:
	{name, first_name?, last_name?, phone, email, verified, tags[], deal_history,
	 direction, column, note_count?, last_active_at?, il_buyer_id?}."""
	_guard()
	if isinstance(buyers, str):
		buyers = json.loads(buyers)
	il_property_id = str(il_property_id).strip()

	# resolve the lead this property maps to (Tier-1 link)
	lead = frappe.db.get_value("CRM Lead", {"il_property_id": il_property_id}, "name")
	if not lead:
		return {"ok": False, "error": f"no CRM Lead linked to il_property_id {il_property_id}"}

	created_buyers = updated_buyers = created_rels = 0
	for row in buyers or []:
		try:
			existed = _find_buyer(
				(row.get("email") or "").strip().lower() or None,
				(row.get("phone") or "").strip() or None,
				(row.get("name") or "").strip() or None,
			)
			buyer = _upsert_buyer(row)
			if existed:
				updated_buyers += 1
			else:
				created_buyers += 1
			_, is_new = _upsert_relationship(lead, buyer, row)
			if is_new:
				created_rels += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"IL buyer ingest failed for {row.get('email') or row.get('name')}")

	frappe.db.commit()
	# live-refresh the lead's Dispo board
	frappe.publish_realtime(
		"crm_il_buyers",
		{"reference_doctype": "CRM Lead", "reference_docname": lead},
		after_commit=True,
	)
	return {
		"ok": True,
		"lead": lead,
		"buyers_created": created_buyers,
		"buyers_updated": updated_buyers,
		"relationships_created": created_rels,
		"total": len(buyers or []),
		"synced_at": str(now_datetime()),
	}


@frappe.whitelist()
def get_linked_properties():
	"""[{lead, il_property_id}] for every lead linked to an IL property — the
	scraper's work-list (which deal boards to scrape)."""
	_guard()
	if not frappe.get_meta("CRM Lead").has_field("il_property_id"):
		return []
	return frappe.get_all(
		"CRM Lead",
		filters={"il_property_id": ("is", "set")},
		fields=["name as lead", "il_property_id"],
	)


def _manual_sync_key(lead):
	return f"investorlift:manual-sync:{lead}"


def _store_manual_sync(lead, payload):
	frappe.cache().set_value(
		_manual_sync_key(lead), payload, expires_in_sec=MANUAL_SYNC_TTL
	)


def _queue_manual_sync(lead, il_property_id):
	"""Create or reuse the active cache request for one linked property."""
	now = time.time()
	current = frappe.cache().get_value(_manual_sync_key(lead)) or {}
	active = current.get("status") == "queued" or (
		current.get("status") == "running"
		and now - float(current.get("claimed_at") or 0) < MANUAL_SYNC_LEASE
	)
	if active:
		return current

	request = {
		"request_id": uuid.uuid4().hex,
		"lead": lead,
		"il_property_id": str(il_property_id).strip(),
		"status": "queued",
		"requested_at": now,
		"requested_by": frappe.session.user,
	}
	_store_manual_sync(lead, request)
	return request


@frappe.whitelist()
def request_deal_sync(lead):
	"""Queue a one-property board refresh for the Mac mini sync daemon.

	The Frappe server cannot scrape investorlift.ai itself: the authenticated,
	persistent browser lives on the mini. This cache record is the small handoff
	between the CRM button and that daemon. Repeated clicks reuse an active request
	instead of scheduling duplicate browser work.
	"""
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.get_meta("CRM Lead").has_field("il_property_id"):
		frappe.throw(_("InvestorLift is not configured."))

	il_property_id = frappe.db.get_value("CRM Lead", lead, "il_property_id")
	if not il_property_id:
		frappe.throw(_("This property is not linked to InvestorLift."))
	return _queue_manual_sync(lead, il_property_id)


@frappe.whitelist()
def request_all_deals_sync():
	"""Queue every InvestorLift-linked property as one daemon batch."""
	_guard()
	targets = get_linked_properties()
	if not targets:
		frappe.throw(_("No properties are linked to InvestorLift."))
	requests = [
		_queue_manual_sync(target.get("lead"), target.get("il_property_id"))
		for target in targets
	]
	return {"requests": requests, "total": len(requests)}


@frappe.whitelist()
def claim_manual_sync_requests():
	"""Claim queued manual refreshes for the authenticated sync daemon.

	A running request becomes claimable again after five minutes so a browser or
	daemon crash cannot lose the user's click forever.
	"""
	_guard()
	now = time.time()
	claimed = []
	for target in get_linked_properties():
		lead = target.get("lead")
		request = frappe.cache().get_value(_manual_sync_key(lead)) or {}
		stale = request.get("status") == "running" and (
			now - float(request.get("claimed_at") or 0) >= MANUAL_SYNC_LEASE
		)
		if request.get("status") != "queued" and not stale:
			continue
		request.update({
			"lead": lead,
			"il_property_id": str(target.get("il_property_id") or "").strip(),
			"status": "running",
			"claimed_at": now,
		})
		_store_manual_sync(lead, request)
		claimed.append(request)
	return claimed


@frappe.whitelist()
def complete_manual_sync(lead, request_id, summary=None):
	"""Finish a claimed request and notify the CRM page that queued it."""
	_guard()
	current = frappe.cache().get_value(_manual_sync_key(lead)) or {}
	if current.get("request_id") != request_id:
		return {"ok": False, "reason": "request was replaced"}
	if isinstance(summary, str):
		summary = json.loads(summary)
	summary = summary or {}
	errors = summary.get("errors") or []
	status = "failed" if errors or summary.get("session") == "dead" else "done"
	current.update({
		"status": status,
		"completed_at": time.time(),
		"summary": summary,
	})
	_store_manual_sync(lead, current)
	frappe.publish_realtime(
		"crm_il_sync_complete",
		{
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
			"request_id": request_id,
			"status": status,
			"summary": summary,
		},
	)
	return {"ok": True, "status": status}


# --------------------------------------------------------------------------- #
# real-time "new buyer requested an address" — webhook-driven (NOT polling)
# --------------------------------------------------------------------------- #
# InvestorLift texts us a notification the moment a buyer requests an address; that
# text is delivered to our Quo line → the OpenPhone `message.received` webhook →
# Sequence Events Log. An after_insert hook on that log (crm/hooks.py) fires
# `on_sequence_event`, which parses the (self-contained) notification and pulls the
# buyer onto the right property's board in real time. Two notification shapes:
#   "New buyer signed up: <name>, <email>, <phone>"
#   "Hi <rep>. <name> sent an address request for <full property address>"
_NEW_BUYER_RE = re.compile(
	r"New buyer signed up:\s*(?P<name>.+?),\s*(?P<email>[^,\s]+@[^,\s]+)\s*,\s*(?P<phone>[+\d().\-\s]+?)\s*$",
	re.I,
)
_ADDR_REQ_RE = re.compile(
	r"(?:hi\s+[^.]+\.\s*)?(?P<name>.+?)\s+sent an address request for\s+(?P<addr>.+?)\.?\s*$",
	re.I,
)


def on_sequence_event(doc, method=None):
	"""after_insert on Sequence Events Log — pull a buyer when an InvestorLift
	address-request notification lands (real-time, webhook-driven)."""
	if (doc.get("event_type") or "") != "message.received":
		return
	try:
		obj = (json.loads(doc.get("payload") or "{}").get("data") or {}).get("object") or {}
	except (ValueError, TypeError):
		return
	m = _ADDR_REQ_RE.search(obj.get("text") or "")
	if not m:
		return
	try:
		_handle_address_request(m.group("name").strip(), m.group("addr").strip())
	except Exception:
		frappe.log_error(frappe.get_traceback(), "IL address-request webhook failed")


def _lookup_signup(buyer_name):
	"""Grab email/phone from the paired 'New buyer signed up' notification (arrives
	seconds before the address request), so we create the buyer with full contact."""
	want = (buyer_name or "").strip().lower()
	for r in frappe.get_all(
		"Sequence Events Log", filters={"event_type": "message.received"},
		fields=["payload"], order_by="creation desc", limit_page_length=40,
	):
		try:
			o = (json.loads(r.payload or "{}").get("data") or {}).get("object") or {}
		except (ValueError, TypeError):
			continue
		mm = _NEW_BUYER_RE.search(o.get("text") or "")
		if mm and mm.group("name").strip().lower() == want:
			return mm.group("email").strip(), mm.group("phone").strip()
	return None, None


def _inquiry_contact(il_property_id, buyer_name):
	"""Look up an address-requester's contact info from the IL admin API: the
	property's inquiries carry a customer_id; the customer record has the email +
	SMS phone (+ verified). Returns {} on any failure — callers degrade to the
	name-only row the webhook used to create."""
	from crm.api import investorlift as il

	want = (buyer_name or "").strip().lower()
	if not (want and il_property_id):
		return {}
	try:
		token = il.get_token()
		h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
		inquiries = requests.get(
			f"{il.API_BASE}/properties/{il_property_id}/inquiries",
			params={"per_page": 100}, headers=h, timeout=25,
		).json().get("data", [])
		for inq in inquiries:
			cid = inq.get("customer_id")
			if not cid:
				continue
			c = requests.get(f"{il.API_BASE}/customers/{cid}", headers=h, timeout=20).json()
			cust = c.get("data") if isinstance(c, dict) and "data" in c else c
			if not isinstance(cust, dict):
				continue
			if (cust.get("full_name") or "").strip().lower() != want:
				continue
			return {
				"email": cust.get("email") or inq.get("from_email"),
				"phone": (cust.get("unsubscribe_sms_data") or {}).get("phone") or inq.get("from_phone"),
				"verified": bool(cust.get("is_id_verified")),
				"il_buyer_id": cid,
			}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "IL inquiry-contact lookup failed")
	return {}


def _handle_address_request(buyer_name, address):
	from crm.api import investorlift as il

	# match the notified address to a linked lead (normalized street|zip key)
	key = il._addr_key(*il._split_address(address))
	if not key:
		return
	lead = None
	for l in frappe.get_all(
		"CRM Lead",
		filters={"il_property_id": ("is", "set"), "property_address": ("is", "set")},
		fields=["name", "property_address"],
	):
		if il._addr_key(*il._split_address(l.property_address)) == key:
			lead = l.name
			break
	if not lead:
		frappe.log_error(f"no linked lead for address '{address}'", "IL address-request")
		return

	email, phone = _lookup_signup(buyer_name)
	row = {"name": buyer_name, "email": email, "phone": phone, "direction": "Inbound", "column": "NEW LEADS"}
	if not (email and phone):
		# no paired signup text (buyer signed up long ago / >40 events back) —
		# pull contact info from the IL API instead (the Marcel Cohen case)
		extra = _inquiry_contact(frappe.db.get_value("CRM Lead", lead, "il_property_id"), buyer_name)
		if extra:
			row["email"] = row["email"] or extra.get("email")
			row["phone"] = row["phone"] or extra.get("phone")
			row["verified"] = extra.get("verified")
			row["il_buyer_id"] = extra.get("il_buyer_id")
	# OpenPhone delivers the same IL notification once per Quo line it reaches,
	# so two copies of this handler can run ~0.2s apart in parallel workers. Both
	# used to pass _find_buyer before either insert committed — that is exactly
	# how Abdul Rehman (BUY-00425/BUY-00426) and Shelton Jackson
	# (BUY-00409/BUY-00410) each got duplicated. Serialize the find+insert on a
	# DB named lock keyed by the buyer, and start a fresh read snapshot once
	# inside — REPEATABLE READ would otherwise keep _find_buyer blind to a row
	# the parallel handler committed while we waited on the lock.
	lock = f"il-buyer:{(buyer_name or '').strip().lower()}"[:64]
	frappe.db.sql("SELECT GET_LOCK(%s, 15)", lock)
	try:
		frappe.db.commit()  # new snapshot: see anything a parallel handler just wrote
		buyer = _upsert_buyer(row)
		# "NEW LEADS" above is a default for a card we may be creating, NOT a reading
		# of IL's board -- see _upsert_relationship's docstring (gw296).
		_upsert_relationship(lead, buyer, row, stage_is_authoritative=False)
		frappe.db.commit()
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock)
	frappe.publish_realtime(
		"crm_il_buyers", {"reference_doctype": "CRM Lead", "reference_docname": lead}, after_commit=True
	)


@frappe.whitelist()
def pull_new_inquiries():
	"""Manual backfill/reconciliation (the webhook is the live path): for each linked
	property, poll `/api/properties/{id}/inquiries`, resolve each requester's customer,
	and upsert a CRM Buyer (il_buyer_id=customer id) + a CRM Lead Buyer (New/Inbound).
	Idempotent — reconciles with scraper/webhook buyers by email; realtime-refreshes."""
	if frappe.session.user != "Administrator" and not any(
		r in MANAGER_ROLES for r in frappe.get_roles()
	):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	if not frappe.get_meta("CRM Lead").has_field("il_property_id"):
		return {"ok": False, "reason": "not provisioned"}

	from crm.api import investorlift as il

	try:
		token = il.get_token()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "IL inquiries: token failed")
		return {"ok": False, "reason": "auth"}
	h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

	leads = frappe.get_all(
		"CRM Lead", filters={"il_property_id": ("is", "set")}, fields=["name", "il_property_id"]
	)
	created, touched = 0, set()
	for lead in leads:
		pid = str(lead.il_property_id).strip()
		try:
			inquiries = requests.get(
				f"{il.API_BASE}/properties/{pid}/inquiries", params={"per_page": 100}, headers=h, timeout=25
			).json().get("data", [])
		except Exception:
			continue
		for inq in inquiries:
			cid = inq.get("customer_id")
			if not cid:
				continue
			# already pulled onto this board? (fast path once il_buyer_id is stamped)
			existing = frappe.db.get_value(BUYER_DOCTYPE, {"il_buyer_id": str(cid)}, "name")
			if existing and frappe.db.exists(LEAD_BUYER_DOCTYPE, {"lead": lead.name, "buyer": existing}):
				continue
			try:
				c = requests.get(f"{il.API_BASE}/customers/{cid}", headers=h, timeout=20).json()
			except Exception:
				continue
			cust = c.get("data") if isinstance(c, dict) and "data" in c else c
			if not isinstance(cust, dict):
				continue
			row = {
				"name": cust.get("full_name"),
				"email": cust.get("email") or inq.get("from_email"),
				"phone": (cust.get("unsubscribe_sms_data") or {}).get("phone") or inq.get("from_phone"),
				"verified": bool(cust.get("is_id_verified")),
				"il_buyer_id": cid,
				"direction": "Inbound",
				"column": "NEW LEADS",
			}
			try:
				buyer = _upsert_buyer(row)
				# same synthesised "NEW LEADS" caveat as the address-request webhook
				_, is_new = _upsert_relationship(
					lead.name, buyer, row, stage_is_authoritative=False
				)
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"IL inquiry pull failed for customer {cid}")
				continue
			if is_new:
				created += 1
				touched.add(lead.name)
		frappe.db.commit()

	for ln in touched:
		frappe.publish_realtime(
			"crm_il_buyers", {"reference_doctype": "CRM Lead", "reference_docname": ln}, after_commit=True
		)
	return {"ok": True, "new_buyers": created}


@frappe.whitelist()
def get_dispo_properties():
	"""Active-dispo properties for the Dispo page property switcher:
	[{lead, label, il_property_id, il_status, buyer_count}].

	A property qualifies three ways, in order of how it actually happens:

	  1. it's under contract / in dispo (`DISPO_LEAD_STATUSES`) — the real rule.
	     A deal needs a buyer board the moment it's ours, and most of ours are
	     never posted to InvestorLift, so IL linkage was never the right gate.
	  2. it has buyers on its board already (covers a lead whose status has since
	     moved on — e.g. Won — without vanishing the board and its history).
	  3. it's linked to an IL property (the original rule, kept for anything IL
	     is tracking that isn't in one of the statuses above)."""
	if not any(r in ("System Manager", "Sales Manager", "Sales User") for r in frappe.get_roles()):
		frappe.throw(_("Only sales users can view dispo."), frappe.PermissionError)
	has_il = frappe.get_meta("CRM Lead").has_field("il_property_id")
	has_buyers = frappe.db.exists("DocType", LEAD_BUYER_DOCTYPE)

	names = set(
		frappe.get_all(
			"CRM Lead", filters={"status": ("in", DISPO_LEAD_STATUSES)},
			pluck="name", limit_page_length=0,
		)
	)
	if has_il:
		names.update(
			r.name
			for r in frappe.get_all(
				"CRM Lead", filters={"il_property_id": ("is", "set")},
				fields=["name"], limit_page_length=0,
			)
		)
	if has_buyers:
		names.update(
			r.lead
			for r in frappe.get_all(
				LEAD_BUYER_DOCTYPE, filters={"lead": ("is", "set")},
				fields=["lead"], group_by="lead", limit_page_length=0,
			)
			if r.lead
		)
	if not names:
		return []

	fields = ["name", "lead_name", "property_address", "modified"]
	if has_il:
		fields += ["il_property_id", "il_status"]
		# so the Dispo board header can link straight to the buyer-facing listing
		if frappe.get_meta("CRM Lead").has_field("il_marketplace_url"):
			fields.append("il_marketplace_url")
	leads = frappe.get_all(
		"CRM Lead",
		filters={"name": ("in", list(names))},
		fields=fields,
		order_by="modified desc",
		limit_page_length=0,
	)
	out = []
	for l in leads:
		out.append({
			"lead": l.name,
			"label": l.property_address or l.lead_name or l.name,
			"il_property_id": l.get("il_property_id"),
			"il_status": l.get("il_status"),
			"il_marketplace_url": l.get("il_marketplace_url"),
			"buyer_count": frappe.db.count(LEAD_BUYER_DOCTYPE, {"lead": l.name}) if has_buyers else 0,
		})
	return out


@frappe.whitelist()
def get_buyer(buyer):
	"""A buyer's profile + every deal (property) they've engaged with — the buyer page."""
	if not any(r in ("System Manager", "Sales Manager", "Sales User") for r in frappe.get_roles()):
		frappe.throw(_("Only sales users can view buyers."), frappe.PermissionError)
	if not frappe.db.exists(BUYER_DOCTYPE, buyer):
		frappe.throw(_("Buyer not found"), frappe.DoesNotExistError)

	fields = ["name", "buyer_name", "first_name", "last_name", "phone", "email",
	          "verified", "buyer_type", "deal_history", "last_active", "il_buyer_id", "modified"]
	meta = frappe.get_meta(BUYER_DOCTYPE)
	if meta.has_field("metro_areas"):
		fields += ["metro_areas", "buybox"]
	for fieldname in ("buybox_cities", "buybox_property_types"):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	if frappe.get_meta(BUYER_DOCTYPE).has_field("quo_tags"):
		fields += ["quo_tags"]
	for fieldname in ("do_not_contact", "do_not_contact_reason",
	                  "do_not_contact_by", "do_not_contact_at"):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	doc = frappe.db.get_value(BUYER_DOCTYPE, buyer, fields, as_dict=True)
	from crm.api.buyers import _json_list, _parse_metros

	doc["metros"] = _parse_metros(doc.get("metro_areas"))
	doc["buybox_cities"] = _json_list(doc.get("buybox_cities"))
	doc["buybox_property_types"] = _json_list(doc.get("buybox_property_types"))

	deals = []
	rels = frappe.get_all(
		LEAD_BUYER_DOCTYPE,
		filters={"buyer": buyer},
		fields=["name", "lead", "interest_stage", "direction", "last_active", "message_count"],
		order_by="modified desc",
	)
	for r in rels:
		info = frappe.db.get_value(
			"CRM Lead", r.lead, ["lead_name", "property_address", "il_property_id"], as_dict=True
		) or {}
		deals.append({
			"lead": r.lead,
			"label": info.get("property_address") or info.get("lead_name") or r.lead,
			"il_property_id": info.get("il_property_id"),
			"interest_stage": r.interest_stage,
			"direction": r.direction,
			"last_active": r.last_active,
			"message_count": r.message_count,
		})

	doc["deals"] = deals
	return doc


def _e164(number):
	"""Best-effort E164 for a US buyer phone ('(313) 502-6343' → '+13135026343')."""
	d = "".join(c for c in (number or "") if c.isdigit())
	if len(d) == 10:
		return "+1" + d
	if len(d) == 11 and d.startswith("1"):
		return "+" + d
	return ("+" + d) if d else ""


@frappe.whitelist()
def get_buyer_conversation(buyer):
	"""Texts with a buyer, time-sorted — read from the stored **Quo Message**
	rows referenced to the buyer (the sequence-events webhook mirrors buyer
	texts now, and ops `backfill_buyer_texts.py` covered history), so the
	thread is fast, complete, carries MMS media, and refreshes live via the
	`quo_message` realtime event. Calls are NOT fetched here — they come from
	CRM Call Log via crm.api.buyers.get_buyer_calls, which carries recordings
	+ transcripts and renders with the lead timeline's CallArea card."""
	if not any(r in ("System Manager", "Sales Manager", "Sales User") for r in frappe.get_roles()):
		frappe.throw(_("Only sales users can view buyer activity."), frappe.PermissionError)
	phone = frappe.db.get_value(BUYER_DOCTYPE, buyer, "phone")
	e164 = _e164(phone)
	if not frappe.db.exists("DocType", "Quo Message"):
		return {"phone": e164 or None, "items": []}

	from zoneinfo import ZoneInfo

	from frappe.utils import get_datetime, get_system_timezone

	from crm.api.sms import _media, _sender_map

	rows = frappe.get_all(
		"Quo Message",
		filters={"reference_doctype": BUYER_DOCTYPE, "reference_docname": buyer},
		fields=["name", "direction", "from", "to", "content", "media", "status",
		        "message_date", "creation"],
		order_by="message_date asc, creation asc",
		limit_page_length=0,
	)
	senders = _sender_map()
	tz = ZoneInfo(get_system_timezone())
	items = []
	for m in rows:
		out = m.direction == "Outgoing"
		sender = senders.get(_last10(m.get("from"))) if out else None
		at = m.message_date or m.creation
		items.append({
			"kind": "text",
			"direction": "outgoing" if out else "incoming",
			"text": m.content or "",
			"media": _media(m.get("media")),
			"at": at,
			"at_epoch": get_datetime(at).replace(tzinfo=tz).timestamp() if at else 0,
			"line": (sender.full_name if sender else None) or m.get("from"),
		})
	return {"phone": e164 or None, "items": items}


@frappe.whitelist()
def get_deal_buyers(lead):
	"""CRM Lead Buyer rows for a lead, grouped for the Dispo board / debugging."""
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists("DocType", LEAD_BUYER_DOCTYPE):
		return []
	fields = [
		"name", "buyer", "buyer_name", "interest_stage", "direction",
		"phone", "buyer_type", "deal_history", "verified", "last_active", "message_count",
	]
	meta = frappe.get_meta(LEAD_BUYER_DOCTYPE)
	fields += [
		fieldname for fieldname in (
			"not_interested_reasons", "not_interested_note",
			"not_interested_by", "not_interested_at",
		) if meta.has_field(fieldname)
	]
	rows = frappe.get_all(
		LEAD_BUYER_DOCTYPE,
		filters={"lead": lead},
		fields=fields,
		order_by="interest_stage asc, modified desc",
		limit_page_length=0,
	)

	# Do-not-contact rides along so the board can mark the card and the bulk-text
	# modal can drop the buyer without a second round trip. It lives on CRM Buyer,
	# not the relationship: asking us to stop texting is a statement about the
	# person, not about one property (gw296).
	if rows and frappe.get_meta(BUYER_DOCTYPE).has_field("do_not_contact"):
		flags = {
			b.name: b for b in frappe.get_all(
				BUYER_DOCTYPE,
				filters={"name": ("in", list({r.buyer for r in rows if r.buyer}))},
				fields=["name", "do_not_contact", "do_not_contact_reason"],
				limit_page_length=0,
			)
		}
		for r in rows:
			flag = flags.get(r.buyer)
			r["do_not_contact"] = int((flag or {}).get("do_not_contact") or 0)
			r["do_not_contact_reason"] = (flag or {}).get("do_not_contact_reason")
	return rows

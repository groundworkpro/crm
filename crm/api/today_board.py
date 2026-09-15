# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""The shared **Today** board — the surface the setters actually work from.

The 5am job still materialises this board; the DM Lance gets is yesterday's
recap (`yesterday_recap`), not the calling list. `crm.api.daily_standup` decides
WHO lands on the board (Dennis's cadence, business-day counting, task suppression
— all of it lives there and is not duplicated here); this module owns what
happens to a card afterwards: tick it Done, Skip it, drag it into a different
order.

Division of responsibility, deliberately:

  * the cadence decides what LANDS on the board (generation, once a day)
  * a human owns the card after that (state + order, persisted)

which is why cards are rows rather than a live recomputation. "Done" and
"Skipped" are judgements a person made; recomputing the list would quietly lose
them, or resurrect a card someone had dismissed, the moment a call got logged.
The board also has to hold still while people work it — a list that reshuffles
underneath you is the thing that made the last attempt unusable.

`CRM Today Item` is autonamed `format:{for_date}-{lead}-{call_number}`. A lead
that owes two calls gets two independently actionable cards (call 1 and call 2),
while the call number keeps generation structurally idempotent.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import timedelta

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.utils import cint, escape_html, getdate, now_datetime

from crm.api.daily_standup import (
	CADENCE_PHASES,
	board_task_rows,
	build_standup,
	is_business_day,
	is_sequence_call_task,
	previous_business_day,
	sequence_day,
)

DOCTYPE = "CRM Today Item"
STATES = ("To Call", "Done", "Skipped")

#: What a card records when it is ticked Done. Deliberately five short answers:
#: the modal is a half-second interruption between two calls, and a list nobody
#: can read at a glance gets whatever option is nearest the thumb. "Other" is
#: the escape hatch and is the one answer that must be explained in words.
DONE_OUTCOMES = (
	"Connected",
	"No Answer",
	"Left a Voicemail",
	"Sent a Text",
	"Booked an Appointment",
	"Other",
)

#: A Done outcome that means contact happened. It takes the lead OUT of any
#: running sequence — the sequence's job was to reach them.
SEQUENCE_EXIT_OUTCOMES = ("Connected",)

#: Skip used to be a blank "why". 60 days of those notes (462 skips, 124 with
#: no reason at all) clustered into the same handful of answers, so the picker
#: is the Done one: one tap, Other still takes a sentence. Kept in step with
#: `SKIP_OUTCOMES` in TodayOutcomeModal.vue.
SKIP_OUTCOMES = (
	"Dead lead",
	"Lost",
	"Already scheduled",
	"Already contacted",
	"Check with Dennis",
	"Follow up later",
	"Not selling",
	"I didn't have time today",
	"Other",
)

#: The default personal priority order. Since 2026-09-09 the board generates
#: only two kinds of card — a task due today (which is how the sequences hand
#: work to a rep) and a deal in flight with nothing booked. The old call-ladder
#: keys (never / week1_am / week1_pm / weekly / monthly) are no longer offered
#: in the Priority modal; cards that still carry them sort after these two.
#: `nudge` = a live lead no sequence is driving, with no dated next step —
#: daily until one is booked, with "N days since last contact" on the card.
PRIORITY_ORDER = ("task", "closer", "nudge")
PRIORITY_DEFAULT_KEY = "crm_today_priority_order"


def _priority_key(phase, call_number=1):
	if phase in ("week1", "week1_partial"):
		return "week1_am" if int(call_number or 1) == 1 else "week1_pm"
	return phase or "task"


def _priority_order():
	raw = frappe.defaults.get_user_default(PRIORITY_DEFAULT_KEY)
	try:
		stored = json.loads(raw) if isinstance(raw, str) else raw
	except (TypeError, ValueError):
		stored = []
	clean = []
	for key in stored or []:
		if key in PRIORITY_ORDER and key not in clean:
			clean.append(key)
	return clean + [key for key in PRIORITY_ORDER if key not in clean]


def _priority_seed(phase, call_number=1):
	key = _priority_key(phase, call_number)
	rank = PRIORITY_ORDER.index(key) if key in PRIORITY_ORDER else len(PRIORITY_ORDER)
	return rank * 10000


def _available() -> bool:
	"""The doctype is provisioned by an ops script; degrade quietly if it isn't
	there yet rather than 500-ing every caller."""
	return bool(frappe.db.exists("DocType", DOCTYPE))


def _supports_call_slots() -> bool:
	"""The first Today schema had one row per lead. Keep reads working while the
	idempotent ops setup adds call_number/total_calls and changes autoname."""
	if not _available():
		return False
	meta = frappe.get_meta(DOCTYPE)
	return bool(meta.has_field("call_number") and meta.has_field("total_calls"))


def _row_fields(with_slots=False):
	fields = ["name", "lead", "state", "sort_order", "phase", "reason",
	          "calls_needed", "done_by", "done_at"]
	if with_slots:
		fields += ["call_number", "total_calls"]
	if _supports_outcome():
		fields += ["outcome", "outcome_note"]
	return fields


def _supports_outcome() -> bool:
	"""`outcome`/`outcome_note` are added by the same idempotent ops script that
	provisions the doctype. Degrade quietly if the app is deployed first: the board
	still resolves cards, it just doesn't record why."""
	if not _available():
		return False
	meta = frappe.get_meta(DOCTYPE)
	return bool(meta.has_field("outcome") and meta.has_field("outcome_note"))


def _supports_resolved_stamp() -> bool:
	"""`resolved_at`/`resolved_by` are added by the same idempotent ops script that
	provisions the doctype. Degrade quietly if the app is deployed first."""
	if not _available():
		return False
	meta = frappe.get_meta(DOCTYPE)
	return bool(meta.has_field("resolved_at") and meta.has_field("resolved_by"))


def _log_outcome_comment(card, state, outcome="", outcome_note="", corrected=False):
	"""Write a Today-board disposition onto the lead's own activity timeline.

	The board is where the judgement gets made, but the lead page is where anyone
	later asks "what happened when we called this person?" — so the answer has to
	live there too, not only on a card that scrolls out of the day and is never
	seen again. This is what makes a skip reason survive past 4pm.

	Best-effort by design: a timeline entry is never worth failing a rep's click
	over, so every failure is logged and swallowed.

	Put-backs are deliberately NOT logged. Undoing a mis-click is not a judgement
	about the seller, and a timeline full of "moved back to To Call" would bury the
	entries that are — the same reasoning that keeps the outcome modal from
	interrogating a put-back.
	"""
	if state not in ("Done", "Skipped"):
		return
	lead = card.get("lead") if isinstance(card, dict) else getattr(card, "lead", None)
	if not lead:
		return
	try:
		label = _("Done") if state == "Done" else _("Skipped")
		head = (
			_("Today board — outcome corrected to {0}").format(label)
			if corrected
			else _("Today board — marked {0}").format(label)
		)
		call_no = (card.get("call_number") if isinstance(card, dict) else getattr(card, "call_number", 0)) or 0
		if call_no and int(call_no) > 1:
			head += " " + _("(call {0})").format(int(call_no))
		detail = " · ".join(escape_html(b) for b in (outcome, outcome_note) if b)
		content = "<div><b>{0}</b>{1}</div>".format(
			escape_html(head), "<br>{0}".format(detail) if detail else ""
		)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "CRM Lead",
				"reference_name": lead,
				"content": content,
				"comment_email": frappe.session.user,
				"comment_by": frappe.session.user,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Today outcome comment failed")


def _state_stamps(state, now=None):
	"""The stamp columns that go with a state change, in ONE place.

	Drag and the hover buttons take different code paths (`reorder_today` writes
	with `db.set_value`; `set_today_state` saves the doc), and they were already
	out of step: a card DRAGGED into Skipped never got `resolved_at`, so the
	intraday pulse — which reads exactly that column — saw a rep who works by
	dragging as idle.

	`done_*` stays Done-only: the Today report's "completed by" list, the
	Lance-only activity pulse and the card UI all read it with that meaning.
	`resolved_*` covers Done AND Skipped, because a skip is a real judgement a
	person made. Moving a card back to "To Call" un-resolves it.
	"""
	now = now or now_datetime()
	updates = {}
	if state == "Done":
		updates["done_by"] = frappe.session.user
		updates["done_at"] = now
	else:
		updates["done_by"] = None
		updates["done_at"] = None
	if _supports_resolved_stamp():
		if state == "To Call":
			updates["resolved_by"] = None
			updates["resolved_at"] = None
		else:
			updates["resolved_by"] = frappe.session.user
			updates["resolved_at"] = now
	return updates


def _guard():
	roles = set(frappe.get_roles())
	if not roles & {"System Manager", "Sales Manager", "Sales User"}:
		frappe.throw(_("Not permitted"), frappe.PermissionError)


#: Sentinel for "don't scope to one person" — the whole-team board everyone saw
#: before leads were split between the setters.
ALL_OWNERS = "all"

#: Owner value for a lead nobody owns. Kept selectable rather than hidden: an
#: ownerless lead still owes a call, and if it only ever showed up on a board
#: nobody looks at, it would quietly go uncalled.
UNASSIGNED = ""


def _resolve_owner(owner):
	"""Whose board are we showing? Defaults to your own.

	The board is deliberately NOT permission-scoped — anyone on the sales team can
	look at anyone's list (it is a five-person company, and the standup is read off
	these lists together). Defaulting to your own is what stops it being a wall of
	other people's work.
	"""
	if owner is None:
		return frappe.session.user
	owner = str(owner).strip()
	if owner.lower() == ALL_OWNERS:
		return ALL_OWNERS
	return owner


#: Roles that mean "works leads", and therefore "can be handed one". A lead is
#: only ever moved to somebody who could actually work it; Guest, Administrator
#: and any disabled login are structurally excluded by `_assignable_users`.
ASSIGNABLE_ROLES = ("Sales User", "Sales Manager")


def _assignable_users():
	"""Everyone a card can be handed to, whether or not they have cards today.

	Deliberately NOT `_owner_options`, which is built from the day's cards: that
	list answers "whose board can I look at", and it cannot answer "who can I hand
	this to" — the person you most need to hand a deal to is precisely the one with
	an empty board. Dennis closes; he owns almost no chase-status leads, so he would
	never appear in a cards-derived list and would be unreachable as a destination.
	"""
	names = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ["in", ASSIGNABLE_ROLES]},
		pluck="parent",
		distinct=True,
	)
	if not names:
		return []
	names = [n for n in names if n not in ("Administrator", "Guest")]
	if not names:
		return []
	users = frappe.get_all(
		"User",
		filters={
			"name": ["in", names],
			"enabled": 1,
			"user_type": "System User",
		},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)
	return [{"user": u.name, "full_name": u.full_name or u.name} for u in users]


def _owner_options(rows):
	"""Everyone with cards today, with their card count, for the board switcher.

	Built from the day's cards rather than from the user list so it can't offer a
	board that is empty, and so an unexpected owner (a lead still on the old
	default owner, say) is visible instead of silently unreachable.
	"""
	counts = Counter((r.get("lead_owner") or UNASSIGNED) for r in rows)
	if not counts:
		return []

	emails = [user for user in counts if user]
	names = {}
	if emails:
		names = {
			u.name: u.full_name or u.name
			for u in frappe.get_all(
				"User", filters={"name": ["in", emails]}, fields=["name", "full_name"]
			)
		}

	options = [
		{
			"user": user,
			"full_name": names.get(user) or (user or _("Unassigned")),
			"count": count,
		}
		for user, count in counts.items()
	]
	options.sort(key=lambda o: (not o["user"], -o["count"], o["full_name"]))
	return options


def _expand_legacy_items(day):
	"""Turn an old two-call lead card into two one-call cards, once.

	Existing cards keep their state and relative order. That matters on the day
	the schema is upgraded: an old Done/Skipped card represented the human's
	judgement for the whole lead, so both generated call cards inherit it rather
	than resurrecting work. Names are intentionally not rewritten; the old
	`date-lead` row becomes call 1 and the new formatted row is call 2.
	"""
	if not _supports_call_slots():
		return 0
	rows = frappe.get_all(
		DOCTYPE,
		filters={"for_date": day},
		fields=_row_fields(True),
		order_by="sort_order asc, name asc",
	)
	legacy = [r for r in rows if int(r.calls_needed or 0) > 1]
	if not legacy:
		return 0

	# Preserve the exact visible order while opening integer gaps for sibling
	# call cards. (sort_order is an Int, so fractions are not available.)
	for i, row in enumerate(rows):
		frappe.db.set_value(
			DOCTYPE, row.name, "sort_order", (i + 1) * 10, update_modified=False
		)
		row.sort_order = (i + 1) * 10

	created = 0
	for row in legacy:
		total = max(1, int(row.calls_needed or 1))
		frappe.db.set_value(
			DOCTYPE,
			row.name,
			{"call_number": 1, "total_calls": total, "calls_needed": 1},
			update_modified=False,
		)
		for slot in range(2, total + 1):
			try:
				frappe.get_doc(
					{
						"doctype": DOCTYPE,
						"for_date": day,
						"lead": row.lead,
						"state": row.state,
						"sort_order": row.sort_order + slot - 1,
						"phase": row.phase,
						"reason": row.reason,
						"calls_needed": 1,
						"call_number": slot,
						"total_calls": total,
						"done_by": row.done_by if row.state == "Done" else None,
						"done_at": row.done_at if row.state == "Done" else None,
					}
				).insert(ignore_permissions=True)
				created += 1
			except frappe.DuplicateEntryError:
				# Concurrent first views can both attempt the expansion; autoname is
				# the lock, so the loser simply observes the row the winner inserted.
				pass
	frappe.db.commit()
	_publish(day)
	return created


def _slots_for(row):
	"""Return (slot numbers still owed, total calls represented today).

	Only the first-week cadence is a twice-daily cadence. If the first call was
	logged before the board generated, create only call 2; weekly/monthly/task
	cards remain a single call even if some unrelated call was already logged.
	"""
	if row.phase in ("never", "week1"):
		total = 2
		first = min(total, int(row.calls_today or 0)) + 1
		return range(first, total + 1), total
	return range(1, max(1, int(row.calls_needed or 1)) + 1), max(
		1, int(row.calls_needed or 1)
	)


@frappe.whitelist()
def generate_today(for_date=None):
	"""Manually sync missing cards from the latest cadence/task state.

	Only ever ADDS. Existing cards keep their human-owned state and position, so a
	standup/status/task change can add newly-due work without resurrecting or
	reordering anything the team already judged.
	"""
	_guard()
	return _generate_today(getdate(for_date or now_datetime()))


#: The board's working day ends at 4pm CT. Cards are only ever added to an OPEN
#: board: work that arrives after the close belongs to the next list, not to a
#: day nobody is working any more.
#:
#: This is not a nicety. Measured on prod: German and Exe resolved EVERY card on
#: their boards on both 2026-08-10 and 08-11 — and both days still read as failed
#: streak days, because 20 new leads landed at 23:20–23:57 on the Monday (40
#: cards) and 4 more at 23:33 on the Tuesday (8 cards). Those late cards were the
#: entire unresolved remainder of both days. Nothing is lost by holding them
#: back, either: all 20 and all 4 of those leads appeared on the NEXT day's board
#: anyway, so the late add was pure duplication that only cost the streak.
#: Moved 17 -> 16 (2026-09-02): 2026-09-01's streak broke the same way on leads
#: that arrived between 4pm and 5pm — after the setters had wrapped the day.
BOARD_CLOSE_HOUR = 16


def _board_is_closed(day, now=None):
	"""Has this board's working day already ended?

	True after 4pm on the day itself, and for any day already past — materialising
	fresh work onto a board nobody will look at again is the same mistake either
	way. A future day is open: generation for tomorrow is exactly what the nightly
	job does.
	"""
	now = now or now_datetime()
	day, today = getdate(day), getdate(now)
	if day != today:
		return day < today
	return now.hour >= BOARD_CLOSE_HOUR


def _generate_today(day):
	"""Internal sync used by the manual button, hooks, and five-minute safety job."""
	if not _available():
		return {"created": 0, "existing": 0, "due": 0, "available": False}
	day = getdate(day or now_datetime())
	if _board_is_closed(day):
		# Deliberately reported rather than silently skipped: the manual "Sync list"
		# button has to be able to say why it added nothing, or it looks broken.
		return {
			"created": 0,
			"existing": frappe.db.count(DOCTYPE, {"for_date": day}),
			"due": 0,
			"available": True,
			"closed": True,
		}
	# Sequence Task/Call steps wait +24h from the previous fire, so they land
	# in the afternoon after this 5am pass. Pull today's still-pending ones
	# forward first so the board lists day 2 / triple-dial instead of going blank
	# for yesterday's finished leads.
	try:
		from crm.api.sequence_drain import materialize_board_steps

		materialize_board_steps(day)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "today_board: sequence materialize failed")
	data = build_standup(day)
	due = data["setter"]["due"]
	with_slots = _supports_call_slots()
	if with_slots:
		_expand_legacy_items(day)
		existing_rows = frappe.get_all(
			DOCTYPE,
			filters={"for_date": day},
			fields=["name", "lead", "call_number"],
		)
		existing = {(r.lead, int(r.call_number or 1)) for r in existing_rows}
	else:
		existing_rows = frappe.get_all(
			DOCTYPE, filters={"for_date": day}, fields=["name", "lead"]
		)
		existing = {r.lead for r in existing_rows}

	created = 0
	for i, r in enumerate(due):
		if with_slots:
			slots, total = _slots_for(r)
			for slot in slots:
				if (r.name, slot) in existing:
					continue
				try:
					frappe.get_doc(
						{
							"doctype": DOCTYPE,
							"for_date": day,
							"lead": r.name,
							"state": "To Call",
							# Keep the morning and afternoon week-one passes apart.
							# The per-user preference is applied again at read time.
							"sort_order": _priority_seed(r.phase, slot) + (i + 1) * 10,
							"phase": r.phase,
							"reason": r.reason,
							"calls_needed": 1,
							"call_number": slot,
							"total_calls": total,
						}
					).insert(ignore_permissions=True)
					created += 1
					existing.add((r.name, slot))
				except frappe.DuplicateEntryError:
					pass
		elif r.name not in existing:
			seed = _priority_seed(r.phase) + i
			frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"for_date": day,
					"lead": r.name,
					"state": "To Call",
					"sort_order": seed,
					"phase": r.phase,
					"reason": r.reason,
					"calls_needed": r.calls_needed,
				}
			).insert(ignore_permissions=True)
			created += 1
			existing.add(r.name)

	if created:
		frappe.db.commit()
		_publish(day)
	return {
		"created": created,
		"existing": len(existing_rows),
		"due": len(due),
		"available": True,
	}


def enqueue_today_sync(doc=None, method=None):
	"""Queue an add-only sync when a new lead or lead task may create work today.

	Imports can create many leads/tasks in one transaction, so active jobs are
	deduplicated. The five-minute scheduler is the safety net for the narrow race
	where a second commit lands while the deduplicated job is already finishing.
	"""
	try:
		day = getdate(now_datetime())
		if not _available() or not is_business_day(day):
			return
		# A lead that arrives at 11pm is tomorrow's work. Stop here rather than in
		# the job so a late-evening import doesn't queue a sync per commit for a
		# board that will refuse all of them anyway.
		if _board_is_closed(day):
			return
		if doc and doc.doctype == "CRM Lead":
			if method == "on_update" and not doc.has_value_changed("status"):
				return
		elif doc and doc.doctype == "CRM Task":
			if doc.reference_doctype != "CRM Lead" or not doc.reference_docname:
				return
		frappe.enqueue(
			"crm.api.today_board.run_today_sync",
			queue="short",
			job_id=f"today-sync-{day}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	except TypeError:  # compatibility with older Frappe enqueue signatures
		frappe.enqueue(
			"crm.api.today_board.run_today_sync",
			queue="short",
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Today board sync enqueue failed")


def run_today_sync():
	"""Worker/scheduler entry: keep today's add-only board current."""
	day = getdate(now_datetime())
	if not is_business_day(day):
		return {"created": 0, "available": _available(), "skipped": "non-business day"}
	if _board_is_closed(day):
		# This job is on `*/5 * * * *` — every five minutes ROUND THE CLOCK, despite
		# what the notes say about business hours. Without this it is the other half
		# of the late-night card problem, alongside the new-lead hook.
		#
		# Warming still runs: a card added at 3:55pm is worked at 4:10, and a rep
		# finishing the day's list after hours deserves the same fast map.
		enqueue_comps_warm(day)
		return {"created": 0, "available": _available(), "skipped": "board closed"}
	try:
		result = _generate_today(day)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Today board automatic sync failed")
		return {"created": 0, "available": _available(), "error": True}
	# After generation, never before: the cards this run just created are exactly
	# the ones most likely to be cold.
	enqueue_comps_warm(day)
	return result


@frappe.whitelist()
def cleanup_late_cards(day, dry_run=1, close_hour=None):
	"""Delete a past day's late-added, never-touched cards — the streak repair.

	Mirrors the manual gw316 cleanup that restored Aug 10/11 to 100%: a card
	materialised at/after the board close is unresolvable by design (nobody is
	working that board any more), so it only ever reads as a rep who did not
	finish. A card is deleted ONLY when all three hold:

	  * created at/after BOARD_CLOSE_HOUR on its own board day (`creation` is
	    stored in the site timezone, America/Chicago, so a plain string compare
	    against the day's close is the right clock);
	  * never touched — still To Call, no outcome, no resolved/done stamp — so a
	    human judgement is never thrown away, however late it was made;
	  * its lead appears on a LATER board day, so deleting loses nothing: the
	    next generation already re-materialised the work.

	Dry-run by default; `dry_run=0` deletes and reports. Deliberately tighter
	than `_guard()`: this rewrites history (the streak), so it is Lance/System
	Manager only.

	`close_hour` overrides the cutoff for repairing days that predate a close-hour
	change (2026-09-01's breakers were materialised at 3:52/3:57pm — before even
	the 4pm close — so sweeping them needs an explicit, human-chosen cutoff). The
	never-touched + later-board invariants still apply in full.
	"""
	if "System Manager" not in frappe.get_roles() and frappe.session.user != "lance.johnson@groundworkpro.com":
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not _available():
		return {"available": False, "candidates": 0}

	dry_run = cint(dry_run)
	day = getdate(day)
	hour = cint(close_hour) if close_hour is not None else BOARD_CLOSE_HOUR
	cutoff = f"{day} {hour:02d}:00:00"

	fields = ["name", "lead", "state", "creation", "done_at"]
	if _supports_call_slots():
		fields.append("call_number")
	if _supports_outcome():
		fields.append("outcome")
	if _supports_resolved_stamp():
		fields.append("resolved_at")

	late = frappe.get_all(
		DOCTYPE,
		filters={"for_date": day, "creation": [">=", cutoff]},
		fields=fields,
		order_by="creation asc",
	)

	deletable, kept_touched, kept_no_later_board = [], [], []
	for row in late:
		touched = (
			row.state != "To Call"
			or row.get("done_at")
			or row.get("resolved_at")
			or (row.get("outcome") or "").strip()
		)
		if touched:
			kept_touched.append(row)
			continue
		# The later board is what makes deletion lossless — without it the lead's
		# owed call would vanish with the card.
		if not frappe.db.exists(DOCTYPE, {"lead": row.lead, "for_date": [">", day]}):
			kept_no_later_board.append(row)
			continue
		deletable.append(row)

	def _shape(rows):
		return [
			{
				"name": r.name,
				"lead": r.lead,
				"state": r.state,
				"created": str(r.creation),
				"call_number": r.get("call_number"),
			}
			for r in rows
		]

	result = {
		"available": True,
		"day": str(day),
		"cutoff": cutoff,
		"dry_run": bool(dry_run),
		"late_cards": len(late),
		"deletable": _shape(deletable),
		"kept_touched": _shape(kept_touched),
		"kept_no_later_board": _shape(kept_no_later_board),
	}

	if dry_run or not deletable:
		return result

	deleted = 0
	for row in deletable:
		frappe.delete_doc(DOCTYPE, row.name, ignore_permissions=True)
		deleted += 1
	frappe.db.commit()
	_publish(day)
	result["deleted"] = deleted
	return result


# ---------------------------------------------------------------------------------
# Prewarming the comps map for the day's work
# ---------------------------------------------------------------------------------
#: A cold comps map is 5-14s of third-party HTTP, and it is cold for most of this
#: board: measured on a real 176-card day, 61 of the 96 distinct leads had never
#: had their comps looked at. Reps open these one after another, so the wait lands
#: on them dozens of times a morning.
#:
#: Warmed here rather than at lead creation because THIS is the work list. Leads
#: come back onto the board on cadence weeks after they arrived, by which time a
#: creation-time warm has long expired (the area cache is 7 days); and a lead that
#: never reaches the board never costs anything.
#:
#: Bounded on BOTH axes. The sweep runs every five minutes, so it does not need to
#: finish in one pass, and it deliberately does not try: a whole 93-lead board is
#: ~6 minutes of network, which is fine spread across the ~40 runs between the 5am
#: generation and the setters starting, and not fine as one 6-minute job holding a
#: worker. Leads are warmed SERIALLY inside a run for the same reason the fetch
#: pool is capped at four — RapidAPI answers a burst with HTTP 429.
WARM_BUDGET_SECONDS = 60
WARM_MAX_LEADS = 12


def enqueue_comps_warm(day=None):
	"""Hand the warming to its own background job. Never inline.

	TWO reasons it is enqueued rather than run inside `run_today_sync`:

	  * that job is on the SHORT queue and a rep's board sync rides on it. A minute
	    of third-party network in there is a minute other short jobs are waiting,
	    to make a map faster that nobody is looking at yet. Prewarming is by
	    definition the least urgent work in the system.
	  * `deduplicate` then does the serialising for free. The sync is triggered by
	    the five-minute scheduler AND by every new lead and lead-task commit, so
	    several can land together — and warming in parallel is exactly how we would
	    walk back into the HTTP 429s that capped the fetch pool at four.
	"""
	day = getdate(day or now_datetime())
	try:
		frappe.enqueue(
			"crm.api.today_board.warm_today_areas",
			queue="long",
			job_id=f"comps-warm-{day}",
			deduplicate=True,
			enqueue_after_commit=True,
			day=str(day),
		)
	except TypeError:  # older Frappe enqueue signatures have no deduplicate
		frappe.enqueue(
			"crm.api.today_board.warm_today_areas",
			queue="long",
			enqueue_after_commit=True,
			day=str(day),
		)
	except Exception:
		# Prewarming is an optimisation. It must never be able to fail a board sync.
		frappe.log_error(frappe.get_traceback(), "Comps warm enqueue failed")


def warm_today_areas(day=None, budget_seconds=None, max_leads=None):
	"""Pull the day's comps circles into cache, a few leads per run.

	The work list is DERIVED from the board every time rather than queued when a
	card is created. Nothing to drift, nothing to race, no state to reset — and it
	self-heals, because a lead whose warm failed simply still looks cold next run.
	The same property that makes it safe makes it cheap: "is this circle warm?" is
	a Redis read, so a sweep over a fully warm board costs nothing at all.
	"""
	import time

	info = {"checked": 0, "warmed": 0, "already_warm": 0, "failed": 0, "remaining": 0}
	if not _available():
		return info
	try:
		from crm.api import comps as comps_api
		from crm.api import zillow as zillow_api

		if not zillow_api._api_key():
			info["skipped"] = "not_configured"
			return info

		day = getdate(day or now_datetime())
		budget = float(budget_seconds or WARM_BUDGET_SECONDS)
		cap = int(max_leads or WARM_MAX_LEADS)
		started = time.time()

		# Two cards per lead in week one, so distinct leads, and in board order so
		# the ones a rep reaches first are warmed first.
		rows = frappe.get_all(
			DOCTYPE,
			filters={"for_date": day},
			fields=["lead", "sort_order"],
			order_by="sort_order asc",
			limit_page_length=0,
		)
		seen, leads = set(), []
		for r in rows:
			if r.lead and r.lead not in seen:
				seen.add(r.lead)
				leads.append(r.lead)

		for lead in leads:
			if info["warmed"] >= cap or (time.time() - started) >= budget:
				# Whatever is left is next run's problem, five minutes from now.
				info["remaining"] = len(leads) - info["checked"]
				break
			info["checked"] += 1
			try:
				res = comps_api.warm_lead_area(lead)
			except Exception:
				# One bad lead must never stop the sweep, and must never fail the
				# board sync it rides on.
				info["failed"] += 1
				frappe.log_error(frappe.get_traceback(), f"Comps warm failed for {lead}")
				continue
			if res.get("warmed"):
				info["warmed"] += 1
			elif res.get("reason") == "already_warm":
				info["already_warm"] += 1
		return info
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Today board comps warm failed")
		info["error"] = True
		return info


@frappe.whitelist()
def warm_status(for_date=None):
	"""How much of a board is already warm. Read-only, and free — no HTTP at all.

	Exists so "is prewarming actually working?" is a question with an answer, rather
	than something inferred from how fast a map felt.
	"""
	_guard()
	from crm.api import comps as comps_api
	from crm.api import zillow_comps

	day = getdate(for_date or now_datetime())
	rows = frappe.get_all(
		DOCTYPE, filters={"for_date": day}, fields=["lead"], limit_page_length=0
	)
	leads = sorted({r.lead for r in rows if r.lead})
	warm = cold = unlocated = 0
	for lead in leads:
		point = frappe.db.get_value(
			"CRM Lead", lead, ["property_lat", "property_lng"], as_dict=True
		)
		lat = (point or {}).get("property_lat")
		lng = (point or {}).get("property_lng")
		if lat is None or lng is None:
			unlocated += 1
		elif zillow_comps.area_is_cached(lat, lng, comps_api.WARM_RADIUS_MI):
			warm += 1
		else:
			cold += 1
	return {
		"for_date": str(day),
		"cards": len(rows),
		"leads": len(leads),
		"warm": warm,
		"cold": cold,
		# Not yet geocoded, so not yet warmable — the sweep geocodes them (free,
		# Census) the first time it reaches them.
		"unlocated": unlocated,
	}


def _publish(day):
	frappe.publish_realtime("crm_today", {"for_date": str(day)}, after_commit=True)


@frappe.whitelist()
def get_today_board(
	for_date=None, auto_generate=1, status=None, priority=None, signal=None, owner=None
):
	"""Everything the board needs in one call: the cards, plus the lead facts the
	cards display (status, phone, address, and how many calls it has had today so
	a rep can see progress without opening the lead).

	Scoped to ONE person's leads by default (`owner`, defaulting to the caller) so
	each setter works their own list. Pass `owner="all"` for the whole team's
	board, which is what everyone saw before ownership was split."""
	if not _available():
		return {"available": False, "columns": [], "date": None}
	_guard()
	day = getdate(for_date or now_datetime())
	owner = _resolve_owner(owner)

	with_slots = _supports_call_slots()
	if with_slots:
		_expand_legacy_items(day)
	rows = frappe.get_all(
		DOCTYPE,
		filters={"for_date": day},
		fields=_row_fields(with_slots),
		order_by="sort_order asc, name asc",
	)
	# generate on first view so the board works immediately, not only after 5am
	if not rows and int(auto_generate or 0):
		generate_today(day)
		rows = frappe.get_all(
			DOCTYPE,
			filters={"for_date": day},
			fields=_row_fields(with_slots),
			order_by="sort_order asc, name asc",
		)
	if not rows:
		return {
			"available": True,
			"date": str(day),
			"columns": _empty_columns(),
			"priority_order": _priority_order(),
			"status_counts": [],
			"owner": owner,
			"owners": [],
			"assignees": _assignable_users(),
		}

	lead_names = list({r.lead for r in rows})
	leads = {
		l.name: l
		for l in frappe.get_all(
			"CRM Lead",
			filters={"name": ["in", lead_names]},
			fields=["name", "lead_name", "status", "lead_owner", "mobile_no", "phone", "email",
			        "property_address", "property_city", "property_state", "property_zip"]
		        + [
			        f for f in ("zillow_zpid", "zillow_fetched_at", "custom_non_refundable",
			                    "custom_non_refundable_reason")
			        if frappe.db.has_column("CRM Lead", f)
		        ],
		)
	}
	calls = frappe.db.sql(
		"""
		select reference_docname n, count(*) c from `tabCRM Call Log`
		where reference_doctype='CRM Lead' and reference_docname in %(names)s
		  and date(creation) = %(d)s
		group by reference_docname
		""",
		{"names": lead_names, "d": day},
		as_dict=True,
	)
	made = {c.n: c.c for c in calls}

	# One open task per lead for the card. Fetch once for the whole board rather
	# than issuing a query per card; due tasks sort before undated tasks.
	task_fields = [
		"name", "reference_doctype", "reference_docname", "title", "description",
		"status", "due_date", "priority", "assigned_to", "creation", "modified",
	]
	task_meta = frappe.get_meta("CRM Task")
	if task_meta.has_field("call_outcome"):
		task_fields.append("call_outcome")
	open_tasks = frappe.get_all(
		"CRM Task",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_docname": ["in", lead_names],
			"status": ["not in", ["Done", "Canceled"]],
		},
		fields=task_fields,
	)
	# Current sequence day first (day 2 beats leftover day 1), then soonest due.
	open_tasks.sort(
		key=lambda task: (
			-sequence_day(task.title),
			task.due_date is None,
			task.due_date or "",
			task.creation or "",
		)
	)
	open_tasks_by_lead = defaultdict(list)
	for task in open_tasks:
		open_tasks_by_lead[task.reference_docname].append(task)
	open_task_by_lead = {}
	open_tasks_shaped = {}
	for lead, group in open_tasks_by_lead.items():
		shaped = []
		for task in board_task_rows(group):
			row = dict(task)
			row.pop("creation", None)
			row.pop("modified", None)
			row["is_completed"] = False
			if is_sequence_call_task(row.get("title")):
				row["title"] = "Triple dial"
			shaped.append(row)
		open_tasks_shaped[lead] = shaped
		open_task_by_lead[lead] = shaped[0] if shaped else None

	# A Done card should show the task the rep just finished, not the next future
	# follow-up. Use the latest task marked Done on this board date; `modified` is
	# the task timeline's existing completion timestamp convention.
	completed_tasks = frappe.get_all(
		"CRM Task",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_docname": ["in", lead_names],
			"status": "Done",
			"modified": [
				"between",
				[f"{day} 00:00:00", f"{day} 23:59:59.999999"],
			],
		},
		fields=task_fields,
		order_by="modified desc",
	)
	completed_task_by_lead = {}
	for task in completed_tasks:
		task.pop("creation", None)
		task["completed_at"] = task.pop("modified", None)
		task["is_completed"] = True
		completed_task_by_lead.setdefault(task.reference_docname, task)

	incoming_by_lead = {}
	if frappe.db.exists("DocType", "Quo Message"):
		incoming = frappe.db.sql(
			"""
			select reference_docname n,
			       max(coalesce(message_date, creation)) last_incoming_text
			from `tabQuo Message`
			where reference_doctype='CRM Lead'
			  and reference_docname in %(names)s
			  and direction='Incoming'
			group by reference_docname
			""",
			{"names": lead_names},
			as_dict=True,
		)
		incoming_by_lead = {row.n: row.last_incoming_text for row in incoming}

	for r in rows:
		l = leads.get(r.lead) or {}
		r["lead_name"] = l.get("lead_name") or r.lead
		r["lead_status"] = l.get("status")
		r["lead_owner"] = l.get("lead_owner") or ""
		r["mobile_no"] = l.get("mobile_no") or l.get("phone")
		r["email"] = l.get("email")
		r["address"] = _address(l)
		# Cheap: these are already on the lead. A miss is only flagged after we have
		# actually asked Zillow — never on a card nobody has opened comps for.
		r["zillow_unresolved"] = bool(l.get("zillow_fetched_at")) and not l.get("zillow_zpid")
		# Bonus-wallet iSTL buy: the provider will not refund. Chip on the card so
		# a setter doesn't spend a 10-dial streak chasing a denial.
		r["non_refundable"] = bool(l.get("custom_non_refundable"))
		r["non_refundable_reason"] = (l.get("custom_non_refundable_reason") or "") or None
		r["calls_today"] = made.get(r.lead, 0)
		r["call_number"] = int(r.get("call_number") or 1)
		r["total_calls"] = int(
			r.get("total_calls") or max(1, int(r.get("calls_needed") or 1))
		)
		r["priority_key"] = _priority_key(r.phase, r.call_number)
		# A Done card shows the task the rep just finished. Everywhere else the
		# soonest OPEN task leads — but a task completed today is still shown when
		# there is no open one, so ticking the card's checkbox doesn't make the row
		# vanish and leave the rep no way to undo a mis-click.
		completed_task = completed_task_by_lead.get(r.lead)
		open_task = open_task_by_lead.get(r.lead)
		open_rows = open_tasks_shaped.get(r.lead) or []
		if r.state == "Done" and completed_task:
			r["task"] = completed_task
			r["tasks"] = [completed_task]
		else:
			r["tasks"] = open_rows or ([completed_task] if completed_task else [])
			r["task"] = open_task or completed_task
		# Open task, not a completed one: "has a task assigned" is the live next
		# action, not yesterday's tick. Cadence is the stored phase — leftover-task
		# cards keep phase=task so they don't pretend to be on the call ladder.
		r["has_task"] = bool(open_task)
		r["in_cadence"] = (r.get("phase") or "") in CADENCE_PHASES
		r["last_incoming_text"] = incoming_by_lead.get(r.lead)

	# The owner selector lists everyone who has cards today, counted BEFORE the
	# owner filter, so a rep with an empty board can still see whose board has
	# work on it and switch to it.
	owners = _owner_options(rows)
	owner_names = {o["user"]: o["full_name"] for o in owners}
	for r in rows:
		key = r.lead_owner or UNASSIGNED
		r["owner_name"] = owner_names.get(key) or (r.lead_owner or _("Unassigned"))
	if owner != ALL_OWNERS:
		rows = [r for r in rows if (r.lead_owner or "") == owner]

	# Everything below this line describes the board you are actually looking at:
	# the status filter counts, the columns and the totals all agree with it.
	status_counts = {}
	for r in rows:
		if r.lead_status:
			status_counts[r.lead_status] = status_counts.get(r.lead_status, 0) + 1
	if status:
		rows = [r for r in rows if r.lead_status == status]
	if priority:
		rows = [r for r in rows if r.priority_key == priority]
	if signal == "incoming":
		rows = [r for r in rows if r.last_incoming_text]
	elif signal == "task":
		rows = [r for r in rows if r.has_task]
	elif signal == "cadence":
		rows = [r for r in rows if r.in_cadence]
	elif signal == "task_only":
		rows = [r for r in rows if r.has_task and not r.in_cadence]
	elif signal == "cadence_only":
		rows = [r for r in rows if r.in_cadence and not r.has_task]

	priority_order = _priority_order()
	priority_rank = {key: i for i, key in enumerate(priority_order)}
	to_call = [r for r in rows if r.state == "To Call"]
	to_call.sort(
		key=lambda r: (
			priority_rank.get(r.priority_key, len(priority_rank)),
			r.sort_order,
			r.name,
		)
	)
	other = [r for r in rows if r.state != "To Call"]
	rows = to_call + other

	cols = _empty_columns()
	by_state = {c["state"]: c for c in cols}
	for r in rows:
		by_state.get(r.state, by_state["To Call"])["items"].append(r)
	for c in cols:
		c["count"] = len(c["items"])
	return {
		"available": True,
		"date": str(day),
		"columns": cols,
		"status_counts": [
			{"status": key, "count": status_counts[key]}
			for key in sorted(status_counts)
		],
		"selected_status": status or "",
		"selected_priority": priority or "",
		"selected_signal": signal or "",
		"priority_order": priority_order,
		"owner": owner,
		"owners": owners,
		# Who a selected card can be handed to. Everyone who works leads, not just
		# whoever happens to have cards today — see `_assignable_users`.
		"assignees": _assignable_users(),
	}


def _scope_rows_to_owner(rows, owner):
	"""Keep only the cards whose lead belongs to `owner`.

	Ownership is read off the lead at request time rather than stamped onto the
	card. A card stamped at 5am would keep pointing at the old rep for the rest of
	the day the moment a lead was reassigned — and reassignment is exactly what the
	backfill and the round robin do.
	"""
	if not rows:
		return []
	lead_names = list({row.lead for row in rows if row.get("lead")})
	if not lead_names:
		return []
	owners = {
		lead.name: (lead.lead_owner or UNASSIGNED)
		for lead in frappe.get_all(
			"CRM Lead", filters={"name": ["in", lead_names]}, fields=["name", "lead_owner"]
		)
	}
	return [row for row in rows if owners.get(row.lead, UNASSIGNED) == owner]


def _tally_report_days(rows):
	by_day = {}
	for row in rows:
		day = getdate(row.for_date)
		stats = by_day.setdefault(day, _empty_report_day(day))
		stats["total"] += 1
		if row.state == "Done":
			stats["done"] += 1
		elif row.state == "Skipped":
			stats["skipped"] += 1
	for stats in by_day.values():
		_finish_report_day(stats)
	return by_day


def _streak_from_days(by_day, today):
	"""100%-resolved business-day streak over `by_day`.

	An unfinished current day does not erase the streak through yesterday.
	"""
	business_dates = sorted(day for day in by_day if is_business_day(day))
	first_day = business_dates[0] if business_dates else today
	cursor = today
	if not is_business_day(cursor) or not by_day.get(cursor, {}).get("perfect"):
		cursor = previous_business_day(cursor)
	current_streak = 0
	through = None
	while cursor >= first_day:
		stats = by_day.get(cursor)
		if not stats or not stats["perfect"]:
			break
		if through is None:
			through = str(cursor)
		current_streak += 1
		cursor = previous_business_day(cursor)
	best_streak = 0
	run = 0
	cursor = first_day
	while cursor <= today:
		if is_business_day(cursor):
			stats = by_day.get(cursor)
			if stats and stats["perfect"]:
				run += 1
				best_streak = max(best_streak, run)
			else:
				run = 0
		cursor += timedelta(days=1)
	return {
		"current": current_streak,
		"best": best_streak,
		"through": through,
	}, business_dates


def _empty_report_day(day):
	return {
		"date": str(getdate(day)),
		"total": 0,
		"done": 0,
		"skipped": 0,
		"remaining": 0,
		"completion_rate": 0,
		"handled_rate": 0,
		"perfect": False,
	}


def _finish_report_day(stats):
	stats["remaining"] = stats["total"] - stats["done"] - stats["skipped"]
	if stats["total"]:
		resolved = stats["done"] + stats["skipped"]
		stats["completion_rate"] = round(resolved * 100 / stats["total"])
		stats["handled_rate"] = stats["completion_rate"]
	stats["perfect"] = bool(stats["total"] and stats["remaining"] == 0)
	return stats


@frappe.whitelist()
def get_today_report(for_date=None, history_days=10, owner=None):
	"""Progress today plus the team's 100%-resolved business-day streak.

	Both Done and Skipped cards count toward the streak. An unfinished current day
	does not erase the streak earned through the previous business day.

	`owner` scopes TODAY's figures (and `completed_by`) to the board on screen — a
	bar reading 12/87 over a 30-card board is worse than no bar. The streak is
	always the TEAM's: one number the whole acquisitions crew shares (Lance,
	2026-08-28, reversing the personal streak from 2026-08-06).
	"""
	_guard()
	today = getdate(for_date or now_datetime())
	owner = _resolve_owner(owner)
	if not _available():
		return {
			"available": False,
			"today": _empty_report_day(today),
			"recent": [],
			"completed_by": [],
			"streak": {"current": 0, "best": 0, "through": None},
		}

	try:
		history_days = max(5, min(30, int(history_days or 10)))
	except (TypeError, ValueError):
		history_days = 10
	start = today - timedelta(days=370)
	rows = frappe.get_all(
		DOCTYPE,
		filters={"for_date": ["between", [start, today]]},
		fields=["for_date", "state", "done_by", "lead"],
		order_by="for_date asc",
		limit_page_length=50000,
	)

	# Streak is ALWAYS the whole team. Today's bar still follows the board on
	# screen, so a personal board doesn't show 12/87 over 30 cards.
	team_by_day = _tally_report_days(rows)
	team_streak, team_dates = _streak_from_days(team_by_day, today)

	scoped_rows = rows if owner == ALL_OWNERS else _scope_rows_to_owner(rows, owner)
	by_day = (
		team_by_day if owner == ALL_OWNERS else _tally_report_days(scoped_rows)
	)
	todays_rows = [row for row in scoped_rows if getdate(row.for_date) == today]
	today_stats = by_day.get(today, _empty_report_day(today))

	recent = [team_by_day[day] for day in sorted(team_dates, reverse=True)[:history_days]]
	recent_total = sum(day["total"] for day in recent)
	recent_resolved = sum(day["done"] + day["skipped"] for day in recent)
	recent_average = round(recent_resolved * 100 / recent_total) if recent_total else 0
	scope_today = "owner" if owner != ALL_OWNERS else "team"

	# Credit list follows today's board, not the team streak.
	people = Counter(
		row.done_by for row in todays_rows if row.state == "Done" and row.done_by
	)
	user_names = {}
	if people:
		user_names = {
			u.name: u.full_name or u.name
			for u in frappe.get_all(
				"User",
				filters={"name": ["in", list(people)]},
				fields=["name", "full_name"],
			)
		}
	completed_by = [
		{"user": user, "name": user_names.get(user, user), "done": count}
		for user, count in people.most_common()
	]

	return {
		"available": True,
		"today": today_stats,
		"recent": recent,
		"recent_average": recent_average,
		"completed_by": completed_by,
		"streak": team_streak,
		"owner": owner,
		"scope": {"today": scope_today, "streak": "team", "recent": "team"},
		"definition": _(
			"A streak day requires every Today card on the team board to be resolved as Done or Skipped."
		),
	}


#: Short labels for the Done-outcome picker. The recap has to fit a glance.
OUTCOME_SHORT = {
	"Left a Voicemail": "VM",
	"Sent a Text": "Texted",
	"Booked an Appointment": "Booked",
}


def yesterday_recap(today=None):
	"""Team recap of the previous business day's Today board.

	What the 5am DM actually sends: did we hit the streak, cards per person
	(done/skip), Done outcomes, grouped skip reasons. `today` is the morning the
	job runs, so passing Friday recaps Thursday.
	"""
	today = getdate(today or now_datetime())
	day = previous_business_day(today)
	empty = {
		"available": _available(),
		"day": str(day),
		"hit": False,
		"streak": {"current": 0, "best": 0, "through": None},
		"total": 0,
		"done": 0,
		"skipped": 0,
		"remaining": 0,
		"people": [],
	}
	if not _available():
		return empty

	start = day - timedelta(days=370)
	fields = ["for_date", "state", "done_by", "lead"]
	if _supports_resolved_stamp():
		fields.append("resolved_by")
	if _supports_outcome():
		fields += ["outcome", "outcome_note"]
	rows = frappe.get_all(
		DOCTYPE,
		filters={"for_date": ["between", [start, day]]},
		fields=fields,
		order_by="for_date asc",
		limit_page_length=50000,
	)
	team_by_day = _tally_report_days(rows)
	streak, _ = _streak_from_days(team_by_day, day)
	stats = team_by_day.get(day, _empty_report_day(day))

	day_rows = [row for row in rows if getdate(row.for_date) == day]
	people_done = Counter()
	people_skip = Counter()
	people_left = Counter()
	people_done_out = defaultdict(Counter)
	people_skip_out = defaultdict(Counter)
	people_skip_display = defaultdict(dict)
	left_rows = [row for row in day_rows if row.state == "To Call"]
	left_owners = {}
	if left_rows:
		lead_names = list({row.lead for row in left_rows if row.get("lead")})
		if lead_names:
			left_owners = {
				lead.name: (lead.lead_owner or "")
				for lead in frappe.get_all(
					"CRM Lead",
					filters={"name": ["in", lead_names]},
					fields=["name", "lead_owner"],
				)
			}
	for row in day_rows:
		if row.state == "Done":
			who = (row.get("resolved_by") or row.done_by or "").strip()
			if who:
				people_done[who] += 1
				label = (row.get("outcome") or "").strip() or "(no outcome)"
				people_done_out[who][label] += 1
		elif row.state == "Skipped":
			who = (row.get("resolved_by") or row.done_by or "").strip()
			label = (row.get("outcome") or "").strip()
			note = _norm_skip_note(row.get("outcome_note") or "")
			# Canned skip reason when we have one; Other / legacy free-text uses
			# the note, so the recap still names what happened.
			reason = label if label and label != "Other" else (note or label or "(no reason)")
			if who:
				people_skip[who] += 1
				key = reason.casefold()
				people_skip_out[who][key] += 1
				people_skip_display[who].setdefault(key, reason)
		elif row.state == "To Call":
			who = (left_owners.get(row.get("lead")) or "").strip()
			people_left[who or "unassigned"] += 1

	users = set(people_done) | set(people_skip) | {
		u for u in people_left if u != "unassigned"
	}
	names = {}
	if users:
		names = {
			u.name: (u.first_name or (u.full_name or u.name).split()[0])
			for u in frappe.get_all(
				"User",
				filters={"name": ["in", list(users)]},
				fields=["name", "first_name", "full_name"],
			)
		}
	people = []
	for user in users:
		done = people_done[user]
		skipped = people_skip[user]
		left = people_left[user]
		people.append({
			"user": user,
			"name": names.get(user, user.split("@")[0]),
			"done": done,
			"skipped": skipped,
			"left": left,
			"total": done + skipped,
			"done_outcomes": _count_list(
				people_done_out[user], short=OUTCOME_SHORT
			),
			"skip_reasons": _count_list(
				people_skip_out[user], display=people_skip_display[user]
			),
		})
	if people_left.get("unassigned"):
		people.append({
			"user": "unassigned",
			"name": "Unassigned",
			"done": 0,
			"skipped": 0,
			"left": people_left["unassigned"],
			"total": 0,
			"done_outcomes": [],
			"skip_reasons": [],
		})
	people.sort(key=lambda p: (-p["total"], -p["left"], p["name"]))

	return {
		"available": True,
		"day": str(day),
		"hit": bool(stats["perfect"]),
		"streak": streak,
		"total": stats["total"],
		"done": stats["done"],
		"skipped": stats["skipped"],
		"remaining": stats["remaining"],
		"people": people,
	}


def _norm_skip_note(note):
	s = " ".join((note or "").strip().split())
	return s.rstrip(".")


def _count_list(counter, short=None, display=None):
	items = []
	for key, count in counter.most_common():
		label = key
		if display:
			label = display.get(key, key)
		if short:
			label = short.get(label, label)
		items.append({"label": label, "count": count})
	return items


def _mix_bits(items):
	bits = []
	for item in items or []:
		bits.append(item["label"] if item["count"] == 1 else f"{item['label']} {item['count']}")
	return " · ".join(bits)


def render_yesterday_recap(d):
	"""Streak line, then one block per person with their own done/skip reasons."""
	day = getdate(d["day"])
	label = day.strftime("%a %-d %b")
	if not d.get("available"):
		return f"**Yesterday {label}** — Today board isn't provisioned yet."
	if not d["total"]:
		return f"**Yesterday {label}** — no board."

	streak_n = int((d.get("streak") or {}).get("current") or 0)
	if d["hit"]:
		head = f"**Yesterday {label}** — streak hit. :fire: {streak_n} day" + (
			"s" if streak_n != 1 else ""
		)
	else:
		left_bits = [
			f"{p['name']} {p['left']}"
			for p in sorted(
				(d.get("people") or []), key=lambda p: (-(p.get("left") or 0), p["name"])
			)
			if p.get("left")
		]
		who = f" — {' · '.join(left_bits)}" if left_bits else ""
		head = f"**Yesterday {label}** — streak missed ({d['remaining']} left{who})."

	blocks = [head]
	people = d.get("people") or []
	if not people:
		blocks.append("_Nobody resolved a card._")
		return "\n".join(blocks)
	for p in people:
		bits = [f"{p['done']} done", f"{p['skipped']} skip"]
		if p.get("left"):
			bits.append(f"{p['left']} left")
		blocks.append("")
		blocks.append(f"**{p['name']}** {p['total']} ({' · '.join(bits)})")
		done_mix = _mix_bits(p.get("done_outcomes"))
		if done_mix:
			blocks.append(f"Done: {done_mix}")
		skip_mix = _mix_bits(p.get("skip_reasons"))
		if skip_mix:
			blocks.append(f"Skipped: {skip_mix}")
	return "\n".join(blocks)


@frappe.whitelist()
def set_today_priority_order(order):
	"""Save this user's cross-device priority order without adding a schema field."""
	_guard()
	if isinstance(order, str):
		try:
			order = json.loads(order)
		except ValueError:
			order = []
	if not isinstance(order, list):
		frappe.throw(_("Invalid priority order."))
	clean = []
	for key in order:
		if key in PRIORITY_ORDER and key not in clean:
			clean.append(key)
	clean += [key for key in PRIORITY_ORDER if key not in clean]
	frappe.defaults.set_user_default(PRIORITY_DEFAULT_KEY, json.dumps(clean))
	return clean


#: trailing country noise that only costs a narrow card its truncation budget
_COUNTRY_TAIL = re.compile(r",\s*(usa|u\.s\.a\.?|united states)\s*$", re.I)


def _address(lead) -> str:
	"""One clean "street, city, ST zip" line for a card.

	Delegates to `agreement._full_property_address` rather than re-deriving the
	rule. A first attempt here compared the raw `property_state` against the
	address, which silently failed on the ~8% of leads whose state is stored
	spelled out ("Minnesota") while the address carries the abbreviation ("MN") —
	producing "611 5th Ave SE, Rochester, MN 55904, USA, Minnesota". The shared
	helper already maps full names to abbreviations and matches the abbreviation
	case-sensitively so "IN" doesn't match the word "in".

	The only thing added on top is dropping a trailing ", USA", which some
	webhook addresses carry and which is pure noise on a narrow card.
	"""
	from crm.api.agreement import _full_property_address

	return _COUNTRY_TAIL.sub("", _full_property_address(lead) or "").strip().rstrip(",")


def _empty_columns():
	return [{"state": s, "items": [], "count": 0} for s in STATES]


def _plain_text(value):
	"""Compact activity previews should never expose stored HTML/JSON markup."""
	if not value:
		return ""
	return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)


def _recent_lead_activity(lead):
	"""Latest calls, texts, and emails for the compact Today lead view.

	This deliberately excludes FCRM Notes. Intake notes include raw vendor payloads,
	which made the modal show a wall of JSON instead of the seller conversation the
	setter actually needs before calling.
	"""
	activity = []

	call_meta = frappe.get_meta("CRM Call Log")
	call_fields = [
		"name", "type", "from", "to", "duration", "caller", "receiver", "creation",
	]
	for optional in ("custom_ai_summary", "custom_call_class"):
		if call_meta.has_field(optional):
			call_fields.append(optional)
	calls = frappe.get_all(
		"CRM Call Log",
		filters={"reference_doctype": "CRM Lead", "reference_docname": lead},
		fields=call_fields,
		order_by="creation desc",
		limit=10,
	)
	for row in calls:
		outgoing = row.type == "Outgoing"
		activity.append(
			{
				"name": row.name,
				"type": "call",
				"direction": "outgoing" if outgoing else "incoming",
				"title": _("Outgoing call") if outgoing else _("Incoming call"),
				"content": _plain_text(row.get("custom_ai_summary")),
				"when": row.creation,
				"duration": row.duration or 0,
				"classification": row.get("custom_call_class") or "",
				"counterparty": row.get("to") if outgoing else row.get("from"),
			}
		)

	if frappe.db.exists("DocType", "Quo Message"):
		texts = frappe.get_all(
			"Quo Message",
			filters={"reference_doctype": "CRM Lead", "reference_docname": lead},
			fields=["name", "direction", "from", "to", "content", "media", "message_date", "creation"],
			order_by="message_date desc, creation desc",
			limit=10,
		)
		for row in texts:
			outgoing = row.direction == "Outgoing"
			content = _plain_text(row.content)
			if not content and row.media:
				content = _("Attachment")
			activity.append(
				{
					"name": row.name,
					"type": "text",
					"direction": "outgoing" if outgoing else "incoming",
					"title": _("Outgoing text") if outgoing else _("Incoming text"),
					"content": content,
					"when": row.message_date or row.creation,
					"counterparty": row.get("to") if outgoing else row.get("from"),
				}
			)

	emails = frappe.get_all(
		"Communication",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"communication_medium": "Email",
		},
		fields=[
			"name", "subject", "content", "sender", "sender_full_name", "recipients",
			"sent_or_received", "communication_date", "creation", "delivery_status",
		],
		order_by="communication_date desc, creation desc",
		limit=10,
	)
	for row in emails:
		outgoing = row.sent_or_received == "Sent"
		activity.append(
			{
				"name": row.name,
				"type": "email",
				"direction": "outgoing" if outgoing else "incoming",
				"title": row.subject or (_("Outgoing email") if outgoing else _("Incoming email")),
				"content": _plain_text(row.content),
				"when": row.communication_date or row.creation,
				"counterparty": row.recipients if outgoing else (row.sender_full_name or row.sender),
				"status": row.delivery_status or "",
			}
		)

	activity.sort(key=lambda item: item["when"], reverse=True)
	return activity[:12]


@frappe.whitelist()
def get_today_lead_snapshot(lead):
	"""A compact, read-only lead view for the Today card modal.

	The full Lead page is intentionally much heavier. Setters need enough context
	to make the call without leaving the queue: contact/property facts, open
	tasks, and recent calls/texts/emails. Every optional field is discovered from
	metadata so an unprovisioned custom field cannot break the board.
	"""
	_guard()
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	meta = frappe.get_meta("CRM Lead")
	base_fields = [
		"name", "lead_name", "status", "mobile_no", "phone", "email", "source",
		"property_address", "property_city", "property_state", "property_zip",
	]
	optional = [
		"lead_summary", "reason_for_sell", "duration_to_sell", "property_condition",
		"property_occupied_by", "property_type", "bedrooms", "bathrooms",
		"square_footage", "year_built", "asking_price", "best_call_time",
	]
	fields = [f for f in base_fields + optional if f == "name" or meta.has_field(f)]
	doc = frappe.db.get_value("CRM Lead", lead, fields, as_dict=True)

	detail_fields = [
		"reason_for_sell", "duration_to_sell", "property_condition",
		"property_occupied_by", "property_type", "bedrooms", "bathrooms",
		"square_footage", "year_built", "asking_price", "best_call_time", "source",
	]
	details = []
	for fieldname in detail_fields:
		value = doc.get(fieldname)
		if value in (None, ""):
			continue
		field = meta.get_field(fieldname)
		details.append(
			{
				"fieldname": fieldname,
				"label": field.label if field else fieldname.replace("_", " ").title(),
				"value": value,
				"fieldtype": field.fieldtype if field else "Data",
			}
		)

	tasks = frappe.get_all(
		"CRM Task",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
			"status": ["not in", ["Done", "Canceled"]],
		},
		fields=["name", "title", "description", "status", "due_date", "priority", "assigned_to"],
		order_by="creation asc",
	)
	# Due first (oldest first), undated last — same ordering as the full Lead's
	# pinned To-do block.
	tasks.sort(key=lambda task: (task.due_date is None, task.due_date or ""))

	return {
		"lead": {
			"name": doc.name,
			"lead_name": doc.lead_name or doc.name,
			"status": doc.status,
			"mobile_no": doc.mobile_no or doc.phone,
			"email": doc.email,
			"address": _address(doc),
			"summary": doc.get("lead_summary"),
		},
		"details": details,
		"tasks": tasks,
		"activity": _recent_lead_activity(lead),
	}


@frappe.whitelist()
def set_today_lead_status(
	item,
	status,
	lost_reason=None,
	lost_notes=None,
	custom_refundable=None,
	custom_refund_status=None,
):
	"""Change the CRM Lead status from a Today card.

	Save the full Lead document (rather than using ``db.set_value``) so status
	history, lost-reason validation, task hygiene, and the normal Lead hooks all
	still run. The Today item anchors the request to a card the user can see.
	"""
	_guard()
	if not status or not frappe.db.exists("CRM Lead Status", status):
		frappe.throw(_("Invalid lead status."))

	item_doc = frappe.get_doc(DOCTYPE, item)
	lead = frappe.get_doc("CRM Lead", item_doc.lead)
	lead.check_permission("write")
	if lead.status == status:
		return {"ok": True, "status": status}

	lead.status = status
	if lost_reason is not None:
		lead.lost_reason = lost_reason
	if lost_notes is not None:
		lead.lost_notes = lost_notes
	if custom_refundable is not None and lead.meta.has_field("custom_refundable"):
		lead.custom_refundable = 1 if custom_refundable else 0
	if custom_refund_status is not None and lead.meta.has_field("custom_refund_status"):
		lead.custom_refund_status = custom_refund_status
	lead.save()

	# Lead hooks may add newly-due work asynchronously; publish immediately too
	# so every open Today board reflects this status change after commit.
	_publish(item_doc.for_date)
	return {"ok": True, "status": lead.status}


@frappe.whitelist()
def set_today_state(item, state, outcome=None, outcome_note=None):
	"""Tick a card Done / Skipped / back To Call, with what happened.

	Resolving a card is the one moment the rep already has the answer in their
	head, so it is the only moment the answer is cheap to collect — asking later
	means reconstructing thirty calls from memory. A Done card carries one of
	`DONE_OUTCOMES`; a Skipped card carries one of `SKIP_OUTCOMES`. Other on
	either side still requires a sentence.

	Re-submitting the SAME state only rewrites the outcome: correcting a
	mis-click must not restamp `resolved_at`, which the intraday pulse reads as
	"this card was resolved in this half hour".
	"""
	if state not in STATES:
		frappe.throw(_("Invalid state."))
	_guard()

	outcome = (outcome or "").strip()
	outcome_note = (outcome_note or "").strip()
	if state == "Done":
		if outcome and outcome not in DONE_OUTCOMES:
			frappe.throw(_("Invalid call outcome."))
		if outcome == "Other" and not outcome_note:
			frappe.throw(_("Say a little more about what happened."))
	elif state == "Skipped":
		if outcome and outcome not in SKIP_OUTCOMES:
			frappe.throw(_("Invalid skip reason."))
		if outcome == "Other" and not outcome_note:
			frappe.throw(_("Say why this one is being skipped."))
	if state == "To Call":
		outcome = ""
		outcome_note = ""

	doc = frappe.get_doc(DOCTYPE, item)
	same_state = doc.state == state
	if same_state and not (outcome or outcome_note):
		return {"ok": True, "state": state}

	doc.state = state
	if not same_state:
		for field, value in _state_stamps(state).items():
			setattr(doc, field, value)
	if _supports_outcome():
		doc.outcome = outcome
		doc.outcome_note = outcome_note
	doc.save(ignore_permissions=True)
	# `same_state` here means the rep re-answered an already-resolved card, i.e.
	# corrected a mis-click. Worth its own timeline line: without it the lead keeps
	# showing the answer that was withdrawn.
	_log_outcome_comment(doc, state, outcome, outcome_note, corrected=same_state)
	# Contact made → the lead leaves its sequence (Lance, 2026-09-09): from here
	# the rep owns the follow-up, and the nudge card chases them for a next
	# step. Paused, not Stopped, like every other exit — resumable.
	if state == "Done" and outcome in SEQUENCE_EXIT_OUTCOMES:
		try:
			from crm.api.sequence_status import pause_all

			pause_all(doc.lead, f"Today card marked {outcome} by {frappe.session.user}")
		except Exception:
			frappe.log_error(frappe.get_traceback(), "today_board: sequence exit failed")
	_publish(doc.for_date)
	return {"ok": True, "state": state, "outcome": outcome, "outcome_note": outcome_note}


@frappe.whitelist()
def assign_today_leads(items, owner):
	"""Hand the leads behind a set of Today cards to somebody else.

	The board is where a rep already has the day's work in front of them, so it is
	the cheapest place to say "these five are Dennis's now" — the alternative is
	opening five leads and editing a side-panel field on each.

	What moves, and why the whole thing hangs off `lead_owner` alone:

	* The **cards** follow automatically. Ownership is read off the lead at request
	  time (`get_today_board`, `_scope_rows_to_owner`), never stamped onto the card,
	  so a reassigned lead's cards simply appear on the new owner's board on the
	  next load — with their state, order and outcome intact. There is deliberately
	  nothing to migrate.
	* The **open tasks** and the stale assignment follow via
	  `crm.api.lead_owner_change`, which is an `on_update` hook — hence `doc.save()`
	  here rather than `db.set_value`. The other bulk owner writer
	  (`lead_owner_backfill`) deliberately uses `set_value` to avoid re-running SLA
	  across hundreds of leads and leaves no Version row; this is a handful of leads
	  moved by a person on purpose, and "changed Lead Owner from German to Dennis"
	  on the timeline is exactly the audit trail anyone will later want.

	Per-lead failures are collected rather than thrown. A lead sitting in a Lost
	status with no lost reason cannot be saved at all (`validate_lost_reason`), and
	one such card must not silently abort the other four moves.
	"""
	_guard()
	if not _available():
		return {"ok": False, "available": False}

	owner = (owner or "").strip()
	if not owner:
		frappe.throw(_("Pick who these leads should go to."))
	if owner not in {u["user"] for u in _assignable_users()}:
		frappe.throw(_("{0} cannot be given leads.").format(owner))

	if isinstance(items, str):
		items = frappe.parse_json(items)
	names = [n for n in (items or []) if n]
	if not names:
		return {"ok": True, "moved": 0, "leads": [], "skipped": 0, "failed": []}

	# A lead can hold two call cards; selecting both is one handover, not two.
	cards = frappe.get_all(
		DOCTYPE, filters={"name": ["in", names]}, fields=["name", "lead", "for_date"]
	)
	leads, days = [], set()
	for card in cards:
		days.add(card.for_date)
		if card.lead not in leads:
			leads.append(card.lead)

	moved, skipped, failed = [], 0, []
	for lead_name in leads:
		try:
			lead = frappe.get_doc("CRM Lead", lead_name)
			lead.check_permission("write")
			if (lead.lead_owner or "") == owner:
				skipped += 1
				continue
			lead.lead_owner = owner
			lead.save()
			moved.append(lead_name)
		except Exception as e:
			label = frappe.db.get_value("CRM Lead", lead_name, "lead_name") or lead_name
			failed.append({"lead": lead_name, "lead_name": label, "error": str(e)})
			frappe.log_error(frappe.get_traceback(), "Today bulk assign failed")

	if moved:
		frappe.db.commit()
		# Every open board is now describing a card set that has changed hands —
		# including the RECEIVER's, which has gained cards it never had.
		for day in days or {getdate(now_datetime())}:
			_publish(day)

	return {
		"ok": True,
		"owner": owner,
		"moved": len(moved),
		"leads": moved,
		"skipped": skipped,
		"failed": failed,
	}


@frappe.whitelist()
def reorder_today(order, state=None, for_date=None):
	"""Persist a drag.

	`order` is the list of item names in their new order within one column — the
	client sends the WHOLE column after a drag, which is what vuedraggable hands
	back anyway.

	The whole column is then renumbered in steps of 10. Renumbering only the
	names passed in is not enough: cards are initially seeded at wide priority
	offsets, so writing 10/20/30 onto only the dragged cards can drop them behind
	untouched neighbours. Any name in the column that wasn't passed keeps its relative position
	and is renumbered after the ones that were, so even a partial list leaves the
	column in a sane, predictable total order rather than a corrupted one.
	"""
	_guard()
	if isinstance(order, str):
		order = frappe.parse_json(order)
	if not order:
		return {"ok": True, "moved": 0}

	first = frappe.get_doc(DOCTYPE, order[0])
	day = getdate(for_date) if for_date else first.for_date
	target = state or first.state

	# everything currently in the destination column, in its present order
	column = frappe.get_all(
		DOCTYPE,
		filters={"for_date": day, "state": target},
		fields=["name"],
		order_by="sort_order asc, name asc",
		pluck="name",
	)
	moved = set(order)
	final = list(order) + [n for n in column if n not in moved]

	newly_resolved = []
	for i, name in enumerate(final):
		updates = {"sort_order": (i + 1) * 10}
		if name in moved:
			cur = frappe.db.get_value(DOCTYPE, name, "state")
			if cur != target:
				newly_resolved.append(name)
				# Normally the client has already called `set_today_state` (that is
				# where the outcome is collected), so this branch is the fallback for
				# a drag that skipped it. Stamp it exactly the same way regardless.
				updates["state"] = target
				updates.update(_state_stamps(target))
				if _supports_outcome() and target == "To Call":
					updates["outcome"] = ""
					updates["outcome_note"] = ""
		# db.set_value keeps a 60-card reorder to one cheap write per row; the
		# realtime publish below is what refreshes everyone else's board.
		frappe.db.set_value(DOCTYPE, name, updates, update_modified=False)
	frappe.db.commit()
	# Only the cards this drag actually RESOLVED get a timeline entry, and only on
	# the fallback path — the normal drag already went through `set_today_state`,
	# which logged it with the outcome the rep gave. Logging here too would double
	# every entry.
	for name in newly_resolved:
		card = frappe.db.get_value(DOCTYPE, name, ["lead", "call_number"], as_dict=True)
		if card:
			_log_outcome_comment(card, target)
	_publish(day)
	return {"ok": True, "moved": len(order), "renumbered": len(final)}


@frappe.whitelist()
def clear_today(for_date=None):
	"""Wipe a day's cards so the next view regenerates from scratch. For fixing a
	bad generation; not part of normal use."""
	_guard()
	if not frappe.has_permission(DOCTYPE, "delete"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	day = getdate(for_date or now_datetime())
	names = frappe.get_all(DOCTYPE, filters={"for_date": day}, pluck="name")
	for n in names:
		frappe.delete_doc(DOCTYPE, n, ignore_permissions=True, force=True)
	frappe.db.commit()
	_publish(day)
	return {"deleted": len(names)}

"""A rep's booked follow-up holds the lead's sequences until that date.

Lance, 2026-09-25 (Darlene Scott, CRM-LEAD-2026-00799): "the scheduled
follow-up task for the future should trump. Period." Germán booked a Follow up
for Oct 11 on 9/11; the Today board correctly went quiet on her, but her 10-day
text sequence kept minting "Text Darlene — day N of 10" / "Call Darlene Scott"
tasks that then sat overdue and invisible for a week.

The rule: when a lead has an open task a PERSON created (owner is a real user,
not the sequence engine's Administrator) due after today, each Active
enrollment on that lead is HELD — its next step moves to 8am the day after the
booked date — and the engine's open tasks on the lead are Canceled. The
sequence then carries on from where it was. Held, not Paused (Lance, same day):
the Long-Term Follow-Up drip backfilled 2026-09-24 has 134 of its 151 leads
with a booked follow-up, and pausing would have silenced nearly all of it.

A hand-made task due today or overdue holds nothing — it puts the lead on the
Today board every day until it is done.

Who made a task: the drainer runs as Administrator (sequence_drain.drain), so
every sequence task is owned by Administrator; reps' tasks are owned by the
rep. On prod since 2026-08-20 that split is clean (2,147 Administrator tasks,
all "Text X — day N of M" / "Call X"; every other task is a user's).

Sequences that govern themselves are left alone. Long-Term Follow-Up (deploy
repo 68259e7, 2026-09-24) puts `not has_open_rep_task` on its steps: the
runner skips each call while a rep task is open and keeps the weekly/monthly
cadence. Holding it too would stack two rules on one sequence, so any sequence
whose steps carry that condition is not held — only its stale engine tasks
are canceled on a booking, like every other sequence's.

Two enforcement points:
- `on_task_change` (CRM Task after_insert / on_update) holds at the moment the
  booking is made or moved later.
- `check_before_step` in the drainer is the safety net for bookings that
  predate this module (Darlene) or bypassed the hook.
"""

from datetime import date, datetime, time as dt_time, timedelta

import frappe
from frappe.utils import getdate, now_datetime


ENGINE_OWNERS = frozenset({"Administrator", "Guest", ""})
CLOSED = ("Done", "Canceled")

def is_manual(owner) -> bool:
	"""Pure: a task a person made (not the sequence engine)."""
	return (owner or "") not in ENGINE_OWNERS

def is_after_today(due, today) -> bool:
	"""Pure: due on a later calendar day than `today`."""
	if not due:
		return False
	due_day = due.date() if isinstance(due, datetime) else due if isinstance(due, date) else getdate(due)
	return due_day > getdate(today)

def is_booking(task, today) -> bool:
	"""Pure: an open, hand-made task on a lead due after today."""
	return (
		(task.get("reference_doctype") == "CRM Lead")
		and bool(task.get("reference_docname"))
		and (task.get("status") or "") not in CLOSED
		and is_manual(task.get("owner"))
		and is_after_today(task.get("due_date"), today)
	)

RESUME_HOUR = 8

#: step condition the runner evaluates itself (crm_sequence_runner_core.py)
REP_TASK_CONDITION = "not has_open_rep_task"

def has_rep_task_rule(steps) -> bool:
	"""Pure: any step already skips itself while a rep task is open."""
	return any(((st.get("condition") or "").strip() == REP_TASK_CONDITION) for st in (steps or []))

def self_governed(sequence) -> bool:
	try:
		return has_rep_task_rule(frappe.get_cached_doc("CRM Sequence", sequence).steps)
	except Exception:
		return False

def hold_target(due):
	"""Pure: 8am the day after the booked date — when the sequence resumes."""
	due_day = due.date() if isinstance(due, datetime) else getdate(due)
	return datetime.combine(due_day + timedelta(days=1), dt_time(RESUME_HOUR, 0))

def needs_hold(next_run, target) -> bool:
	"""Pure: the enrollment would run before the hold ends."""
	return not next_run or next_run < target

def _reason(due, user=None):
	who = f" by {user}" if user else ""
	return f"follow-up booked{who} for {frappe.utils.format_datetime(due, 'd MMM')}"

def cancel_engine_tasks(lead) -> list:
	"""Cancel every open sequence-made task on `lead`. Returns their names."""
	names = frappe.get_all(
		"CRM Task",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
			"owner": "Administrator",
			"status": ["not in", list(CLOSED)],
		},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("CRM Task", name, "status", "Canceled")
	return names

def hold_enrollments(lead, due, reason) -> list:
	"""Move every Active enrollment on `lead` that would run before the hold
	ends to `hold_target(due)`. Returns the enrollment names held."""
	target = hold_target(due)
	held = []
	for row in frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"lead": lead, "status": "Active"},
		fields=["name", "next_run", "sequence"],
	):
		if self_governed(row.sequence):
			continue
		nr = frappe.utils.get_datetime(row.next_run) if row.next_run else None
		if not needs_hold(nr, target):
			continue
		frappe.db.set_value(
			"CRM Sequence Enrollment",
			row.name,
			{
				"next_run": target,
				"last_log": "{0} held until {1}: {2}".format(now_datetime(), target, reason),
			},
			update_modified=False,
		)
		held.append(row.name)
	return held

def hold_for_booking(lead, due, reason):
	"""Hold the lead's Active enrollments past `due` and cancel the engine's
	open tasks. Returns (held enrollments, canceled tasks). Never raises."""
	try:
		held = hold_enrollments(lead, due, reason)
		canceled = cancel_engine_tasks(lead)
		return held, canceled
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence_booking: hold failed")
		return [], []

def on_task_change(doc, method=None):
	"""CRM Task after_insert / on_update: a hand-made booking after today
	holds the lead's sequences until the day after it."""
	try:
		if not is_booking(doc, now_datetime()):
			return
		if method == "on_update" and not (
			doc.has_value_changed("due_date") or doc.has_value_changed("status")
		):
			return
		hold_for_booking(doc.reference_docname, doc.due_date, _reason(doc.due_date, doc.owner))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence_booking: task hook failed")

def future_booking(lead, now=None):
	"""Latest open hand-made task on `lead` due after today, else None — the
	sequence waits until every booked follow-up has had its day."""
	now = now or now_datetime()
	eod = datetime.combine(getdate(now), datetime.max.time())
	row = frappe.db.sql(
		"""
		select max(due_date) from `tabCRM Task`
		where reference_doctype = 'CRM Lead' and reference_docname = %(lead)s
		  and status not in ('Done', 'Canceled')
		  and owner not in ('Administrator', 'Guest', '')
		  and due_date > %(eod)s
		""",
		{"lead": lead, "eod": eod},
	)
	return row[0][0] if row and row[0] else None

def check_before_step(enr) -> bool:
	"""Drainer guard: False (after holding) when the lead has a booked
	follow-up after today; True otherwise. Fails open."""
	try:
		if self_governed(enr.sequence):
			return True
		due = future_booking(enr.lead)
		if not due:
			return True
		hold_for_booking(enr.lead, due, _reason(due))
		frappe.db.commit()
		return False
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence_booking: step guard failed")
		return True

def backfill(dry_run=1):
	"""One-off: apply the hold to every Active enrollment whose lead already
	has a booked follow-up (bookings made before this module existed).

	    bench --site crm.groundworkpro.com execute \
	      crm.api.sequence_booking.backfill --kwargs '{"dry_run": 0}'
	"""
	dry = frappe.utils.cint(dry_run)
	leads = sorted(
		set(frappe.get_all("CRM Sequence Enrollment", filters={"status": "Active"}, pluck="lead"))
	)
	held = canceled = hit = 0
	for lead in leads:
		due = future_booking(lead)
		if not due:
			continue
		hit += 1
		if dry:
			continue
		h, c = hold_for_booking(lead, due, _reason(due) + " (backfill)")
		held += len(h)
		canceled += len(c)
	if not dry:
		frappe.db.commit()
	out = {"dry_run": bool(dry), "leads_with_booking": hit, "held": held, "tasks_canceled": canceled}
	print(out)
	return out

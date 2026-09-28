"""A rep's booked follow-up ends the short sequence and hands off to the long one.

Lance, 2026-09-25 (Darlene Scott, CRM-LEAD-2026-00799): Germán booked a Follow
up for Oct 11 on 9/11; the Today board correctly went quiet on her, but her
New Lead 10-Day sequence kept minting "Text Darlene — day N of 10" / "Call
Darlene Scott" tasks that then sat overdue and invisible for a week.

The rule (Lance, same day, after first trying a date hold): "it doesn't make
any sense to pause the 10-day follow-up ... if they were in the 10-day and
then create another to-do in the future, it should go to weekly."

So when a lead has an open task a PERSON created (owner is a real user, not
the sequence engine's Administrator) due after today:

- An Active enrollment in a sequence with `then_enroll` (New Lead 10-Day ->
  Long-Term Follow-Up) is Stopped and the lead is enrolled in the next
  sequence — the same handoff the runner makes when the sequence finishes
  (`chain_next` in the deploy repo's crm_sequence_runner_core.py), same gates.
- Sequences that govern themselves are left alone. Long-Term Follow-Up puts
  `not has_open_rep_task` on its steps (deploy repo 68259e7): the runner skips
  each weekly/monthly call while a rep task is open, keeps the cadence, and
  resumes once the task is closed. That is the "pause and resume" part.
- Any other sequence (Under Contract Seller Check-In) runs as before.
- The engine's open tasks on the lead (owner Administrator) are Canceled: the
  rep's booking is the plan now.

A hand-made task due today or overdue changes nothing here — it puts the lead
on the Today board every day until it is done.

Who made a task: the drainer runs as Administrator (sequence_drain.drain), so
every sequence task is owned by Administrator; reps' tasks are owned by the
rep. On prod since 2026-08-20 that split is clean (2,147 Administrator tasks,
all "Text X — day N of M" / "Call X"; every other task is a user's).

Two enforcement points:
- `on_task_change` (CRM Task after_insert / on_update) acts the moment the
  booking is made or moved later.
- `check_before_step` in the drainer is the safety net for bookings that
  predate this module or bypassed the hook.
"""

from datetime import date, datetime

import frappe
from frappe.utils import getdate, now_datetime

ENGINE_OWNERS = frozenset({"Administrator", "Guest", ""})
CLOSED = ("Done", "Canceled")

#: step condition the runner evaluates itself (crm_sequence_runner_core.py)
REP_TASK_CONDITION = "not has_open_rep_task"

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

def has_rep_task_rule(steps) -> bool:
	"""Pure: any step already skips itself while a rep task is open."""
	return any(((st.get("condition") or "").strip() == REP_TASK_CONDITION) for st in (steps or []))

def parse_statuses(raw):
	"""Pure: one status per line; blank -> [] (= any status)."""
	return [line.strip() for line in (raw or "").splitlines() if line.strip()]

def self_governed(sequence) -> bool:
	try:
		return has_rep_task_rule(frappe.get_cached_doc("CRM Sequence", sequence).steps)
	except Exception:
		return False

def next_sequence(sequence):
	"""The `then_enroll` target of `sequence`, else None."""
	try:
		return frappe.get_cached_doc("CRM Sequence", sequence).get("then_enroll") or None
	except Exception:
		return None

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

def enroll_next(lead, nxt, from_seq) -> bool:
	"""Enroll `lead` in `nxt` under the runner's chain_next gates: target
	enabled, lead not converted, lead status inside its lead_statuses, never
	enrolled in it before. Returns True if enrolled."""
	if not frappe.db.get_value("CRM Sequence", nxt, "enabled"):
		return False
	row = frappe.db.get_value("CRM Lead", lead, ["status", "converted"], as_dict=True)
	if not row or row.converted:
		return False
	allowed = parse_statuses(frappe.db.get_value("CRM Sequence", nxt, "lead_statuses"))
	if allowed and (row.status or "") not in allowed:
		return False
	if frappe.db.exists("CRM Sequence Enrollment", {"lead": lead, "sequence": nxt}):
		return False
	frappe.get_doc(
		{
			"doctype": "CRM Sequence Enrollment",
			"lead": lead,
			"sequence": nxt,
			"status": "Active",
			"last_log": "{0} enrolled: follow-up booked, ended {1} early".format(now_datetime(), from_seq),
		}
	).insert(ignore_permissions=True)
	return True

def hand_off(lead, reason) -> list:
	"""Stop each Active enrollment on `lead` whose sequence chains to a next
	one, and enroll the lead there. Returns [(stopped, next_sequence)]."""
	out = []
	for row in frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"lead": lead, "status": "Active"},
		fields=["name", "sequence"],
	):
		if self_governed(row.sequence):
			continue
		nxt = next_sequence(row.sequence)
		if not nxt:
			continue
		frappe.db.set_value(
			"CRM Sequence Enrollment",
			row.name,
			{
				"status": "Stopped",
				"last_log": "{0} stopped: {1}; handed to {2}".format(now_datetime(), reason, nxt),
			},
			update_modified=False,
		)
		enroll_next(lead, nxt, row.sequence)
		out.append((row.name, nxt))
	return out

def apply_booking(lead, reason):
	"""Hand off chained sequences and cancel the engine's open tasks.
	Returns (handed off, canceled tasks). Never raises."""
	try:
		handed = hand_off(lead, reason)
		canceled = cancel_engine_tasks(lead)
		return handed, canceled
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence_booking: apply failed")
		return [], []

def on_task_change(doc, method=None):
	"""CRM Task after_insert / on_update: a hand-made booking after today
	ends the short sequence and hands the lead to the long one."""
	try:
		if not is_booking(doc, now_datetime()):
			return
		if method == "on_update" and not (
			doc.has_value_changed("due_date") or doc.has_value_changed("status")
		):
			return
		apply_booking(doc.reference_docname, _reason(doc.due_date, doc.owner))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence_booking: task hook failed")

def future_booking(lead, now=None):
	"""Latest open hand-made task on `lead` due after today, else None."""
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
	"""Drainer guard: False (after handing off) when this enrollment's
	sequence chains onward and the lead has a booked follow-up after today.
	Fails open."""
	try:
		if self_governed(enr.sequence) or not next_sequence(enr.sequence):
			return True
		due = future_booking(enr.lead)
		if not due:
			return True
		apply_booking(enr.lead, _reason(due))
		frappe.db.commit()
		return False
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence_booking: step guard failed")
		return True

def backfill(dry_run=1):
	"""One-off: hand off every Active chained enrollment whose lead already has
	a booked follow-up (bookings made before this rule).

	    bench --site crm.groundworkpro.com execute \\
	      crm.api.sequence_booking.backfill --kwargs '{"dry_run": 0}'
	"""
	dry = frappe.utils.cint(dry_run)
	rows = [
		r
		for r in frappe.get_all(
			"CRM Sequence Enrollment", filters={"status": "Active"}, fields=["lead", "sequence"]
		)
		if not self_governed(r.sequence) and next_sequence(r.sequence)
	]
	leads = sorted({r.lead for r in rows if future_booking(r.lead)})
	handed = canceled = 0
	if not dry:
		for lead in leads:
			due = future_booking(lead)
			h, c = apply_booking(lead, _reason(due) + " (backfill)")
			handed += len(h)
			canceled += len(c)
		frappe.db.commit()
	out = {"dry_run": bool(dry), "leads": len(leads), "handed_off": handed, "tasks_canceled": canceled, "lead_names": leads}
	print(out)
	return out

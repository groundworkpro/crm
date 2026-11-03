"""The lead page's Sequences card: which sequences a lead is in, and Resume / Pause /
Stop / Move to the next sequence without leaving the lead.

Why (Dennis, 2026-11-03, Manuel Lago CRM-LEAD-2026-01449): a text reply paused
his New Lead 10-Day on Sep 28. Paused never resumes by itself and never finishes,
so it never handed him to Long-Term Follow-Up — and the only Resume button was in
the sequence's Enrollments table, which loads 99 of 803 rows (he was #495) and
lists lead IDs, not names. 13 leads were stuck like that.

`move_next` is the handoff the runner makes when a sequence finishes
(`chain_next`), done by hand: stop this enrollment, enroll the lead in the
sequence's `then_enroll` target under the same gates
(`sequence_booking.enroll_next`), and cancel the engine's open tasks on the lead
if nothing else is still running.
"""

import frappe
from frappe.utils import now_datetime

from crm.api import sequence_booking as sb

ACTIONS = ("resume", "pause", "stop", "move_next")
_REASON_MARKS = ("auto-paused:", "PAUSED by fail-safe:", "auto-stopped:", "stopped:", "paused:", "resumed:")


def step_groups(steps) -> list:
	"""Pure: for each flat step row, its 1-based group number. Consecutive rows
	with the same type+subject+message are one step with several paths (the
	sequence editor groups them the same way)."""
	out, key_prev, n = [], None, 0
	for st in steps or []:
		key = (st.get("step_type"), st.get("subject"), st.get("message"))
		if key != key_prev:
			n += 1
			key_prev = key
		out.append(n)
	return out


def progress(current_step, groups) -> tuple:
	"""Pure: (steps done, total steps) in grouped terms. `current_step` is the
	flat count of rows already run."""
	total = groups[-1] if groups else 0
	done = 0
	if current_step and groups:
		done = groups[min(int(current_step), len(groups)) - 1]
	return done, total


def reason(log) -> str:
	"""Pure: the human part of `last_log` ("Text reply received"), else ''."""
	if not log:
		return ""
	for mark in _REASON_MARKS:
		i = log.find(mark)
		if i >= 0:
			return log[i + len(mark):].strip()
	return ""


def actions_for(status, nxt, already_in_next) -> list:
	"""Pure: what the card offers for an enrollment."""
	if status == "Active":
		out = ["pause", "stop"]
	elif status == "Paused":
		out = ["resume", "stop"]
	else:
		return []
	if nxt and not already_in_next:
		out.insert(0, "move_next")
	return out


def _check(lead):
	frappe.has_permission("CRM Lead", "write", lead, throw=True)


@frappe.whitelist()
def for_lead(lead):
	"""Every enrollment of `lead`, newest first, with progress and actions."""
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	rows = frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"lead": lead},
		fields=["name", "sequence", "status", "current_step", "next_run", "last_log", "creation"],
		order_by="creation desc",
	)
	enrolled = {r.sequence for r in rows}
	out = []
	for r in rows:
		try:
			seq = frappe.get_cached_doc("CRM Sequence", r.sequence)
			groups = step_groups([s.as_dict() for s in seq.steps])
			nxt = seq.get("then_enroll") or None
		except frappe.DoesNotExistError:
			groups, nxt = [], None
		done, total = progress(r.current_step, groups)
		out.append(
			{
				"name": r.name,
				"sequence": r.sequence,
				"status": r.status,
				"done": done,
				"total": total,
				"next_run": r.next_run,
				"reason": reason(r.last_log),
				"last_log": r.last_log,
				"then_enroll": nxt,
				"actions": actions_for(r.status, nxt, nxt in enrolled),
			}
		)
	return out


@frappe.whitelist(methods=["POST"])
def set_state(enrollment, action):
	"""Resume / pause / stop an enrollment, or move the lead to the next sequence."""
	if action not in ACTIONS:
		frappe.throw("Unknown action")
	enr = frappe.get_doc("CRM Sequence Enrollment", enrollment)
	_check(enr.lead)
	user = frappe.session.user
	now = now_datetime()
	if action not in actions_for(enr.status, sb.next_sequence(enr.sequence), False):
		frappe.throw("This sequence is {0}; it can't be changed that way.".format(enr.status))

	if action == "resume":
		vals = {"status": "Active", "last_log": "{0} resumed: by {1}".format(now, user)}
		# a step overdue for weeks would fire at once; that is the step the rep is resuming
	elif action == "pause":
		vals = {"status": "Paused", "last_log": "{0} paused: by {1}".format(now, user)}
	elif action == "stop":
		vals = {"status": "Stopped", "last_log": "{0} stopped: by {1}".format(now, user)}
	else:
		nxt = sb.next_sequence(enr.sequence)
		if frappe.db.exists("CRM Sequence Enrollment", {"lead": enr.lead, "sequence": nxt}):
			frappe.throw("This lead is already in {0}.".format(nxt))
		if not sb.enroll_next(enr.lead, nxt, enr.sequence, why="moved by {0}".format(user)):
			status = frappe.db.get_value("CRM Lead", enr.lead, "status")
			frappe.throw("{0} doesn't run for leads in {1}.".format(nxt, status))
		vals = {"status": "Stopped", "last_log": "{0} stopped: moved to {1} by {2}".format(now, nxt, user)}

	frappe.db.set_value("CRM Sequence Enrollment", enr.name, vals)
	if action in ("stop", "move_next"):
		# the old sequence's open tasks go; the next one schedules its own (first
		# Long-Term call is a week out). Left alone while another sequence runs.
		others = frappe.get_all(
			"CRM Sequence Enrollment",
			filters={"lead": enr.lead, "status": "Active", "name": ["!=", enr.name]},
			pluck="sequence",
		)
		if should_cancel_tasks(action, others, sb.next_sequence(enr.sequence)):
			sb.cancel_engine_tasks(enr.lead)
	return for_lead(enr.lead)


def should_cancel_tasks(action, other_active, nxt) -> bool:
	"""Pure: cancel the engine's open tasks when nothing else is running — for a
	move, the sequence just entered doesn't count."""
	others = [s for s in other_active if not (action == "move_next" and s == nxt)]
	return not others

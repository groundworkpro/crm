"""Real-time driver for CRM Sequence steps (single-driver model).

The sequence engine (`CRM Sequence Runner Core`, a Server Script) advances ONE
step per call. Server Scripts run in a RestrictedPython sandbox with no
`time.sleep` and no delayed enqueue (no rq_scheduler), so they can't honor
seconds-level waits. This module runs in a normal background worker (not
sandboxed): it sleeps the real wait between steps and reuses the engine for the
actual per-step action, so the send logic stays in exactly one place.

Single-driver model — `drain()` is the ONLY thing that advances an enrollment,
and `job_id="seqdrain:<enrollment>"` (deduped via is_job_enqueued, which covers
QUEUED *and* STARTED jobs) guarantees at most one drainer per enrollment ever
runs. That is what makes the cron + drainer handoff race-free: there is never a
second driver that could fire the same step. The old blanket `CRM Sequence
Runner` core-cron is DISABLED so it can't act as a second driver.

Two enqueue paths, both funnelling to the same per-enrollment job_id:
  - `enqueue_for_lead` (CRM Lead doc-event) -> `drain_lead` -> drain, for the
    instant burst the moment a lead enrolls (low latency).
  - `drain_due` (1-min scheduler in hooks.py) -> drain, the backstop that keeps
    sub-minute follow-on steps (e.g. rapid texts after a longer wait) firing
    without being rounded up to the cron tick.

Performance: drainers SLEEP, so they run on a DEDICATED queue (`seqdrain`) with
its own worker container — a sleeping drainer never blocks the main background
queue (emails, ring-alerts, contact sync). Waits longer than LONG_WAIT_SECONDS
are handed back (the drainer returns; `drain_due` re-enqueues it once it is due),
so a single drainer holds its worker for at most ~LONG_WAIT_SECONDS. For higher
lead volume, scale the drain-worker (replicas) or move to delay-based scheduling
(rq_scheduler) to avoid sleeping entirely.
"""

import time
from datetime import datetime, time as dt_time, timedelta

import frappe
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime
from frappe.utils.background_jobs import is_job_enqueued
from frappe.utils.safe_exec import call_with_form_dict

from crm.api.daily_standup import is_business_day
from crm.api import sequence_booking
from crm.api.sequence_status import check_before_step

# dedicated queue so sleeping drainers never block the main background worker
DRAIN_QUEUE = "seqdrain"
# the drainer sleeps waits up to this; longer waits are handed back to drain_due.
# Kept >= the 1-min scheduler interval so every step still fires on time (there
# is always a scheduler tick within this window before a step is due).
LONG_WAIT_SECONDS = 60
# drain_due enqueues an enrollment once it is due within this window. Must be
# <= LONG_WAIT_SECONDS, else a freshly-enqueued drainer would immediately hand
# the step back instead of sleeping for it.
LOOKAHEAD_SECONDS = 60
# safety cap on steps drained in one job (no real sequence chains this many
# sub-minute steps; guards against a misconfigured loop holding a worker open)
MAX_STEPS = 50
# Fail-safe: the engine catches step exceptions internally (logs "CRM Sequence
# Runner error") and leaves the enrollment Active-and-due, so before this
# existed a failing send (e.g. Quo out of prepaid credits, Jul 2026) was
# retried forever — flooding the Error Log and then blasting the whole stale
# backlog the moment the upstream problem cleared. Now a due run that advances
# nothing counts as a failure: the drain job stops (so retries happen once per
# drain_due tick, not in a hot loop) and after this many consecutive failures
# the enrollment is Paused and FAILSAFE_NOTIFY is emailed. Resume from the
# sequence's Enrollments list once the cause is fixed.
MAX_CONSECUTIVE_FAILURES = 10
FAILSAFE_NOTIFY = "lance.johnson@groundworkpro.com"

# Quiet hours. Step waits are relative to the PREVIOUS step, so a lead that
# arrived at 11pm would otherwise get its "1 day" follow-up text at 11pm, ten
# nights running. A timed Text/Call step that comes due outside
# [QUIET_START_HOUR, QUIET_END_HOUR) site-local (America/Chicago) is pushed to
# the next QUIET_START_HOUR instead. Only steps whose own wait is at least
# QUIET_MIN_WAIT_SECONDS are held: the wait-0 / few-second intro burst on a new
# lead is the point of the sequence and fires whenever the lead does. The
# schedule then drifts by one day once and stays inside the window after.
QUIET_START_HOUR = 8
QUIET_END_HOUR = 20
QUIET_MIN_WAIT_SECONDS = 3600
QUIET_STEP_TYPES = ("Text", "Call", "Task")

# Business days. The engine's waits are CALENDAR days, so a lead enrolled on a
# Wednesday had "day 4 of 10" land on Saturday and "day 5" on Sunday — two days
# nobody works — and the rep opened Monday to three overdue to-dos on one lead
# (Exe, 2026-09-14). Measured on prod that morning: 133 of the 224 open sequence
# tasks were due on a weekend, and 65 of 74 leads carried three or more. A step
# that lands on a weekend or a US federal holiday is rolled to the next business
# morning through the SAME `is_business_day` gate as the standup, board
# generation and the streak (site_config `crm_holidays` included), so they cannot
# disagree about what a working day is.
#
# Applied per STEP, to that step's own due date: nothing is skipped and no day of
# the cadence is dropped — it simply stretches across the weekend, which is what
# "1 week = 5 business days" has meant here since the standup was written. The
# wait-0 / sub-hour intro burst is exempt for the same reason quiet hours exempt
# it: a lead that arrives on Saturday is answered on Saturday.

_WAIT_SECONDS = {
	"Seconds": 1,
	"Minutes": 60,
	"Hours": 3600,
	"Days": 86400,
	"Weeks": 7 * 86400,
	"Months": 30 * 86400,
}


def _step_wait_seconds(step) -> int:
	return int(step.get("wait_value") or 0) * _WAIT_SECONDS.get(step.get("wait_unit") or "Days", 86400)


def quiet_hold_until(now, step):
	"""Pure: the datetime a step should be deferred to, or None to run now.

	`now` is site-local (frappe.utils.now_datetime). Held only for the step
	types that reach the seller, and only when the step's own wait is long
	enough to be a scheduled follow-up rather than part of an instant burst."""
	if not step or step.get("step_type") not in QUIET_STEP_TYPES:
		return None
	if _step_wait_seconds(step) < QUIET_MIN_WAIT_SECONDS:
		return None
	if QUIET_START_HOUR <= now.hour < QUIET_END_HOUR:
		return None
	open_today = now.replace(hour=QUIET_START_HOUR, minute=0, second=0, microsecond=0)
	if now.hour < QUIET_START_HOUR:
		return open_today
	return add_to_date(open_today, days=1)


def business_hold_until(due, step):
	"""Pure: `due` rolled forward to the next business morning, or None.

	None means leave it alone — either the step is exempt (not seller/rep-facing,
	or part of the intro burst) or `due` already lands on a working day. That
	makes this idempotent, which matters: `drain_due` re-aligns every active
	enrollment once a minute, and a rule computed from the CURRENT due date (not
	from `now`) cannot walk a step a day further into the future on each pass."""
	if not due or not step or step.get("step_type") not in QUIET_STEP_TYPES:
		return None
	if _step_wait_seconds(step) < QUIET_MIN_WAIT_SECONDS:
		return None
	day = getdate(due)
	if is_business_day(day):
		return None
	while not is_business_day(day):
		day += timedelta(days=1)
	return datetime.combine(day, dt_time(QUIET_START_HOUR, 0))


def hold_until(now, step):
	"""Pure: when this step may fire, or None for 'right now'.

	One answer to "is it a decent hour AND a working day", so the drainer and the
	next_run realignment cannot end up applying one rule without the other."""
	hold = quiet_hold_until(now, step)
	return business_hold_until(hold or now, step) or hold


def calendar_due(now, step):
	"""Pure: 8:00 on the calendar day a Days/Weeks wait lands, else None.

	The engine adds 24 hours, so a Monday 11:39 step comes due Tuesday 11:39 —
	after the 5am Today board has already been built, so yesterday's finished
	leads vanish until the afternoon. A 1-day wait means the next calendar
	morning, not +24h. Wait-0 / hours / minutes are left alone (the intro
	burst and quiet hours handle those)."""
	if not step:
		return None
	unit = step.get("wait_unit") or "Days"
	val = int(step.get("wait_value") or 0)
	if unit == "Weeks":
		val *= 7
	elif unit != "Days":
		return None
	if val <= 0:
		return None
	start = now.date() if hasattr(now, "date") else now
	target = start + timedelta(days=val)
	return datetime.combine(target, dt_time(QUIET_START_HOUR, 0))


def _next_step(enr):
	try:
		steps = frappe.get_cached_doc("CRM Sequence", enr.sequence).steps
		idx = int(enr.current_step or 0)
		return steps[idx] if idx < len(steps) else None
	except Exception:
		return None


def _run_core(enrollment):
	"""Run the API-type engine scoped to one enrollment — exactly what the
	sandbox `run_script("CRM Sequence Runner Core", enrollment=...)` does, so the
	actual send/skip logic stays in the single engine script."""
	call_with_form_dict(
		lambda: frappe.get_doc("Server Script", "CRM Sequence Runner Core").execute_method(),
		{"enrollment": enrollment},
	)


def _enqueue_drain(enrollment):
	"""Enqueue the (single) drainer for an enrollment, on the dedicated queue.
	is_job_enqueued covers QUEUED and STARTED, so a drainer already running for
	this enrollment is never duplicated — the core of the race-free guarantee."""
	job_id = "seqdrain:" + enrollment
	if not is_job_enqueued(job_id):
		frappe.enqueue(
			"crm.api.sequence_drain.drain",
			enrollment=enrollment,
			queue=DRAIN_QUEUE,
			job_id=job_id,
		)


def _lock_key(enrollment):
	return "seqdrain:" + enrollment


def drain(enrollment):
	"""Worker job (seqdrain queue): drive ONE enrollment through its due steps in
	real time, sleeping each step's real wait. Sole advancer of an enrollment."""
	# The job inherits the session that enqueued it. From an inbound webhook
	# that is GUEST, and CRM Task.after_insert -> assign_to rejects Guest after
	# the task row is already in: an orphan task per failed attempt (Richard
	# Vega, 2026-09-09: three "day 1" tasks). The engine's work is the site's,
	# not the caller's.
	frappe.set_user("Administrator")
	# One driver per enrollment, enforced in the database. job_id dedupe is a
	# check-then-enqueue and two drain_due ticks (or drain_lead + drain_due)
	# can slip through it; when they did, both ran step 1 and one lost the
	# enrollment save on TimestampMismatch AFTER its task insert had committed.
	got = frappe.db.sql("select get_lock(%s, 0)", _lock_key(enrollment))[0][0]
	if not got:
		return
	try:
		_drain_locked(enrollment)
	finally:
		try:
			frappe.db.sql("select release_lock(%s)", _lock_key(enrollment))
		except Exception:
			pass


def _drain_locked(enrollment):
	for _ in range(MAX_STEPS):
		enr = frappe.get_doc("CRM Sequence Enrollment", enrollment)
		if enr.status != "Active":
			return
		if enr.next_run:
			delta = (get_datetime(enr.next_run) - now_datetime()).total_seconds()
			if delta > LONG_WAIT_SECONDS:
				return  # long wait — drain_due re-enqueues this once it is due
			if delta > 0:
				time.sleep(delta)
		# Sequence-level status gate (crm/api/sequence_status.py): a lead that
		# has left the statuses this sequence runs in is Paused here, right
		# before the step would fire — the safety net behind the on_update hook.
		if not check_before_step(enr):
			return
		# A rep's booked follow-up after today trumps the sequence
		# (crm/api/sequence_booking.py): a chained sequence (New Lead 10-Day)
		# ends and hands the lead to the next one (Long-Term Follow-Up).
		if not sequence_booking.check_before_step(enr):
			return
		# Quiet hours + business days: a scheduled Text/Call/Task that comes due
		# at night, on a weekend or on a holiday waits for the next working
		# morning. Written to next_run so drain_due picks it up then.
		hold = hold_until(now_datetime(), _next_step(enr))
		if hold:
			frappe.db.set_value(
				"CRM Sequence Enrollment", enr.name, "next_run", hold, update_modified=False
			)
			frappe.db.commit()
			return
		before = (enr.current_step, str(enr.next_run), str(enr.modified))
		_run_core(enrollment)
		frappe.db.commit()
		enr = frappe.get_doc("CRM Sequence Enrollment", enrollment)
		if (enr.current_step, str(enr.next_run), str(enr.modified)) == before:
			# a due run that advanced nothing = the step raised (the engine
			# caught + logged it). Count it and stop this job — the next
			# drain_due tick retries, so failures accrue once a minute.
			if enr.status == "Active":
				_record_failure(enr)
			return
		_reset_failures(enr)
		_align_next_run(enr)


def _record_failure(enr):
	"""Bump the enrollment's consecutive-failure count; pause + notify at the
	threshold. No-ops when the fail_count field isn't provisioned yet, or when
	the sequence is disabled (the engine idles on those by design)."""
	if not frappe.db.has_column("CRM Sequence Enrollment", "fail_count"):
		return
	if not frappe.db.get_value("CRM Sequence", enr.sequence, "enabled"):
		return
	fails = (enr.get("fail_count") or 0) + 1
	if fails < MAX_CONSECUTIVE_FAILURES:
		frappe.db.set_value(
			"CRM Sequence Enrollment", enr.name, "fail_count", fails, update_modified=False
		)
		frappe.db.commit()
		return
	frappe.db.set_value(
		"CRM Sequence Enrollment",
		enr.name,
		{
			"status": "Paused",
			"fail_count": fails,
			"last_log": "{0} PAUSED by fail-safe: step {1} failed {2} runs in a row "
			"(see Error Log). Fix the cause, then set the enrollment back to Active.".format(
				now_datetime(), (enr.current_step or 0) + 1, fails
			),
		},
		update_modified=False,
	)
	frappe.db.commit()
	_notify_pause(enr, fails)


def _reset_failures(enr):
	"""Progress happened — clear the consecutive-failure count (if any)."""
	if enr.get("fail_count"):
		frappe.db.set_value(
			"CRM Sequence Enrollment", enr.name, "fail_count", 0, update_modified=False
		)
		frappe.db.commit()


def _notify_pause(enr, fails):
	"""Email the admin that an enrollment was paused. Never raises — a mail
	failure must not break the drainer."""
	try:
		lead_name = frappe.db.get_value("CRM Lead", enr.lead, "lead_name") or enr.lead
		lead_url = frappe.utils.get_url("/crm/leads/" + enr.lead)
		frappe.sendmail(
			recipients=[FAILSAFE_NOTIFY],
			subject="Sequence paused: {0} — {1}".format(enr.sequence, lead_name),
			message=(
				"<p>The sequence enrollment <b>{0}</b> (<b>{1}</b> for lead "
				'<a href="{2}">{3}</a>) was paused after step {4} failed '
				"{5} runs in a row.</p>"
				"<p>The step's error is in the site's Error Log (\"CRM Sequence "
				"Runner error\"). Fix the cause — e.g. Quo out of prepaid credits — "
				"then set the enrollment back to <b>Active</b> on the sequence's "
				"Enrollments list to resume where it left off, or <b>Stopped</b> "
				"if the queued messages are stale and shouldn't go out.</p>"
			).format(
				enr.name,
				enr.sequence,
				lead_url,
				lead_name,
				(enr.current_step or 0) + 1,
				fails,
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sequence fail-safe: pause email failed")


# Task/Call steps only create a CRM Task for the rep — they do not text the
# seller — so it is safe (and necessary) to pull them forward onto the morning
# board. SMS Text steps stay on their own clock / quiet hours.
BOARD_STEP_TYPES = ("Task", "Call")


def _align_next_run(enr):
	"""Move a scheduled step's next_run to when it should actually fire.

	Two corrections, in order: back to 8am of the calendar morning a Days/Weeks
	wait lands on (the engine adds a flat +24h, which lands mid-afternoon, after
	the 5am board is already built), then forward off a weekend or holiday. Both
	are derived from the step and its own due date rather than from `now`, so
	running this every minute over every active enrollment is a no-op once one is
	aligned — it can neither drift nor ratchet."""
	if not enr.next_run:
		return
	step = _next_step(enr)
	current = get_datetime(enr.next_run)
	target = current
	morning = calendar_due(now_datetime(), step)
	if morning and morning < target:
		target = morning
	target = business_hold_until(target, step) or target
	if target == current:
		return
	frappe.db.set_value(
		"CRM Sequence Enrollment", enr.name, "next_run", target, update_modified=False
	)
	frappe.db.commit()
	enr.next_run = target


def materialize_board_steps(day=None):
	"""Fire today's due-later Task/Call sequence steps now so the Today board
	can list them. Called at the start of board generation; SMS Text steps are
	left untouched. Returns how many enrollments were drained."""
	if not frappe.db.exists("DocType", "CRM Sequence Enrollment"):
		return 0
	day = getdate(day or now_datetime())
	eod = datetime.combine(day, datetime.max.time())
	now = now_datetime()
	rows = frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"status": "Active", "next_run": ["<=", eod]},
		fields=["name"],
	)
	n = 0
	for row in rows:
		enr = frappe.get_doc("CRM Sequence Enrollment", row.name)
		if enr.status != "Active":
			continue
		step = _next_step(enr)
		if not step or step.get("step_type") not in BOARD_STEP_TYPES:
			continue
		nr = get_datetime(enr.next_run) if enr.next_run else now
		if nr > now:
			frappe.db.set_value(
				"CRM Sequence Enrollment",
				enr.name,
				"next_run",
				now,
				update_modified=False,
			)
			frappe.db.commit()
		drain(enr.name)
		n += 1
	return n


def drain_lead(lead):
	"""Quick job (off the sleeping queue): enqueue a drainer for each Active
	enrollment of a freshly-enrolled lead. Run from a worker (after commit) so the
	enrollment the Sequence Auto Enroll server script creates in the lead's save
	transaction is already visible."""
	for e in frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"lead": lead, "status": "Active"},
		fields=["name"],
	):
		_enqueue_drain(e.name)


def drain_due():
	"""1-min scheduler backstop: enqueue a drainer for every Active enrollment due
	within the lookahead window (or with no next_run yet). Sole periodic driver —
	replaces the old `CRM Sequence Runner` core-cron, which is disabled."""
	# Snap leftover +24h waits down to 8am of that morning so they become due
	# with the Today board instead of mid-afternoon.
	for row in frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"status": "Active"},
		fields=["name", "sequence", "current_step", "next_run"],
	):
		try:
			_align_next_run(row)
		except Exception:
			pass
	soon = add_to_date(now_datetime(), seconds=LOOKAHEAD_SECONDS)
	rows = frappe.get_all(
		"CRM Sequence Enrollment",
		filters={"status": "Active"},
		or_filters=[["next_run", "<=", soon], ["next_run", "is", "not set"]],
		fields=["name"],
	)
	for e in rows:
		_enqueue_drain(e.name)


def enqueue_for_lead(doc, method=None):
	"""CRM Lead after_insert / on_update doc-event: when a lead's source is set or
	changed (the same trigger as auto-enroll), enqueue an instant drainer for it
	after commit. job_id dedupes concurrent saves of the same lead."""
	if doc.source and doc.has_value_changed("source"):
		frappe.enqueue(
			"crm.api.sequence_drain.drain_lead",
			lead=doc.name,
			enqueue_after_commit=True,
			job_id="seqdrain-lead:" + doc.name,
			queue="short",
		)

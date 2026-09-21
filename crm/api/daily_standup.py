# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Cadence for the Today board, plus the 5am recap DM.

ONE definition of "what has to happen today". The Today board is generated from
it at 5am; `get_standup_lead_names` still drills the same set into CRM Leads.
The Mattermost DM is **not** that list anymore — Lance asked it off. The job
still materialises the board, then DMs a four-line recap of *yesterday*
(streak / cards per person / done+skip outcomes). The previous attempt at this
report was abandoned because its lists were wrong ("the due list had 33 leads,
but most were Dead Lead"), so every rule here is written down and every
exclusion is explicit.

The cadence is Dennis's, posted in the Acq channel 2026-07-31:

    1. Call/Text 2x's per day for 1 week
    2. After the first week, call once per week for 3 weeks.
    3. Then move to once a month until they tell us to stop.

with two clarifications from Lance:

  * "Call/Text" means call AND text, but **only calls are metered here**. Texts
    are fast and don't compete for the same capacity, so they never gate a lead
    and never count against the daily number.
  * "1 week" means **5 BUSINESS days**. Nobody calls on weekends (the call log
    is flat every Sat/Sun), so counting calendar days silently burned ~2 days of
    a lead's most valuable week. Business-day counting also makes a lead cost 13
    calls in month one instead of 17.

Suppression rule (Lance): a lead with an open task whose due date is in the
FUTURE is scheduled, and is therefore NOT on today's list — the cadence only
governs leads nobody has explicitly booked. A task due today or overdue puts the
lead ON the list. This is what stops the report telling a rep to cold-dial a
seller Dennis already booked for Tuesday.

Roles, not owners. `lead_owner`/`_assign` say Dennis owns ~99% of leads, but the
calling is done by German and Exe while Dennis closes. Splitting by owner would
hand the setters an empty list, so the report emits ONE shared setter queue plus
a separate closer list.
"""

import json
import re
from datetime import datetime, timedelta
from functools import lru_cache

import frappe
import requests
from frappe.utils import get_datetime, getdate, now_datetime

# ── cadence ────────────────────────────────────────────────────────────────────

#: statuses that used to be the whole chase set. Kept as a fallback if CRM Lead
#: Status rows are missing, and as the standup's "early pipeline" grouping.
CHASE_STATUSES = ("New", "Called No Answer", "Follow Up", "Future Follow Up")

#: statuses Dennis works (closest-to-closing first — this order is the report order)
CLOSER_STATUSES = ("Contract Sent", "Make Offer", "Underwriting")

#: Under contract / disposition. Contract Sent is still closer work on Today;
#: Photos & Lockbox and after is not the setters' board (Lance, 2026-09-11).
#: "Signed Contract" was retired 2026-09-22 — signed deals rest in Contract
#: Sent until photos start. Keep in sync with investorlift_ingest.DISPO_LEAD_STATUSES.
POST_CONTRACT_STATUSES = (
	"Photos & Lockbox In Progress",
	"Submit to Dispo",
	"Dispo Accepted",
	"Buyer Assigned",
)

#: A lead in one of these CRM Lead Status.types is done — cadence must not
#: resurrect it. Lost is Dead/Not Interested; Won is a closed deal.
TERMINAL_STATUS_TYPES = ("Lost", "Won")
#: `closer` = a deal in flight (CLOSER_STATUSES) with no follow-up scheduled.
#: It surfaces EVERY day until someone books the next step — the ask was that a
#: lead in Underwriting / Make Offer / Contract Sent can never quietly sit
#: without a plan, which the weekly/monthly sweeps let happen.
#: Only `closer` still generates cards from the board itself; the old ladder
#: phases are kept so cards created before 2026-09-09 keep their labels.
CADENCE_PHASES = ("never", "week1", "week1_partial", "weekly", "monthly", "closer", "nudge")

#: "Text Joe — day 3 of 10" on a sequence Task. Used to pick the CURRENT day's
#: work over leftover yesterday tasks when both are still open.
_DAY_IN_TITLE = re.compile(r"\bday\s+(\d+)\s+of\s+(\d+)\b", re.I)


def sequence_day(title):
	"""Pure: the N in 'day N of M', else 0."""
	m = _DAY_IN_TITLE.search(title or "")
	return int(m.group(1)) if m else 0


def is_sequence_call_task(title):
	"""Pure: a sequence Call step titles the task 'Call {lead_name}'."""
	t = (title or "").strip()
	return t.lower().startswith("call ") and not sequence_day(t)


def with_triple_dial(title, has_call):
	"""Pure: name a text-day task as a triple-dial day when a Call task is also open."""
	title = title or ""
	if has_call and sequence_day(title) and "triple dial" not in title.lower():
		return title + " · triple dial"
	return title


def _task_title(task):
	if task is None:
		return ""
	if isinstance(task, dict):
		return task.get("title") or ""
	return getattr(task, "title", None) or ""


def board_task_rows(tasks):
	"""Pure: Today-card rows — current-day sequence text, then the Call (triple dial).

	`tasks` should already be sorted highest day-N first, then soonest due.
	Leftover day-1 texts lose to day 2; a Call step is its own row so the card
	shows both jobs on a triple-dial day."""
	text = call = other = None
	for task in tasks or []:
		title = _task_title(task)
		if is_sequence_call_task(title):
			if call is None:
				call = task
			continue
		if sequence_day(title):
			if text is None:
				text = task
			continue
		if other is None:
			other = task
	rows = []
	if text:
		rows.append(text)
	elif other:
		rows.append(other)
	if call:
		rows.append(call)
	return rows

#: phase 1 — first N business days after first contact, 2 calls per business day
PHASE1_BUSINESS_DAYS = 5
PHASE1_CALLS_PER_DAY = 2

#: phase 2 — through business-day N, one call per business week
PHASE2_BUSINESS_DAYS = 20
PHASE2_INTERVAL = 5

#: phase 3 — one call per business month
PHASE3_INTERVAL = 22

#: calls a lead costs in its first month (10 in week 1 + 3 weekly) — the divisor
#: behind the "how many new leads can we take" number.
CALLS_PER_LEAD_MONTH1 = PHASE1_BUSINESS_DAYS * PHASE1_CALLS_PER_DAY + 3

#: obvious non-leads that must never reach a call list
EXCLUDE_LEAD_NAMES = ("lance test", "test lead for agreement")

#: who gets the DM
DEFAULT_DM_USER = "lancejohnson"
DEFAULT_MM_BASE = "https://app.groundworkpro.com/mattermost/api/v4"


# ── business-day helpers ───────────────────────────────────────────────────────


def _nth_weekday(year, month, weekday, n):
	"""The n-th `weekday` (Mon=0) of a month; n=-1 means the last one."""
	if n > 0:
		first = datetime(year, month, 1).date()
		offset = (weekday - first.weekday()) % 7
		return first + timedelta(days=offset + 7 * (n - 1))
	nxt = datetime(year + (month == 12), (month % 12) + 1, 1).date()
	last = nxt - timedelta(days=1)
	return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d):
	"""Federal observance: a Saturday holiday is taken Friday, a Sunday one Monday."""
	if d.weekday() == 5:
		return d - timedelta(days=1)
	if d.weekday() == 6:
		return d + timedelta(days=1)
	return d


@lru_cache(maxsize=16)
def us_federal_holidays(year) -> frozenset:
	"""The eleven US federal holidays for `year`, as observed.

	Computed, not fetched — no `holidays` package in the Frappe image, and the
	rules have not changed since Juneteenth was added in 2021.
	"""
	fixed = [(1, 1), (6, 19), (7, 4), (11, 11), (12, 25)]
	days = {_observed(datetime(year, m, d).date()) for m, d in fixed}
	days |= {
		_nth_weekday(year, 1, 0, 3),  # MLK Day
		_nth_weekday(year, 2, 0, 3),  # Presidents' Day
		_nth_weekday(year, 5, 0, -1),  # Memorial Day
		_nth_weekday(year, 9, 0, 1),  # Labor Day
		_nth_weekday(year, 10, 0, 2),  # Columbus Day
		_nth_weekday(year, 11, 3, 4),  # Thanksgiving
	}
	return frozenset(days)


def _extra_holidays() -> set:
	"""site_config `crm_holidays`: a list of ISO dates the team also takes off
	(company days, the Friday after Thanksgiving). Malformed → none."""
	try:
		raw = frappe.conf.get("crm_holidays") or []
		if isinstance(raw, str):
			raw = json.loads(raw)
		return {getdate(x) for x in raw}
	except Exception:
		return set()


def is_holiday(d) -> bool:
	d = getdate(d)
	return d in us_federal_holidays(d.year) or d in _extra_holidays()


def is_business_day(d) -> bool:
	"""Weekday and not a holiday.

	Holidays matter for the same reason weekends do: the call log is flat, so a
	board generated on Labor Day breaks the streak for work nobody was asked to
	do. Every business-day helper below routes through this one so the
	standup, the pulse, board generation and the streak cannot disagree.
	"""
	d = getdate(d)
	return d.weekday() < 5 and not is_holiday(d)


def business_days_between(a, b) -> int:
	"""Whole business days from `a` to `b` (weekends excluded, endpoints exclusive
	of the start). Returns a large number when `a` is missing, so 'never called'
	sorts as maximally overdue."""
	if a is None:
		return 9999
	a, b = getdate(a), getdate(b)
	if a > b:
		return 0
	n, cur = 0, a
	while cur < b:
		cur += timedelta(days=1)
		if is_business_day(cur):
			n += 1
	return n


def previous_business_day(d):
	d = getdate(d) - timedelta(days=1)
	while not is_business_day(d):
		d -= timedelta(days=1)
	return d


# ── the list ───────────────────────────────────────────────────────────────────


def _live_lead_filter() -> str:
	"""Parked import leads are invisible everywhere else (the board, the
	dashboard); they must be invisible here too or the list is instantly wrong."""
	if frappe.db.has_column("CRM Lead", "import_hidden"):
		return " and (l.import_hidden is null or l.import_hidden != 1)"
	return ""


def _tracked_statuses():
	"""Every live status — not just New / Called No Answer / Follow Up.

	Cadence still decides who is DUE today; this only decides who is eligible.
	Keyed on CRM Lead Status.type so a renamed or new pipeline status is
	picked up without a code change.
	"""
	try:
		names = frappe.get_all(
			"CRM Lead Status",
			filters={"type": ["not in", list(TERMINAL_STATUS_TYPES)]},
			pluck="name",
		)
	except Exception:
		names = []
	if not names:
		return CHASE_STATUSES
	return tuple(n for n in names if n not in POST_CONTRACT_STATUSES)


def _fetch_chase_rows(today):
	"""Every live lead with the three facts the cadence needs: when we
	first tried, when we last called, and how many calls it has had today."""
	statuses = _tracked_statuses()
	rows = frappe.db.sql(
		f"""
		select l.name, l.lead_name, l.status, l.creation, l.property_address,
		       l.lead_owner, l.mobile_no
		from `tabCRM Lead` l
		where l.status in %(statuses)s
		  and (l.converted is null or l.converted != 1)
		  {_live_lead_filter()}
		""",
		{"statuses": statuses},
		as_dict=True,
	)
	rows = [r for r in rows if (r.lead_name or "").strip().lower() not in EXCLUDE_LEAD_NAMES]
	if not rows:
		return []

	names = [r.name for r in rows]

	calls = frappe.db.sql(
		"""
		select reference_docname n, min(creation) first_call, max(creation) last_call,
		       sum(case when date(creation) = %(today)s then 1 else 0 end) calls_today
		from `tabCRM Call Log`
		where reference_doctype = 'CRM Lead' and reference_docname in %(names)s
		group by reference_docname
		""",
		{"names": names, "today": today},
		as_dict=True,
	)
	call_map = {c.n: c for c in calls}

	# A task after today is the suppression signal. A task later *today* still
	# belongs on today's list; a tomorrow-or-later appointment trumps cadence.
	tasks = frappe.db.sql(
		"""
		select reference_docname n,
		       min(case when due_date > %(eod)s then due_date end) next_future_due,
		       sum(case when due_date is not null and due_date <= %(eod)s then 1 else 0 end) due_now,
		       substring_index(
		         group_concat(
		           case when due_date is not null and due_date <= %(eod)s then title end
		           order by creation desc
		           separator '\n'
		         ),
		         '\n', 1
		       ) due_title
		from `tabCRM Task`
		where reference_doctype = 'CRM Lead' and reference_docname in %(names)s
		  and status not in ('Done', 'Canceled')
		group by reference_docname
		""",
		{
			"names": names,
			"eod": datetime.combine(getdate(today), datetime.max.time()),
		},
		as_dict=True,
	)
	task_map = {t.n: t for t in tasks}

	# Last CONTACT is calls and texts, either direction — the number the nudge
	# card prints ("12 days since last contact"). A text the seller sent is
	# contact; a voicemail we left is at least an attempt. Guarded on the Quo
	# Message doctype so a site without texting still builds a board.
	text_map = {}
	if frappe.db.exists("DocType", "Quo Message"):
		for t in frappe.db.sql(
			"""
			select reference_docname n, max(coalesce(message_date, creation)) last_text
			from `tabQuo Message`
			where reference_doctype = 'CRM Lead' and reference_docname in %(names)s
			  and coalesce(status, '') not in ('scheduled', 'canceled')
			group by reference_docname
			""",
			{"names": names},
			as_dict=True,
		):
			text_map[t.n] = t.last_text

	# A lead an Active sequence is driving gets its work from the sequence's
	# tasks; the board must not nag it for a next step on top.
	in_sequence = set()
	if frappe.db.exists("DocType", "CRM Sequence Enrollment"):
		in_sequence = set(
			frappe.get_all(
				"CRM Sequence Enrollment",
				filters={"status": "Active", "lead": ["in", names]},
				pluck="lead",
			)
		)

	for r in rows:
		c = call_map.get(r.name)
		t = task_map.get(r.name)
		r.first_call = c.first_call if c else None
		r.last_call = c.last_call if c else None
		r.calls_today = int(c.calls_today or 0) if c else 0
		r.next_future_due = t.next_future_due if t else None
		r.tasks_due_now = int(t.due_now or 0) if t else 0
		r.due_task_title = t.due_title if t else None
		last_text = text_map.get(r.name)
		r.last_contact = max([d for d in (r.last_call, last_text) if d], default=None)
		r.in_sequence = r.name in in_sequence
	return rows


def last_contact_label(row, today) -> str:
	"""Pure: "3 days since last contact" / "last contact today" / "never contacted"."""
	last = getattr(row, "last_contact", None) or getattr(row, "last_call", None)
	if not last:
		return "never contacted"
	days = (getdate(today) - getdate(last)).days
	if days <= 0:
		return "last contact today"
	if days == 1:
		return "1 day since last contact"
	return f"{days} days since last contact"


def _classify(row, today):
	"""Return (phase, calls_needed, due, reason) for one chase lead.

	Never-called leads stay first. A due task is the next priority, ahead of the
	cadence follow-up passes. Any task after today suppresses the card entirely,
	even when an older task is still overdue: the later appointment is the rep's
	current plan and must trump the automatic cadence.
	"""
	# scheduled after today -> not today's problem
	if row.next_future_due:
		return ("scheduled", 0, False,
		        f"booked {frappe.utils.format_datetime(row.next_future_due, 'd MMM')}")

	# Photos & Lockbox and later is dispo, not the calling list — even a due
	# task on it must not mint a Today card.
	if row.status in POST_CONTRACT_STATUSES:
		return ("dispo", 0, False, row.status)

	# A deal in flight with no next step booked is due every single day. This
	# outranks the call ladder on purpose: by the time a lead is in Underwriting
	# it has been reached, so "never called" / "2 calls today" say nothing
	# useful about it — what matters is that nobody has written down what
	# happens next. Booking any task after today clears it (the branch above).
	ago = last_contact_label(row, today)

	if row.status in CLOSER_STATUSES:
		reason = f"{row.status} · no next step · {ago}"
		if row.tasks_due_now:
			title = (row.due_task_title or "").strip()
			reason = f"{row.status} · " + (f"task: {title}" if title else "task due") + f" · {ago}"
		return ("closer", 1, True, reason)

	# The board's own call ladder (never-called → 2/day week 1 → weekly →
	# monthly) is GONE (Lance, 2026-09-09: "the only cadence we want now are in
	# the sequences"). Three things make a card: a task due today (which is
	# what the New Lead 10-Day sequence produces), a deal in flight with
	# nothing booked (above), and — since the same day — a live lead that NO
	# sequence is driving and that has no dated next step. That last one is
	# the poke: it comes back every day, naming how long since last contact,
	# until someone books a task after today.
	if row.tasks_due_now:
		title = (row.due_task_title or "").strip()
		generic = title.lower() in ("", "follow up", "follow up call", "call back", "call")
		task_reason = "task due" if generic else f"task: {title}"
		return ("task", 1, True, f"{task_reason} · {ago}")

	if getattr(row, "in_sequence", False):
		return ("sequence", 0, False, f"in a sequence · {ago}")

	return ("nudge", 1, True, f"no next step · {ago}")


#: display order — never-called first, then explicit due tasks, then cadence.
_PHASE_RANK = {"closer": 0, "never": 1, "task": 2, "nudge": 3, "week1": 4, "weekly": 5, "monthly": 6}

_PHASE_LABEL = {
	"closer": "Deal in flight — no follow-up scheduled",
	"nudge": "No next step — book one",
	"never": "Never called",
	"week1": "Week 1 — 2 calls/day",
	"weekly": "Weekly sweep",
	"monthly": "Monthly sweep",
	"task": "Task due (not yet due by cadence)",
}


def build_setter_queue(today=None):
	today = getdate(today or now_datetime())
	out = []
	for r in _fetch_chase_rows(today):
		phase, need, due, reason = _classify(r, today)
		r.phase, r.calls_needed, r.due, r.reason = phase, need, due, reason
		r.days_silent = business_days_between(r.last_call, today) if r.last_call else 9999
		out.append(r)
	due_rows = [r for r in out if r.due]
	due_rows.sort(key=lambda r: (_PHASE_RANK.get(r.phase, 9), -r.days_silent, r.lead_name or ""))
	return {"all": out, "due": due_rows}


def build_closer_list(today=None):
	"""Dennis's side: everything close to money, ordered closest-first, with the
	reason it needs attention today."""
	today = getdate(today or now_datetime())
	has_dd = frappe.db.has_column("CRM Lead", "dd_expiration_date")
	dd = "l.dd_expiration_date" if has_dd else "null"
	rows = frappe.db.sql(
		f"""
		select l.name, l.lead_name, l.status, l.property_address, {dd} dd_expiration_date,
		       l.modified
		from `tabCRM Lead` l
		where l.status in %(statuses)s
		  and (l.converted is null or l.converted != 1)
		  {_live_lead_filter()}
		""",
		{"statuses": CLOSER_STATUSES},
		as_dict=True,
	)
	if not rows:
		return []
	names = [r.name for r in rows]

	act = frappe.db.sql(
		"""
		select n, max(ts) last_touch from (
		  select reference_docname n, max(creation) ts from `tabCRM Call Log`
		    where reference_doctype='CRM Lead' and reference_docname in %(names)s group by n
		  union all
		  select reference_docname n, max(creation) ts from `tabQuo Message`
		    where reference_doctype='CRM Lead' and reference_docname in %(names)s group by n
		) x group by n
		""",
		{"names": names},
		as_dict=True,
	) if frappe.db.exists("DocType", "Quo Message") else frappe.db.sql(
		"""
		select reference_docname n, max(creation) last_touch from `tabCRM Call Log`
		where reference_doctype='CRM Lead' and reference_docname in %(names)s group by n
		""",
		{"names": names},
		as_dict=True,
	)
	touch = {a.n: a.last_touch for a in act}

	tasks = frappe.db.sql(
		"""
		select reference_docname n, count(*) due_now, min(title) title
		from `tabCRM Task`
		where reference_doctype='CRM Lead' and reference_docname in %(names)s
		  and status not in ('Done','Canceled') and due_date is not null and due_date <= %(eod)s
		group by reference_docname
		""",
		{"names": names, "eod": datetime.combine(today, datetime.max.time())},
		as_dict=True,
	)
	task_map = {t.n: t for t in tasks}

	for r in rows:
		r.last_touch = touch.get(r.name)
		r.days_silent = business_days_between(r.last_touch, today) if r.last_touch else 9999
		t = task_map.get(r.name)
		r.task_due = t.title if t else None
		flags = []
		if r.task_due:
			flags.append(f"task due: {r.task_due}")
		if r.dd_expiration_date:
			left = (getdate(r.dd_expiration_date) - today).days
			if left <= 3:
				flags.append(f"**DD {'expired' if left < 0 else 'expires in ' + str(left) + 'd'}**")
		if r.days_silent >= 3:
			flags.append("no contact " + ("ever" if r.days_silent > 900 else f"in {r.days_silent} business days"))
		r.flags = flags
	rows.sort(key=lambda r: (CLOSER_STATUSES.index(r.status), -r.days_silent))
	return rows


def build_capacity(today=None):
	"""Yesterday's calling vs what the cadence owes today, and what that implies
	for how many new leads we can actually take on."""
	today = getdate(today or now_datetime())
	prev = previous_business_day(today)
	per_rep = frappe.db.sql(
		"""
		select caller, count(*) calls,
		       sum(case when duration >= 60 then 1 else 0 end) conversations
		from `tabCRM Call Log`
		where type='Outgoing' and reference_doctype='CRM Lead'
		  and date(creation) = %(d)s and caller is not null and caller != ''
		group by caller order by calls desc
		""",
		{"d": prev},
		as_dict=True,
	)
	return {
		"prev_day": prev,
		"per_rep": per_rep,
		"calls_made": sum(r.calls for r in per_rep),
		"conversations": sum(r.conversations or 0 for r in per_rep),
	}


def build_standup(today=None):
	today = getdate(today or now_datetime())
	setter = build_setter_queue(today)
	closer = build_closer_list(today)
	cap = build_capacity(today)

	by_status = {}
	for r in setter["all"]:
		by_status[r.status] = by_status.get(r.status, 0) + 1
	closer_counts = {}
	for r in closer:
		closer_counts[r.status] = closer_counts.get(r.status, 0) + 1

	calls_owed = sum(r.calls_needed for r in setter["due"])
	headroom = max(0, cap["calls_made"] - calls_owed)
	return {
		"date": today,
		"setter": setter,
		"closer": closer,
		"capacity": cap,
		"by_status": by_status,
		"closer_counts": closer_counts,
		"calls_owed": calls_owed,
		"intake": round(max(0, (cap["calls_made"] - 9)) / CALLS_PER_LEAD_MONTH1, 1),
		"headroom": headroom,
	}


# ── rendering ──────────────────────────────────────────────────────────────────

CRM = "https://crm.groundworkpro.com/crm"


def _lead_link(r):
	return f"[{r.lead_name or r.name}]({CRM}/leads/{r.name})"


def render_markdown(d, limit=25):
	today = d["date"]
	L = []
	L.append(f"## Standup — {today.strftime('%a %-d %b %Y')}")

	bs, cc = d["by_status"], d["closer_counts"]
	status_bits = [f"**{name}** {count}" for name, count in sorted(bs.items(), key=lambda kv: (-kv[1], kv[0]))]
	if status_bits:
		L.append(" · ".join(status_bits))
	else:
		L.append("_No live leads in the cadence._")
	closer_bits = [f"**{name}** {count}" for name, count in sorted(cc.items(), key=lambda kv: (-kv[1], kv[0]))]
	if closer_bits:
		L.append("Closer: " + " · ".join(closer_bits))

	# 1. closest to closing first
	closer = [r for r in d["closer"] if r.flags]
	L.append(f"\n### :moneybag: Dennis — {len(closer)} need attention")
	if not closer:
		L.append("_Nothing flagged. Everything close to money was touched recently._")
	for r in closer:
		L.append(f"- **{r.status}** · {_lead_link(r)} — " + "; ".join(r.flags))

	# 2. the shared calling queue, grouped so the top of the list is the right
	#    work rather than whichever 25 rows happened to sort first
	due = d["setter"]["due"]
	L.append(f"\n### :telephone_receiver: Calling queue — {len(due)} leads, {d['calls_owed']} calls")
	if not due:
		L.append("_Nobody is due. Everything live is either called today, booked, or not yet on cadence._")

	per_group = {"never": 99, "week1": 12, "weekly": 8, "monthly": 5, "task": 5}
	for phase in ("never", "week1", "weekly", "monthly", "task"):
		group = [r for r in due if r.phase == phase]
		if not group:
			continue
		calls = sum(r.calls_needed for r in group)
		head = f"\n**{_PHASE_LABEL[phase]}** — {len(group)} leads, {calls} calls"
		if phase == "never":
			head += "  :warning: _start here_"
		L.append(head)
		cap = per_group[phase]
		for r in group[:cap]:
			L.append(f"- {_lead_link(r)} · {r.status} · needs {r.calls_needed} — {r.reason}")
		if len(group) > cap:
			L.append(f"- _…and {len(group) - cap} more_")

	# 3. capacity / intake
	cap = d["capacity"]
	L.append(f"\n### :chart_with_upwards_trend: Capacity")
	who = ", ".join(f"{r.caller.split('@')[0]} {r.calls}" for r in cap["per_rep"]) or "nobody logged a call"
	L.append(
		f"Last business day ({cap['prev_day'].strftime('%a %-d %b')}): **{cap['calls_made']} calls**, "
		f"**{int(cap['conversations'])} real conversations** ({who})."
	)
	L.append(
		f"Today the cadence owes **{d['calls_owed']} calls**. At that rate we can carry "
		f"**~{d['intake']} new leads per business day**."
	)

	# 4. what the paid data is costing, and whether it is about to stop working.
	# One line: this DM is the list Lance runs the morning call from, so spend is
	# context rather than the point. It earns its place because BatchData is PREPAID
	# and its failure mode is silent — the wallet ran to $0.26 with reps' tax pulls
	# 403ing for a week before anyone noticed.
	try:
		from crm.api import batchdata_wallet

		line = batchdata_wallet.standup_line()
		if line:
			L.append(f"\n{line}")
	except Exception:
		# A spend line must never cost anyone their morning call list.
		frappe.log_error(frappe.get_traceback(), "standup: BatchData line failed")
	return "\n".join(L)


# ── delivery ───────────────────────────────────────────────────────────────────


def _mm_conf():
	base = (frappe.conf.get("mattermost_base") or DEFAULT_MM_BASE).rstrip("/")
	return base, frappe.conf.get("mattermost_token"), (
		frappe.conf.get("standup_dm_user") or DEFAULT_DM_USER
	)


def _mm(path, token, base, method="GET", body=None):
	r = requests.request(
		method,
		base + path,
		headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
		data=json.dumps(body) if body is not None else None,
		timeout=20,
	)
	r.raise_for_status()
	return r.json()


def post_to_talk(text, channel="standup", label="Standup"):
	"""ALSO land a bot post in Talk, when Talk exists (ops setup_talk.py).

	The Mattermost DM is kept during the transition; this is additive. Written
	with mirror=False — the DM IS the Mattermost copy, and mirroring the channel
	post too would put it in Mattermost twice. Never raises."""
	try:
		from crm.api.talk import post_as_bot

		return post_as_bot(channel, text, author_label=label, mirror=False)
	except Exception:
		frappe.log_error(title="standup: Talk post failed", message=frappe.get_traceback())
		return None


def send_dm(text):
	"""DM the standup to the configured user as the `pi` bot. Returns the post id.

	Absent a token this is a no-op rather than an error, so the feature lies
	dormant on any site that has not been configured (same shape as the contract
	parser's `notify_mini`). Shared with the BatchData wallet alert, so Talk
	delivery is NOT here — each caller names its own channel."""
	base, token, user = _mm_conf()
	if not token:
		frappe.log_error(title="standup: no mattermost_token in site_config", message="skipped DM")
		return None
	me = _mm("/users/me", token, base)
	target = _mm(f"/users/username/{user}", token, base)
	ch = _mm("/channels/direct", token, base, "POST", [me["id"], target["id"]])
	post = _mm("/posts", token, base, "POST", {"channel_id": ch["id"], "message": text})
	return post["id"]


def send_daily_standup():
	"""Scheduler entry point — 5am CT on business days.

	Still generates today's board first. The DM is yesterday's recap, not the
	calling list. Wrapped so a delivery failure is logged rather than crashing
	the scheduler (which would silently take the whole cron slot down)."""
	try:
		today = getdate(now_datetime())
		if not is_business_day(today):
			return
		# materialise the shared Today board first, so the board the reps open is
		# already populated when they sit down — the DM no longer describes it
		try:
			from crm.api.today_board import generate_today

			generate_today(today)
		except Exception:
			frappe.log_error(
				title="standup: could not generate Today board", message=frappe.get_traceback()
			)
		from crm.api.today_board import render_yesterday_recap, yesterday_recap
		from crm.api.spend_report import render_spend

		text = render_yesterday_recap(yesterday_recap(today))
		spend = render_spend(commit_snapshot=True)
		if spend:
			text = f"{text}\n\n{spend}"
		post_to_talk(text)
		send_dm(text)
	except Exception:
		frappe.log_error(title="standup: send_daily_standup failed", message=frappe.get_traceback())


@frappe.whitelist()
def preview_standup(today=None, send=0, note=None):
	"""Dry run of the 5am recap DM. Only actually DMs when send=1. `note` prefixes
	the DM so a preview can never be mistaken for the real 5am post.

	`today` is the morning the job would run (so Friday recaps Thursday). The old
	calling-list markdown is still `render_markdown(build_standup())` if needed.
	"""
	from crm.api.today_board import render_yesterday_recap, yesterday_recap
	from crm.api.spend_report import render_spend

	data = yesterday_recap(getdate(today) if today else None)
	text = render_yesterday_recap(data)
	spend = render_spend(commit_snapshot=bool(int(send or 0)))
	if spend:
		text = f"{text}\n\n{spend}"
	if note:
		text = f"_{note}_\n\n{text}"
	if int(send or 0):
		post_to_talk(text)
	post = send_dm(text) if int(send or 0) else None
	return {
		"markdown": text,
		"sent": bool(post),
		"post_id": post,
		"recap": data,
	}


@frappe.whitelist()
def get_standup_lead_names(bucket="setter", today=None):
	"""The same list the DM is built from, as bare lead names, so the CRM Leads
	view can drill to exactly what standup discussed. This is why the report and
	the board cannot disagree — one definition, two renderers."""
	data = build_standup(getdate(today) if today else None)
	if bucket == "closer":
		return [r.name for r in data["closer"] if r.flags]
	if bucket == "never":
		return [r.name for r in data["setter"]["due"] if r.days_silent > 900]
	return [r.name for r in data["setter"]["due"]]

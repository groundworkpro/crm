"""Groundwork custom leads dashboard.

A focused, single-call dashboard answering three questions:
  1. New leads per day over the selected range (+ a total).
  2. Status changes for leads — how many entered vs. exited each stage.
  3. New leads broken down by source.

Status-change data comes from the `CRM Status Change Log` child table that the
Lead doctype writes to on every status change (see crm_lead.py -> add_status_change_log).
Each completed row means: the lead entered `from` at `from_date` and left it for
`to` at `to_date` — i.e. one "exited `from`" event and one "entered `to`" event,
both stamped at `to_date`.
"""

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count, Date, Sum

from crm.api.dashboard import get_leads_by_source
from crm.utils import sales_user_only


def live(query, Lead):
	"""Drop parked, bulk-imported leads (`import_hidden`) from a dashboard query.

	The Leads board already hides a freshly imported batch until someone promotes
	it (crm.api.lead_import.apply_import_visibility). The dashboard has to agree:
	otherwise a 500-row LeadPack that nobody is allowed to see on the board still
	lands on the landing page the day it's imported, spiking "New leads",
	inventing a status cohort and burying the real numbers.

	Guarded by has_column so this is safe before the ops script adds the field.
	NULL is treated as visible — an older lead predating the custom field is not
	a parked one.
	"""
	if not frappe.db.has_column("CRM Lead", "import_hidden"):
		return query
	return query.where(Lead.import_hidden.isnull() | (Lead.import_hidden != 1))


@frappe.whitelist()
@sales_user_only
def get_leads_dashboard(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Return all three datasets for the Groundwork leads dashboard in one call."""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())

	# Sales users only ever see their own leads, regardless of the passed-in user.
	roles = frappe.get_roles(frappe.session.user)
	is_sales_manager = "Sales Manager" in roles or "System Manager" in roles
	if "Sales User" in roles and not is_sales_manager:
		user = frappe.session.user

	return {
		"new_leads_trend": _new_leads_trend(from_date, to_date, user),
		"status_changes": _status_changes(from_date, to_date, user),
		"leads_by_source": get_leads_by_source(from_date, to_date, user),
		"summary": _summary(from_date, to_date, user),
	}


def _summary(from_date, to_date, user):
	"""Two headline numbers: new leads created, and status changes recorded."""
	Lead = DocType("CRM Lead")
	leads_q = (
		frappe.qb.from_(Lead)
		.select(Count("*").as_("count"))
		.where(Date(Lead.creation).between(from_date, to_date))
	)
	leads_q = live(leads_q, Lead)
	if user:
		leads_q = leads_q.where(Lead.lead_owner == user)
	new_leads = (leads_q.run(as_dict=True)[0].count) or 0

	SCL = DocType("CRM Status Change Log")
	changes_q = (
		frappe.qb.from_(SCL)
		.join(Lead)
		.on(SCL.parent == Lead.name)
		.select(Count("*").as_("count"))
		.where(
			(SCL.parenttype == "CRM Lead")
			& (SCL.to.isnotnull())
			& (SCL.to != "")
			& (Date(SCL.to_date).between(from_date, to_date))
		)
	)
	changes_q = live(changes_q, Lead)
	if user:
		changes_q = changes_q.where(Lead.lead_owner == user)
	status_changes = (changes_q.run(as_dict=True)[0].count) or 0

	return [
		{
			"title": _("New leads"),
			"tooltip": _("Leads created in the selected range"),
			"value": new_leads,
		},
		{
			"title": _("Status changes"),
			"tooltip": _("Stage transitions recorded for leads in the selected range"),
			"value": status_changes,
		},
	]


def _new_leads_trend(from_date, to_date, user):
	"""Daily count of leads created, zero-filled across the whole range."""
	Lead = DocType("CRM Lead")
	query = (
		frappe.qb.from_(Lead)
		.select(Date(Lead.creation).as_("date"), Count("*").as_("leads"))
		.where(Date(Lead.creation).between(from_date, to_date))
		.groupby(Date(Lead.creation))
		.orderby(Date(Lead.creation))
	)
	query = live(query, Lead)
	if user:
		query = query.where(Lead.lead_owner == user)

	counts = {
		frappe.utils.get_datetime(row.date).strftime("%Y-%m-%d"): row.leads or 0
		for row in query.run(as_dict=True)
	}

	# Zero-fill every day in the range so the chart reads cleanly.
	data = []
	day = frappe.utils.getdate(from_date)
	last = frappe.utils.getdate(to_date)
	while day <= last:
		key = day.strftime("%Y-%m-%d")
		data.append({"date": key, "leads": counts.get(key, 0)})
		day = frappe.utils.add_days(day, 1)

	return {
		"data": data,
		"title": _("New leads per day"),
		"subtitle": _("Leads created each day in the selected range"),
		"xAxis": {
			"title": _("Date"),
			"key": "date",
			"type": "time",
			"timeGrain": "day",
		},
		"yAxis": {"title": _("New leads")},
		"series": [{"name": "leads", "type": "bar"}],
	}


def _status_changes(from_date, to_date, user):
	"""Per-stage entered vs. exited counts from the lead status change log.

	`entered S` = completed rows whose `to` is S (lead moved into S).
	`exited S`  = completed rows whose `from` is S (lead moved out of S).
	Both are dated by `to_date` (the moment the transition happened).
	"""
	SCL = DocType("CRM Status Change Log")
	Lead = DocType("CRM Lead")
	# `from` is a Python keyword, so the column must be reached via getattr
	# (Frappe's query builder resolves any attribute access to a column).
	scl_from = getattr(SCL, "from")

	base_cond = (
		(SCL.parenttype == "CRM Lead")
		& (SCL.to.isnotnull())
		& (SCL.to != "")
		& (Date(SCL.to_date).between(from_date, to_date))
	)

	entered_q = (
		frappe.qb.from_(SCL)
		.join(Lead)
		.on(SCL.parent == Lead.name)
		.select(SCL.to.as_("stage"), Count("*").as_("count"))
		.where(base_cond)
		.groupby(SCL.to)
	)
	exited_q = (
		frappe.qb.from_(SCL)
		.join(Lead)
		.on(SCL.parent == Lead.name)
		.select(scl_from.as_("stage"), Count("*").as_("count"))
		.where(base_cond & (scl_from.isnotnull()) & (scl_from != ""))
		.groupby(scl_from)
	)
	entered_q = live(entered_q, Lead)
	exited_q = live(exited_q, Lead)
	if user:
		entered_q = entered_q.where(Lead.lead_owner == user)
		exited_q = exited_q.where(Lead.lead_owner == user)

	stages = {}
	for row in entered_q.run(as_dict=True):
		stages.setdefault(row.stage, {"entered": 0, "exited": 0})["entered"] = row.count or 0
	for row in exited_q.run(as_dict=True):
		stages.setdefault(row.stage, {"entered": 0, "exited": 0})["exited"] = row.count or 0

	data = [
		{"stage": stage, "entered": vals["entered"], "exited": vals["exited"]}
		for stage, vals in stages.items()
	]
	# Busiest stages first.
	data.sort(key=lambda r: r["entered"] + r["exited"], reverse=True)

	return {
		"data": data,
		"title": _("Status changes by stage"),
		"subtitle": _("How many leads entered and exited each stage"),
		"xAxis": {
			"title": _("Stage"),
			"key": "stage",
			"type": "category",
		},
		"yAxis": {"title": _("Leads")},
		"series": [
			{"name": "entered", "type": "bar"},
			{"name": "exited", "type": "bar"},
		],
	}


# ---------------------------------------------------------------------------
# Status Change Report
# ---------------------------------------------------------------------------
# A richer, drill-downable view of how leads move between statuses. Two lenses:
#
#   cohort  (default) — pick the leads CREATED in the range, then show where
#                       each one went. Answers "what happened to the leads
#                       created yesterday?". Started = the status each lead
#                       began in; Ended = where it is now.
#   flow    (the GIF) — pick every transition that HAPPENED in the range.
#                       Started/Ended are full-population snapshots at the
#                       window edges; Entered/Left are the flows during it.
#
# Both lenses satisfy the same identity per stage S:
#     Ended[S] = Started[S] + Entered[S] - Left[S]   (Net = Ended - Started)
# so the table always reconciles.
#
# Everything is derived from `CRM Status Change Log` (one completed row per
# transition: lead left `from` for `to` at `to_date`) plus each lead's current
# `status` and `creation`. Creation into a status is NOT a log row, so we
# reconstruct each lead's initial status from the earliest log row's `from`
# (falling back to the current status when a lead never changed).

CREATED_SOURCE = "__created__"  # synthetic inflow node: lead created into a stage


@frappe.whitelist()
@sales_user_only
def get_status_change_report(
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
	mode: str = "cohort",
):
	"""Per-stage status-change table + inflow/outflow breakdown for each stage."""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())
	from_date = str(frappe.utils.getdate(from_date))
	to_date = str(frappe.utils.getdate(to_date))

	# Sales users only ever see their own leads, regardless of the passed-in user.
	roles = frappe.get_roles(frappe.session.user)
	is_sales_manager = "Sales Manager" in roles or "System Manager" in roles
	if "Sales User" in roles and not is_sales_manager:
		user = frappe.session.user

	mode = "flow" if mode == "flow" else "cohort"
	stages = _flow_table(from_date, to_date, user) if mode == "flow" else _cohort_table(
		from_date, to_date, user
	)

	# Order by the configured status position, busiest stages last as a tiebreak;
	# attach each status' colour for the indicator dot.
	meta = _status_meta()
	for s in stages:
		s["color"] = meta.get(s["stage"], {}).get("color")
		s["position"] = meta.get(s["stage"], {}).get("position", 9999)
	stages.sort(key=lambda r: (r["position"], -(r["ended"] + r["started"])))

	return {
		"mode": mode,
		"from_date": from_date,
		"to_date": to_date,
		"stages": stages,
	}


def _status_meta():
	"""{status_name: {color, position}} from CRM Lead Status."""
	rows = frappe.get_all(
		"CRM Lead Status", fields=["name", "color", "position"], order_by="position asc"
	)
	return {r.name: {"color": r.color, "position": r.position or 0} for r in rows}


def _sums(matrix):
	"""From {(from, to): count} return (entered_by_to, left_by_from)."""
	entered, left = {}, {}
	for (f, t), c in matrix.items():
		if t:
			entered[t] = entered.get(t, 0) + c
		if f:
			left[f] = left.get(f, 0) + c
	return entered, left


def _initial_status_map(lead_rows):
	"""Map each lead -> the status it was created in.

	The earliest status-change-log row's `from` is the creation status (the log
	starts the first time status changes, capturing the prior value). Leads that
	never changed status have no log row -> fall back to their current status.
	"""
	if not lead_rows:
		return {}
	names = [l["name"] for l in lead_rows]
	current = {l["name"]: l["status"] for l in lead_rows}

	SCL = DocType("CRM Status Change Log")
	scl_from = getattr(SCL, "from")
	initial = {}
	# Chunk the IN list so a huge cohort can't blow the SQL statement size.
	for chunk in _chunks(names, 5000):
		rows = (
			frappe.qb.from_(SCL)
			.select(SCL.parent, scl_from.as_("from_status"), SCL.idx)
			.where((SCL.parenttype == "CRM Lead") & (SCL.parent.isin(chunk)))
			.orderby(SCL.parent)
			.orderby(SCL.idx)
		).run(as_dict=True)
		for r in rows:
			if r.parent not in initial:  # lowest idx == earliest == creation status
				initial[r.parent] = r.from_status

	return {n: (initial.get(n) or current.get(n)) for n in names}


def _chunks(seq, size):
	for i in range(0, len(seq), size):
		yield seq[i : i + size]


def _cohort_table(from_date, to_date, user):
	"""Leads CREATED in [from_date, to_date], tracked across their whole journey."""
	Lead = DocType("CRM Lead")
	SCL = DocType("CRM Status Change Log")
	scl_from = getattr(SCL, "from")

	lq = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.status)
		.where(Date(Lead.creation).between(from_date, to_date))
	)
	lq = live(lq, Lead)
	if user:
		lq = lq.where(Lead.lead_owner == user)
	leads = lq.run(as_dict=True)

	# Ended = where each cohort lead is now.
	ended = {}
	for l in leads:
		if l.status:
			ended[l.status] = ended.get(l.status, 0) + 1

	# Started = the status each cohort lead began in.
	initial = _initial_status_map(leads)
	started = {}
	for st in initial.values():
		if st:
			started[st] = started.get(st, 0) + 1

	# Transition matrix among the cohort (all-time — their full journey).
	matrix = {}
	names = [l.name for l in leads]
	for chunk in _chunks(names, 5000):
		rows = (
			frappe.qb.from_(SCL)
			.select(scl_from.as_("f"), SCL.to.as_("t"), Count("*").as_("c"))
			.where(
				(SCL.parenttype == "CRM Lead")
				& (SCL.parent.isin(chunk))
				& (SCL.to.isnotnull())
				& (SCL.to != "")
			)
			.groupby(scl_from)
			.groupby(SCL.to)
		).run(as_dict=True)
		for r in rows:
			key = (r.f or "", r.t)
			matrix[key] = matrix.get(key, 0) + r.c

	entered, left = _sums(matrix)
	# created_in[S] == started[S] for the cohort (every lead was created in range).
	return _assemble(ended, started, entered, left, matrix, created_in=started)


def _flow_table(from_date, to_date, user):
	"""Every transition that happened in [from_date, to_date] (GIF semantics)."""
	Lead = DocType("CRM Lead")
	SCL = DocType("CRM Status Change Log")
	scl_from = getattr(SCL, "from")
	completed = (SCL.parenttype == "CRM Lead") & (SCL.to.isnotnull()) & (SCL.to != "")

	# Current occupancy of every status (the "now" anchor for snapshots).
	cq = frappe.qb.from_(Lead).select(Lead.status, Count("*").as_("c")).groupby(Lead.status)
	cq = live(cq, Lead)
	if user:
		cq = cq.where(Lead.lead_owner == user)
	now_count = {r.status: r.c for r in cq.run(as_dict=True) if r.status}

	def matrix_for(cond):
		q = (
			frappe.qb.from_(SCL)
			.join(Lead)
			.on(SCL.parent == Lead.name)
			.select(scl_from.as_("f"), SCL.to.as_("t"), Count("*").as_("c"))
			.where(completed & cond)
			.groupby(scl_from)
			.groupby(SCL.to)
		)
		q = live(q, Lead)
		if user:
			q = q.where(Lead.lead_owner == user)
		return {(r.f or "", r.t): r.c for r in q.run(as_dict=True)}

	matrix = matrix_for(Date(SCL.to_date).between(from_date, to_date))  # in window
	after = matrix_for(Date(SCL.to_date) > to_date)  # transitions after window end

	entered_trans, left_trans = _sums(matrix)
	after_to, after_from = _sums(after)

	# Leads created on/after the window start, split by created-in-window vs after,
	# bucketed by the status they were created into.
	created_in, created_after = _created_initial(from_date, to_date, user)

	# Snapshot at window end: undo every transition after the window, and remove
	# leads that didn't exist yet at the window end.
	#   ended[S] = now[S] + (transitions S->* after) - (transitions *->S after)
	#              - (leads created into S after the window end)
	stages = (
		set(now_count)
		| set(entered_trans)
		| set(left_trans)
		| set(after_to)
		| set(after_from)
		| set(created_in)
		| set(created_after)
	)
	stages.discard("")
	stages.discard(None)

	ended, started, entered, left = {}, {}, {}, {}
	for s in stages:
		ended[s] = (
			now_count.get(s, 0)
			+ after_from.get(s, 0)
			- after_to.get(s, 0)
			- created_after.get(s, 0)
		)
		# In flow mode an arrival is a transition-in OR a creation-in.
		entered[s] = entered_trans.get(s, 0) + created_in.get(s, 0)
		left[s] = left_trans.get(s, 0)
		started[s] = ended[s] - entered[s] + left[s]

	return _assemble(ended, started, entered, left, matrix, created_in=created_in)


def _created_initial(from_date, to_date, user):
	"""({initial_status: count_in_window}, {initial_status: count_after_window}).

	Looks only at leads created on/after the window start (bounded set), then
	buckets each by its creation date and the status it was created into.
	"""
	Lead = DocType("CRM Lead")
	lq = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.status, Date(Lead.creation).as_("created"))
		.where(Date(Lead.creation) >= from_date)
	)
	lq = live(lq, Lead)
	if user:
		lq = lq.where(Lead.lead_owner == user)
	leads = lq.run(as_dict=True)

	initial = _initial_status_map(leads)
	to_d = frappe.utils.getdate(to_date)
	in_window, after = {}, {}
	for l in leads:
		st = initial.get(l.name)
		if not st:
			continue
		bucket = after if frappe.utils.getdate(l.created) > to_d else in_window
		bucket[st] = bucket.get(st, 0) + 1
	return in_window, after


def _assemble(ended, started, entered, left, matrix, created_in):
	"""Build the per-stage rows with their inflow/outflow breakdowns."""
	stages = set(ended) | set(started) | set(entered) | set(left)
	stages.discard("")
	stages.discard(None)

	rows = []
	for s in stages:
		st = started.get(s, 0)
		en = ended.get(s, 0)
		net = en - st

		# Inflows: real transitions f -> s, plus the synthetic "created into s".
		inflows = [
			{"source": f or CREATED_SOURCE, "count": c}
			for (f, t), c in matrix.items()
			if t == s and c
		]
		if created_in.get(s):
			inflows.append({"source": CREATED_SOURCE, "count": created_in[s]})
		inflows.sort(key=lambda r: -r["count"])

		# Outflows: real transitions s -> t.
		outflows = [
			{"dest": t, "count": c} for (f, t), c in matrix.items() if f == s and t and c
		]
		outflows.sort(key=lambda r: -r["count"])

		rows.append(
			{
				"stage": s,
				"entered": entered.get(s, 0),
				"left": left.get(s, 0),
				"started": st,
				"ended": en,
				"net": net,
				"net_pct": round(net / st * 100) if st else None,
				"inflows": inflows,
				"outflows": outflows,
			}
		)
	return rows


DRILL_CAP = 5000  # most a single drill-down filter should carry into the list


@frappe.whitelist()
@sales_user_only
def get_status_transition_leads(
	to_stage: str,
	from_stage: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
	mode: str = "cohort",
):
	"""Lead names behind one cell of the report (a flow edge or a stage).

	  from_stage == CREATED_SOURCE -> leads created into `to_stage`.
	  from_stage set, to_stage set  -> leads that went from_stage -> to_stage.
	  from_stage falsy, to_stage set -> leads currently in `to_stage` (Ended).

	The caller feeds the returned names into the Leads list as a `name in [...]`
	filter, so the in-app list shows exactly this set.
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())
	from_date = str(frappe.utils.getdate(from_date))
	to_date = str(frappe.utils.getdate(to_date))

	roles = frappe.get_roles(frappe.session.user)
	is_sales_manager = "Sales Manager" in roles or "System Manager" in roles
	if "Sales User" in roles and not is_sales_manager:
		user = frappe.session.user

	mode = "flow" if mode == "flow" else "cohort"

	if from_stage == CREATED_SOURCE:
		names = _created_into_leads(to_stage, from_date, to_date, user)
	elif from_stage:
		names = _transition_leads(from_stage, to_stage, from_date, to_date, user, mode)
	else:
		names = _occupancy_leads(to_stage, from_date, to_date, user, mode)

	names = sorted(set(names))
	return {
		"names": names[:DRILL_CAP],
		"count": len(names),
		"truncated": len(names) > DRILL_CAP,
	}


def _transition_leads(from_stage, to_stage, from_date, to_date, user, mode):
	Lead = DocType("CRM Lead")
	SCL = DocType("CRM Status Change Log")
	scl_from = getattr(SCL, "from")
	cond = (
		(SCL.parenttype == "CRM Lead")
		& (scl_from == from_stage)
		& (SCL.to == to_stage)
	)
	q = (
		frappe.qb.from_(SCL)
		.join(Lead)
		.on(SCL.parent == Lead.name)
		.select(SCL.parent.as_("name"))
		.where(cond)
	)
	if mode == "flow":
		q = q.where(Date(SCL.to_date).between(from_date, to_date))
	else:
		q = q.where(Date(Lead.creation).between(from_date, to_date))
	q = live(q, Lead)
	if user:
		q = q.where(Lead.lead_owner == user)
	return [r.name for r in q.run(as_dict=True)]


def _created_into_leads(to_stage, from_date, to_date, user):
	"""Leads created in the window whose initial (creation) status was to_stage."""
	Lead = DocType("CRM Lead")
	lq = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.status)
		.where(Date(Lead.creation).between(from_date, to_date))
	)
	lq = live(lq, Lead)
	if user:
		lq = lq.where(Lead.lead_owner == user)
	leads = lq.run(as_dict=True)
	initial = _initial_status_map(leads)
	return [name for name, st in initial.items() if st == to_stage]


def _occupancy_leads(to_stage, from_date, to_date, user, mode):
	"""Leads currently sitting in `to_stage` (the Ended column).

	In cohort mode this is restricted to the created-in-window cohort; in flow
	mode it is the full current population in that status.
	"""
	Lead = DocType("CRM Lead")
	q = frappe.qb.from_(Lead).select(Lead.name).where(Lead.status == to_stage)
	if mode == "cohort":
		q = q.where(Date(Lead.creation).between(from_date, to_date))
	q = live(q, Lead)
	if user:
		q = q.where(Lead.lead_owner == user)
	return [r.name for r in q.run(as_dict=True)]


@frappe.whitelist()
@sales_user_only
def get_leads_by_source_names(
	source: str,
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
):
	"""Lead names behind one slice of the "Leads by source" donut.

	Mirrors `get_leads_by_source` exactly (leads created in the range, grouped by
	`source` with null/blank rolled up to "Empty") so the drilled list count
	matches the chart. Feeds the Leads list a `name in [...]` filter.
	"""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())
	from_date = str(frappe.utils.getdate(from_date))
	to_date = str(frappe.utils.getdate(to_date))

	roles = frappe.get_roles(frappe.session.user)
	is_sales_manager = "Sales Manager" in roles or "System Manager" in roles
	if "Sales User" in roles and not is_sales_manager:
		user = frappe.session.user

	Lead = DocType("CRM Lead")
	q = (
		frappe.qb.from_(Lead)
		.select(Lead.name)
		.where(Date(Lead.creation).between(from_date, to_date))
	)
	if source == "Empty":
		q = q.where((Lead.source.isnull()) | (Lead.source == ""))
	else:
		q = q.where(Lead.source == source)
	q = live(q, Lead)
	if user:
		q = q.where(Lead.lead_owner == user)

	names = sorted({r.name for r in q.run(as_dict=True)})
	return {
		"names": names[:DRILL_CAP],
		"count": len(names),
		"truncated": len(names) > DRILL_CAP,
	}


# ---------------------------------------------------------------------------
# Activity summary (calls / texts / agreements)
# ---------------------------------------------------------------------------
# Three outreach metrics for the range, each able to unfold into the exact leads
# behind it. Outbound (what the team placed) and inbound (the lead's replies) are
# split out — per Lance, so we can see who's responding — as unique leads AND
# total actions, with call talk time alongside.
#   calls       — CRM Call Log rows referencing a CRM Lead (`type` Outgoing/Incoming)
#   texts       — Quo Message rows referencing a CRM Lead (`direction` Outgoing/
#                 Incoming; custom doctype managed by the ops repo, may be absent)
#   agreements  — CRM Esign Agreement rows (always "sent" — no inbound;
#                 custom doctype, may be absent)
# All three are dated by `creation`, matching the rest of this dashboard. Each
# row is keyed by the lead it references, so the same fetch powers the headline
# counts and the per-lead unfold/drill-down.


# Acquisition-phase statuses (New → Contract Sent). Keep in sync with the
# ACQ_STAGES constant in frontend/src/components/Dashboard/StatusChangeReport.vue.
ACQ_STATUSES = (
	"New",
	"Called No Answer",
	"Follow Up",
	"Underwriting",
	"Make Offer",
	"Contract Sent",
)


def _call_rows(from_date, to_date, user):
	CL = DocType("CRM Call Log")
	Lead = DocType("CRM Lead")
	q = (
		frappe.qb.from_(CL)
		.join(Lead)
		.on(CL.reference_docname == Lead.name)
		.select(
			CL.reference_docname.as_("ref"),
			CL.type.as_("dir"),
			Lead.status.as_("status"),
			Count("*").as_("c"),
			Sum(CL.duration).as_("secs"),
		)
		.where(
			(CL.reference_doctype == "CRM Lead") & Date(CL.creation).between(from_date, to_date)
		)
		.groupby(CL.reference_docname)
		.groupby(CL.type)
		.groupby(Lead.status)
	)
	q = live(q, Lead)
	if user:
		q = q.where(Lead.lead_owner == user)
	return [
		{
			"ref": r.ref,
			"out": r.dir == "Outgoing",
			"c": r.c or 0,
			"secs": int(r.secs or 0),
			"status": r.status,
		}
		for r in q.run(as_dict=True)
	]


def _text_rows(from_date, to_date, user):
	if not frappe.db.exists("DocType", "Quo Message"):
		return []
	QM = DocType("Quo Message")
	Lead = DocType("CRM Lead")
	q = (
		frappe.qb.from_(QM)
		.join(Lead)
		.on(QM.reference_docname == Lead.name)
		.select(
			QM.reference_docname.as_("ref"),
			QM.direction.as_("dir"),
			Lead.status.as_("status"),
			Count("*").as_("c"),
		)
		.where(
			(QM.reference_doctype == "CRM Lead") & Date(QM.creation).between(from_date, to_date)
		)
		.groupby(QM.reference_docname)
		.groupby(QM.direction)
		.groupby(Lead.status)
	)
	q = live(q, Lead)
	if user:
		q = q.where(Lead.lead_owner == user)
	return [
		{"ref": r.ref, "out": r.dir == "Outgoing", "c": r.c or 0, "status": r.status}
		for r in q.run(as_dict=True)
	]


def _agreement_rows(from_date, to_date, user):
	if not frappe.db.exists("DocType", "CRM Esign Agreement"):
		return []
	AG = DocType("CRM Esign Agreement")
	Lead = DocType("CRM Lead")
	q = (
		frappe.qb.from_(AG)
		.join(Lead)
		.on(AG.lead == Lead.name)
		.select(AG.lead.as_("ref"), Lead.status.as_("status"), Count("*").as_("c"))
		.where(Date(AG.creation).between(from_date, to_date))
		.groupby(AG.lead)
		.groupby(Lead.status)
	)
	q = live(q, Lead)
	if user:
		q = q.where(Lead.lead_owner == user)
	# Agreements have no inbound direction — every one is outbound ("sent").
	return [
		{"ref": r.ref, "out": True, "c": r.c or 0, "status": r.status} for r in q.run(as_dict=True)
	]


ACTIVITY_ROWS = {
	"calls": _call_rows,
	"texts": _text_rows,
	"agreements": _agreement_rows,
}


def _tally(rows):
	"""Aggregate grouped activity rows into per-lead totals plus headline counts.

	`secs` (call talk time) rides along so the Leads-called row / unfold can show
	time on the phone; it's 0 for texts and agreements.
	"""
	per_lead = {}
	for r in rows:
		ref = r["ref"]
		if not ref:
			continue
		secs = r.get("secs", 0)
		d = per_lead.setdefault(
			ref,
			{
				"out": 0,
				"inb": 0,
				"secs_out": 0,
				"secs_in": 0,
				# "acq" = the lead's CURRENT status is acquisition-phase.
				"acq": r.get("status") in ACQ_STATUSES,
			},
		)
		if r["out"]:
			d["out"] += r["c"]
			d["secs_out"] += secs
		else:
			d["inb"] += r["c"]
			d["secs_in"] += secs
	acq = [d for d in per_lead.values() if d["acq"]]
	counts = {
		"unique_out": sum(1 for d in per_lead.values() if d["out"]),
		"unique_in": sum(1 for d in per_lead.values() if d["inb"]),
		"total_out": sum(d["out"] for d in per_lead.values()),
		"total_in": sum(d["inb"] for d in per_lead.values()),
		"secs_out": sum(d["secs_out"] for d in per_lead.values()),
		"secs_in": sum(d["secs_in"] for d in per_lead.values()),
		"acq_unique_out": sum(1 for d in acq if d["out"]),
		"acq_unique_in": sum(1 for d in acq if d["inb"]),
		"acq_total_out": sum(d["out"] for d in acq),
		"acq_total_in": sum(d["inb"] for d in acq),
		"acq_secs_out": sum(d["secs_out"] for d in acq),
		"acq_secs_in": sum(d["secs_in"] for d in acq),
	}
	return per_lead, counts


def _activity_summary(from_date, to_date, user):
	"""Headline counts for each outreach activity (calls / texts / agreements)."""
	titles = {
		"calls": _("Leads called"),
		"texts": _("Leads texted"),
		"agreements": _("Agreements sent"),
	}
	out = []
	for key in ("calls", "texts", "agreements"):
		_per_lead, counts = _tally(ACTIVITY_ROWS[key](from_date, to_date, user))
		out.append({"key": key, "title": titles[key], **counts})
	return out


@frappe.whitelist()
@sales_user_only
def get_activity_leads(
	activity: str,
	scope: str = "any",
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
):
	"""Leads behind one activity metric, with per-lead out/in counts (the unfold).

	`scope` picks the set: "out" = leads we reached out to, "in" = leads who
	responded (inbound), "any" = either (the unfold's full list). Each lead row
	carries both outbound and inbound counts (and talk time for calls) so the
	unfold can show who's responding. `names` feeds the Leads list a
	`name in [...]` drill-down filter.
	"""
	if activity not in ACTIVITY_ROWS:
		frappe.throw(_("Unknown activity {0}").format(activity))
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())
	from_date = str(frappe.utils.getdate(from_date))
	to_date = str(frappe.utils.getdate(to_date))

	roles = frappe.get_roles(frappe.session.user)
	is_sales_manager = "Sales Manager" in roles or "System Manager" in roles
	if "Sales User" in roles and not is_sales_manager:
		user = frappe.session.user

	scope = scope if scope in ("out", "in", "any") else "any"
	per_lead, _counts = _tally(ACTIVITY_ROWS[activity](from_date, to_date, user))

	items = []
	for name, d in per_lead.items():
		out, inb = d["out"], d["inb"]
		if scope == "out" and not out:
			continue
		if scope == "in" and not inb:
			continue
		if not (out or inb):
			continue
		items.append(
			{
				"name": name,
				"out": out,
				"inb": inb,
				"count": out + inb,
				"secs_out": d["secs_out"],
				"secs_in": d["secs_in"],
				"secs": d["secs_out"] + d["secs_in"],
				"acq": d["acq"],
			}
		)
	# Sort by the scope's relevant count so the drilled set reads busiest-first.
	def sort_key(r):
		if scope == "out":
			return (-r["out"], r["name"])
		if scope == "in":
			return (-r["inb"], r["name"])
		return (-r["count"], r["name"])

	items.sort(key=sort_key)

	# Resolve display names in chunked IN-list queries.
	name_map = {}
	for chunk in _chunks([it["name"] for it in items], 5000):
		for r in frappe.get_all(
			"CRM Lead",
			filters={"name": ["in", chunk]},
			fields=["name", "lead_name", "first_name", "last_name"],
		):
			name_map[r.name] = (
				r.lead_name
				or " ".join(x for x in [r.first_name, r.last_name] if x)
				or r.name
			)
	for it in items:
		it["lead_name"] = name_map.get(it["name"], it["name"])

	capped = items[:DRILL_CAP]
	return {
		"leads": capped,
		"names": [it["name"] for it in capped],
		"count": len(items),
		"truncated": len(items) > DRILL_CAP,
	}


# ---------------------------------------------------------------------------
# Contacted leads — the people-first Activity ledger
# ---------------------------------------------------------------------------
# One row per lead touched in the range (any call / text / agreement), with the
# direction split, talk time, and the lead's CURRENT status bucketed into the
# pipeline segment (acq / dispo / other). The dashboard scopes client-side
# (Acq | Dispo | All), so a single fetch powers all three views.

DISPO_STATUSES = (
	"Photos & Lockbox In Progress",
	"Submit to Dispo",
	"Dispo Accepted",
	"Buyer Assigned",
)


def _stage_bucket(status):
	if status in ACQ_STATUSES:
		return "acq"
	if status in DISPO_STATUSES:
		return "dispo"
	return "other"


@frappe.whitelist()
@sales_user_only
def get_contacted_leads(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
):
	"""Every lead contacted in the range, with per-lead activity + pipeline bucket."""
	if not from_date or not to_date:
		from_date = frappe.utils.get_first_day(from_date or frappe.utils.nowdate())
		to_date = frappe.utils.get_last_day(to_date or frappe.utils.nowdate())
	from_date = str(frappe.utils.getdate(from_date))
	to_date = str(frappe.utils.getdate(to_date))

	roles = frappe.get_roles(frappe.session.user)
	is_sales_manager = "Sales Manager" in roles or "System Manager" in roles
	if "Sales User" in roles and not is_sales_manager:
		user = frappe.session.user

	merged = {}

	def slot(r):
		ref = r.get("ref")
		if not ref:
			return None
		status = r.get("status")
		return merged.setdefault(
			ref,
			{
				"name": ref,
				"status": status,
				"bucket": _stage_bucket(status),
				"calls_out": 0,
				"calls_in": 0,
				"secs": 0,
				"texts_out": 0,
				"texts_in": 0,
				"agreements": 0,
			},
		)

	for r in _call_rows(from_date, to_date, user):
		d = slot(r)
		if d is None:
			continue
		d["calls_out" if r["out"] else "calls_in"] += r["c"]
		d["secs"] += r.get("secs", 0)
	for r in _text_rows(from_date, to_date, user):
		d = slot(r)
		if d is None:
			continue
		d["texts_out" if r["out"] else "texts_in"] += r["c"]
	for r in _agreement_rows(from_date, to_date, user):
		d = slot(r)
		if d is None:
			continue
		d["agreements"] += r["c"]

	# Busiest first: most touches, then most talk time.
	items = sorted(
		merged.values(),
		key=lambda d: (
			-(d["calls_out"] + d["calls_in"] + d["texts_out"] + d["texts_in"] + d["agreements"]),
			-d["secs"],
			d["name"],
		),
	)
	capped = items[:DRILL_CAP]

	name_map = {}
	for chunk in _chunks([it["name"] for it in capped], 5000):
		for r in frappe.get_all(
			"CRM Lead",
			filters={"name": ["in", chunk]},
			fields=["name", "lead_name", "first_name", "last_name"],
		):
			name_map[r.name] = (
				r.lead_name
				or " ".join(x for x in [r.first_name, r.last_name] if x)
				or r.name
			)
	for it in capped:
		it["lead_name"] = name_map.get(it["name"], it["name"])

	return {"leads": capped, "count": len(items), "truncated": len(items) > DRILL_CAP}

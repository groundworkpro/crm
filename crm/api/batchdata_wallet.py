# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""BatchData wallet: what is left, what we spent, and a shout when it runs dry.

Why this exists
---------------
BatchData is PREPAID, and when the wallet empties nothing announces it. The
features just stop: "Fetch Tax Info" 403s in the rep's face, and the comps map's
BatchData fallback silently returns nothing for the leads that have no comps of
their own — which are exactly the leads that needed it. Both wrote a line to the
Error Log, which nobody reads.

Found live on 2026-08-21: the wallet was at **$0.26** with 24 failures already
logged over the previous week, including reps' tax pulls failing that morning.
Nobody knew.

Two free endpoints do all the work here (`/wallet/balance` and
`/wallet/consumption-report` are both $0.00), so checking costs nothing and can
be done as often as we like.

What a dollar buys, so the numbers below mean something: `/property/search` and
the comps-only token bill **$0.03 per RECORD RETURNED** (not per call). A tax
pull uses the deed-history token (Basic + Core tax + mortgage/liens + deed
history) and is **$0.22** a lead. The comps fallback takes 10 rows, so it is
$0.30 a lead.

BOTH API keys share ONE wallet
------------------------------
`batchdata_api_key` (broad) and `batchdata_comps_api_key` (comps-only) are two
tokens on the same balance — verified, both report the identical figure to the
cent. There is no separate "comps budget", so one runaway job starves every
BatchData feature at once.

Reading the numbers honestly
----------------------------
The consumption report is the only per-day history available, and it is the right
tool for "what did we spend and on what". But its totals do NOT reconcile with
the wallet: for 2026-08-14..18 it reported ~$185 of API spend while the balance
moved only -$35.75. So it is reported as BatchData's own figure, and the balance
is reported separately as ground truth, rather than blending the two into one
number that is quietly wrong.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import frappe
from frappe import _

BASE = "https://api.batchdata.com"
TIMEOUT = 20

#: Warn below this. Production usage is ~$0.30/day, so this is not about the
#: steady rate — it is about headroom. One careless paging loop costs $32
#: (measured), and a rep clicking "Fetch Tax Info" needs the wallet to work THAT
#: MOMENT, not on the next top-up. Deliberately generous.
LOW_BALANCE_USD = 25.0

#: Below this nothing works at all; say so in stronger words.
EMPTY_BALANCE_USD = 1.0

#: One alert per day per kind. A wallet that is empty is empty all day, and an
#: alert that repeats every five minutes is an alert that gets muted.
_ALERT_KEY = "crm:batchdata-wallet-alert"

#: What one app-initiated BatchData call costs.
#: Tax pull = deed-history token, stamped on CRM Property Tax Pull.cost ($0.22).
#: Comps fallback = 10 rows × $0.03. The tax constant is only a fallback for a
#: row written before the cost field existed.
TAX_PULL_COST = 0.22
COMPS_FALLBACK_COST = 0.30


def _api_key() -> str:
	# The broad token first: it can read the wallet and is the one most features
	# use. Either works for /wallet/* — they share the balance.
	return frappe.conf.get("batchdata_api_key") or frappe.conf.get("batchdata_comps_api_key") or ""


def configured() -> bool:
	return bool(_api_key())


def _get(path, params=None):
	"""One FREE wallet GET. None on any failure — this must never raise."""
	key = _api_key()
	if not key:
		return None
	url = f"{BASE}{path}"
	if params:
		url += "?" + urllib.parse.urlencode(params)
	req = urllib.request.Request(
		url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
	)
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
			return json.loads(resp.read().decode("utf-8", "replace"))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData wallet: read failed")
		return None


def balance():
	"""Dollars left, or None if we could not ask. FREE."""
	body = _get("/api/v1/wallet/balance")
	try:
		return float(((body or {}).get("results") or {}).get("balance"))
	except (TypeError, ValueError):
		return None


def spend_report(days=7):
	"""Per-day and per-endpoint spend straight from BatchData. FREE.

	GOTCHA — the query params are snake_case. `startDate`/`endDate` are accepted
	and then rejected with a 422 saying the start date is required.
	"""
	try:
		days = max(1, min(90, int(days)))
	except (TypeError, ValueError):
		days = 7
	start = frappe.utils.add_days(frappe.utils.nowdate(), -days)
	body = _get(
		"/api/v1/wallet/consumption-report",
		{"start_date": start, "end_date": frappe.utils.nowdate()},
	)
	results = (body or {}).get("results") or {}
	rows = results.get("data") or []

	def amount(v):
		# by_origin is {origin: {debit: n}}; by_endpoint is {endpoint: n}.
		if isinstance(v, dict):
			try:
				return float(v.get("debit") or 0)
			except (TypeError, ValueError):
				return 0.0
		try:
			return float(v or 0)
		except (TypeError, ValueError):
			return 0.0

	by_day, by_endpoint = {}, {}
	for row in rows:
		# Hourly buckets keyed `period`, e.g. "2026-08-14T08:00:00".
		day = str(row.get("period") or "")[:10]
		if not day:
			continue
		entry = by_day.setdefault(day, {"spent": 0.0, "topped_up": 0.0})
		entry["spent"] += amount(row.get("debit"))
		entry["topped_up"] += amount(row.get("credit"))
		for endpoint, value in (row.get("by_endpoint") or {}).items():
			by_endpoint[endpoint] = by_endpoint.get(endpoint, 0.0) + amount(value)

	days_list = [
		{"date": d, "spent": round(v["spent"], 2), "topped_up": round(v["topped_up"], 2)}
		for d, v in sorted(by_day.items())
	]
	return {
		"days": days_list,
		"by_endpoint": [
			{"endpoint": k, "spent": round(v, 2)}
			for k, v in sorted(by_endpoint.items(), key=lambda kv: -kv[1])
			if v >= 0.005
		],
		"total": round(sum(d["spent"] for d in days_list), 2),
		"summary": results.get("summary") or {},
	}


def _spent_on(report, date_str):
	for row in report.get("days") or []:
		if row["date"] == date_str:
			return row["spent"]
	return 0.0


# ---------------------------------------------------------------------------------
# What the APP spent (as opposed to what the account spent)
# ---------------------------------------------------------------------------------
def app_spend_between(start, end):
	"""What the CRM ITSELF cost between two dates, from OUR OWN records.

	Deliberately NOT the consumption report. That reports everything on the wallet,
	which includes hand-run API exploration — and the two are nothing alike:
	measured 2026-08-14..21, the account spent **$205** while the app spent about
	**$0.45**. Answering "what is the app costing us" with a number dominated by
	somebody's afternoon in a terminal is worse than not answering.

	There are exactly two billed callers in the whole system, which is what makes
	this countable at all: the tax pull (ops server script) and the comps fallback.
	"""
	out = {"tax_pulls": 0, "tax_cost": 0.0, "comps_leads": 0, "comps_cost": 0.0}

	# Tax pulls are EXACT: the doctype stores the cost on every row.
	if frappe.db.exists("DocType", "CRM Property Tax Pull"):
		row = frappe.db.sql(
			"""SELECT COUNT(*) n, COALESCE(SUM(COALESCE(cost, %s)), 0) c
			   FROM `tabCRM Property Tax Pull`
			   WHERE DATE(COALESCE(pulled_at, creation)) BETWEEN %s AND %s""",
			(TAX_PULL_COST, start, end),
			as_dict=True,
		)
		if row:
			out["tax_pulls"] = int(row[0].n or 0)
			out["tax_cost"] = float(row[0].c or 0)

	# The comps fallback keeps no event log — only a per-lead cache stamp — so this
	# is a LOWER BOUND: a lead re-fetched twice in the window counts once. With a
	# 90-day cache on a hit that is rare, but it is a bound, not a total.
	#
	# Only non-empty results are counted, because billing is per row RETURNED: an
	# address BatchData cannot match costs $0.00.
	if frappe.db.has_column("CRM Lead", "batchdata_comps_fetched_at"):
		row = frappe.db.sql(
			"""SELECT COUNT(*) n FROM `tabCRM Lead`
			   WHERE batchdata_comps_fetched_at IS NOT NULL
			     AND DATE(batchdata_comps_fetched_at) BETWEEN %s AND %s
			     AND batchdata_comps IS NOT NULL
			     AND TRIM(batchdata_comps) NOT IN ('', '[]')""",
			(start, end),
			as_dict=True,
		)
		if row:
			out["comps_leads"] = int(row[0].n or 0)
			out["comps_cost"] = out["comps_leads"] * COMPS_FALLBACK_COST

	out["total"] = round(out["tax_cost"] + out["comps_cost"], 2)
	return out


def app_spend():
	"""App spend for today, this week (from Monday) and this month, to date."""
	today = frappe.utils.nowdate()
	date = frappe.utils.getdate(today)
	week_start = frappe.utils.add_days(today, -date.weekday())
	month_start = date.replace(day=1)
	return {
		"today": app_spend_between(today, today),
		"week": app_spend_between(week_start, today),
		"month": app_spend_between(str(month_start), today),
	}


def app_spend_line():
	"""'App spend: $0.30 today · $1.20 this week · $6.60 this month'.

	Swallows its own failures on purpose. This string is built as an ARGUMENT to
	the alert, so an exception here would take down the very message it decorates —
	and losing "the wallet is empty" because a spend query failed is the worst
	trade in this file. A missing number is a footnote; a missing alert is an
	outage nobody hears about.
	"""
	try:
		s = app_spend()
		return (
			f"App spend: **${s['today']['total']:,.2f}** today"
			f" · **${s['week']['total']:,.2f}** this week"
			f" · **${s['month']['total']:,.2f}** this month"
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData wallet: app spend failed")
		return _("App spend: unavailable")


@frappe.whitelist()
def get_spend(days=14):
	"""Balance + per-day spend, for a person who wants to look. Costs nothing."""
	from crm.api.comps import _guard

	_guard()
	if not configured():
		return {"available": False, "reason": "not_configured"}
	report = spend_report(days)
	bal = balance()
	yesterday = frappe.utils.add_days(frappe.utils.nowdate(), -1)
	return {
		"available": True,
		"balance": bal,
		"low": bal is not None and bal < LOW_BALANCE_USD,
		"today": _spent_on(report, frappe.utils.nowdate()),
		"yesterday": _spent_on(report, yesterday),
		"days": report["days"],
		"by_endpoint": report["by_endpoint"],
		"total": report["total"],
		# Said out loud rather than buried: these two numbers come from different
		# places and are known not to agree.
		"note": _(
			"Per-day figures are BatchData's own consumption report, which does not "
			"reconcile exactly with the wallet balance. The balance is the ground truth."
		),
	}


def _already_alerted(kind):
	"""One alert per kind per day.

	GOTCHA — stored WITHOUT `expires_in_sec`. `frappe.cache().get_value()`
	memoizes a miss as None into the per-request local cache, while the
	expiring `set_value` path writes only to Redis, so a set-then-get in one
	request reads back None forever. Storing plainly populates both; freshness is
	judged from the value itself.
	"""
	today = frappe.utils.nowdate()
	try:
		seen = frappe.cache().get_value(_ALERT_KEY) or {}
		if isinstance(seen, dict) and seen.get(kind) == today:
			return True
		seen = seen if isinstance(seen, dict) else {}
		seen[kind] = today
		frappe.cache().set_value(_ALERT_KEY, seen)
	except Exception:
		return False
	return False


def _notify(text, kind):
	"""Tell Lance, once. Never raises — an alert must not break its caller."""
	if _already_alerted(kind):
		return False
	try:
		from crm.api.daily_standup import post_to_talk, send_dm

		# Ops alerts land in Talk's #ops-bot too (when Talk exists); the DM stays.
		post_to_talk(text, channel="ops-bot", label="BatchData")
		send_dm(text)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData wallet: alert failed")
		return False


def _empty_message(bal, source=""):
	where = f" ({source})" if source else ""
	amount = f"${bal:,.2f}" if bal is not None else _("unknown")
	return (
		f"🔴 **BatchData wallet is empty** — balance {amount}{where}.\n"
		f"Tax pulls and the comps fallback are failing right now.\n"
		f"{app_spend_line()}\n"
		f"Top up at https://app.batchdata.com to re-enable them."
	)


def report_wallet_empty(source=""):
	"""Called from a 403 'Insufficient balance'. Alerts the moment a rep is hit.

	The periodic check below would catch this too, but up to half an hour later —
	and by then a rep has already had a button fail on them with no explanation.
	"""
	_notify(_empty_message(balance(), source), "empty")


def check_balance():
	"""Warn BEFORE it runs dry. Free, so it can run as often as we like.

	Returns a small dict so the caller (the 5am standup) can render a line rather
	than making its own second call.
	"""
	if not configured():
		return {"available": False}
	bal = balance()
	if bal is None:
		return {"available": False}
	report = spend_report(7)
	yesterday = _spent_on(report, frappe.utils.add_days(frappe.utils.nowdate(), -1))
	state = "ok"
	if bal < EMPTY_BALANCE_USD:
		state = "empty"
		_notify(_empty_message(bal), "empty")
	elif bal < LOW_BALANCE_USD:
		state = "low"
		_notify(
			f"🟠 **BatchData is low** — ${bal:,.2f} left.\n"
			f"{app_spend_line()}\n"
			f"Top up at https://app.batchdata.com before tax pulls start failing.",
			"low",
		)
	return {
		"available": True,
		"balance": bal,
		"state": state,
		"yesterday": yesterday,
		"week": report["total"],
	}


def watch_balance():
	"""Cheap periodic guard, safe to call from any job. Never raises.

	The 5am standup alone was not enough to honour "tell me when it is empty": a
	wallet that runs dry at 10am would have gone unreported until the next morning,
	while every tax pull in between failed in a rep's face. This rides the
	half-hourly pulse instead, so the gap is at most 30 minutes of business hours.

	Costs nothing — `/wallet/balance` is free — and `_notify` deduplicates to one
	message per kind per day, so a half-hourly check cannot become half-hourly spam.
	"""
	try:
		if not configured():
			return None
		return check_balance()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData wallet: watch failed")
		return None


def standup_line():
	"""One line for the 5am DM: what is left, and what we are burning.

	Kept to a single line on purpose \u2014 that DM is the list Lance runs the morning
	call from, and spend is context, not the point of it.
	"""
	try:
		info = check_balance()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData wallet: standup line failed")
		return ""
	if not info.get("available"):
		return ""
	mark = {"empty": "🔴", "low": "🟠"}.get(info["state"], "")
	tail = " — top up" if info["state"] in ("empty", "low") else ""
	# APP spend, not account spend. The account number is dominated by whatever was
	# run by hand that week ($205 against the app's $0.45) and would read here as if
	# the CRM had done it.
	try:
		s = app_spend()
		spend = (
			f" · app spend ${s['today']['total']:,.2f} today · "
			f"${s['week']['total']:,.2f} wk · ${s['month']['total']:,.2f} mo"
		)
	except Exception:
		spend = ""
	return f"{mark} BatchData ${info['balance']:,.2f} left{spend}{tail}".strip()

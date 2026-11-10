"""Intraday Today-board pulse — posted to the Acq channel every 30 minutes.

The 5am standup DM says what the day looks like; the Today board is where the
setters work it; this is the half-hourly heartbeat in between, so pace is visible
while the day can still be changed rather than at 6pm when it can't. It posts to
the team channel rather than a DM so everyone in acquisitions is looking at the
same number.

One message carries four things:

  * **delta** — cards resolved since the last pulse
  * **rolling total** — where the day stands, as a progress bar
  * **pace** — how much of the working day has elapsed vs. how much of the board
    is resolved, i.e. how many cards over or under we are right now, plus what's
    left against the hours left
  * **talk time** — Quo minutes in the window

Talk time is here deliberately. Cards-per-half-hour on its own punishes exactly
the behaviour we want: a setter deep in a 20-minute conversation with a motivated
seller resolves fewer cards than one dialing voicemails, and a bare "+1" would
read as a rebuke for doing the job right. The renderer therefore leads with the
conversation whenever the window was quiet on cards but busy on the phone.

Definitions are reused, never re-derived:

  * board state / totals — `CRM Today Item`, the same rows `today_board` serves
  * call attribution — `caller` -> `receiver` -> `User.custom_quo_number`, the
    exact chain `activity_progress._call_events` uses, so the pulse and the Team
    Activity report cannot disagree about whose call it was
  * business days — `daily_standup.is_business_day`

The delta window is a **watermark**, not a fixed 30 minutes: it runs from the last
successfully posted pulse to now. A skipped or failed slot therefore folds its
cards into the next message instead of dropping them, so the deltas across a day
always sum to the day's resolved total.
"""

import json
from datetime import timedelta

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

from crm.api import telephony
from crm.api.daily_standup import DEFAULT_MM_BASE, is_business_day

DOCTYPE = "CRM Today Item"

#: Empty means the whole team, matching the shared board totals. A hardcoded
#: rep roster silently hid Exe/Dennis's completions and calls when Ger was off.
#: Keep today_pulse_users as an explicit site override, not a default roster.
DEFAULT_PULSE_USERS = ()

#: Where the pulse posts. The Acq channel, so the whole acquisitions team sees
#: the same pace the setters are working against.
DEFAULT_PULSE_TEAM = "groundwork"
DEFAULT_PULSE_CHANNEL = "acq"

#: Watermark of the last successful post, as a Frappe default (no new doctype —
#: same approach as the Today priority order and the activity goals).
WATERMARK_KEY = "crm_today_pulse_watermark"

#: The working window, in SITE time (America/Chicago). Cron fires the job on the
#: half hour; these bounds decide whether it actually says anything.
WINDOW_START = (9, 30)
WINDOW_END = (17, 0)

#: A window with no cards but at least this much talk time is reported as
#: conversation rather than as a zero.
LONG_CALL_SECONDS = 300

#: Hours of ACTUAL WORK (measured from the first resolved card, not from the
#: start of the window) before an observed cards-per-hour rate is worth quoting.
MIN_RATE_HOURS = 1.0

PROGRESS_WIDTH = 20
CRM_TODAY_URL = "https://crm.groundworkpro.com/crm/today"


# ── helpers ────────────────────────────────────────────────────────────────────


def _available() -> bool:
	return bool(frappe.db.exists("DocType", DOCTYPE))


def _pulse_users():
	configured = frappe.conf.get("today_pulse_users")
	if isinstance(configured, str):
		configured = [u.strip() for u in configured.split(",") if u.strip()]
	return tuple(configured or DEFAULT_PULSE_USERS)


def _pulse_target():
	return (
		frappe.conf.get("today_pulse_team") or DEFAULT_PULSE_TEAM,
		frappe.conf.get("today_pulse_channel") or DEFAULT_PULSE_CHANNEL,
	)


def _resolved_stamp_field():
	"""`resolved_at` (Done + Skipped) when the ops script has run, else `done_at`.

	Falling back to `done_at` keeps the pulse working on a site where the app is
	deployed before the schema upgrade; it just under-counts skips until then, so
	the caller surfaces that rather than silently reporting a wrong number.
	"""
	if not _available():
		return None
	meta = frappe.get_meta(DOCTYPE)
	return "resolved_at" if meta.has_field("resolved_at") else "done_at"


def _fmt_minutes(seconds):
	seconds = int(seconds or 0)
	if seconds <= 0:
		return "0m"
	if seconds < 60:
		return f"{seconds}s"
	minutes = seconds // 60
	if minutes < 60:
		return f"{minutes}m"
	hours, minutes = divmod(minutes, 60)
	return f"{hours}h{minutes:02d}m"


def _plural(n, word):
	return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _fmt_clock(dt):
	dt = get_datetime(dt)
	hour = dt.hour % 12 or 12
	return f"{hour}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def _progress_bar(resolved, total, width=PROGRESS_WIDTH):
	"""Two tones: resolved vs still to call.

	Done and Skipped are deliberately NOT split into separate shades. Three tones
	needed a legend to decode, and a nudge that has to be decoded every thirty
	minutes is not glanceable — the first preview reader took the middle shade to
	mean "in progress". The Done/Skipped texture lives in the counts line instead.

	Proportional rounding alone would make the bar lie, in both directions:

	  * with 1 card left of 21+, rounding fills every cell — the bar reads
	    "finished" while the board is not;
	  * the first resolved card on a big board rounds to zero cells, so real
	    progress renders as a completely empty bar.

	So a non-zero side never rounds away to nothing, and the bar is only ever
	completely full when the board is genuinely clear.
	"""
	if not total:
		return "░" * width
	resolved = max(0, min(int(resolved), int(total)))
	remaining = total - resolved
	filled = int(round(resolved * width / total))
	if resolved and filled == 0:
		filled = 1
	if remaining and filled >= width:
		filled = width - 1
	return "█" * filled + "░" * (width - filled)


def _window_bounds(now):
	day = getdate(now)
	start = get_datetime(f"{day} {WINDOW_START[0]:02d}:{WINDOW_START[1]:02d}:00")
	end = get_datetime(f"{day} {WINDOW_END[0]:02d}:{WINDOW_END[1]:02d}:00")
	return start, end


def _read_watermark(now):
	"""Last successful post time, clamped into today's working window.

	Clamping matters on the first pulse of the day: a raw yesterday-evening
	watermark would sweep every overnight change into the 9:30am delta.
	"""
	start, _ = _window_bounds(now)
	raw = frappe.db.get_default(WATERMARK_KEY)
	if not raw:
		return start
	try:
		mark = get_datetime(raw)
	except Exception:
		return start
	if mark < start or mark > now:
		return start
	return mark


def _write_watermark(at):
	frappe.db.set_default(WATERMARK_KEY, str(at))


# ── data ───────────────────────────────────────────────────────────────────────


def _board_stats(day, stamp_field=None):
	fields = ["state"] + ([stamp_field] if stamp_field else [])
	rows = frappe.get_all(
		DOCTYPE,
		filters={"for_date": day},
		fields=fields,
		limit_page_length=50000,
	)
	stats = {"total": len(rows), "done": 0, "skipped": 0}
	stamps = []
	for row in rows:
		if row.state == "Done":
			stats["done"] += 1
		elif row.state == "Skipped":
			stats["skipped"] += 1
		if stamp_field and row.get(stamp_field):
			stamps.append(get_datetime(row.get(stamp_field)))
	stats["resolved"] = stats["done"] + stats["skipped"]
	stats["remaining"] = stats["total"] - stats["resolved"]
	stats["pct"] = round(stats["resolved"] * 100 / stats["total"]) if stats["total"] else 0
	# When work actually started, which is what the observed rate is measured
	# against. The setters routinely start an hour or more after the window opens.
	stats["first_at"] = min(stamps) if stamps else None
	return stats


def _delta_cards(day, since, until, stamp_field, users):
	"""Cards resolved in (since, until]. Restricted to the reported users when the
	row carries an attribution; unattributed rows are counted, because a resolved
	card is progress on the shared board either way."""
	who_field = "resolved_by" if stamp_field == "resolved_at" else "done_by"
	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"for_date": day,
			stamp_field: ["between", [since, until]],
		},
		fields=["state", who_field],
		limit_page_length=50000,
	)
	done = skipped = 0
	for row in rows:
		actor = row.get(who_field)
		if actor and users and actor not in users:
			continue
		if row.state == "Done":
			done += 1
		elif row.state == "Skipped":
			skipped += 1
	return {"done": done, "skipped": skipped, "total": done + skipped}


def _number_users(users):
	"""Quo number -> user, the same map `activity_progress` builds."""
	# One mapping for every provider, shared with the Team Activity report and the
	# SMS sender attribution -- so the pulse cannot disagree with the report about
	# whose call it was, and neither goes blind when a rep's line is on Telnyx.
	return telephony.line_owners(users=users)


def _calls(day, since, until, users):
	"""Quo calls that STARTED in the window, attributed exactly as the Team
	Activity report attributes them.

	Windowing on `start_time` (not end) matches that report and keeps one call in
	exactly one window; a conversation still running when the pulse fires lands in
	the next one, which is also when its full duration is known.
	"""
	number_users = _number_users(users)
	rows = frappe.get_all(
		"CRM Call Log",
		filters={"start_time": ["between", [since, until]]},
		fields=["caller", "receiver", "from", "to", "type", "duration", "status", "start_time"],
		order_by="duration desc",
		limit_page_length=5000,
	)
	calls = 0
	talk = 0
	longest = 0
	connected = 0
	for row in rows:
		workspace_number = row.get("from") if row.type == "Outgoing" else row.get("to")
		user = (
			row.get("caller")
			or row.get("receiver")
			or number_users.get(telephony.last10(workspace_number))
		)
		if users and user not in users:
			continue
		duration = int(row.duration or 0)
		calls += 1
		talk += duration
		longest = max(longest, duration)
		if duration >= 60:
			connected += 1
	return {
		"calls": calls,
		"talk_seconds": talk,
		"longest_seconds": longest,
		"conversations": connected,
	}


def build_pulse(now=None, since=None):
	"""Everything one message needs. Pure read — safe to call for a preview."""
	now = get_datetime(now or now_datetime())
	day = getdate(now)
	users = _pulse_users()
	stamp_field = _resolved_stamp_field()
	# (window start is only needed for the watermark clamp, inside _read_watermark)
	window_end = _window_bounds(now)[1]

	since = get_datetime(since) if since else _read_watermark(now)
	board = _board_stats(day, stamp_field) if _available() else {
		"total": 0, "done": 0, "skipped": 0, "resolved": 0, "remaining": 0,
		"pct": 0, "first_at": None,
	}
	delta = (
		_delta_cards(day, since, now, stamp_field, users)
		if stamp_field
		else {"done": 0, "skipped": 0, "total": 0}
	)
	calls = _calls(day, since, now, users)
	day_calls = _calls(day, f"{day} 00:00:00", now, users)

	# Pace against the end of the working day, using the rate actually achieved so
	# far rather than a target nobody agreed to.
	#
	# A rate needs enough elapsed time to mean anything: three cards closed by
	# 9:40am is not "36 per hour". Below MIN_RATE_HOURS the observed rate is
	# withheld entirely rather than reported as a number that will be wrong in
	# both directions, and the message just states what's left to do.
	#
	# Crucially the clock starts at the FIRST RESOLVED CARD, not at the top of the
	# window. The setters routinely start an hour or more after 9:30, and charging
	# them for that time made the pulse open every day with a false "behind"
	# warning built out of hours nobody was working.
	# NOTE: an elapsed-vs-resolved "N cards behind pace" verdict lived here and was
	# removed deliberately. The board routinely carries more cards than a day can
	# hold (81-111 generated against ~87 resolved on a good day), so it read
	# "behind" almost every day — which is a statement about board size, not about
	# the person working it, and a warning that fires daily stops being read at
	# all. Board overload belongs in the standup's intake-capacity number, not in a
	# half-hourly nudge. See git history if it's ever wanted back.
	hours_left = max(0.0, (window_end - now).total_seconds() / 3600.0)
	worked_hours = (
		max(0.0, (now - board["first_at"]).total_seconds() / 3600.0)
		if board.get("first_at")
		else 0.0
	)
	rate = (
		board["resolved"] / worked_hours
		if worked_hours >= MIN_RATE_HOURS and board["resolved"]
		else None
	)
	needed = board["remaining"] / hours_left if hours_left > 0.05 else None

	return {
		"now": now,
		"date": str(day),
		"since": since,
		"window_end": window_end,
		"board": board,
		"delta": delta,
		"calls": calls,
		"day_calls": day_calls,
		"pace": {
			"hours_left": hours_left,
			"worked_hours": worked_hours,
			"started_at": board.get("first_at"),
			"rate": rate,
			"needed": needed,
			# Without a trustworthy rate, assume on track — the pulse should not
			# open the day by warning about a pace it cannot yet measure.
			"on_track": (
				board["remaining"] == 0
				or needed is None
				or rate is None
				or rate >= needed
			),
		},
		"counts_skips": stamp_field == "resolved_at",
		"available": bool(stamp_field),
	}


# ── rendering ──────────────────────────────────────────────────────────────────


def render_markdown(d):
	board = d["board"]
	delta = d["delta"]
	calls = d["calls"]
	pace = d["pace"]
	L = []

	L.append(f"**Today pulse — {_fmt_clock(d['now'])}**")

	if not board["total"]:
		L.append("")
		L.append("No cards on the board today.")
		return "\n".join(L)

	# The headline. A quiet card count next to real talk time is reported as the
	# conversation it was, not as a zero.
	parts = []
	if delta["done"]:
		parts.append(f"**+{delta['done']}** done")
	if delta["skipped"]:
		parts.append(f"+{delta['skipped']} skipped")
	since_txt = f"since {_fmt_clock(d['since'])}"
	if parts:
		L.append(f"{' · '.join(parts)} {since_txt}")
	elif calls["talk_seconds"] >= LONG_CALL_SECONDS:
		L.append(
			f"No cards closed {since_txt} — but {_fmt_minutes(calls['talk_seconds'])} "
			f"on the phone, longest {_fmt_minutes(calls['longest_seconds'])}. "
			"Deep in a conversation."
		)
	else:
		L.append(f"Nothing resolved {since_txt}.")

	L.append("")
	L.append(
		f"`{_progress_bar(board['resolved'], board['total'])}`  "
		f"**{board['pct']}%** · {board['resolved']} of {board['total']}"
	)
	L.append("")
	L.append(
		f"Done **{board['done']}** · Skipped {board['skipped']} · "
		f"Left **{board['remaining']}**"
	)

	# Phone line: window first (that's the news), day total for context.
	if calls["calls"] or d["day_calls"]["calls"]:
		if calls["calls"]:
			bits = [
				f"{_plural(calls['calls'], 'call')} / "
				f"{_fmt_minutes(calls['talk_seconds'])} talk this window"
			]
			if calls["longest_seconds"] >= LONG_CALL_SECONDS:
				bits.append(f"longest {_fmt_minutes(calls['longest_seconds'])}")
		else:
			bits = ["no calls this window"]
		bits.append(
			f"today {_plural(d['day_calls']['calls'], 'call')} / "
			f"{_fmt_minutes(d['day_calls']['talk_seconds'])}"
		)
		L.append("📞 " + " · ".join(bits))

	if board["remaining"] == 0:
		L.append("")
		L.append("🎉 **Board clear.**")
	elif pace["needed"] is not None:
		head = (
			f"{board['remaining']} left · {pace['hours_left']:.1f}h to "
			f"{_fmt_clock(d['window_end'])}"
		)
		if pace["on_track"]:
			line = f"✅ {head} — need ~{pace['needed']:.0f}/hr"
			if pace["rate"] is not None:
				line += f", running ~{pace['rate']:.0f}/hr"
		elif pace["rate"] is not None:
			# Late in the day the required rate becomes arithmetically true but
			# useless ("need ~62/hr" with 30 minutes left). Project what the
			# current pace actually lands instead — that's the number worth
			# acting on, for the board today and for capacity planning.
			landing = int(pace["rate"] * pace["hours_left"])
			carry = max(0, board["remaining"] - landing)
			line = (
				f"⚠️ {head} — at ~{pace['rate']:.0f}/hr that's about "
				f"{landing} more, ~{carry} carrying over"
			)
		else:
			line = f"⚠️ {head} — need ~{pace['needed']:.0f}/hr"
		L.append("")
		L.append(line)
	else:
		L.append("")
		L.append(f"⏰ Day's up — {board['remaining']} left on the board.")

	if not d["counts_skips"]:
		L.append("")
		L.append("_(skips not yet timestamped — run the ops schema upgrade)_")

	L.append("")
	L.append(f"[Open the board]({CRM_TODAY_URL})")
	return "\n".join(L)


# ── delivery ───────────────────────────────────────────────────────────────────


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


def send_to_channel(text):
	"""Post to the Acq channel as the `pi` bot. Returns the post id.

	Absent a token this no-ops rather than raising, matching `daily_standup.send_dm`
	so the feature lies dormant on an unconfigured site.
	"""
	base = (frappe.conf.get("mattermost_base") or DEFAULT_MM_BASE).rstrip("/")
	token = frappe.conf.get("mattermost_token")
	if not token:
		frappe.log_error(
			title="today pulse: no mattermost_token in site_config", message="skipped post"
		)
		return None

	team, channel_name = _pulse_target()
	channel = _mm(f"/teams/name/{team}/channels/name/{channel_name}", token, base)
	post = _mm(
		"/posts", token, base, "POST", {"channel_id": channel["id"], "message": text}
	)
	return post["id"]


def pulse_enabled():
	"""The half-hourly post is opt-in (site config `today_pulse_enabled`)."""
	return bool(frappe.conf.get("today_pulse_enabled"))


def send_today_pulse():
	"""Scheduler entry — every 30 minutes, 9:30am–5:00pm CT on business days.

	Wrapped so a delivery failure is logged rather than crashing the scheduler and
	silently taking the whole cron slot down (the standup job learned this first).
	"""
	try:
		now = get_datetime(now_datetime())
		if not is_business_day(getdate(now)):
			return

		# Piggyback the BatchData wallet check on this slot. It is FREE and it needs
		# to run during BUSINESS HOURS -- the 5am standup alone meant a wallet that
		# emptied at 10am went unreported until the next morning while every tax pull
		# in between failed in a rep's face. Deliberately ABOVE the window guard
		# below, so it still runs on the 9:00 and 17:00 ticks that the pulse itself
		# sits out. It alerts Lance directly and never touches this message.
		try:
			from crm.api import batchdata_wallet

			batchdata_wallet.watch_balance()
		except Exception:
			frappe.log_error(
				title="today pulse: wallet check failed", message=frappe.get_traceback()
			)

		# The Acq channel is being archived (Lance, 2026-11-10: "get rid of the acq
		# updates"). The post is off unless a site sets today_pulse_enabled; the
		# wallet check above keeps running on this slot either way.
		if not pulse_enabled():
			return

		start, end = _window_bounds(now)
		# Cron fires on every half hour of 9–17; these bounds are the real window.
		if now < start - timedelta(minutes=1) or now > end + timedelta(minutes=1):
			return
		if not _available():
			return

		data = build_pulse(now)
		post = send_to_channel(render_markdown(data))
		# Only advance the watermark on a delivered post, so a failed slot folds
		# its cards into the next message instead of losing them.
		if post:
			_write_watermark(now)
		return post
	except Exception:
		frappe.log_error(
			title="today pulse: send_today_pulse failed", message=frappe.get_traceback()
		)


@frappe.whitelist()
def preview_pulse(now=None, since=None, send=0, note=None):
	"""Dry run. Returns the exact markdown the scheduler would post; only actually
	posts when send=1, and never moves the watermark."""
	if frappe.session.user != "Administrator":
		roles = set(frappe.get_roles())
		if not roles & {"System Manager", "Sales Manager"}:
			frappe.throw(_("Not permitted"), frappe.PermissionError)

	data = build_pulse(now, since)
	text = render_markdown(data)
	if note:
		text = f"_{note}_\n\n{text}"
	post = send_to_channel(text) if int(send or 0) else None
	return {
		"markdown": text,
		"sent": bool(post),
		"post_id": post,
		"board": data["board"],
		"delta": data["delta"],
		"calls": data["calls"],
		"counts_skips": data["counts_skips"],
		"since": str(data["since"]),
	}

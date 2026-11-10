"""Who owns which phone line, and which provider a row came from.

This exists because Telnyx is coming and Quo is not leaving on the same day. The
plan is to run both in parallel, and the plan's own warning is the reason this
module is written BEFORE any Telnyx traffic exists:

    every report must union both providers or it silently under-counts.

The Team Activity report, the intraday pulse, the standup and the lead-owner
backfill all answer "whose call was that?" by walking `caller` -> `receiver` ->
the user's sending line. During parallel running, half the calls are invisible to
a chain that only knows about Quo — no error, just a smaller number that looks
plausible. That is the same failure family as the `reference_doctype` trap and
the incoming-`userId` trap, both of which cost weeks of wrong figures.

So: ONE place that knows which numbers belong to which people, across providers,
and ONE normaliser.

WHY LAST-10. There were NINE separate `_last10`/`_digits` helpers in this app
(activity_progress, sms, quo_contacts, do_not_contact, agreement_adopt,
call_transcript, investorlift_ingest, investorlift_2fa, lead_import) and they did
not agree: `activity_progress` and `today_pulse` matched a user's line by EXACT
STRING against `User.custom_quo_number`, so a line stored as "+16125551234" never
matched a call log carrying "6125551234". A number is the same number however it
was typed, and last-10 is what every other part of this codebase already settled
on.

CALLS ALREADY CARRY THEIR PROVIDER — and it is NOT the field the plan assumed.
The plan proposed re-stamping `telephony_medium` (every row says "Manual",
upstream's default). Measured on prod first: all 4,192 rows are
`medium = "Quo"`, `telephony_medium = "Manual"`. The ops webhook has been
writing `medium` since the mirror was built, so the discriminator exists, is
100% populated and is correct — no migration, no re-stamp, nothing to run.
Telnyx writes `medium = "Telnyx"` and every downstream reader works.
`telephony_medium` is read only as a fallback, because it is what upstream's own
telephony framework sets and a call placed through that path would carry it.

TEXTS have no equivalent, hence `Quo Message.provider` (ops:
setup_provider_columns.py). The doctype keeps its name: renaming one with 4,357
rows buys nothing a column does not.

Nothing here writes. It is deliberately a read model: the providers' own webhooks
own their rows.
"""

import frappe

QUO = "Quo"
TELNYX = "Telnyx"
PROVIDERS = (QUO, TELNYX)

#: What an unstamped row means. Every call log and text that exists today came
#: from Quo, so a blank/"Manual" medium is Quo — not "unknown". Guessing the
#: other way would drop all 4,000 historical rows out of every report the moment
#: a provider filter was applied.
LEGACY_PROVIDER = QUO

#: Per-provider sending line on CRM Telephony Agent. `custom_quo_number` on User
#: predates this and stays authoritative for Quo (it is what the compose box and
#: the send scripts read); the agent row is where a SECOND line lives, because a
#: rep will hold a Quo line and a Telnyx line at the same time.
AGENT_FIELDS = {TELNYX: "custom_telnyx_number"}


def last10(value) -> str:
	"""The last ten digits of a phone number, or "".

	The one normaliser. `+1 (612) 555-1234`, `16125551234` and `6125551234` are
	the same line, and every part of this codebase that has ever compared numbers
	has had to learn that separately.
	"""
	digits = "".join(ch for ch in str(value or "") if ch.isdigit())
	return digits[-10:] if len(digits) >= 10 else digits


def normalize_provider(value) -> str:
	"""Map whatever is stored to one of PROVIDERS.

	"Manual" is what every historical CRM Call Log row carries — upstream's
	default, never set by us — so it means "before we recorded this", which is Quo.
	"""
	text = (value or "").strip()
	if not text or text.lower() == "manual":
		return LEGACY_PROVIDER
	for provider in PROVIDERS:
		if text.lower() == provider.lower():
			return provider
	return text


def call_provider(row) -> str:
	"""Which provider handled a CRM Call Log row.

	`medium` first: the Quo mirror has always written it, so it is populated on
	every historical row. `telephony_medium` is the fallback for a call placed
	through upstream's own telephony framework, which sets that one instead.
	"""
	return normalize_provider(_get(row, "medium") or _get(row, "telephony_medium"))


def message_provider(row) -> str:
	"""Which provider carried a Quo Message row.

	The doctype keeps its name — renaming a doctype with 4,357 rows to say
	"Message" buys nothing that a column does not.
	"""
	return normalize_provider(_get(row, "provider"))


def _get(row, field):
	if row is None:
		return None
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def _agent_lines():
	"""Per-provider lines from CRM Telephony Agent, as {user: {provider: number}}.

	Guarded on the columns existing: the agent doctype ships with upstream but our
	per-provider fields are added by ops, and a report must not fail because a
	site has not run a setup script yet.
	"""
	fields = ["user"]
	for provider, field in AGENT_FIELDS.items():
		if frappe.db.has_column("CRM Telephony Agent", field):
			fields.append(field)
	if len(fields) == 1:
		return {}
	rows = frappe.get_all("CRM Telephony Agent", fields=fields, limit_page_length=500)
	out = {}
	for row in rows:
		for provider, field in AGENT_FIELDS.items():
			value = (row.get(field) or "").strip() if field in fields else ""
			if value:
				out.setdefault(row.user, {})[provider] = value
	return out


def line_owners(users=None, providers=None) -> dict:
	"""{last10 number: user} across every provider a user sends from.

	`users` optionally restricts to a set of logins (the pulse reports on a
	subset). `providers` optionally restricts which lines count, which is what
	makes a per-provider breakdown possible later without a second mapping.
	"""
	wanted = set(providers or PROVIDERS)
	out = {}

	if QUO in wanted and frappe.db.has_column("User", "custom_quo_number"):
		for row in frappe.get_all(
			"User", filters={"enabled": 1}, fields=["name", "custom_quo_number"],
			limit_page_length=500,
		):
			number = last10(row.custom_quo_number)
			if number and (not users or row.name in users):
				out[number] = row.name

	for user, lines in _agent_lines().items():
		if users and user not in users:
			continue
		for provider, value in lines.items():
			if provider not in wanted:
				continue
			number = last10(value)
			if number:
				out[number] = user
	return out


def user_lines(user, providers=None) -> dict:
	"""{provider: number} for one user — what they can send from."""
	wanted = set(providers or PROVIDERS)
	out = {}
	if QUO in wanted and frappe.db.has_column("User", "custom_quo_number"):
		value = (frappe.db.get_value("User", user, "custom_quo_number") or "").strip()
		if value:
			out[QUO] = value
	for provider, value in _agent_lines().get(user, {}).items():
		if provider in wanted:
			out[provider] = value
	return out


def sending_line(user, provider=None):
	"""The number `user` sends from on `provider` (default: whatever they have).

	Deliberately returns the stored string, not the normalised form: it is going
	back out to an API that wants E.164, and last10 is for COMPARING numbers, not
	for dialling them.
	"""
	lines = user_lines(user)
	if provider:
		return lines.get(provider)
	for candidate in PROVIDERS:
		if lines.get(candidate):
			return lines[candidate]
	return None


def our_numbers(providers=None) -> set:
	"""Every line WE own, as last10 — the configured ones.

	Used to tell a teammate-to-teammate call apart from real outreach. Callers
	that can afford a live provider lookup should union this with it:
	`activity_progress._workspace_lines()` reads Quo's own phone-number list
	because shared lines (the "Backup Number") belong to no user and so appear
	nowhere in the mapping above.
	"""
	return set(line_owners(providers=providers))


# ══════════════════════════════════════════════════════════════════════════════
# THE PHONE DESK — Telnyx, conference-first (next workspace).
#
# Everything below is the write side the read model above deliberately did not
# have. Lines are `CRM Phone Line` rows (ops setup_phone_lines.py): a number, an
# owner, and members with separate view / use / ring rights, because "Exe's
# number is his but Dennis may dial from it" and "Germán may read its texts but
# must not ring" are different grants. Recording resolves per line through the
# workspace default (`CRM Telephony Settings`, a Single).
#
# Call state while a call is up lives in Redis under the desk id (see
# crm/integrations/telnyx/desk.py for the leg model). It is transient by design:
# the durable record is the CRM Call Log the webhook writes, and a restart
# mid-call costs the desk its live view, not the log.
# ══════════════════════════════════════════════════════════════════════════════

import json as _json

from frappe import _
from frappe.utils import now as _now

from crm.integrations.telnyx import desk

LINE = "CRM Phone Line"
LINE_MEMBER = "CRM Phone Line Member"
SETTINGS = "CRM Telephony Settings"
STATE_HASH = "crm:telnyx:desks"
BY_CALL_LOG = "crm:telnyx:desk_by_call_log"


def desk_enabled() -> bool:
	return frappe.db.exists("DocType", LINE) and frappe.db.exists("DocType", SETTINGS)


# ── settings & lines ───────────────────────────────────────────────────────────


def phone_settings() -> dict:
	"""Workspace telephony settings with safe defaults before the ops script runs."""
	out = {
		"recording_default": True,
		"ring_cell_fallback": False,
		"ring_timeout_secs": desk.RING_TIMEOUT_SECS,
		"join_tone_url": (frappe.conf.get("telnyx_join_tone_url") or "").strip() or None,
		"consent_note": desk.DEFAULT_CONSENT_NOTE,
	}
	if not frappe.db.exists("DocType", SETTINGS):
		return out
	try:
		row = frappe.get_cached_doc(SETTINGS) if hasattr(frappe, "get_cached_doc") else frappe.get_doc(SETTINGS)
		out["recording_default"] = bool(row.get("recording_default"))
		out["ring_cell_fallback"] = bool(row.get("ring_cell_fallback"))
		out["ring_timeout_secs"] = int(row.get("ring_timeout_secs") or desk.RING_TIMEOUT_SECS)
		if row.get("join_tone_url"):
			out["join_tone_url"] = row.get("join_tone_url")
		if row.get("consent_note"):
			out["consent_note"] = row.get("consent_note")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Telephony settings read failed")
	return out


@frappe.whitelist()
def get_phone_settings():
	return phone_settings()


@frappe.whitelist()
def save_phone_settings(recording_default=None, ring_cell_fallback=None, ring_timeout_secs=None, join_tone_url=None):
	"""Admin-only. The consent note is read-only on purpose: the toggle is not consent."""
	frappe.only_for(("System Manager", "Sales Manager"))
	if not frappe.db.exists("DocType", SETTINGS):
		frappe.throw(_("Telephony settings are not set up on this site yet."))
	row = frappe.get_doc(SETTINGS)
	if recording_default is not None:
		row.recording_default = 1 if str(recording_default).lower() in ("1", "true") else 0
	if ring_cell_fallback is not None:
		row.ring_cell_fallback = 1 if str(ring_cell_fallback).lower() in ("1", "true") else 0
	if ring_timeout_secs is not None:
		row.ring_timeout_secs = max(5, min(int(ring_timeout_secs), 120))
	if join_tone_url is not None:
		row.join_tone_url = (join_tone_url or "").strip()
	row.save(ignore_permissions=True)
	return phone_settings()


def all_lines() -> list[dict]:
	"""Every line with its members, as plain dicts `desk.line_access` understands."""
	if not frappe.db.exists("DocType", LINE):
		return []
	fields = ["name", "number", "label", "owner_user", "recording", "active"]
	if frappe.db.has_column(LINE, "emoji"):
		fields.append("emoji")
	rows = frappe.get_all(LINE, fields=fields, limit_page_length=200)
	mfields = ["parent", "user", "view", "use", "ring"]
	if frappe.db.has_column(LINE_MEMBER, "mute"):
		mfields.append("mute")
	members = frappe.get_all(LINE_MEMBER, fields=mfields, limit_page_length=5000)
	by_line = {}
	for m in members:
		by_line.setdefault(m.parent, []).append({"user": m.user, "view": m.view, "use": m.use, "ring": m.ring, "mute": bool(m.get("mute"))})
	out = []
	for r in rows:
		out.append(
			{
				"name": r.name,
				"number": r.number,
				"label": r.label,
				"emoji": (r.get("emoji") or "").strip(),
				"owner": r.owner_user,
				"recording": r.recording or "inherit",
				"active": bool(r.active),
				"members": by_line.get(r.name, []),
			}
		)
	return out


def line_by_number(number) -> dict | None:
	key = last10(number)
	for line in all_lines():
		if last10(line["number"]) == key:
			return line
	return None


def line_by_name(name) -> dict | None:
	for line in all_lines():
		if line["name"] == name or last10(line["number"]) == last10(name):
			return line
	return None


@frappe.whitelist()
def search_numbers(area_code: str, limit: int = 12):
	"""Available local numbers in an area code. Managers only; spends nothing."""
	if not _is_manager():
		frappe.throw(_("Only a manager can search for numbers."), frappe.PermissionError)
	from crm.integrations.telnyx import api as telnyx_api

	if not telnyx_api.enabled():
		frappe.throw(_("Telnyx is not configured on this site."))
	return telnyx_api.search_local_numbers(area_code, limit)


@frappe.whitelist()
def buy_number(number: str, label: str = "", emoji: str = ""):
	"""Purchase a Telnyx number and add it as a CRM Phone Line. Managers only."""
	if not _is_manager():
		frappe.throw(_("Only a manager can buy a number."), frappe.PermissionError)
	from crm.integrations.telnyx import api as telnyx_api

	if not telnyx_api.enabled():
		frappe.throw(_("Telnyx is not configured on this site."))
	e164 = desk.e164(number)
	if not e164:
		frappe.throw(_("That does not look like a phone number."))
	if frappe.db.exists(LINE, {"number": e164}) or frappe.db.exists(LINE, e164):
		frappe.throw(_("That number is already a line in the CRM."))
	ordered = telnyx_api.order_local_number(e164)
	phone_id = None
	try:
		for item in ordered.get("phone_numbers") or []:
			if isinstance(item, dict) and item.get("id"):
				phone_id = item.get("id")
				break
	except Exception:
		phone_id = None
	doc = frappe.get_doc(
		{
			"doctype": LINE,
			"number": e164,
			"label": (label or "").strip() or e164,
			"emoji": (emoji or "").strip(),
			"owner_user": frappe.session.user,
			"provider": TELNYX,
			"recording": "inherit",
			"active": 1,
			"telnyx_number_id": phone_id or "",
		}
	).insert(ignore_permissions=True)
	return {"ok": True, "name": doc.name, "number": e164, "order": ordered.get("id")}


@frappe.whitelist()
def set_my_mute(line: str, muted: int = 1):
	"""Mute notifications on a line you can see, without dropping view/use."""
	row = _require_line(line, "view")
	if not frappe.db.has_column(LINE_MEMBER, "mute"):
		frappe.throw(_("Mute is not set up on this site yet."))
	user = frappe.session.user
	muted = 1 if int(muted) else 0
	doc = frappe.get_doc(LINE, row["name"])
	found = False
	for m in doc.members:
		if m.user == user:
			m.mute = muted
			found = True
			break
	if not found:
		doc.append("members", {"user": user, "view": 1, "use": 1 if row["owner"] == user else 0, "ring": 1 if row["owner"] == user else 0, "mute": muted})
	doc.save(ignore_permissions=True)
	return {"ok": True, "muted": bool(muted), "line": row["name"]}


@frappe.whitelist()
def lines():
	"""Lines the session user can see, with their access and effective recording."""
	settings = phone_settings()
	user = frappe.session.user
	out = []
	for line in all_lines():
		access = desk.line_access(line, user)
		if not (access.get("view") or access.get("use") or access.get("ring")):
			continue
		out.append(
			{
				"name": line["name"],
				"number": line["number"],
				"label": line["label"] or line["number"],
				"emoji": line.get("emoji") or "",
				"owner": line["owner"],
				"active": line["active"],
				"access": access,
				"muted": bool(access.get("mute")),
				"recording": line["recording"],
				"recording_effective": desk.resolve_recording(line["recording"], settings["recording_default"]),
				"members": line["members"],
			}
		)
	return out


def _require_line(line_name: str, permission: str) -> dict:
	line = line_by_name(line_name) if line_name else None
	if not line:
		# No line named: fall back to the user's own Telnyx line from the agent table.
		own = sending_line(frappe.session.user, provider=TELNYX)
		line = line_by_number(own) if own else None
		if not line and own:
			line = {"name": own, "number": own, "label": own, "owner": frappe.session.user, "recording": "inherit", "active": True, "members": []}
	if not line:
		frappe.throw(_("Pick a phone line first."))
	if not line.get("active", True):
		frappe.throw(_("That line is inactive."))
	if not desk.can(line, frappe.session.user, permission):
		frappe.throw(_("You cannot {0} this line.").format(permission), frappe.PermissionError)
	return line


# ── state store ────────────────────────────────────────────────────────────────


def _load(desk_id: str) -> dict | None:
	raw = frappe.cache().hget(STATE_HASH, desk_id)
	if not raw:
		return None
	return _json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def _save(state: dict):
	frappe.cache().hset(STATE_HASH, state["desk_id"], _json.dumps(state))
	if state.get("call_log"):
		frappe.cache().hset(BY_CALL_LOG, state["call_log"], state["desk_id"])


def _drop(state: dict):
	frappe.cache().hdel(STATE_HASH, state["desk_id"])
	if state.get("call_log"):
		frappe.cache().hdel(BY_CALL_LOG, state["call_log"])


def _by_call_log(call_log: str) -> dict | None:
	desk_id = frappe.cache().hget(BY_CALL_LOG, call_log)
	if isinstance(desk_id, bytes):
		desk_id = desk_id.decode()
	return _load(desk_id) if desk_id else None


def _all_states() -> list[dict]:
	out = []
	for raw in (frappe.cache().hgetall(STATE_HASH) or {}).values():
		try:
			out.append(_json.loads(raw) if isinstance(raw, (str, bytes)) else raw)
		except Exception:
			continue
	return out


def publish_transcript(state: dict, segment: dict):
	"""Live dual-track words, only to people on this call."""
	row = {"call_log": state.get("call_log"), "desk_id": state.get("desk_id"), **segment}
	users = {state.get("rep")} | {s.get("user") for s in (state.get("supervisors") or {}).values()}
	users.discard(None)
	for user in users:
		try:
			frappe.publish_realtime("crm_transcript", row, user=user, after_commit=True)
		except Exception:
			pass


def publish_call(state: dict, event: str = "update"):
	"""`crm_telnyx_call` — site-wide, so every open dock and team bar updates."""
	try:
		frappe.publish_realtime(
			"crm_telnyx_call",
			{"event": event, **desk.active_call_row(state, _lead_name(state.get("lead")))},
			after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Telephony: realtime publish failed")


def _lead_name(lead):
	if not lead:
		return None
	try:
		return frappe.db.get_value("CRM Lead", lead, "lead_name") or lead
	except Exception:
		return lead


# ── rep reachability ───────────────────────────────────────────────────────────


def sip_username(user: str) -> str | None:
	"""The rep's WebRTC SIP username (from their telephony credential), cached."""
	field = "custom_telnyx_sip_username"
	if frappe.db.has_column("User", field):
		cached = (frappe.db.get_value("User", user, field) or "").strip()
		if cached:
			return cached
	key = f"crm:telnyx:sip:{user}"
	cached = frappe.cache().get_value(key)
	if cached:
		return cached.decode() if isinstance(cached, bytes) else cached
	try:
		from crm.integrations.telnyx import api as telnyx_api

		credential_id = telnyx_api._user_credential(user)
		data = telnyx_api.get_credential(credential_id)
		username = (data or {}).get("sip_username")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Telephony: SIP username lookup failed")
		return None
	if username:
		if frappe.db.has_column("User", field):
			frappe.db.set_value("User", user, field, username, update_modified=False)
		frappe.cache().set_value(key, username)
	return username


def user_mobile(user: str) -> str:
	for field in ("mobile_no", "phone"):
		if frappe.db.has_column("User", field):
			value = frappe.db.get_value("User", user, field)
			if value:
				return desk.e164(value)
	return ""


def _rep_target(user: str) -> tuple[str, str]:
	"""(to, via): the softphone when the rep has a credential, else their cell."""
	username = sip_username(user)
	if username:
		return desk.sip_uri(username), "softphone"
	cell = user_mobile(user)
	if cell:
		return cell, "cell"
	frappe.throw(_("You have no softphone credential or mobile number on file."))


# ── endpoints ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def dial(to: str, line: str = None, lead: str = None):
	"""Start a conference-first outbound call.

	Rings the REP first (their softphone, else their cell). When that leg is
	answered the webhook creates the conference and dials the other party into
	it, so the seller never hears dead air while a browser decides whether to
	pick up. Returns the rep leg's call_control_id and the desk id.
	"""
	from crm.integrations.telnyx import api as telnyx_api

	if not telnyx_api.enabled():
		frappe.throw(_("Telnyx is not configured on this site."))
	number = desk.e164(to)
	if not number:
		frappe.throw(_("That does not look like a phone number."))
	from crm.api.do_not_contact import is_blocked_number

	if is_blocked_number(number):
		frappe.throw(_("{0} has asked not to be contacted.").format(to))
	line_row = _require_line(line, "use")
	if not telnyx_api.voicemail_greeting(frappe.session.user):
		frappe.throw(
			_("Set your voicemail greeting before making calls — sellers call this number back."),
			title=_("Voicemail not set up"),
		)
	connection = (frappe.conf.get("telnyx_connection_id") or "").strip()
	if not connection:
		frappe.throw(_("No Telnyx call control application is configured."))

	desk_id = desk.new_desk_id()
	rep_to, via = _rep_target(frappe.session.user)
	state = desk.new_state(desk_id, "outbound", line_row["number"], rep=frappe.session.user, lead=lead, to=number, frm=line_row["number"])
	state["started_at"] = _now()
	client_state = desk.encode_client_state(
		kind="rep_leg", desk=desk_id, user=frappe.session.user, lead=lead, to=number, line=line_row["number"], via=via,
	)
	data = telnyx_api._post("/calls", desk.dial_leg_payload(connection, rep_to, line_row["number"], client_state))
	state["rep_leg"] = data.get("call_control_id")
	_save(state)
	publish_call(state, "dialing")
	return {"ok": True, "call_control_id": state["rep_leg"], "desk_id": desk_id, "to": number, "from": line_row["number"]}


@frappe.whitelist()
def active_calls():
	"""Every call currently up on the desk, for the team bar and the Live list."""
	out = []
	for state in _all_states():
		if state.get("state") == "ended":
			continue
		out.append(desk.active_call_row(state, _lead_name(state.get("lead"))))
	return out


def _dial_supervisor(state: dict, user: str, mode: str, transfer: bool = False) -> dict:
	from crm.integrations.telnyx import api as telnyx_api

	connection = (frappe.conf.get("telnyx_connection_id") or "").strip()
	if not connection:
		frappe.throw(_("No Telnyx call control application is configured."))
	sup_to, via = _rep_target(user)
	client_state = desk.encode_client_state(
		kind="supervisor_leg", desk=state["desk_id"], user=user, mode=mode, via=via, transfer=1 if transfer else None,
	)
	data = telnyx_api._post("/calls", desk.dial_leg_payload(connection, sup_to, state.get("line") or "", client_state))
	ccid = data.get("call_control_id")
	state.setdefault("supervisors", {})[ccid] = {"user": user, "mode": mode, "joined": False, "transfer": bool(transfer)}
	_save(state)
	return {"ok": True, "call_control_id": ccid, "mode": mode, "desk_id": state["desk_id"], "user": user}


@frappe.whitelist()
def join(call_log: str, mode: str = "monitor"):
	"""A closer joins an in-progress call as a supervisor."""
	if mode not in desk.MODES:
		frappe.throw(_("Unknown mode."))
	state = _by_call_log(call_log)
	if not state or state.get("state") != "active" or not state.get("conference_id"):
		frappe.throw(_("That call is not in progress."))
	if state.get("rep") == frappe.session.user:
		frappe.throw(_("You are already on this call."))
	line = line_by_number(state.get("line"))
	if line and not (desk.can(line, frappe.session.user, "use") or _is_manager()):
		frappe.throw(_("You cannot join calls on this line."), frappe.PermissionError)
	return _dial_supervisor(state, frappe.session.user, mode)


@frappe.whitelist()
def invite(call_log: str, user: str, mode: str = "barge"):
	"""The rep brings a teammate onto the live conference. Seller hears no join beep."""
	if mode not in desk.MODES:
		frappe.throw(_("Unknown mode."))
	state = _by_call_log(call_log)
	if not state or state.get("state") != "active" or not state.get("conference_id"):
		frappe.throw(_("That call is not in progress."))
	if state.get("rep") != frappe.session.user and not _is_manager():
		frappe.throw(_("Only the rep on this call can invite."), frappe.PermissionError)
	if not user or user == state.get("rep"):
		frappe.throw(_("Pick a teammate."))
	return _dial_supervisor(state, user, mode)


@frappe.whitelist()
def transfer(call_log: str, user: str):
	"""Blind transfer: ring the teammate; when they answer, the original rep drops."""
	state = _by_call_log(call_log)
	if not state or state.get("state") != "active" or not state.get("conference_id"):
		frappe.throw(_("That call is not in progress."))
	if state.get("rep") != frappe.session.user and not _is_manager():
		frappe.throw(_("Only the rep on this call can transfer."), frappe.PermissionError)
	if not user or user == state.get("rep"):
		frappe.throw(_("Pick a teammate to transfer to."))
	state["transfer_to"] = user
	_save(state)
	out = _dial_supervisor(state, user, "barge", transfer=True)
	out["transfer"] = True
	return out


@frappe.whitelist()
def set_mode(call_log: str, mode: str):
	"""Flip the session user's supervisor role live: monitor → whisper → barge."""
	from crm.integrations.telnyx import api as telnyx_api

	if mode not in desk.MODES:
		frappe.throw(_("Unknown mode."))
	state = _by_call_log(call_log)
	if not state:
		frappe.throw(_("That call is not in progress."))
	mine = [c for c, s in (state.get("supervisors") or {}).items() if s.get("user") == frappe.session.user]
	if not mine:
		frappe.throw(_("You have not joined this call."))
	ccid = mine[0]
	telnyx_api.conference_command(state["conference_id"], "update", desk.update_mode_payload(ccid, mode, state.get("rep_leg")))
	state["supervisors"][ccid]["mode"] = mode
	_save(state)
	publish_call(state)
	return {"ok": True, "mode": mode}


def _is_manager() -> bool:
	return bool({"System Manager", "Sales Manager"} & set(frappe.get_roles()))


@frappe.whitelist()
def live_one(lead: str, note: str = "", call_log: str = None):
	"""'Got a live one' from the desk: the existing alert, plus the call it is about."""
	from crm.api.live_one import alert

	if not call_log:
		for state in _all_states():
			if state.get("rep") == frappe.session.user and state.get("state") == "active":
				call_log = state.get("call_log")
				break
	return alert(lead, note, call_log)


def _history_ours(from_n, to_n, ours: set[str]) -> str:
	if last10(from_n) in ours:
		return desk.e164(from_n) or from_n or ""
	if last10(to_n) in ours:
		return desk.e164(to_n) or to_n or ""
	return ""


def _history_peer(from_n, to_n, ours: set[str], inbound: bool) -> str:
	"""The number that is not us. Falls back to the far-side field by direction."""
	far = from_n if inbound else to_n
	us = to_n if inbound else from_n
	if last10(far) and last10(far) not in ours:
		return desk.e164(far) or far or ""
	if last10(us) and last10(us) not in ours:
		return desk.e164(us) or us or ""
	return desk.e164(far) or far or ""


def _shape_call(r, ours: set[str]) -> dict:
	inbound = (r.type or "") == "Incoming"
	lead = r.reference_docname if r.reference_docname and r.reference_doctype == "CRM Lead" else (r.reference_docname if r.reference_docname else None)
	rep = r.caller if not inbound else r.receiver
	return {
		"kind": "call",
		"name": r.name,
		"number": _history_peer(r.get("from"), r.get("to"), ours, inbound),
		"line": _history_ours(r.get("from"), r.get("to"), ours),
		"at": r.start_time,
		"direction": "Incoming" if inbound else "Outgoing",
		"status": r.status,
		"result": r.status,
		"duration": r.duration,
		"rep": rep,
		"rep_name": rep,
		"lead": lead,
		"lead_name": None,
		"provider": call_provider(r),
		"recording": "ready" if r.recording_url else "none",
		"recording_url": r.recording_url,
		"has_transcript": bool(r.get("custom_transcript")),
		"transcript": r.get("custom_transcript") or "",
		"summary": r.get("custom_ai_summary"),
		"outcome": r.get("custom_call_class"),
		"reference_doctype": r.reference_doctype if r.reference_docname else None,
		"reference_docname": r.reference_docname,
	}


def _shape_text(r, ours: set[str]) -> dict:
	inbound = (r.direction or "").lower().startswith("in")
	lead = r.reference_docname if r.reference_docname and r.reference_doctype == "CRM Lead" else (r.reference_docname if r.reference_docname else None)
	media = r.get("media")
	try:
		media = _json.loads(media) if isinstance(media, str) and media else (media or [])
	except ValueError:
		media = []
	return {
		"kind": "text",
		"name": r.name,
		"number": _history_peer(r.get("from"), r.get("to"), ours, inbound),
		"line": _history_ours(r.get("from"), r.get("to"), ours),
		"at": r.message_date,
		"direction": "in" if inbound else "out",
		"text": r.content,
		"status": r.status,
		"provider": message_provider(r),
		"media": media,
		"rep": r.get("sent_by"),
		"sender_name": r.get("sent_by"),
		"lead": lead,
		"lead_name": None,
		"reference_doctype": r.reference_doctype if r.reference_docname else None,
		"reference_docname": r.reference_docname,
	}


@frappe.whitelist()
def history(number: str = None, limit: int = 100):
	"""Call + text timeline.

	With `number`: that peer, oldest first (the dock conversation).
	Without: the session user's recent activity across their lines, newest first
	(the dock's Recent / Conversations tabs).
	"""
	key = last10(number)
	limit = max(1, min(int(limit or 100), 500))
	ours = {last10(l["number"]) for l in lines() if last10(l["number"])}
	if number and not key:
		return []
	if key:
		ours.add(key)
	where_num = "RIGHT(REGEXP_REPLACE(COALESCE(`from`,''), '[^0-9]', ''), 10) = %s OR RIGHT(REGEXP_REPLACE(COALESCE(`to`,''), '[^0-9]', ''), 10) = %s"
	events = []
	if frappe.db.exists("DocType", "CRM Call Log"):
		fields = ["name", "`from`", "`to`", "type", "status", "duration", "start_time", "end_time", "caller", "receiver", "medium", "recording_url", "reference_doctype", "reference_docname"]
		for extra in ("custom_transcript", "custom_ai_summary", "custom_call_class"):
			if frappe.db.has_column("CRM Call Log", extra):
				fields.append(extra)
		if key:
			rows = frappe.db.sql(
				f"SELECT {', '.join(fields)} FROM `tabCRM Call Log` WHERE {where_num} ORDER BY start_time DESC LIMIT %s",
				(key, key, limit), as_dict=True,
			)
		else:
			ours_list = list(ours) or ["________"]
			in_list = ",".join(["%s"] * len(ours_list))
			rows = frappe.db.sql(
				f"""SELECT {', '.join(fields)} FROM `tabCRM Call Log`
				    WHERE caller = %s OR receiver = %s
				       OR RIGHT(REGEXP_REPLACE(COALESCE(`from`,''), '[^0-9]', ''), 10) IN ({in_list})
				       OR RIGHT(REGEXP_REPLACE(COALESCE(`to`,''), '[^0-9]', ''), 10) IN ({in_list})
				    ORDER BY start_time DESC LIMIT %s""",
				(frappe.session.user, frappe.session.user, *ours_list, *ours_list, limit),
				as_dict=True,
			)
		for r in rows:
			events.append(_shape_call(r, ours if not key else {key}))
	if frappe.db.exists("DocType", "Quo Message"):
		fields = ["name", "`from`", "`to`", "direction", "content", "message_date", "status", "reference_doctype", "reference_docname"]
		for extra in ("provider", "media", "sent_by"):
			if frappe.db.has_column("Quo Message", extra):
				fields.append(extra)
		if key:
			rows = frappe.db.sql(
				f"SELECT {', '.join(fields)} FROM `tabQuo Message` WHERE {where_num} ORDER BY message_date DESC LIMIT %s",
				(key, key, limit), as_dict=True,
			)
		else:
			ours_list = list(ours) or ["________"]
			in_list = ",".join(["%s"] * len(ours_list))
			rows = frappe.db.sql(
				f"""SELECT {', '.join(fields)} FROM `tabQuo Message`
				    WHERE COALESCE(sent_by,'') = %s
				       OR RIGHT(REGEXP_REPLACE(COALESCE(`from`,''), '[^0-9]', ''), 10) IN ({in_list})
				       OR RIGHT(REGEXP_REPLACE(COALESCE(`to`,''), '[^0-9]', ''), 10) IN ({in_list})
				    ORDER BY message_date DESC LIMIT %s""",
				(frappe.session.user, *ours_list, *ours_list, limit),
				as_dict=True,
			)
		for r in rows:
			events.append(_shape_text(r, ours if not key else {key}))
	events.sort(key=lambda e: str(e.get("at") or ""), reverse=not bool(key))
	return events[:limit] if not key else events[-limit:]


@frappe.whitelist()
def inbox(limit: int = 200, line: str = None):
	"""One row per peer number, newest first — including numbers with no CRM lead."""
	events = history(limit=limit)
	if line:
		row = line_by_name(line)
		want = last10((row or {}).get("number") or line)
		events = [e for e in events if last10(e.get("line")) == want]
	groups: dict[str, dict] = {}
	order: list[str] = []
	for e in events:
		key = last10(e.get("number"))
		if not key:
			continue
		if key not in groups:
			order.append(key)
			groups[key] = {
				"number": e.get("number") or "",
				"lead": e.get("lead"),
				"lead_name": e.get("lead_name"),
				"last_at": e.get("at"),
				"last_kind": e.get("kind"),
				"last_text": (e.get("text") or e.get("status") or ""),
				"direction": e.get("direction"),
				"calls": 0,
				"texts": 0,
			}
		g = groups[key]
		if e.get("kind") == "call":
			g["calls"] += 1
		else:
			g["texts"] += 1
		if e.get("lead") and not g.get("lead"):
			g["lead"] = e.get("lead")
			g["lead_name"] = e.get("lead_name")
	leads = [g["lead"] for g in groups.values() if g.get("lead")]
	names = {}
	if leads and frappe.db.exists("DocType", "CRM Lead"):
		names = {
			r.name: r.lead_name
			for r in frappe.get_all("CRM Lead", filters={"name": ("in", leads)}, fields=["name", "lead_name"])
		}
	out = []
	for key in order:
		g = groups[key]
		g["lead_name"] = names.get(g.get("lead")) or g.get("lead_name")
		g["title"] = g["lead_name"] or g["number"]
		out.append(g)
	return out


@frappe.whitelist()
def send_text(to: str, text: str, line: str = None):
	"""Text from a line the session user may use. Lands as a Quo Message (provider Telnyx)."""
	from crm.integrations.telnyx import api as telnyx_api

	line_row = _require_line(line, "use")
	number = desk.e164(to)
	if not number:
		frappe.throw(_("That does not look like a phone number."))
	from crm.api.do_not_contact import is_blocked_number

	if is_blocked_number(number):
		frappe.throw(_("{0} has asked not to be contacted.").format(to))
	from crm.api.text_delivery import text_block_reason

	reason = text_block_reason(number)
	if reason:
		frappe.throw(reason)
	doctype, name = None, None
	try:
		from crm.integrations.telnyx.webhook import _link

		doctype, name = _link(number)
	except Exception:
		pass
	return telnyx_api.send_sms(number, text, reference_doctype=doctype, reference_docname=name, frm=line_row["number"])


@frappe.whitelist()
def lookup(number: str):
	"""Lead + DNC for a number, used by the dock before dial/text."""
	from crm.api.do_not_contact import is_blocked_number

	e = desk.e164(number)
	if not e:
		return {"number": "", "dnc": False, "lead": None, "lead_name": None}
	doctype = name = None
	try:
		from crm.integrations.telnyx.webhook import _link

		doctype, name = _link(e)
	except Exception:
		pass
	lead = name if doctype == "CRM Lead" else None
	return {
		"number": e,
		"dnc": bool(is_blocked_number(e)),
		"lead": lead,
		"lead_name": _lead_name(lead) if lead else None,
		"doctype": doctype,
		"name": name,
	}


@frappe.whitelist()
def link_lead(lead: str, call_log: str = None, number: str = None):
	"""Attach a lead to the live call and/or this number's recent call logs."""
	if not lead or not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("That lead does not exist."))
	if call_log and frappe.db.exists("CRM Call Log", call_log):
		frappe.db.set_value("CRM Call Log", call_log, {"reference_doctype": "CRM Lead", "reference_docname": lead})
		state = _by_call_log(call_log)
		if state:
			state["lead"] = lead
			_save(state)
			publish_call(state)
	elif number:
		key = last10(number)
		if key and frappe.db.exists("DocType", "CRM Call Log"):
			for name in frappe.get_all(
				"CRM Call Log",
				filters=[["from", "like", f"%{key}"]],
				pluck="name",
				limit=20,
			):
				frappe.db.set_value("CRM Call Log", name, {"reference_doctype": "CRM Lead", "reference_docname": lead})
	return {"ok": True, "lead": lead, "lead_name": _lead_name(lead)}


@frappe.whitelist()
def hold(call_log: str, held: int = 1):
	"""Hold or resume the EXTERNAL party in the conference (not the rep)."""
	from crm.integrations.telnyx import api as telnyx_api

	state = _by_call_log(call_log)
	if not state or not state.get("conference_id"):
		frappe.throw(_("That call is not in progress."))
	peer = desk.external_leg(state)
	if not peer:
		frappe.throw(_("There is no other party to hold."))
	action, body = desk.hold_payload([peer], held=bool(int(held)))
	telnyx_api.conference_command(state["conference_id"], action, body)
	state["held"] = bool(int(held))
	_save(state)
	publish_call(state)
	return {"ok": True, "held": state["held"]}




@frappe.whitelist()
def decline(call_log: str = None, desk_id: str = None):
	"""Reject an inbound ring. Same hangup on our unanswered leg."""
	return hangup(call_log=call_log, desk_id=desk_id)

@frappe.whitelist()
def hangup(call_log: str = None, desk_id: str = None):
	"""End the session user's leg (rep ends the call; a supervisor just leaves)."""
	from crm.integrations.telnyx import api as telnyx_api

	state = _by_call_log(call_log) if call_log else (_load(desk_id) if desk_id else None)
	if not state:
		return {"ok": True, "ignored": "no call"}
	user = frappe.session.user
	for ccid, s in list((state.get("supervisors") or {}).items()):
		if s.get("user") == user:
			telnyx_api.command(ccid, "hangup")
			return {"ok": True, "left": True}
	if state.get("rep") == user:
		for ccid in (state.get("rep_leg"), desk.external_leg(state)):
			if ccid:
				telnyx_api.command(ccid, "hangup")
		return {"ok": True, "ended": True}
	frappe.throw(_("You are not on this call."), frappe.PermissionError)

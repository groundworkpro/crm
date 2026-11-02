"""AI call-review bot — the daily "Integrity Report" (Groundwork).

Once a day this reviews every recorded sales call from the prior day with an LLM (Groq)
and reports back to Lance on:
  1. INTEGRITY — anything not fully honest, or "salesy" instead of plainly clear
     (the floor: judged on every call).
  2. MOTIVATION — how well the rep got to *why* the seller is selling (scored 0-5
     when relevant; null on follow-ups / logistics / voicemails).
  3. Where the lead is at + what could've been done differently.

It emails Lance one digest, and stores a `CRM Call AI Review` row per call so the
notes show inside the CRM (the Call Review tab — restricted to Lance — renders them).

Architecture mirrors the rest of this fork's app-code features:
  - The LLM call lives here in app code (the server-script sandbox can't do it),
    reading the key from site_config (`frappe.conf.get`), like the underwriting /
    e-sign features read their tokens.
  - The storage doctype (`CRM Call AI Review`) is an OPS-repo deliverable
    (`../frappe-crm-deploy`); the `groq_api_key` site-config value is mirrored from
    the existing Infisical GROQ_API_KEY. Everything here guards on
    `frappe.db.exists` / `has_field` so it no-ops cleanly on an unprovisioned site.

Reuses `crm.api.call_transcript._build_transcript` for clean rep/lead dialogue.
"""

import json
import time

import frappe
import requests
from frappe import _
from frappe.utils import add_days, get_url, getdate, now_datetime, strip_html, today

from crm.api.call_transcript import _build_transcript, _last10

# --- config (model is swappable via site_config `call_review_model`) --------------
# Groq gpt-oss-120b (key `groq_api_key` in site_config, from Infisical GROQ_API_KEY).
# Gemini 2.5 Flash until 2026-11-02, when its prepaid credit ran out.
MODEL = "openai/gpt-oss-120b"
LLM_URL = "https://api.groq.com/openai/v1/chat/completions"
# Reasoning tokens count against this cap — keep it generous so the JSON is never cut off.
MAX_OUTPUT_TOKENS = 8000
HTTP_TIMEOUT = 120
MAX_RETRIES = 3

MIN_SECONDS = 60  # only review calls with real talk time + a transcript
MAX_DIALOGUE_CHARS = 24000  # head+tail-truncate pathological transcripts, never drop

DIGEST_RECIPIENT = "lance.johnson@groundworkpro.com"
# Who may see the Call Review tab + these AI notes (also enforced in reports.py).
CALL_REVIEW_USER = "lance.johnson@groundworkpro.com"

AI_REVIEW_DOCTYPE = "CRM Call AI Review"
MEMORY_DOCTYPE = "CRM Call Review Memory"  # reviewer-taught rules + per-contact facts

OVERALL_FLAGS = ("ok", "review", "serious")
_FLAG_RANK = {"serious": 0, "review": 1, "ok": 2}

SYSTEM_PROMPT = """You review recorded sales calls for a real-estate WHOLESALING company that buys houses directly from homeowners. You are coaching the rep, and the owner wants two things above all: that reps are completely honest and plain-spoken, and that they actually uncover the seller's real motivation.

Return your assessment via the provided JSON schema. Judge ONLY what is in the transcript.

Each transcript line is prefixed with its start time in whole seconds, like "[123] REP: ...". Use those numbers to locate moments precisely.

If a section titled "GUIDANCE THE REVIEWER HAS TAUGHT YOU" is present, treat it as authoritative and apply it. It has general house rules and facts about this specific contact. For example, if it says this call is to a dispo partner (a fellow investor we sell deals to — not a homeowner selling their house), then normal investor shop-talk (assigning the deal, exit strategies, partners) is NOT an integrity issue, and motivation does not apply (set it null).

1) INTEGRITY (the floor — assess on EVERY call)
Flag anything that is not fully honest, is vague/jargony, or is "salesy" persuasion instead of plain clarity. The company wants reps to say plainly what they actually do.
- BAD (vague menu-of-options jargon meant to sound impressive): "we have multiple exit strategies like buy and hold, fix and flip, or wholesale."
- GOOD (plain, honest, concrete): "we're going to buy it and resell it to a builder or homeowner as quickly as we can — we actually work on getting it pre-sold right away."
Also flag: overpromising, implying things that aren't true, hiding that the company resells the property, pressure tactics, or fuzzy answers to direct questions.

GROUNDWORK'S APPROVED FRAMING (the owner's talking points — hold reps to these):
- Up-front wholesale disclosure: on a discovery or offer conversation, the rep should tell the seller plainly and EARLY that we buy the house and immediately resell it — to a builder or investor. The model answer: "We will buy your house and immediately resell it to a builder. Our contract with you lets us market it to builders and renovation experts before we buy it. Sometimes we own it for all of five minutes; sometimes the builder closes on your house and we never own it at all. The important thing is you get your number. And if after 7-10 days we realize we can't make the project work, we'll tell you what we got wrong." Flag it when a rep dances around, delays, or obscures this on a call where the process comes up.
- Say we'll make a profit: when explaining how the deal works, the rep should plainly acknowledge we'll make a profit. Flag evasion when the seller asks or the topic naturally arises.
- People visiting the house are "builders" or "investors" — the people we're looking to sell to. Flag calling them "contractors" or "partners" (usually not accurate).
- Multiple visits: if visits come up, the rep should set the expectation honestly — "we might have to send multiple people to your house before we close, but we'll let you know beforehand; you don't have to have the house cleaned up."

BANNED PHRASES (flag every use, regardless of call type):
- "OUR builders / OUR investors / OUR partners" — we're virtually wholesaling and generally have no pre-existing relationship; the possessive implies one.
- The old three-exit-strategies menu ("buy and hold, fix and flip, or assign/wholesale") — the real plan is a double-close; the menu is misdirection.
- Describing prospective buyers who'll visit as "contractors" or "partners".

NOVATIONS (tight guardrails — apply whenever a novation deal is pitched or discussed):
A novation means we list the seller's house on the open market with a real-estate agent under our agreement. The seller must come away understanding all four of these, plainly stated — flag the call if the rep advances a novation while obscuring, skipping, or misrepresenting any of them:
1. The work: we're just going to list it with an agent — and the seller could do that themselves.
2. Access: we'll need access to the property and to come by for showings.
3. The money: they'll make LESS money going this route with us than listing it themselves, even after accounting for agent fees.
4. Our value: what they're paying for is simplicity — we work with the agent, handle the negotiations, and manage the process.

Only flag a MISSING disclosure (wholesale up front, profit, multiple visits) when the call is the kind where it belonged — a discovery/offer/process conversation. Never ding a voicemail, scheduling call, or quick logistics touch for not covering them. A banned phrase, though, is a flag on any call.

For each issue give the rep's VERBATIM quote, `at_time` (the [seconds] number from the start of the transcript line where the quote begins), why it's a problem, and a plain better_phrasing. (For a missing disclosure, quote the moment it should have happened — e.g. the seller's question that got the fuzzy answer — or use an empty quote with at_time null if there's no single moment.) If the call is clean, return an empty list — do not invent issues.

2) MOTIVATION (score 0-5, or null)
Score how well the rep uncovered WHY the seller wants to sell (the real situation/timeline/pain behind it), only when the call is the kind where that applies.
- 5: clearly drew out the underlying reason, timeline, and what the seller actually needs.
- 3: surface reason only ("just want to sell").
- 0: never explored it on a call where they should have.
- null: a follow-up, logistics/scheduling call, voicemail, or otherwise not a discovery conversation — don't force a score.
Put your reasoning in motivation_reason (one or two sentences).

3) STATUS + COACHING
lead_status: one short sentence on where this lead seems to be after the call.
what_could_be_better: 1-3 short, specific coaching points the rep could apply next time. Empty string if the call was genuinely clean and well-run.

overall_flag:
- "serious": a real integrity problem (dishonesty, hiding the resale, pressure).
- "review": worth a look (salesy/vague language, or weak motivation discovery on a discovery call).
- "ok": clean and on-message.

Be fair and concrete. Reps are busy; only flag things that actually matter."""

# Gemini responseSchema dialect (OpenAPI subset; _json_schema converts it for Groq): uppercase types, `nullable`,
# `propertyOrdering`, no `additionalProperties`.
RESULT_SCHEMA = {
	"type": "OBJECT",
	"properties": {
		"motivation_score": {"type": "INTEGER", "nullable": True},
		"motivation_reason": {"type": "STRING"},
		"integrity_issues": {
			"type": "ARRAY",
			"items": {
				"type": "OBJECT",
				"properties": {
					"quote": {"type": "STRING"}, "at_time": {"type": "INTEGER", "nullable": True},
					"why": {"type": "STRING"},
					"better_phrasing": {"type": "STRING"},
				},
				"required": ["quote", "at_time", "why", "better_phrasing"],
				"propertyOrdering": ["quote", "at_time", "why", "better_phrasing"],
			},
		},
		"lead_status": {"type": "STRING"},
		"what_could_be_better": {"type": "STRING"},
		"overall_flag": {"type": "STRING", "enum": list(OVERALL_FLAGS)},
	},
	"required": [
		"motivation_score",
		"motivation_reason",
		"integrity_issues",
		"lead_status",
		"what_could_be_better",
		"overall_flag",
	],
	"propertyOrdering": [
		"motivation_score",
		"motivation_reason",
		"integrity_issues",
		"lead_status",
		"what_could_be_better",
		"overall_flag",
	],
}


# When the reviewer replies, the LLM returns a revised review PLUS the lessons to
# remember (global house rules and/or per-contact facts).
REPLY_SCHEMA = {
	"type": "OBJECT",
	"properties": {
		"review": RESULT_SCHEMA,
		"memory_items": {
			"type": "ARRAY",
			"items": {
				"type": "OBJECT",
				"properties": {
					"scope": {"type": "STRING", "enum": ["global", "contact"]},
					"lesson": {"type": "STRING"},
				},
				"required": ["scope", "lesson"],
				"propertyOrdering": ["scope", "lesson"],
			},
		},
	},
	"required": ["review", "memory_items"],
	"propertyOrdering": ["review", "memory_items"],
}


# ---------------------------------------------------------------------------------
# Scheduler entry point
# ---------------------------------------------------------------------------------
def run_daily_integrity_report(date: str = None):
	"""`daily_long` scheduler target. Reviews the prior day's qualifying calls,
	stores a CRM Call AI Review per call, and emails Lance one digest.

	Idempotent: calls already reviewed are skipped, so it's safe to re-run.
	Never raises (a scheduler job must not blow up the worker)."""
	if not frappe.db.exists("DocType", AI_REVIEW_DOCTYPE):
		frappe.logger("call_review_ai").info("CRM Call AI Review doctype not provisioned; skipping")
		return
	if not (frappe.conf.get("groq_api_key") or "").strip():
		frappe.logger("call_review_ai").info("groq_api_key not configured; skipping")
		return

	# Default to yesterday. Calls are stored in Chicago wall-clock (see reports.py /
	# CallReview.vue) — we filter on that same value, no UTC conversion.
	day = getdate(date) if date else getdate(add_days(today(), -1))
	start = f"{day} 00:00:00"
	end = f"{day} 23:59:59.999999"

	candidates = _candidate_calls(start, end)
	already = set(
		frappe.get_all(
			AI_REVIEW_DOCTYPE,
			filters={"call_log": ["in", [c["name"] for c in candidates] or [""]]},
			pluck="call_log",
		)
	) if candidates else set()

	reviewed, failed = 0, 0
	for c in candidates:
		if c["name"] in already:
			continue
		try:
			result = _review_one(c["name"])
			if result is not None:
				reviewed += 1
		except Exception:
			failed += 1
			frappe.log_error(
				title="AI call review failed",
				message=f"call={c['name']}\n{frappe.get_traceback()}",
			)
		frappe.db.commit()

	_send_digest(day, candidates, failed)
	frappe.logger("call_review_ai").info(
		f"Integrity report {day}: {len(candidates)} candidates, {reviewed} newly reviewed, {failed} failed"
	)


def _candidate_calls(start, end):
	"""Calls in the window with real talk time AND a stored transcript."""
	meta = frappe.get_meta("CRM Call Log")
	if not meta.has_field("custom_transcript"):
		return []
	return frappe.get_all(
		"CRM Call Log",
		filters={
			"start_time": ["between", [start, end]],
			"duration": [">=", MIN_SECONDS],
			"custom_transcript": ["is", "set"],
		},
		fields=[
			"name", "type", "duration", "start_time",
			"caller", "receiver", "from", "to", "reference_doctype", "reference_docname",
		],
		order_by="start_time asc",
	)


# ---------------------------------------------------------------------------------
# Single-call review (shared by the daily job and the manual endpoint)
# ---------------------------------------------------------------------------------
def _review_one(call_log: str):
	"""Build input → call the LLM → persist one CRM Call AI Review row.
	Returns the parsed+validated result dict, or None if there's no usable
	transcript."""
	doc = frappe.get_doc("CRM Call Log", call_log)
	transcript = _build_transcript(doc)
	if not transcript.get("has_transcript"):
		return None

	user_content = _build_llm_input(doc, transcript)
	result = _normalize_result(_call_llm(user_content))
	_persist_review(doc, result)
	return result


def _build_llm_input(doc, transcript):
	"""Assemble the per-call user message: rep/lead labels, the dialogue, and a
	compact snapshot of where the lead is at (status, recent notes/tasks/comments,
	prior-call AI summaries)."""
	parts = []
	parts.append(f"Rep: {transcript.get('rep_name')}    Seller/lead: {transcript.get('lead_name')}")
	parts.append(f"Call direction: {doc.get('type')}    Duration (s): {int(doc.get('duration') or 0)}")

	ctx = _lead_context(doc.get("reference_doctype"), doc.get("reference_docname"))
	if ctx:
		parts.append("\n--- Where this lead is at ---\n" + ctx)

	mem = _memory_section(doc)
	if mem:
		parts.append("\n--- GUIDANCE THE REVIEWER HAS TAUGHT YOU ---\n" + mem)

	parts.append("\n--- Call transcript ---\n" + _render_dialogue(transcript.get("dialogue") or []))
	return "\n".join(parts)


def _render_dialogue(dialogue):
	lines = [f"[{int(seg.get('start') or 0)}] {'REP' if seg['speaker'] == 'rep' else 'LEAD'}: {seg['content']}" for seg in dialogue]
	text = "\n".join(lines)
	if len(text) > MAX_DIALOGUE_CHARS:
		half = MAX_DIALOGUE_CHARS // 2
		text = text[:half] + "\n\n[... middle of call omitted for length ...]\n\n" + text[-half:]
	return text or "(empty transcript)"


def _lead_context(ref_doctype, ref_docname):
	"""A compact, background-safe snapshot of the lead (no heavy activity tuple)."""
	if ref_doctype != "CRM Lead" or not ref_docname or not frappe.db.exists("CRM Lead", ref_docname):
		return ""
	lead = frappe.get_doc("CRM Lead", ref_docname)
	bits = []
	header = []
	if lead.get("status"):
		header.append(f"status={lead.get('status')}")
	if lead.get("source"):
		header.append(f"source={lead.get('source')}")
	for f in ("property_address", "first_call_motivated", "first_call_on_price"):
		if lead.get(f):
			header.append(f"{f}={lead.get(f)}")
	if header:
		bits.append("Lead: " + ", ".join(header))

	tasks = frappe.get_all(
		"CRM Task",
		filters={"reference_docname": ref_docname, "status": ["not in", ["Done", "Cancelled", "Canceled"]]},
		fields=["title", "due_date"],
		order_by="due_date asc",
		limit=5,
	)
	if tasks:
		bits.append("Open tasks: " + "; ".join(
			f"{t['title']}" + (f" (due {t['due_date']})" if t.get("due_date") else "") for t in tasks
		))

	comments = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "CRM Lead", "reference_name": ref_docname, "comment_type": "Comment"},
		fields=["content", "creation"],
		order_by="creation desc",
		limit=6,
	)
	if comments:
		cleaned = [strip_html(c["content"] or "").strip() for c in reversed(comments)]
		cleaned = [c for c in cleaned if c]
		if cleaned:
			bits.append("Recent comments:\n- " + "\n- ".join(cleaned))

	if frappe.get_meta("CRM Call Log").has_field("custom_ai_summary"):
		prior = frappe.get_all(
			"CRM Call Log",
			filters={"reference_docname": ref_docname, "custom_ai_summary": ["is", "set"]},
			fields=["custom_ai_summary"],
			order_by="start_time desc",
			limit=3,
		)
		summaries = [p["custom_ai_summary"].strip() for p in reversed(prior) if (p.get("custom_ai_summary") or "").strip()]
		if summaries:
			bits.append("Prior call summaries:\n- " + "\n- ".join(summaries))

	return "\n".join(bits)


# ---------------------------------------------------------------------------------
# Reviewer-taught memory (global house rules + per-contact facts)
# ---------------------------------------------------------------------------------
def _other_party_last10(doc):
	"""Last 10 digits of the non-rep party on the call — the per-contact key."""
	their = doc.to if doc.get("type") == "Outgoing" else doc.get("from")
	return _last10(their)


def _memory_section(doc):
	"""Render reviewer-taught guidance for this call: global house rules + facts
	about this specific contact (matched by phone). '' if none / unprovisioned."""
	if not frappe.db.exists("DocType", MEMORY_DOCTYPE):
		return ""
	bits = []
	rules = [
		(r.get("lesson") or "").strip()
		for r in frappe.get_all(MEMORY_DOCTYPE, filters={"scope": "global"}, fields=["lesson"], order_by="creation asc")
	]
	rules = [r for r in rules if r]
	if rules:
		bits.append("General house rules:\n- " + "\n- ".join(rules))
	phone = _other_party_last10(doc)
	if phone:
		facts = [
			(f.get("lesson") or "").strip()
			for f in frappe.get_all(MEMORY_DOCTYPE, filters={"scope": "contact", "phone": phone}, fields=["lesson"], order_by="creation asc")
		]
		facts = [f for f in facts if f]
		if facts:
			bits.append("About THIS contact:\n- " + "\n- ".join(facts))
	return "\n\n".join(bits)


def _record_memory(items, doc):
	"""Persist memory_items from the reply call. contact-scoped items are keyed to
	this call's other-party phone. Skips exact-duplicate lessons. Returns the
	lessons newly remembered."""
	if not items or not frappe.db.exists("DocType", MEMORY_DOCTYPE):
		return []
	phone = _other_party_last10(doc)
	display = ""
	if doc.get("reference_doctype") == "CRM Lead" and doc.get("reference_docname"):
		display = frappe.db.get_value("CRM Lead", doc.reference_docname, "lead_name") or doc.reference_docname
	learned = []
	for it in items:
		if not isinstance(it, dict):
			continue
		scope = it.get("scope")
		lesson = (it.get("lesson") or "").strip()
		if scope not in ("global", "contact") or not lesson:
			continue
		filters = {"scope": scope, "lesson": lesson}
		if scope == "contact":
			filters["phone"] = phone
		if frappe.db.exists(MEMORY_DOCTYPE, filters):
			continue
		row = frappe.new_doc(MEMORY_DOCTYPE)
		row.scope = scope
		row.lesson = lesson
		row.source_call = doc.name
		if scope == "contact":
			row.phone = phone
			row.display_name = display
			row.reference_doctype = doc.get("reference_doctype")
			row.reference_docname = doc.get("reference_docname")
		row.save(ignore_permissions=True)
		learned.append(lesson)
	return learned


# ---------------------------------------------------------------------------------
# LLM call (Groq, OpenAI-compatible; moved off Gemini 2026-11-02 when its credit ran out)
# ---------------------------------------------------------------------------------
def _json_schema(node):
	"""Gemini schema dialect -> strict JSON Schema (lowercase types, nullable as a
	type union, no propertyOrdering, closed objects)."""
	out = {}
	t = node["type"].lower()
	out["type"] = [t, "null"] if node.get("nullable") else t
	if "enum" in node:
		out["enum"] = node["enum"]
	if t == "object":
		out["properties"] = {k: _json_schema(v) for k, v in node["properties"].items()}
		out["required"] = node["required"]
		out["additionalProperties"] = False
	if t == "array":
		out["items"] = _json_schema(node["items"])
	return out


def _call_llm(user_content, schema=RESULT_SCHEMA, system=SYSTEM_PROMPT):
	"""POST to Groq chat completions with a strict JSON schema + the shared system
	rubric. Returns the parsed JSON object. Raises on persistent failure."""
	api_key = (frappe.conf.get("groq_api_key") or "").strip()
	if not api_key:
		frappe.throw(_("groq_api_key is not configured."))

	body = {
		"model": frappe.conf.get("call_review_model") or MODEL,
		"messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
		"response_format": {
			"type": "json_schema",
			"json_schema": {"name": "review", "strict": True, "schema": _json_schema(schema)},
		},
		# Groq's default output cap is 3,072 tokens, reasoning included — always set it
		"max_completion_tokens": MAX_OUTPUT_TOKENS,
		"temperature": 0.2,
	}
	headers = {"content-type": "application/json", "authorization": f"Bearer {api_key}"}

	last_err = None
	for attempt in range(MAX_RETRIES):
		try:
			resp = requests.post(LLM_URL, headers=headers, json=body, timeout=HTTP_TIMEOUT)
		except requests.RequestException as e:
			last_err = e
			time.sleep(2 ** attempt)
			continue

		if resp.status_code == 200:
			choice = (resp.json().get("choices") or [{}])[0]
			text = (choice.get("message") or {}).get("content") or ""
			if choice.get("finish_reason") != "stop" or not text:
				raise ValueError(f"Groq returned no complete answer (finish_reason={choice.get('finish_reason')})")
			return json.loads(text)

		# 429 / 5xx → retry with backoff; other 4xx → fail fast
		if resp.status_code == 429 or resp.status_code >= 500:
			last_err = Exception(f"Groq {resp.status_code}: {resp.text[:300]}")
			time.sleep(2 ** attempt)
			continue
		raise Exception(f"Groq {resp.status_code}: {resp.text[:500]}")

	raise Exception(f"Groq call failed after {MAX_RETRIES} attempts: {last_err}")


def _clean_issues(raw_issues):
	"""Coerce each integrity issue to clean strings + an int|None at_time (seconds)."""
	out = []
	for it in raw_issues if isinstance(raw_issues, list) else []:
		if not isinstance(it, dict):
			continue
		at = it.get("at_time")
		try:
			at = int(at) if at is not None else None
		except (TypeError, ValueError):
			at = None
		if at is not None and at < 0:
			at = None
		out.append({
			"quote": (it.get("quote") or "").strip(),
			"at_time": at,
			"why": (it.get("why") or "").strip(),
			"better_phrasing": (it.get("better_phrasing") or "").strip(),
		})
	return out


def _normalize_result(raw):
	"""Defensive validation — the schema already constrains shape, but clamp the
	score range (json_schema can't express min/max) and coerce the obvious bits."""
	score = raw.get("motivation_score")
	if isinstance(score, bool):  # bools are ints in python
		score = None
	if score is not None:
		try:
			score = max(0, min(5, int(score)))
		except (TypeError, ValueError):
			score = None
	issues = _clean_issues(raw.get("integrity_issues"))
	flag = raw.get("overall_flag")
	if flag not in OVERALL_FLAGS:
		flag = "review" if issues else "ok"
	return {
		"motivation_score": score,
		"motivation_reason": (raw.get("motivation_reason") or "").strip(),
		"integrity_issues": issues,
		"lead_status": (raw.get("lead_status") or "").strip(),
		"what_could_be_better": (raw.get("what_could_be_better") or "").strip(),
		"overall_flag": flag,
	}


def _parse_score(v):
	"""motivation_score is stored as Data ('' = null/n-a, else '0'-'5'). Convert
	back to int|None for the API/UI."""
	if v in (None, ""):
		return None
	try:
		return int(v)
	except (TypeError, ValueError):
		return None


def _persist_review(doc, result, feedback=None):
	"""One CRM Call AI Review row per call. The doctype autonames by `call_log`,
	so a duplicate insert is the idempotency guard."""
	existing = frappe.db.get_value(AI_REVIEW_DOCTYPE, {"call_log": doc.name}, "name")
	if existing:
		row = frappe.get_doc(AI_REVIEW_DOCTYPE, existing)
	else:
		row = frappe.new_doc(AI_REVIEW_DOCTYPE)
	row.call_log = doc.name
	row.reference_doctype = doc.get("reference_doctype")
	row.reference_docname = doc.get("reference_docname")
	# Data field: "" preserves null (n/a) distinctly from "0" (scored zero)
	row.motivation_score = "" if result["motivation_score"] is None else str(result["motivation_score"])
	row.motivation_reason = result["motivation_reason"]
	row.integrity_issues = json.dumps(result["integrity_issues"])
	row.lead_status = result["lead_status"]
	row.what_could_be_better = result["what_could_be_better"]
	row.overall_flag = result["overall_flag"]
	row.model = frappe.conf.get("call_review_model") or MODEL
	if feedback is not None and row.meta.has_field("feedback"):
		row.feedback = feedback
	row.reviewed_at = now_datetime()
	try:
		row.save(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		frappe.db.rollback()  # a concurrent run won the race; fine — it's done


def _mmss(seconds):
	"""Seconds → m:ss label for the email/UI."""
	if seconds is None:
		return ""
	s = int(seconds)
	return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------------
# Digest email
# ---------------------------------------------------------------------------------
def _send_digest(day, candidates, failed):
	"""Assemble the digest from the persisted rows for the day's candidate calls,
	so the email is consistent whether freshly computed or re-run."""
	call_names = [c["name"] for c in candidates]
	rows = []
	if call_names:
		rows = frappe.get_all(
			AI_REVIEW_DOCTYPE,
			filters={"call_log": ["in", call_names]},
			fields=[
				"call_log", "reference_doctype", "reference_docname",
				"motivation_score", "motivation_reason", "integrity_issues",
				"lead_status", "what_could_be_better", "overall_flag",
			],
		)

	cand_by_name = {c["name"]: c for c in candidates}
	lead_names = list({r["reference_docname"] for r in rows if r.get("reference_doctype") == "CRM Lead" and r.get("reference_docname")})
	lead_disp = {}
	if lead_names:
		for ld in frappe.get_all("CRM Lead", filters={"name": ["in", lead_names]}, fields=["name", "lead_name", "first_name", "last_name"]):
			lead_disp[ld["name"]] = ld.get("lead_name") or " ".join(x for x in [ld.get("first_name"), ld.get("last_name")] if x) or ld["name"]

	rep_emails = list({(cand_by_name.get(r["call_log"], {}).get("caller") or cand_by_name.get(r["call_log"], {}).get("receiver")) for r in rows})
	rep_emails = [e for e in rep_emails if e]
	rep_names = {}
	if rep_emails:
		for u in frappe.get_all("User", filters={"name": ["in", rep_emails]}, fields=["name", "full_name"]):
			rep_names[u["name"]] = u.get("full_name") or u["name"]

	items = []
	for r in rows:
		cand = cand_by_name.get(r["call_log"], {})
		ref = r.get("reference_docname")
		route = "deals" if r.get("reference_doctype") == "CRM Deal" else "leads"
		# No lead on the call → deep-link the (Lance-only) Call Review page to this
		# exact call instead, and label the item with the other party's number.
		if ref:
			link = get_url(f"/crm/{route}/{ref}?call={r['call_log']}#activity")
		else:
			link = get_url(f"/crm/reports/calls?date={day}&call={r['call_log']}")
		rep_email = cand.get("caller") or cand.get("receiver") or ""
		their_number = (cand.get("to") if cand.get("type") == "Outgoing" else cand.get("from")) or ""
		try:
			issues = json.loads(r.get("integrity_issues") or "[]")
		except Exception:
			issues = []
		for iss in issues:
			at = iss.get("at_time")
			if at is None:
				iss["link"] = link
			elif ref:
				iss["link"] = get_url(f"/crm/{route}/{ref}?call={r['call_log']}&t={at}#activity")
			else:
				iss["link"] = get_url(f"/crm/reports/calls?date={day}&call={r['call_log']}&t={at}")
			iss["at_label"] = _mmss(at)
		items.append({
			"flag": r.get("overall_flag") or "ok",
			"lead_name": lead_disp.get(ref) or ref or (their_number and _("Unknown ({0})").format(their_number)) or _("Unknown lead"),
			"link": link,
			"rep_name": rep_names.get(rep_email) or rep_email or _("Unassigned"),
			"duration": int(cand.get("duration") or 0),
			"motivation_score": _parse_score(r.get("motivation_score")),
			"motivation_reason": r.get("motivation_reason") or "",
			"integrity_issues": issues,
			"lead_status": r.get("lead_status") or "",
			"what_could_be_better": r.get("what_could_be_better") or "",
		})

	items.sort(key=lambda x: (_FLAG_RANK.get(x["flag"], 3), -(x["motivation_score"] if x["motivation_score"] is not None else 99)))
	attention = [i for i in items if i["flag"] in ("serious", "review")]
	clean = [i for i in items if i["flag"] == "ok"]

	frappe.sendmail(
		recipients=[DIGEST_RECIPIENT],
		subject=_("Call Integrity Report — {0}").format(day),
		template="crm_call_review_report",
		args={
			"day": str(day),
			"reviewed_count": len(items),
			"attention": attention,
			"clean": clean,
			"clean_count": len(clean),
			"failed_count": failed,
		},
		now=True,
	)


# ---------------------------------------------------------------------------------
# Manual / testing endpoint
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def review_call_now(call_log: str, force=0):
	"""Review a single call on demand and return the result. Lance / System Manager
	only. Returns the stored row if one exists and `force` is off."""
	if frappe.session.user != CALL_REVIEW_USER and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	if not frappe.db.exists("CRM Call Log", call_log):
		frappe.throw(_("Call {0} does not exist.").format(call_log), frappe.DoesNotExistError)
	if not frappe.db.exists("DocType", AI_REVIEW_DOCTYPE):
		frappe.throw(_("CRM Call AI Review is not provisioned yet."))

	if not frappe.utils.cint(force):
		existing = frappe.db.get_value(AI_REVIEW_DOCTYPE, {"call_log": call_log}, "name")
		if existing:
			return _row_to_result(frappe.get_doc(AI_REVIEW_DOCTYPE, existing))

	result = _review_one(call_log)
	if result is None:
		frappe.throw(_("No usable transcript for this call yet."))
	frappe.db.commit()
	return result


def _row_to_result(row):
	try:
		issues = json.loads(row.get("integrity_issues") or "[]")
	except Exception:
		issues = []
	return {
		"motivation_score": _parse_score(row.get("motivation_score")),
		"motivation_reason": row.get("motivation_reason") or "",
		"integrity_issues": issues,
		"lead_status": row.get("lead_status") or "",
		"what_could_be_better": row.get("what_could_be_better") or "",
		"overall_flag": row.get("overall_flag") or "ok",
	}


@frappe.whitelist()
def reply_to_review(call_log: str, feedback: str):
	"""The reviewer replies to the AI on a call. Re-reviews the call honoring the
	reply, stores the reply, and remembers any lessons it teaches — global house
	rules and/or facts about this specific contact — for future reviews.
	Lance / System Manager only. Returns the revised review + `learned` lessons."""
	if frappe.session.user != CALL_REVIEW_USER and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	feedback = (feedback or "").strip()
	if not feedback:
		frappe.throw(_("Reply is empty."))
	if not frappe.db.exists("CRM Call Log", call_log):
		frappe.throw(_("Call {0} does not exist.").format(call_log), frappe.DoesNotExistError)
	if not frappe.db.exists("DocType", AI_REVIEW_DOCTYPE):
		frappe.throw(_("CRM Call AI Review is not provisioned yet."))

	doc = frappe.get_doc("CRM Call Log", call_log)
	transcript = _build_transcript(doc)
	if not transcript.get("has_transcript"):
		frappe.throw(_("No usable transcript for this call."))

	prior = "(none)"
	existing = frappe.db.get_value(AI_REVIEW_DOCTYPE, {"call_log": call_log}, "name")
	if existing:
		er = frappe.get_doc(AI_REVIEW_DOCTYPE, existing)
		prior = (
			f"overall_flag={er.get('overall_flag')}; motivation={er.get('motivation_score')}; "
			f"reason={er.get('motivation_reason')}; what_could_be_better={er.get('what_could_be_better')}"
		)

	user_content = (
		_build_llm_input(doc, transcript)
		+ "\n\n--- YOUR PRIOR REVIEW ---\n" + prior
		+ "\n\n--- THE REVIEWER'S REPLY (authoritative — apply it) ---\n" + feedback
		+ "\n\nRevise your review of THIS call to honor the reply, then extract any reusable "
		"lessons as memory_items: scope 'global' for general house rules that apply to all "
		"calls, scope 'contact' for facts about this specific person/number (e.g. 'this is a "
		"dispo partner, not a seller'). Return an empty memory_items list if the reply teaches "
		"nothing reusable. Keep each lesson one concise sentence."
	)
	data = _call_llm(user_content, schema=REPLY_SCHEMA)
	review = _normalize_result(data.get("review") or {})
	_persist_review(doc, review, feedback=feedback)
	learned = _record_memory(data.get("memory_items") or [], doc)
	frappe.db.commit()
	out = dict(review)
	out["learned"] = learned
	return out

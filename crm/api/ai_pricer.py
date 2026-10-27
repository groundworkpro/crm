"""Ask AI: the property pricer (comping skill) as a chat bubble on every CRM page.

The pricer runs on agentbox with Lance's pi setup, not here: OVH cannot reach
agentbox, so agentbox pulls. A request pushes a job onto a Redis list in this
site's redis-cache; `comping/crm_worker.py` (repo groundworkpro/comping-computers)
pops it over ssh, prices the house (~45 s) or answers a follow-up, and writes the
result to `crm:ai_pricer:job:<id>`. The bubble polls `get_job`.

Keys are unprefixed ("shared" in Frappe's terms) so the worker needs no site name:
  crm:ai_pricer:queue        job list (worker BLPOPs)
  crm:ai_pricer:job:<id>     {"status": queued|running|done|error, ...}
  crm:ai_pricer:worker_seen  heartbeat; missing = worker down, say so up front

Who sees it: site_config `ai_pricer_users` (list of logins). Default "*", every
logged-in user (Lance opened it to everyone 2026-10-26).

Transcript (Lance, 2026-10-27: "store both sides of the chat"): every rep message
and every AI reply is a row in **CRM AI Chat Message** (doctype made by the deploy
repo's `scripts/setup_ai_chat_log.py`), grouped by the bubble's `chat` id. The rep's
side is written in `ask`; the AI's side when `get_job` first sees the job finish
(`crm:ai_pricer:log:<id>` carries who/what until then, and deleting it is the
once-only claim). Until the doctype exists nothing is written, and a failed write
never breaks the chat.
"""

from __future__ import annotations

import html
import json
import uuid

import frappe
from frappe import _

QUEUE = "crm:ai_pricer:queue"
JOB = "crm:ai_pricer:job:"
SEEN = "crm:ai_pricer:worker_seen"
LOG = "crm:ai_pricer:log:"
CHAT_DOCTYPE = "CRM AI Chat Message"
TTL = 86400
DEFAULT_USERS = ("*",)
MAX_QUESTION = 2000
MAX_NOTES = 6


def _allowed(user=None) -> bool:
	user = user or frappe.session.user
	users = frappe.conf.get("ai_pricer_users") or DEFAULT_USERS
	if isinstance(users, str):
		users = [u.strip() for u in users.split(",")]
	return "*" in users or user in users


def _guard():
	if not _allowed():
		frappe.throw(_("Ask AI is not turned on for you yet."), frappe.PermissionError)


def _redis():
	"""Frappe's RedisWrapper prefixes keys in rpush/lpush; the worker reads raw keys."""
	import redis

	return frappe.cache(), redis.Redis


def _push(job: dict):
	r, base = _redis()
	base.set(r, JOB + job["id"], json.dumps({"status": "queued", "kind": job["kind"]}), ex=TTL)
	base.rpush(r, QUEUE, json.dumps(job))


def _read(job_id: str) -> dict | None:
	r, base = _redis()
	raw = base.get(r, JOB + job_id)
	return json.loads(raw) if raw else None


def _log_supported() -> bool:
	return bool(frappe.db.exists("DocType", CHAT_DOCTYPE))


def _clean_chat(chat) -> str:
	chat = str(chat or "")
	return chat if 8 <= len(chat) <= 40 and chat.replace("-", "").isalnum() else uuid.uuid4().hex


def _summary(answer: dict) -> str:
	"""One readable line for a price card; the full card is kept as JSON beside it."""
	money = lambda n: f"${round(n):,}" if isinstance(n, (int, float)) else "?"  # noqa: E731
	offers = answer.get("offers") or {}
	comps = "; ".join(f"{c.get('address')} {money(c.get('price'))}" for c in answer.get("comps_used") or [])
	return (
		f"ARV {money(answer.get('arv'))} ({money(answer.get('arv_low'))}-{money(answer.get('arv_high'))}), "
		f"{answer.get('confidence')} confidence, repair tier {answer.get('repair_tier')}, "
		f"repairs {money(offers.get('repairs'))}, offer {money(offers.get('offer'))}. Comps: {comps}"
	)


def _write_log(row: dict):
	"""Insert one transcript row. Never raises: logging must not break the chat."""
	try:
		if not _log_supported():
			return
		frappe.get_doc({"doctype": CHAT_DOCTYPE, **row}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Ask AI: chat log write failed")


def _log_reply(job_id: str, result: dict):
	"""Write the AI's side once, the first time a poll sees the job finished."""
	r, base = _redis()
	raw = base.get(r, LOG + job_id)
	if not raw or not base.delete(r, LOG + job_id):
		return
	meta = json.loads(raw)
	answer = result.get("answer")
	error = result.get("status") == "error"
	_write_log(
		{
			**meta,
			"role": "ai",
			"error": 1 if error else 0,
			"text": (result.get("error") if error else _summary(answer) if answer else result.get("text")) or "",
			"answer": json.dumps(answer) if answer else None,
		}
	)


def _worker_up() -> bool:
	r, base = _redis()
	return bool(base.get(r, SEEN))


def _address(doc) -> str:
	from crm.api.comps import _full_address

	return _full_address(doc)


def _notes(lead: str) -> str:
	"""Recent notes on the lead, newest first, as plain text for the model."""
	rows = frappe.get_all(
		"FCRM Note",
		filters={"reference_doctype": "CRM Lead", "reference_docname": lead},
		fields=["title", "content", "creation"],
		order_by="creation desc",
		limit=MAX_NOTES,
	)
	out = []
	for n in rows:
		text = frappe.utils.strip_html(n.get("content") or "").strip()
		if text:
			out.append(f"- {str(n.get('creation'))[:10]} {n.get('title') or ''}: {text[:600]}")
	return "\n".join(out)


def build_job(kind: str, lead: str, address: str, question: str = "", history=None, context: str = "") -> dict:
	if kind not in ("price", "ask"):
		frappe.throw(_("Unknown request {0}.").format(kind))
	if not address:
		frappe.throw(_("This lead has no property address to price."))
	question = (question or "").strip()[:MAX_QUESTION]
	if kind == "ask" and not question:
		frappe.throw(_("Type a question first."))
	hist = [
		{"role": "ai" if m.get("role") == "ai" else "user", "text": str(m.get("text") or "")[:1500]}
		for m in (history or [])[-12:]
		if isinstance(m, dict)
	]
	return {
		"id": uuid.uuid4().hex,
		"kind": kind,
		"lead": lead,
		"address": address,
		"question": question,
		"history": hist,
		"context": context,
		"user": frappe.session.user,
	}


@frappe.whitelist()
def status():
	"""Whether to draw the bubble, and whether the pricer is up right now."""
	if not _allowed():
		return {"enabled": False}
	return {"enabled": True, "online": _worker_up()}


@frappe.whitelist()
def lead_info(lead: str):
	"""Address and name for the bubble's lead chip."""
	_guard()
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	doc = frappe.get_doc("CRM Lead", lead)
	return {"name": doc.name, "address": _address(doc), "title": doc.get("lead_name") or doc.name}


@frappe.whitelist(methods=["POST"])
def ask(lead: str, kind: str = "price", question: str = "", history=None, chat=None):
	_guard()
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	if isinstance(history, str):
		history = json.loads(history or "[]")
	if not _worker_up():
		frappe.throw(_("The pricer is offline right now (agentbox worker not running). Try again in a minute."))
	doc = frappe.get_doc("CRM Lead", lead)
	job = build_job(kind, lead, _address(doc), question, history, _notes(lead) if kind == "ask" else "")
	_push(job)
	meta = {"chat": _clean_chat(chat), "lead": lead, "user": job["user"], "kind": kind, "job": job["id"]}
	if _log_supported():
		r, base = _redis()
		base.set(r, LOG + job["id"], json.dumps(meta), ex=TTL)
		_write_log({**meta, "role": "user", "text": job["question"] or "Price this house"})
	return {"job": job["id"], "chat": meta["chat"]}


def _history_row(row) -> dict:
	"""A transcript row as the bubble draws it (price cards come back as their JSON)."""
	out = {"role": row.role, "text": row.text or "", "job": row.job, "kind": row.kind}
	if row.error:
		out["error"] = True
	elif row.role == "ai" and row.answer:
		try:
			out["result"] = json.loads(row.answer)
		except ValueError:
			pass
	return out


@frappe.whitelist()
def chat_history(lead: str):
	"""The rep's latest Ask AI chat on this lead, so reopening the bubble picks it back up.

	Only the caller's own chats (Exe, 2026-10-27: "when we open the Ask AI chat, we can
	see the previous messages"). `pending` is the job of a question still waiting for
	its reply (asked, then the tab was reloaded), so the bubble can resume polling it.
	"""
	_guard()
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	empty = {"chat": None, "messages": [], "pending": None}
	if not _log_supported():
		return empty
	user = frappe.session.user
	last = frappe.get_all(
		CHAT_DOCTYPE,
		filters={"lead": lead, "user": user},
		fields=["chat"],
		order_by="creation desc",
		limit=1,
	)
	if not last:
		return empty
	chat = last[0].chat
	rows = frappe.get_all(
		CHAT_DOCTYPE,
		filters={"lead": lead, "user": user, "chat": chat},
		fields=["role", "kind", "job", "text", "answer", "error", "creation"],
		order_by="creation asc",
		limit=200,
	)
	replied = {r.job for r in rows if r.role == "ai"}
	pending = next((r.job for r in reversed(rows) if r.role == "user" and r.job and r.job not in replied), None)
	if pending and not _read(pending):
		pending = None
	return {"chat": chat, "messages": [_history_row(r) for r in rows], "pending": pending}


def _check_job_id(job):
	if not job or len(job) != 32 or not job.isalnum():
		frappe.throw(_("Bad job id."))


def _money(n) -> str:
	return f"${round(n):,}" if isinstance(n, (int, float)) else "?"


def _para(text: str) -> str:
	return "<p>" + html.escape(str(text)).replace("\n", "<br>") + "</p>"


def comment_html(answer: dict | None, text: str = "", question: str = "") -> str:
	"""The AI's reply as a timeline comment: a price card in full, or the reply text."""
	out = ["<p><b>Ask AI analysis</b></p>"]
	if question and question != "Price this house":
		out.append("<p><i>Asked: " + html.escape(question[:500]) + "</i></p>")
	if answer:
		conf = {"high": "high", "med": "medium", "low": "low"}.get(answer.get("confidence"), answer.get("confidence") or "?")
		out.append(
			f"<p><b>ARV {_money(answer.get('arv'))}</b> (range {_money(answer.get('arv_low'))}–"
			f"{_money(answer.get('arv_high'))}), {html.escape(str(conf))} confidence</p>"
		)
		if answer.get("subject_note"):
			out.append(_para(answer["subject_note"]))
		comps = answer.get("comps_used") or []
		if comps:
			items = "".join(
				f"<li>{html.escape(str(c.get('address') or ''))} {_money(c.get('price'))}"
				+ (f" — {html.escape(str(c.get('why')))}" if c.get("why") else "")
				+ "</li>"
				for c in comps
			)
			out.append(f"<p>Comps used:</p><ul>{items}</ul>")
		offers = answer.get("offers") or {}
		if offers:
			pick = {"classic": "Classic 70%", "two_x": "2× repairs"}.get(offers.get("recommend"))
			out.append(
				f"<p>Offers: Classic 70% {_money(offers.get('classic'))} · 2× repairs {_money(offers.get('two_x'))}"
				+ (f" · use {pick}" if pick else "")
				+ f"<br>Repairs {_money(offers.get('repairs'))} (tier {html.escape(str(answer.get('repair_tier') or '?'))}), "
				f"fee {_money(offers.get('fee'))}</p>"
			)
		if answer.get("would_change_it"):
			out.append(_para("Would change it: " + str(answer["would_change_it"])))
	else:
		out.append(_para(text))
	return "".join(out)


def _reply_for(lead: str, job: str):
	"""(answer, text, question) for a finished job: transcript first, Redis as fallback."""
	question = ""
	if _log_supported():
		rows = frappe.get_all(
			CHAT_DOCTYPE,
			filters={"lead": lead, "job": job},
			fields=["role", "text", "answer", "error"],
			order_by="creation asc",
			limit=4,
		)
		question = next((r.text or "" for r in rows if r.role == "user"), "")
		ai = next((r for r in rows if r.role == "ai" and not r.error), None)
		if ai:
			try:
				return (json.loads(ai.answer) if ai.answer else None), ai.text or "", question
			except ValueError:
				return None, ai.text or "", question
	result = _read(job) or {}
	if result.get("status") == "done" and (result.get("answer") or result.get("text")):
		return result.get("answer"), result.get("text") or "", question
	frappe.throw(_("That AI reply is not available any more. Ask again, then save it."))


@frappe.whitelist(methods=["POST"])
def save_comment(lead: str, job: str):
	"""Post one AI reply on the lead's timeline as a Comment (Dennis, 2026-10-27:
	"a button that saves the AI analysis as a comment in the CRM").

	The content is rebuilt here from the stored reply, not taken from the browser,
	so the comment is exactly what the AI said.
	"""
	_guard()
	_check_job_id(job)
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	answer, text, question = _reply_for(lead, job)
	doc = frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"content": comment_html(answer, text, question),
			"comment_email": frappe.session.user,
			"comment_by": frappe.session.user,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"comment": getattr(doc, "name", None)}


@frappe.whitelist()
def get_job(job: str):
	_guard()
	_check_job_id(job)
	result = _read(job) or {"status": "error", "error": "This request expired. Ask again."}
	if result.get("status") in ("done", "error"):
		_log_reply(job, result)
	return result

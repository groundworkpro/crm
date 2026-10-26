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


@frappe.whitelist()
def get_job(job: str):
	_guard()
	if not job or len(job) != 32 or not job.isalnum():
		frappe.throw(_("Bad job id."))
	result = _read(job) or {"status": "error", "error": "This request expired. Ask again."}
	if result.get("status") in ("done", "error"):
		_log_reply(job, result)
	return result

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

Who sees it: site_config `ai_pricer_users` (list of logins), default Lance + Exe
for the first week. "*" opens it to every logged-in user.
"""

from __future__ import annotations

import json
import uuid

import frappe
from frappe import _

QUEUE = "crm:ai_pricer:queue"
JOB = "crm:ai_pricer:job:"
SEEN = "crm:ai_pricer:worker_seen"
TTL = 86400
DEFAULT_USERS = ("lance.johnson@groundworkpro.com", "exe.ortiz@groundworkpro.com", "Administrator")
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
def ask(lead: str, kind: str = "price", question: str = "", history=None):
	_guard()
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	if isinstance(history, str):
		history = json.loads(history or "[]")
	if not _worker_up():
		frappe.throw(_("The pricer is offline right now (agentbox worker not running). Try again in a minute."))
	doc = frappe.get_doc("CRM Lead", lead)
	job = build_job(kind, lead, _address(doc), question, history, _notes(lead) if kind == "ask" else "")
	_push(job)
	return {"job": job["id"]}


@frappe.whitelist()
def get_job(job: str):
	_guard()
	if not job or len(job) != 32 or not job.isalnum():
		frappe.throw(_("Bad job id."))
	return _read(job) or {"status": "error", "error": "This request expired. Ask again."}

"""AVM training: rerun the 13 comp steps on many leads at once with a trial setup, and mark each step right or wrong.

Exe, 2026-11-02: "bulk re-run a set of comps and check them at the same step(s)". No new doctype:
- a run (title, trial setup, steps to check, leads + job ids) is a small JSON default `avm_training_run:<id>`,
  listed newest first in `avm_training_runs`;
- each lead's result is a CRM AI Chat Message (kind decide) whose chat id is the run id, written by
  ai_pricer._log_reply the first time a poll sees the job finished;
- each right/wrong mark is a CRM AI Suggestion (source Comps page, reason_type "AVM training", chat = run id),
  so it shows in the review queue with the reason.
The team's answer for a lead is its latest saved cash offer ARV (`data-cash-offer` comment).
"""

import html
import json
import re
import uuid

import frappe
from frappe import _

from crm.api import ai_pricer as P

BULK = "crm:ai_pricer:bulk"   # the worker takes these one at a time, after any rep's job
INDEX, RUN = "avm_training_runs", "avm_training_run:"
MAX_LEADS, MAX_RUNS = 25, 40
REASON_TYPE = "AVM training"
CHAT_PREFIX = "avmtrain"  # chat ids of training runs; ai_pricer.chat_history skips them so the lead page chat stays the rep's
_OFFER = re.compile(r'data-cash-offer="([^"]*)"')


def _get_default(key: str):
	try:
		return json.loads(frappe.db.get_default(key) or "null")
	except ValueError:
		return None


def _run(name: str) -> dict:
	run = _get_default(RUN + str(name or "")) if str(name or "").isalnum() else None
	if not isinstance(run, dict):
		frappe.throw(_("Training run {0} not found.").format(name), frappe.DoesNotExistError)
	return run


def clean_setup(setup) -> dict:
	"""A trial setup in the same shape as the team's (comp_steps_setup); bad values throw."""
	if isinstance(setup, str):
		setup = json.loads(setup or "{}")
	out = {}
	for k, v in (setup or {}).items():
		if not str(k).isdigit() or not 1 <= int(k) <= 13 or not isinstance(v, dict):
			continue
		n, row = int(k), {}
		if v.get("tool") not in (None, ""):
			if not P._tool_ok(v["tool"]):
				frappe.throw(_("Step {0} cannot use {1}.").format(n, v["tool"]))
			row["tool"] = v["tool"]
		if v.get("note"):
			row["note"] = str(v["note"]).strip()[:500]
		if n == 5 and v.get("size_band") not in (None, ""):
			if not 0.05 <= float(v["size_band"]) <= 1:
				frappe.throw(_("Size band is step 5, between 5% and 100%."))
			row["size_band"] = round(float(v["size_band"]), 2)
		if n == 7 and v.get("max_mi") not in (None, ""):
			if not 0.25 <= float(v["max_mi"]) <= 5:
				frappe.throw(_("Radius is step 7, between 0.25 and 5 miles."))
			row["max_mi"] = round(float(v["max_mi"]), 2)
		if row:
			out[str(n)] = row
	return out


def _norm(setup: dict, n: int) -> dict:
	"""A step's setting with defaults filled in, so a full trial setup compares equal to a sparse team one."""
	c = (setup or {}).get(str(n)) or {}
	return {"tool": c.get("tool") or P.default_tool(n), "note": (c.get("note") or "").strip(),
	        "size_band": float(c.get("size_band") or 0.3) if n == 5 else 0, "max_mi": float(c.get("max_mi") or 2.0) if n == 7 else 0}


def team_arvs(leads: list[str]) -> dict[str, dict]:
	"""Latest saved cash offer per lead: {lead: {arv, at, by}} (first scenario with an ARV)."""
	if not leads:
		return {}
	rows = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "CRM Lead", "reference_name": ["in", leads], "content": ["like", "%data-cash-offer%"]},
		fields=["reference_name", "content", "creation", "owner"],
		order_by="creation desc",
	)
	out = {}
	for r in rows:
		if r.reference_name in out:
			continue
		m = _OFFER.search(r.content or "")
		try:
			offer = json.loads(html.unescape(m.group(1))) if m else {}
		except ValueError:
			continue
		arv = next((s.get("arv") for s in offer.get("scenarios") or [] if isinstance(s, dict) and s.get("arv")), None)
		if arv:
			out[r.reference_name] = {"arv": float(arv), "at": str(r.creation)[:10], "by": r.owner}
	return out


@frappe.whitelist()
def options():
	"""What the start form needs: the team setup, models, Practice sets, and leads with a saved offer."""
	P._guard()
	sets = []
	if frappe.db.exists("DocType", "CRM Practice Set"):
		for s in frappe.get_all("CRM Practice Set", fields=["name", "title"], order_by="modified desc", limit=30):
			leads = [p.source_lead for p in frappe.get_all("CRM Practice Property", filters={"practice_set": s.name},
			                                              fields=["source_lead"], order_by="sort_order asc") if p.source_lead]
			if leads:
				sets.append({"name": s.name, "title": s.title, "leads": leads})
	recent = frappe.db.sql(
		"""select reference_name, max(creation) at from `tabComment`
		where reference_doctype='CRM Lead' and content like '%%data-cash-offer%%'
		group by reference_name order by at desc limit 80""",
		as_dict=True,
	)
	names = [r.reference_name for r in recent]
	arvs = team_arvs(names)
	addr = {d.name: d for d in frappe.get_all("CRM Lead", filters={"name": ["in", names or [""]]},
	                                          fields=["name", "lead_name", "property_address"])}
	leads = [{"lead": n, "address": (addr[n].property_address or addr[n].lead_name or n) if n in addr else n,
	          "team_arv": arvs[n]["arv"], "at": arvs[n]["at"]} for n in names if n in arvs and n in addr]
	return {"setup": P.step_setup_dict(), "sets": sets, "leads": leads, "max_leads": MAX_LEADS}


@frappe.whitelist(methods=["POST"])
def start(title: str, leads, setup=None, steps=None):
	"""Queue the 13 steps on every lead with this trial setup. Returns the run name."""
	P._guard()
	leads = json.loads(leads) if isinstance(leads, str) else list(leads or [])
	steps = json.loads(steps) if isinstance(steps, str) else list(steps or [])
	leads = list(dict.fromkeys(str(x) for x in leads if x))[:MAX_LEADS + 1]
	if not leads:
		frappe.throw(_("Pick at least one lead."))
	if len(leads) > MAX_LEADS:
		frappe.throw(_("Up to {0} leads per run.").format(MAX_LEADS))
	steps = sorted({int(n) for n in steps if str(n).isdigit() and 1 <= int(n) <= 13})
	setup = clean_setup(setup)
	if not P._worker_up():
		frappe.throw(_("The pricer is offline right now (agentbox worker not running). Try again in a minute."))
	name = uuid.uuid4().hex[:12]
	user, title = frappe.session.user, (title or "").strip()[:140] or _("Training run")
	r, base = P._redis()
	rows = []
	for lead in leads:
		frappe.has_permission("CRM Lead", "read", lead, throw=True)
		job = P.build_job("decide", lead, P._address(frappe.get_doc("CRM Lead", lead)))
		job["setup"] = setup
		base.set(r, P.JOB + job["id"], json.dumps({"status": "queued", "kind": "decide"}), ex=P.TTL)
		base.rpush(r, BULK, json.dumps(job))
		meta = {"chat": CHAT_PREFIX + name, "lead": lead, "user": user, "kind": "decide", "job": job["id"]}
		if P._log_supported():
			base.set(r, P.LOG + job["id"], json.dumps({**meta, "question": ""}), ex=P.TTL)
			P._write_log({**meta, "role": "user", "text": _("AVM training run: {0}").format(title)})
		rows.append({"lead": lead, "address": job["address"], "job": job["id"]})
	run = {"name": name, "title": title, "user": user, "at": str(frappe.utils.now_datetime())[:16],
	       "setup": setup, "team_setup": P.step_setup_dict(), "steps": steps, "leads": rows}
	frappe.db.set_default(RUN + name, json.dumps(run))
	index = [n for n in (_get_default(INDEX) or []) if isinstance(n, str)]
	for old in index[MAX_RUNS - 1:]:
		frappe.db.set_default(RUN + old, "")
	frappe.db.set_default(INDEX, json.dumps([name] + index[: MAX_RUNS - 1]))
	frappe.db.commit()
	return {"name": name}


def _results(run: dict) -> dict[str, dict]:
	"""{job: {status, decide|error}}: logs any job that finished since the last look."""
	done = {}
	if P._log_supported():
		for row in frappe.get_all(P.CHAT_DOCTYPE, filters={"chat": CHAT_PREFIX + run["name"], "role": "ai"},
		                          fields=["job", "error", "text", "answer"]):
			done[row.job] = row
	out = {}
	for x in run["leads"]:
		job = x["job"]
		if job not in done:
			res = P._read(job) or {"status": "expired"}
			if res.get("status") in ("done", "error") and P._log_supported():
				P._log_reply(job, res)
			if res.get("status") == "done" and res.get("decide"):
				out[job] = {"status": "done", "decide": res["decide"]}
			elif res.get("status") == "error":
				out[job] = {"status": "error", "error": res.get("error") or ""}
			else:
				out[job] = {"status": res.get("status") or "queued"}
			continue
		row = done[job]
		if row.error:
			out[job] = {"status": "error", "error": row.text or ""}
		else:
			try:
				out[job] = {"status": "done", "decide": json.loads(row.answer or "null")}
			except ValueError:
				out[job] = {"status": "error", "error": "unreadable answer"}
	return out


def _marks(name: str) -> dict:
	"""Latest mark per (lead, step): {"lead|n": {right, reason, by, at}}."""
	if not frappe.db.exists("DocType", "CRM AI Suggestion"):
		return {}
	rows = frappe.get_all("CRM AI Suggestion", filters={"chat": CHAT_PREFIX + name, "reason_type": REASON_TYPE},
	                      fields=["lead", "step", "new_value", "correction", "user", "creation"], order_by="creation asc")
	return {f"{r.lead}|{r.step}": {"right": r.new_value == "Right", "reason": "" if r.new_value == "Right" else r.correction,
	                               "by": r.user, "at": str(r.creation)[:16]} for r in rows}


def _gap(ai, team):
	return round(ai / team - 1, 4) if ai and team else None


def summary(run: dict, results: dict, marks: dict, arvs: dict) -> dict:
	gaps = [_gap((results.get(x["job"]) or {}).get("decide", {}).get("arv") if (results.get(x["job"]) or {}).get("decide") else None,
	             (arvs.get(x["lead"]) or {}).get("arv")) for x in run["leads"]]
	gaps = sorted(abs(g) for g in gaps if g is not None)
	steps = {}
	for key, m in marks.items():
		n = key.rsplit("|", 1)[1]
		s = steps.setdefault(n, {"right": 0, "wrong": 0})
		s["right" if m["right"] else "wrong"] += 1
	return {
		"leads": len(run["leads"]),
		"done": sum(1 for v in results.values() if v["status"] == "done"),
		"failed": sum(1 for v in results.values() if v["status"] in ("error", "expired")),
		"compared": len(gaps),
		"median_gap": gaps[len(gaps) // 2] if gaps else None,
		"within_10": sum(g <= 0.10 for g in gaps),
		"marks": steps,
	}


@frappe.whitelist()
def runs():
	P._guard()
	out = []
	for name in _get_default(INDEX) or []:
		run = _get_default(RUN + name)
		if not isinstance(run, dict):
			continue
		res = _results(run)
		out.append({k: run[k] for k in ("name", "title", "user", "at", "steps")}
		           | {"summary": summary(run, res, _marks(name), team_arvs([x["lead"] for x in run["leads"]]))})
	return out


@frappe.whitelist()
def run(name: str):
	"""One run: per lead the AI's ARV, the team's saved ARV, the step cards, and the marks."""
	P._guard()
	run = _run(name)
	from crm.api import ai_suggestions

	res, marks, team = _results(run), _marks(name), P.step_setup_dict()
	arvs = team_arvs([x["lead"] for x in run["leads"]])
	rows = []
	for x in run["leads"]:
		r = res.get(x["job"]) or {"status": "queued"}
		d = r.get("decide") or {}
		team_arv = (arvs.get(x["lead"]) or {}).get("arv")
		rows.append(x | {"status": r["status"], "error": r.get("error"), "arv": d.get("arv"), "confidence": d.get("confidence"),
		                 "team_arv": team_arv, "team_at": (arvs.get(x["lead"]) or {}).get("at"), "gap": _gap(d.get("arv"), team_arv),
		                 "steps": d.get("steps") or [], "comps": [c.get("address") for c in d.get("comps_used") or []]})
	return run | {"rows": rows, "marks": marks, "summary": summary(run, res, marks, arvs),
	              "differs": [n for n in range(1, 14) if _norm(run["setup"], n) != _norm(team, n)],
	              "team_wide": ai_suggestions.is_reviewer()}


@frappe.whitelist(methods=["POST"])
def mark(name: str, lead: str, step, right, reason: str = ""):
	"""Mark one step on one lead right or wrong. Wrong needs a reason."""
	P._guard()
	from crm.api import ai_suggestions

	run = _run(name)
	step = int(step)
	right = str(right).lower() in ("1", "true", "yes")
	reason = (reason or "").strip()[: P.MAX_QUESTION]
	row = next((x for x in run["leads"] if x["lead"] == lead), None)
	if not row or not 1 <= step <= 13:
		frappe.throw(_("That lead or step is not in this run."))
	if not right and not reason:
		frappe.throw(_("Say what the step got wrong."))
	tool = (run["setup"].get(str(step)) or {}).get("tool") or P.default_tool(step)
	ok = ai_suggestions.record(
		"Comps page", frappe.session.user, reason or "Right", lead=lead, address=row["address"], chat=CHAT_PREFIX + name, job=row["job"],
		lesson=f"Step {step} ({P._describe(step, 'tool', tool)}) on {row['address']}: {'right' if right else 'wrong: ' + reason}",
		context=f"AVM training run {run['title']}",
		change={"step": step, "changed": f"Step {step} check", "old_value": P._describe(step, "tool", tool),
		        "new_value": "Right" if right else "Wrong", "reason_type": REASON_TYPE,
		        "scope": "Teach the agent" if not right else "This house only"},
	)
	if not ok:
		frappe.throw(_("Could not save the mark."))
	frappe.db.commit()
	return _marks(name)


@frappe.whitelist(methods=["POST"])
def apply_setup(name: str, reason: str):
	"""Make this run's trial setup the team's, one logged change per step (same log as the comps page).
	The pricer's owner only: the team's suggestions never change the model in general (Exe, 2026-11-04)."""
	from crm.api import ai_suggestions

	P._guard()
	if not ai_suggestions.is_reviewer():
		frappe.throw(_("Only the pricer's owner changes the team setup. Your marks are saved as suggestions."), frappe.PermissionError)
	run = _run(name)
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why you changed the step."))
	from crm.api import ai_suggestions

	team, lines = P.step_setup_dict(), []
	why = f"{reason} (AVM training run {run['title']})"
	label = {"tool": "tool", "note": "team rule", "size_band": "size band", "max_mi": "radius"}
	for n in range(1, 14):
		want, cur = run["setup"].get(str(n)) or {}, team.get(str(n)) or {}
		if _norm(run["setup"], n) == _norm(team, n):
			continue
		kw = {"tool": want.get("tool") or P.default_tool(n), "note": want.get("note") or ""}
		if n == 5:
			kw["size_band"] = want.get("size_band") or 0.3
		if n == 7:
			kw["max_mi"] = want.get("max_mi") or 2.0
		default = {"tool": P.default_tool(n), "note": "", "size_band": 0.3, "max_mi": 2.0}
		P.save_step(n, why, **kw)
		for k, v in kw.items():
			old = cur.get(k, default[k])
			if old == v:
				continue
			o, nv = P._describe(n, k, old), P._describe(n, k, v)
			line = f"Changed Step {n} {label[k]}: {o or '(empty)'} -> {nv or '(empty)'}. Why: {why}"
			ai_suggestions.record("Comps page", frappe.session.user, why, lesson=f"Step {n}: {line}", chat=CHAT_PREFIX + name,
			                      change={"step": n, "changed": f"Step {n} {label[k]}", "old_value": o, "new_value": nv,
			                              "reason_type": REASON_TYPE, "scope": "Teach the agent"})
			lines.append(line)
	frappe.db.commit()
	return {"setup": P.step_setup_dict(), "lines": lines}

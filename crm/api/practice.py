# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Practice comps — the same map as a real lead, sandboxed per person.

Acq reps price deals on the comps page. This is that page, pointed at a
set of properties Lance picked, with two differences that are the whole point:

  * each person's hides / picks / offer calc live on THEIR attempt, never on
    the CRM Lead — two people can take the same ten-house test and not see
    each other's work, and nothing they do rewrites a live deal
  * a set can carry a time limit, defaulted per listing (e.g. 3 minutes each)
    with a whole-set option (10 properties in 30 minutes), and we record how
    long the whole run and each house took

The map itself is `get_lead_comps` against the source lead, with the lead's
team-wide `comps_hidden` / `comps_selected` swapped out for the attempt's.
Zillow/geocode caches on the source lead may still fill in (`update_modified=
False`); those are not a human edit. Offer saves and underwriting sheets are
refused.

Doctypes (`CRM Practice Set` / `Property` / `Attempt`) come from ops
`scripts/setup_practice.py`. Everything here is `has_doctype`-guarded so the
app deploys before that script runs.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, get_fullname, now_datetime, time_diff_in_seconds
from werkzeug.utils import send_file

from crm.api.comps import _guard, get_lead_comps
from crm.api.practice_condition import pick_seller_note

SET = "CRM Practice Set"
PROP = "CRM Practice Property"
ATTEMPT = "CRM Practice Attempt"
SALES_ROLES = ("System Manager", "Sales Manager", "Sales User")
MANAGER_ROLES = ("System Manager", "Sales Manager")
STATUSES = ("In Progress", "Submitted", "Timed Out")
META = "_run"
LANCE_USER = "lance.johnson@groundworkpro.com"
VIEW_LOG = "CRM Practice View Log"

#: nginx `client_max_body_size` is 50m, so chunks stay well under that. The
#: whole take is capped because a forgotten recorder should not fill the disk.
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_RECORDING_BYTES = 400 * 1024 * 1024
_ATTEMPT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Timestamped emoji/comments on a recording (Frappe Comment, no extra doctype).
_REACTION_V = 1
_MAX_REACTIONS = 200
_MAX_REACTION_TEXT = 500


def _available() -> bool:
	return bool(
		frappe.db.exists("DocType", SET)
		and frappe.db.exists("DocType", PROP)
		and frappe.db.exists("DocType", ATTEMPT)
	)


def _is_manager() -> bool:
	return any(role in MANAGER_ROLES for role in frappe.get_roles())


def _assert_manager() -> None:
	if not _is_manager():
		frappe.throw(_("Only a manager can delete practice sets."), frappe.PermissionError)


def _flags() -> dict:
	return {"can_manage": True, "can_delete": _is_manager()}


def _need() -> None:
	_guard()
	if not _available():
		frappe.throw(_("Practice is not set up on this site yet."))


def _now():
	return now_datetime()


def _json(raw) -> dict:
	if not raw:
		return {}
	if isinstance(raw, dict):
		return raw
	try:
		val = json.loads(raw)
		return val if isinstance(val, dict) else {}
	except Exception:
		return {}


def _dump(val) -> str:
	return json.dumps(val, ensure_ascii=False, default=str)


def _has_recording_col() -> bool:
	return frappe.db.has_column(ATTEMPT, "recording_url")


def _has_time_mode_col(doctype: str) -> bool:
	return frappe.db.has_column(doctype, "time_limit_mode")


def _raw_time_mode(obj) -> str:
	if obj is None:
		return ""
	if isinstance(obj, dict):
		return str(obj.get("time_limit_mode") or "")
	val = getattr(obj, "time_limit_mode", None)
	if val:
		return str(val)
	get = getattr(obj, "get", None)
	if callable(get):
		try:
			return str(get("time_limit_mode") or "")
		except Exception:
			return ""
	return ""


def _time_mode(obj, results: dict | None = None) -> str:
	"""per_listing or set. Empty/missing (legacy rows) is whole-set."""
	raw = _raw_time_mode(obj)
	if raw == "per_listing":
		return "per_listing"
	if raw == "set":
		return "set"
	if results is None and obj is not None and getattr(obj, "results", None) is not None:
		results = _results(obj)
	if isinstance(results, dict):
		m = results.get(META)
		if isinstance(m, dict) and m.get("time_limit_mode") == "per_listing":
			return "per_listing"
	return "set"


def _safe_id(name: str) -> str:
	if not _ATTEMPT_NAME_RE.match(name or ""):
		frappe.throw(_("Invalid name."))
	return name


def _part_path(attempt: str, property: str) -> str:
	return frappe.get_site_path(
		"private", "files",
		f"practice-{_safe_id(attempt)}-{_safe_id(property)}.webm.part",
	)


def _dest_path(attempt: str, property: str) -> str:
	return frappe.get_site_path(
		"private", "files",
		f"practice-{_safe_id(attempt)}-{_safe_id(property)}.webm",
	)


def _chunk_prefix(attempt: str, property: str) -> str:
	return frappe.get_site_path(
		"private", "files",
		f"practice-{_safe_id(attempt)}-{_safe_id(property)}.webm.part.",
	)


def _chunk_path(attempt: str, property: str, seq: int) -> str:
	return f"{_chunk_prefix(attempt, property)}{seq:06d}"


def _chunk_manifest_path(attempt: str, property: str) -> str:
	return f"{_dest_path(attempt, property)}.chunks.json"


def _chunk_rows(attempt: str, property: str) -> list[tuple[int, str]]:
	prefix = _chunk_prefix(attempt, property)
	folder, stem = os.path.dirname(prefix), os.path.basename(prefix)
	try:
		names = os.listdir(folder)
	except OSError:
		return []
	rows = []
	for name in names:
		if not name.startswith(stem):
			continue
		tail = name[len(stem):]
		if tail.isdigit():
			rows.append((int(tail), os.path.join(folder, name)))
	return sorted(rows)


def _chunk_manifest(attempt: str, property: str) -> dict[str, int]:
	path = _chunk_manifest_path(attempt, property)
	try:
		with open(path, encoding="utf-8") as fh:
			data = json.load(fh)
		return {
			str(int(key)): max(0, int(value))
			for key, value in (data or {}).items()
		}
	except (OSError, TypeError, ValueError, json.JSONDecodeError):
		return {}


def _write_chunk_manifest(attempt: str, property: str, manifest: dict[str, int]) -> None:
	path = _chunk_manifest_path(attempt, property)
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as fh:
		json.dump(manifest, fh, sort_keys=True)
	os.replace(tmp, path)


def _assemble_recording_chunks(attempt: str, property: str) -> tuple[str, list[str]]:
	"""Build one WebM from durable, idempotent sequence files.

	A missing sequence means upload was interrupted. Keep every chunk in place so
	a later retry/recovery can complete it; never publish a file with a hole.
	"""
	rows = _chunk_rows(attempt, property)
	if not rows:
		return "", []
	seqs = [seq for seq, _path in rows]
	if seqs != list(range(seqs[-1] + 1)):
		return "", []

	dest = _dest_path(attempt, property)
	tmp = dest + ".assembling"
	total = 0
	try:
		with open(tmp, "wb") as out:
			for _seq, path in rows:
				with open(path, "rb") as src:
					shutil.copyfileobj(src, out, length=1024 * 1024)
				total += os.path.getsize(path)
				if total > MAX_RECORDING_BYTES:
					frappe.throw(_("Recording is too large."))
		dest_size = os.path.getsize(dest) if os.path.exists(dest) else 0
		if total > dest_size:
			os.replace(tmp, dest)
			try:
				os.remove(dest + ".seekable")
			except OSError:
				pass
		else:
			os.remove(tmp)
	except Exception:
		try:
			os.remove(tmp)
		except OSError:
			pass
		raise
	return dest, [path for _seq, path in rows]


def _cleanup_recording_chunks(attempt: str, property: str, paths: list[str]) -> None:
	for path in [*paths, _chunk_manifest_path(attempt, property)]:
		try:
			os.remove(path)
		except OSError:
			pass


def _file_recording_url(attempt: str, prop: str) -> str:
	"""Play is the file on disk, not the JSON stamp — a later results write
	used to wipe `recording_url` while leaving a 50–100 MB webm behind."""
	dest, _chunks = _assemble_recording_chunks(attempt, prop)
	# Read-time recovery may run while MediaRecorder is still producing later
	# sequences (`touch_property` returns a freshly shaped attempt). Keep every
	# chunk until explicit finish stamps the completed take; deleting seq 0 here
	# stranded the tail and made the partial read-time assembly permanent.
	dest = dest or _dest_path(attempt, prop)
	part = _part_path(attempt, prop)
	try:
		dest_size = os.path.getsize(dest) if os.path.exists(dest) else 0
		part_size = os.path.getsize(part) if os.path.exists(part) else 0
	except OSError:
		return ""
	if part_size > dest_size:
		try:
			os.replace(part, dest)
			dest_size = part_size
		except OSError:
			pass
	if dest_size <= 0:
		return ""
	return f"/private/files/{os.path.basename(dest)}"


def _ensure_seekable(path: str) -> str:
	"""MediaRecorder webm has no cues, so <video> cannot scrub. ffmpeg -c copy
	rewrites timestamps; a sidecar marks it done so we do not pay twice."""
	if not path or not os.path.exists(path):
		return path
	marker = path + ".seekable"
	if os.path.exists(marker):
		return path
	ffmpeg = shutil.which("ffmpeg")
	if not ffmpeg:
		return path
	tmp = path + ".tmp.webm"
	try:
		subprocess.run(
			[
				ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
				"-fflags", "+genpts", "-i", path, "-c", "copy", tmp,
			],
			check=True,
			timeout=180,
		)
		if os.path.getsize(tmp) > 0:
			os.replace(tmp, path)
			open(marker, "wb").close()
	except Exception:
		try:
			os.remove(tmp)
		except OSError:
			pass
	return path


def remux_attempt_recordings(attempt: str) -> None:
	"""Make every take for this run seekable. Globs files so a missing
	recording_url stamp cannot skip a house."""
	safe = _safe_id(attempt)
	folder = frappe.get_site_path("private", "files")
	prefix = f"practice-{safe}-"
	try:
		names = os.listdir(folder)
	except OSError:
		return
	for name in names:
		if not name.startswith(prefix) or not name.endswith(".webm"):
			continue
		if ".tmp." in name or name.endswith(".part"):
			continue
		_ensure_seekable(os.path.join(folder, name))


def _enqueue_remux(attempt: str) -> None:
	try:
		frappe.enqueue(
			"crm.api.practice.remux_attempt_recordings",
			attempt=attempt,
			queue="long",
			job_id=f"practice-remux-{attempt}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	except TypeError:
		frappe.enqueue(
			"crm.api.practice.remux_attempt_recordings",
			attempt=attempt,
			queue="long",
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Practice remux enqueue failed")


def _recording_url(att) -> str:
	if _has_recording_col():
		url = att.get("recording_url") if hasattr(att, "get") else getattr(att, "recording_url", None)
		if url:
			return str(url)
	name = att.get("name") if hasattr(att, "get") else getattr(att, "name", None)
	if not name:
		return ""
	return (
		frappe.db.get_value(
			"File",
			{"attached_to_doctype": ATTEMPT, "attached_to_name": name},
			"file_url",
		)
		or ""
	)


def _purge_recording(name: str) -> None:
	folder = frappe.get_site_path("private", "files")
	prefix = f"practice-{_safe_id(name)}"
	try:
		for fname in os.listdir(folder):
			if fname.startswith(prefix):
				try:
					os.remove(os.path.join(folder, fname))
				except OSError:
					pass
	except OSError:
		pass
	for fname in frappe.get_all(
		"File",
		filters={"attached_to_doctype": ATTEMPT, "attached_to_name": name},
		pluck="name",
	):
		try:
			frappe.delete_doc("File", fname, ignore_permissions=True, force=True)
		except Exception:
			pass


def _user_label(user: str) -> str:
	return frappe.db.get_value("User", user, "full_name") or user


def _log_view(*, kind: str, practice_set: str, attempt: str = "", subject_user: str = "") -> None:
	"""Lance-only audit. Never throws — a view log must not block the page."""
	if not frappe.db.exists("DocType", VIEW_LOG):
		return
	if kind == "attempt" and subject_user == frappe.session.user:
		return
	try:
		frappe.get_doc(
			{
				"doctype": VIEW_LOG,
				"viewer": frappe.session.user,
				"kind": kind,
				"practice_set": practice_set,
				"attempt": attempt or None,
				"subject_user": subject_user or None,
				"viewed_at": _now(),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Practice view log failed")


def _set_title(name: str) -> str:
	return frappe.db.get_value(SET, name, "title") or name


def _dt(val) -> str:
	return str(val) if val else ""


def _get_set(name: str):
	if not frappe.db.exists(SET, name):
		frappe.throw(_("Practice set {0} does not exist.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(SET, name)


def _get_prop(name: str):
	if not frappe.db.exists(PROP, name):
		frappe.throw(_("Practice property {0} does not exist.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(PROP, name)


def _get_attempt(name: str, *, write: bool = False):
	if not frappe.db.exists(ATTEMPT, name):
		frappe.throw(_("Practice attempt {0} does not exist.").format(name), frappe.DoesNotExistError)
	if write:
		# results is one JSON blob; without the row lock, finish_recording's
		# stamp loses to a concurrent set_comp_state / save_offer / touch.
		frappe.db.sql(
			"select name from `tabCRM Practice Attempt` where name=%s for update",
			name,
		)
	doc = frappe.get_doc(ATTEMPT, name)
	mine = doc.user == frappe.session.user
	if write and not mine:
		frappe.throw(_("Not your practice run."), frappe.PermissionError)
	if not write and not mine:
		# Others may review a finished run (picks, condition, video). An
		# in-progress one stays private unless you're a manager.
		if doc.status == "In Progress" and not _is_manager():
			frappe.throw(_("Not permitted."), frappe.PermissionError)
	return doc


def _has_seller_note() -> bool:
	return bool(frappe.db.has_column(PROP, "seller_note"))


def _used_seller_notes(set_name: str) -> set[str]:
	if not _has_seller_note():
		return set()
	return {
		(n or "").strip()
		for n in frappe.get_all(
			PROP,
			filters={"practice_set": set_name},
			pluck="seller_note",
		)
		if (n or "").strip()
	}


def _stamp_seller_note(name: str, practice_set: str) -> str:
	"""One seller-voice line per house. Empty rows (pre-this-field) fill on read."""
	if not _has_seller_note():
		return ""
	note = pick_seller_note(_used_seller_notes(practice_set))
	frappe.db.set_value(PROP, name, "seller_note", note, update_modified=False)
	return note


def _properties(set_name: str) -> list[dict]:
	fields = [
		"name",
		"source_lead",
		"lead_name",
		"property_address",
		"property_city",
		"property_state",
		"property_zip",
		"sort_order",
	]
	has_note = _has_seller_note()
	if has_note:
		fields.append("seller_note")
	rows = frappe.get_all(
		PROP,
		filters={"practice_set": set_name},
		fields=fields,
		order_by="sort_order asc, creation asc",
	)
	if not has_note:
		for row in rows:
			row["seller_note"] = ""
		return rows
	used = {
		(r.get("seller_note") or "").strip()
		for r in rows
		if (r.get("seller_note") or "").strip()
	}
	for row in rows:
		note = (row.get("seller_note") or "").strip()
		if note:
			row["seller_note"] = note
			continue
		note = pick_seller_note(used)
		frappe.db.set_value(PROP, row["name"], "seller_note", note, update_modified=False)
		row["seller_note"] = note
		used.add(note)
	return rows


def _elapsed(att) -> int:
	start = get_datetime(att.started_at)
	end = get_datetime(att.finished_at) if att.finished_at else _now()
	if not start:
		return 0
	wall = max(0, int(time_diff_in_seconds(end, start)))
	return max(0, wall - _paused_seconds(att, end))


def _paused_seconds(att, now=None) -> int:
	meta = _meta(_results(att))
	total = int(meta.get("paused_seconds") or 0)
	started = meta.get("pause_started_at")
	if started:
		try:
			total += max(0, int(time_diff_in_seconds(now or _now(), get_datetime(started))))
		except Exception:
			pass
	return total


def _remaining(att) -> int | None:
	limit = int(att.time_limit_min or 0) * 60
	if limit <= 0:
		return None
	if _time_mode(att) == "per_listing":
		results = _results(att)
		cur = _meta(results).get("current_property")
		slot = results.get(cur) if cur else None
		if not isinstance(slot, dict):
			return limit
		live = _live_elapsed(slot, _now(), _is_paused(results))
		return max(0, limit - live)
	return max(0, limit - _elapsed(att))


def _is_paused(results: dict) -> bool:
	return bool(_meta(results).get("pause_started_at"))


def _meta(results: dict) -> dict:
	m = results.get(META)
	if not isinstance(m, dict):
		m = {"paused_seconds": 0, "pause_started_at": None, "current_property": ""}
		results[META] = m
	m.setdefault("paused_seconds", 0)
	return m


def _iter_slots(results: dict):
	for key, slot in results.items():
		if key == META or not isinstance(slot, dict):
			continue
		yield key, slot


def _close_segment(slot: dict, now) -> None:
	started = slot.get("segment_started_at")
	if not started:
		return
	try:
		add = max(0, int(time_diff_in_seconds(now, get_datetime(started))))
	except Exception:
		add = 0
	slot["elapsed_seconds"] = int(slot.get("elapsed_seconds") or 0) + add
	slot["segment_started_at"] = None


def _open_segment(slot: dict, now) -> None:
	if slot.get("segment_started_at"):
		return
	slot["segment_started_at"] = str(now)
	if not slot.get("opened_at"):
		slot["opened_at"] = str(now)


def _live_elapsed(slot: dict, now, paused: bool) -> int:
	base = int(slot.get("elapsed_seconds") or 0)
	if paused or not slot.get("segment_started_at"):
		return base
	try:
		return base + max(0, int(time_diff_in_seconds(now, get_datetime(slot["segment_started_at"]))))
	except Exception:
		return base


def _int_or_none(val):
	if val is None or val == "":
		return None
	try:
		return int(round(float(val)))
	except (TypeError, ValueError):
		return None


def _formula_label(sc: dict) -> str:
	kind = sc.get("kind") or "cash"
	pct = sc.get("pct")
	pct_s = ""
	if pct not in (None, ""):
		try:
			pct_s = f" · {int(round(float(pct) * 100))}%"
		except (TypeError, ValueError):
			pct_s = ""
	if kind == "novation":
		return f"Novation{pct_s}"
	if kind == "list":
		return "List it"
	if kind == "rental":
		return f"Rental MAO{pct_s}"
	mult = 2 if int(_int_or_none(sc.get("mult")) or 1) == 2 else 1
	name = "2× repairs" if mult == 2 else "Classic"
	return f"{name}{pct_s}"


def _offer_calcs(slot: dict) -> list:
	"""ARV / repairs / offer / formula for every saved scene on this house."""
	off = slot.get("offer") if isinstance(slot.get("offer"), dict) else None
	if not off:
		return []
	out = []
	for sc in off.get("scenarios") or []:
		if not isinstance(sc, dict):
			continue
		kind = sc.get("kind") or off.get("kind") or "cash"
		if kind not in ("novation", "list", "rental"):
			kind = "cash"
		row = {
			"kind": kind,
			"arv": _int_or_none(sc.get("arv")),
			"repairs": None if kind in ("novation", "list") else _int_or_none(sc.get("repairs")),
			"offer": _int_or_none(sc.get("offer")),
			"formula": _formula_label({**sc, "kind": kind}),
		}
		if row["arv"] is None and row["offer"] is None:
			continue
		out.append(row)
	return out


def _offer_amount(slot: dict):
	calcs = _offer_calcs(slot)
	return calcs[0]["offer"] if calcs else None


def _results(att) -> dict:
	return _json(att.results)


def _write_results(att, results: dict) -> None:
	prev = _json(frappe.db.get_value(ATTEMPT, att.name, "results"))
	for key, old in _iter_slots(prev):
		url = (old.get("recording_url") or "").strip()
		if not url:
			continue
		slot = results.get(key)
		if isinstance(slot, dict) and not (slot.get("recording_url") or "").strip():
			slot["recording_url"] = url
	payload = _dump(results)
	frappe.db.set_value(ATTEMPT, att.name, "results", payload)
	att.results = payload


def _slot(results: dict, prop: str) -> dict:
	slot = results.get(prop)
	if not isinstance(slot, dict):
		slot = {}
		results[prop] = slot
	slot.setdefault("hidden", [])
	slot.setdefault("selected", [])
	return slot


def _submit(att, *, timed_out: bool = False) -> None:
	if att.status != "In Progress":
		return
	now = _now()
	results = _results(att)
	meta = _meta(results)
	if meta.get("pause_started_at"):
		try:
			meta["paused_seconds"] = int(meta.get("paused_seconds") or 0) + max(
				0, int(time_diff_in_seconds(now, get_datetime(meta["pause_started_at"])))
			)
		except Exception:
			pass
		meta["pause_started_at"] = None
	for _key, slot in _iter_slots(results):
		_close_segment(slot, now)
		elapsed = int(slot.get("elapsed_seconds") or 0)
		if slot.get("opened_at") and not slot.get("done_at"):
			slot["done_at"] = str(now)
		slot["duration_seconds"] = elapsed or slot.get("duration_seconds") or 0
	frappe.db.set_value(
		ATTEMPT,
		att.name,
		{
			"status": "Timed Out" if timed_out else "Submitted",
			"finished_at": now,
			"results": _dump(results),
		},
	)
	att.status = "Timed Out" if timed_out else "Submitted"
	att.finished_at = now
	att.results = _dump(results)
	_enqueue_remux(att.name)


def _expire_overtime_listings(att) -> None:
	"""Close any house that has used up the per-listing clock. Does not submit the set."""
	limit = int(att.time_limit_min or 0) * 60
	if limit <= 0 or _time_mode(att) != "per_listing":
		return
	results = _results(att)
	if _is_paused(results):
		return
	now = _now()
	changed = False
	for _key, slot in _iter_slots(results):
		if slot.get("timed_out") or slot.get("done_at"):
			continue
		live = _live_elapsed(slot, now, False)
		if live < limit:
			continue
		_close_segment(slot, now)
		slot["timed_out"] = True
		slot["done_at"] = str(now)
		slot["duration_seconds"] = int(slot.get("elapsed_seconds") or 0)
		changed = True
	if changed:
		_write_results(att, results)


def _maybe_expire(att):
	if att.status != "In Progress":
		return att
	if _time_mode(att) == "per_listing":
		_expire_overtime_listings(att)
		return att
	rem = _remaining(att)
	if rem == 0 and int(att.time_limit_min or 0) > 0:
		_submit(att, timed_out=True)
		return frappe.get_doc(ATTEMPT, att.name)
	return att


def _shape_attempt(att, properties: list[dict] | None = None) -> dict:
	results = _results(att)
	now = _now()
	paused = _is_paused(results)
	props = properties if properties is not None else _properties(att.practice_set)
	out_props = []
	for p in props:
		slot = results.get(p.name) or {}
		if not isinstance(slot, dict):
			slot = {}
		live = _live_elapsed(slot, now, paused)
		out_props.append(
			{
				**{k: p.get(k) for k in (
					"name", "source_lead", "lead_name", "property_address",
					"property_city", "property_state", "property_zip", "sort_order",
					"seller_note",
				)},
				"opened_at": slot.get("opened_at") or "",
				"done_at": slot.get("done_at") or "",
				"timed_out": bool(slot.get("timed_out")),
				"duration_seconds": slot.get("duration_seconds") if slot.get("done_at") else live,
				"elapsed_seconds": live,
				"selected_count": len(slot.get("selected") or []),
				"hidden_count": len(slot.get("hidden") or []),
				"has_offer": bool(slot.get("offer")),
				"offer": _offer_amount(slot),
				"calcs": _offer_calcs(slot),
				"recording_url": (
					slot.get("recording_url") or _file_recording_url(att.name, p.name)
				),
				"condition": slot.get("condition") or "",
			}
		)
	return {
		"name": att.name,
		"practice_set": att.practice_set,
		"set_title": _set_title(att.practice_set),
		"user": att.user,
		"user_name": _user_label(att.user),
		"status": att.status,
		"started_at": _dt(att.started_at),
		"finished_at": _dt(att.finished_at),
		"time_limit_min": int(att.time_limit_min or 0),
		"time_limit_mode": _time_mode(att, results),
		"elapsed_seconds": _elapsed(att),
		"remaining_seconds": _remaining(att),
		"paused": paused,
		"mine": att.user == frappe.session.user,
		"recording_url": _recording_url(att),
		"properties": out_props,
	}


def _shape_set(doc, *, with_properties: bool = False) -> dict:
	n = frappe.db.count(PROP, {"practice_set": doc.name})
	mine = frappe.get_all(
		ATTEMPT,
		filters={"practice_set": doc.name, "user": frappe.session.user},
		fields=["name", "status", "started_at", "finished_at", "time_limit_min"],
		order_by="creation desc",
		limit_page_length=1,
	)
	last = None
	if mine:
		row = mine[0]
		# elapsed needs a full doc only for in-progress; approximate from stamps
		start = get_datetime(row.started_at)
		end = get_datetime(row.finished_at) if row.finished_at else _now()
		elapsed = max(0, int(time_diff_in_seconds(end, start))) if start else 0
		last = {
			"name": row.name,
			"status": row.status,
			"elapsed_seconds": elapsed,
		}
	out = {
		"name": doc.name,
		"title": doc.title,
		"time_limit_min": int(doc.time_limit_min or 0),
		"time_limit_mode": _time_mode(doc),
		"notes": doc.notes or "",
		"is_active": int(doc.is_active or 0),
		"property_count": n,
		"owner": doc.owner,
		"modified": _dt(doc.modified),
		"my_attempt": last,
	}
	if with_properties:
		out["properties"] = _properties(doc.name)
	return out


# ---------------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def list_sets() -> dict:
	"""Every set, for every sales user."""
	_guard()
	if not _available():
		return {"available": False, **_flags(), "sets": []}
	fields = ["name", "title", "time_limit_min", "notes", "is_active", "owner", "modified"]
	if _has_time_mode_col(SET):
		fields.append("time_limit_mode")
	rows = frappe.get_all(
		SET,
		fields=fields,
		order_by="modified desc",
	)
	return {
		"available": True,
		**_flags(),
		"sets": [_shape_set(frappe._dict(r)) for r in rows],
	}


@frappe.whitelist()
def get_set(name: str) -> dict:
	_need()
	doc = _get_set(name)
	_log_view(kind="set", practice_set=name, subject_user=doc.owner)
	out = _shape_set(doc, with_properties=True)
	out.update(_flags())
	out["available"] = True
	return out


@frappe.whitelist()
def save_set(
	name: str = "",
	title: str = "",
	time_limit_min: int | str = 0,
	time_limit_mode: str = "",
	notes: str = "",
	is_active: int | str = 1,
) -> dict:
	_need()
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Give the set a name."))
	try:
		limit = max(0, int(time_limit_min or 0))
	except (TypeError, ValueError):
		limit = 0
	active = 0 if str(is_active) in ("0", "false", "False") else 1
	raw_mode = str(time_limit_mode or "").strip()
	if raw_mode == "per_listing":
		mode = "per_listing"
	elif raw_mode == "set":
		mode = "set"
	else:
		# New sets default per listing; an existing save that omits the field
		# keeps whatever is already on the doc (empty = whole-set, legacy).
		mode = "per_listing" if not name else ""
	if name:
		doc = _get_set(name)
		# `Document.title` can be a read-only helper off `title_field`; set() is
		# what actually dirties the column so Save persists the new name.
		doc.set("title", title)
		doc.set("time_limit_min", limit)
		doc.set("notes", notes or "")
		doc.set("is_active", active)
		if mode and _has_time_mode_col(SET):
			doc.set("time_limit_mode", mode)
		doc.save(ignore_permissions=True)
	else:
		payload = {
			"doctype": SET,
			"title": title,
			"time_limit_min": limit,
			"notes": notes or "",
			"is_active": active,
		}
		if _has_time_mode_col(SET):
			payload["time_limit_mode"] = mode or "per_listing"
		doc = frappe.get_doc(payload)
		doc.insert(ignore_permissions=True)
	return _shape_set(doc, with_properties=True) | {"available": True, **_flags()}


@frappe.whitelist()
def delete_set(name: str) -> dict:
	_need()
	_assert_manager()
	_get_set(name)
	for att in frappe.get_all(ATTEMPT, filters={"practice_set": name}, pluck="name"):
		_purge_recording(att)
		frappe.delete_doc(ATTEMPT, att, ignore_permissions=True, force=True)
	for prop in frappe.get_all(PROP, filters={"practice_set": name}, pluck="name"):
		frappe.delete_doc(PROP, prop, ignore_permissions=True, force=True)
	frappe.delete_doc(SET, name, ignore_permissions=True, force=True)
	return {"ok": True}


@frappe.whitelist()
def search_leads(q: str = "") -> list[dict]:
	"""Leads with an address, for adding to a set."""
	_need()
	q = (q or "").strip()
	filters = [["property_address", "is", "set"]]
	or_filters = None
	if q:
		like = f"%{q}%"
		or_filters = [
			["lead_name", "like", like],
			["property_address", "like", like],
			["name", "like", like],
		]
	return frappe.get_all(
		"CRM Lead",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "lead_name", "property_address", "property_city", "property_state"],
		order_by="modified desc",
		limit_page_length=20,
	)


def _insert_property(practice_set: str, lead: str, sort_order: int) -> dict:
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if frappe.db.exists(PROP, {"practice_set": practice_set, "source_lead": lead}):
		frappe.throw(_("That property is already in this set."))
	src = frappe.db.get_value(
		"CRM Lead",
		lead,
		[
			"name",
			"lead_name",
			"property_address",
			"property_city",
			"property_state",
			"property_zip",
		],
		as_dict=True,
	)
	address = (src.property_address or "").strip()
	if not address:
		frappe.throw(_("That lead has no property address to comp."))
	fields = {
		"doctype": PROP,
		"practice_set": practice_set,
		"source_lead": lead,
		"lead_name": src.lead_name or "",
		"property_address": address,
		"property_city": src.property_city or "",
		"property_state": src.property_state or "",
		"property_zip": src.property_zip or "",
		"sort_order": sort_order,
	}
	if frappe.db.has_column("CRM Lead", "property_lat"):
		latlng = frappe.db.get_value(
			"CRM Lead", lead, ["property_lat", "property_lng"], as_dict=True
		)
		if latlng:
			fields["property_lat"] = latlng.property_lat
			fields["property_lng"] = latlng.property_lng
	doc = frappe.get_doc(fields)
	doc.insert(ignore_permissions=True)
	note = _stamp_seller_note(doc.name, practice_set)
	return {
		"name": doc.name,
		"source_lead": doc.source_lead,
		"lead_name": doc.lead_name,
		"property_address": doc.property_address,
		"property_city": doc.property_city,
		"property_state": doc.property_state,
		"property_zip": doc.property_zip,
		"sort_order": doc.sort_order,
		"seller_note": note,
	}


@frappe.whitelist()
def add_property(practice_set: str, lead: str) -> dict:
	_need()
	_get_set(practice_set)
	n = frappe.db.count(PROP, {"practice_set": practice_set})
	return _insert_property(practice_set, lead, (n + 1) * 10)


def _split_needles(raw) -> list[str]:
	if not raw:
		return []
	if isinstance(raw, list):
		parts = raw
	else:
		parts = re.split(r"[,\n;]+", str(raw))
	return [p.strip() for p in parts if str(p).strip()]


def _area_hit(value: str, needles: list[str], *, zip_mode: bool = False) -> bool:
	if not needles:
		return True
	val = (value or "").strip()
	if zip_mode:
		digits = re.sub(r"\D", "", val)[:5]
		return any(re.sub(r"\D", "", n)[:5] == digits for n in needles if digits)
	low = val.lower()
	return any(n.lower() in low or low == n.lower() for n in needles)


@frappe.whitelist()
def add_random_properties(
	practice_set: str,
	count: int | str = 5,
	states: str = "",
	cities: str = "",
	counties: str = "",
	zips: str = "",
) -> dict:
	"""Fill a set with N random addressed leads. Optional area filters.

	Values inside one field are OR (OH or IN). Filled fields AND together
	(Ohio AND Columbus). County is has_column-guarded.
	"""
	_need()
	_get_set(practice_set)
	try:
		wanted = max(0, min(50, int(count or 0)))
	except (TypeError, ValueError):
		wanted = 0
	if wanted <= 0:
		return {"added": 0, "wanted": 0, "properties": []}
	state_n = _split_needles(states)
	city_n = _split_needles(cities)
	county_n = _split_needles(counties)
	zip_n = _split_needles(zips)
	taken = [
		n for n in frappe.get_all(
			PROP, filters={"practice_set": practice_set}, pluck="source_lead"
		) if n
	]
	filters = [["property_address", "is", "set"]]
	if taken:
		filters.append(["name", "not in", taken])
	fields = ["name", "property_city", "property_state", "property_zip"]
	has_county = frappe.db.has_column("CRM Lead", "property_county")
	if has_county:
		fields.append("property_county")
	rows = frappe.get_all(
		"CRM Lead",
		filters=filters,
		fields=fields,
		limit_page_length=5000,
	)
	pool = []
	for row in rows:
		if not _area_hit(row.property_state, state_n):
			continue
		if not _area_hit(row.property_city, city_n):
			continue
		if not _area_hit(row.property_zip, zip_n, zip_mode=True):
			continue
		if county_n and has_county and not _area_hit(row.get("property_county") or "", county_n):
			continue
		pool.append(row.name)
	matched = len(pool)
	if len(pool) > wanted:
		pool = random.sample(pool, wanted)
	n = frappe.db.count(PROP, {"practice_set": practice_set})
	added = []
	for i, lead in enumerate(pool):
		try:
			added.append(_insert_property(practice_set, lead, (n + i + 1) * 10))
		except Exception:
			continue
	return {"added": len(added), "wanted": wanted, "matched": matched, "properties": added}


@frappe.whitelist()
def remove_property(name: str) -> dict:
	"""Drop a house from the set. Refused once anyone has started a run."""
	_need()
	prop = _get_prop(name)
	if frappe.db.exists(ATTEMPT, {"practice_set": prop.practice_set}):
		frappe.throw(_("A run has already started — houses can't be removed from this set."))
	frappe.delete_doc(PROP, name, ignore_permissions=True, force=True)
	return {"ok": True}


@frappe.whitelist()
def reorder_properties(practice_set: str, names: str | list | None = None) -> dict:
	_need()
	_get_set(practice_set)
	if isinstance(names, str):
		names = json.loads(names or "[]")
	names = [str(n) for n in (names or [])]
	existing = {p.name for p in _properties(practice_set)}
	if set(names) != existing:
		frappe.throw(_("Property list does not match this set."))
	for i, name in enumerate(names):
		frappe.db.set_value(PROP, name, "sort_order", (i + 1) * 10)
	return {"ok": True, "properties": _properties(practice_set)}


# ---------------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def start_attempt(practice_set: str) -> dict:
	"""Resume an in-progress run, or start a new one."""
	_need()
	doc = _get_set(practice_set)
	props = _properties(practice_set)
	if not props:
		frappe.throw(_("This set has no properties yet."))
	open_ones = frappe.get_all(
		ATTEMPT,
		filters={
			"practice_set": practice_set,
			"user": frappe.session.user,
			"status": "In Progress",
		},
		pluck="name",
		limit_page_length=1,
	)
	if open_ones:
		att = _maybe_expire(_get_attempt(open_ones[0], write=True))
		if att.status == "In Progress":
			return {"available": True, **_shape_attempt(att, props)}
	mode = _time_mode(doc)
	payload = {
		"doctype": ATTEMPT,
		"practice_set": practice_set,
		"user": frappe.session.user,
		"status": "In Progress",
		"started_at": _now(),
		"time_limit_min": int(doc.time_limit_min or 0),
		"results": "{}",
	}
	if _has_time_mode_col(ATTEMPT):
		payload["time_limit_mode"] = mode
	att = frappe.get_doc(payload)
	att.insert(ignore_permissions=True)
	results = _results(att)
	_meta(results)["time_limit_mode"] = mode
	_write_results(att, results)
	return {"available": True, **_shape_attempt(att, props)}


@frappe.whitelist()
def get_attempt(name: str) -> dict:
	_need()
	att = _get_attempt(name)
	if att.user != frappe.session.user:
		_log_view(
			kind="attempt",
			practice_set=att.practice_set,
			attempt=att.name,
			subject_user=att.user,
		)
	elif att.status == "In Progress":
		att = _maybe_expire(_get_attempt(name, write=True))
	return {"available": True, **_flags(), **_shape_attempt(att)}


@frappe.whitelist()
def touch_property(attempt: str, property: str) -> dict:
	"""Start this house's clock; close the previous house's segment."""
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status != "In Progress":
		return {"available": True, **_shape_attempt(att)}
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	now = _now()
	results = _results(att)
	meta = _meta(results)
	paused = _is_paused(results)
	for key, other in _iter_slots(results):
		if key != property:
			_close_segment(other, now)
	slot = _slot(results, property)
	if slot.get("timed_out"):
		pass
	elif not paused:
		_open_segment(slot, now)
	elif not slot.get("opened_at"):
		slot["opened_at"] = str(now)
	meta["current_property"] = property
	_write_results(att, results)
	return {"available": True, **_shape_attempt(att)}


@frappe.whitelist()
def pause_attempt(attempt: str) -> dict:
	"""Freeze the clocks and (on the client) the recorder."""
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status != "In Progress":
		return {"available": True, **_shape_attempt(att)}
	now = _now()
	results = _results(att)
	meta = _meta(results)
	if meta.get("pause_started_at"):
		return {"available": True, **_shape_attempt(att)}
	for _key, slot in _iter_slots(results):
		_close_segment(slot, now)
	meta["pause_started_at"] = str(now)
	_write_results(att, results)
	return {"available": True, **_shape_attempt(att)}


@frappe.whitelist()
def resume_attempt(attempt: str) -> dict:
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status != "In Progress":
		return {"available": True, **_shape_attempt(att)}
	now = _now()
	results = _results(att)
	meta = _meta(results)
	started = meta.get("pause_started_at")
	if started:
		try:
			meta["paused_seconds"] = int(meta.get("paused_seconds") or 0) + max(
				0, int(time_diff_in_seconds(now, get_datetime(started)))
			)
		except Exception:
			pass
		meta["pause_started_at"] = None
	cur = meta.get("current_property")
	if cur and results.get(cur):
		_open_segment(_slot(results, cur), now)
	_write_results(att, results)
	return {"available": True, **_shape_attempt(att)}


@frappe.whitelist()
def mark_property_done(attempt: str, property: str) -> dict:
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status != "In Progress":
		return {"available": True, **_shape_attempt(att)}
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	now = _now()
	results = _results(att)
	slot = _slot(results, property)
	_close_segment(slot, now)
	if not slot.get("opened_at"):
		slot["opened_at"] = str(now)
	if not slot.get("done_at"):
		slot["done_at"] = str(now)
		slot["duration_seconds"] = int(slot.get("elapsed_seconds") or 0)
		_write_results(att, results)
	return {"available": True, **_shape_attempt(att)}


@frappe.whitelist()
def save_condition(attempt: str, property: str, text: str = "") -> dict:
	"""Condition notes for this house. Lives on the attempt, never the real lead."""
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status != "In Progress":
		frappe.throw(_("This practice run is finished."))
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	note = (text or "").strip()
	if len(note) > 4000:
		note = note[:4000]
	results = _results(att)
	slot = _slot(results, property)
	if not slot.get("opened_at"):
		slot["opened_at"] = str(_now())
	slot["condition"] = note
	_write_results(att, results)
	return {"available": True, **_shape_attempt(att)}


@frappe.whitelist()
def submit_attempt(attempt: str) -> dict:
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status == "In Progress":
		_submit(att, timed_out=False)
		att = frappe.get_doc(ATTEMPT, att.name)
	return {"available": True, **_shape_attempt(att)}


@frappe.whitelist()
def list_results(practice_set: str) -> dict:
	"""Submitted runs for everyone; in-progress stays private to the owner."""
	_need()
	_get_set(practice_set)
	filters: dict[str, Any] = {"practice_set": practice_set}
	fields = [
		"name", "user", "status", "started_at", "finished_at",
		"time_limit_min", "results",
	]
	if _has_recording_col():
		fields.append("recording_url")
	if _has_time_mode_col(ATTEMPT):
		fields.append("time_limit_mode")
	rows = frappe.get_all(
		ATTEMPT,
		filters=filters,
		fields=fields,
		order_by="creation desc",
		limit_page_length=200,
	)
	props = _properties(practice_set)
	n_props = len(props)
	out = []
	me = frappe.session.user
	for r in rows:
		if r.status == "In Progress" and r.user != me and not _is_manager():
			continue
		att = frappe._dict(r)
		# _shape_attempt expects .practice_set
		att.practice_set = practice_set
		shaped = _shape_attempt(att, props)
		done = sum(1 for p in shaped["properties"] if p.get("done_at"))
		opened = sum(1 for p in shaped["properties"] if p.get("opened_at"))
		out.append(
			{
				**{k: shaped[k] for k in (
					"name", "user", "user_name", "status", "started_at",
					"finished_at", "time_limit_min", "elapsed_seconds",
					"remaining_seconds", "recording_url", "paused", "mine",
				)},
				"done": done,
				"opened": opened,
				"property_count": n_props,
				"properties": shaped["properties"],
			}
		)
	return {
		"available": True,
		**_flags(),
		"can_view_log": frappe.session.user == LANCE_USER,
		"attempts": out,
	}


@frappe.whitelist()
def list_view_log(practice_set: str = "") -> dict:
	"""Who opened whose work. Lance only."""
	_need()
	if frappe.session.user != LANCE_USER:
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	if not frappe.db.exists("DocType", VIEW_LOG):
		return {"available": False, "rows": []}
	filters = {}
	if practice_set:
		filters["practice_set"] = practice_set
	rows = frappe.get_all(
		VIEW_LOG,
		filters=filters,
		fields=[
			"name", "viewer", "kind", "practice_set", "attempt",
			"subject_user", "viewed_at",
		],
		order_by="viewed_at desc",
		limit_page_length=200,
	)
	out = []
	for r in rows:
		out.append(
			{
				"name": r.name,
				"viewer": r.viewer,
				"viewer_name": _user_label(r.viewer),
				"kind": r.kind,
				"practice_set": r.practice_set,
				"set_title": _set_title(r.practice_set) if r.practice_set else "",
				"attempt": r.attempt or "",
				"subject_user": r.subject_user or "",
				"subject_name": _user_label(r.subject_user) if r.subject_user else "",
				"viewed_at": _dt(r.viewed_at),
			}
		)
	return {"available": True, "rows": out}


# ---------------------------------------------------------------------------------
# Screen + mic recording
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def upload_recording_chunk(attempt: str, property: str, seq: int | str = 0) -> dict:
	"""Persist one idempotent MediaRecorder slice for this house.

	Each sequence gets its own file. Retrying a request after a lost response
	overwrites that sequence instead of appending it twice; an interrupted take
	keeps every acknowledged slice available for later assembly/recovery.
	"""
	_need()
	att = _get_attempt(attempt, write=True)
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	upload = (frappe.request.files or {}).get("file")
	if not upload:
		frappe.throw(_("No file received."))
	data = upload.stream.read()
	if not data:
		return {"ok": True, "bytes": 0}
	if len(data) > MAX_CHUNK_BYTES:
		frappe.throw(_("Recording chunk is too large."))
	try:
		seq_n = int(seq)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid recording sequence."))
	if seq_n < 0 or seq_n > 100_000:
		frappe.throw(_("Invalid recording sequence."))

	path = _chunk_path(att.name, prop.name, seq_n)
	manifest = _chunk_manifest(att.name, prop.name)
	key = str(seq_n)
	previous = manifest.get(key)
	if previous is None and os.path.exists(path):
		previous = os.path.getsize(path)
	previous = previous or 0
	total = sum(manifest.values()) - previous + len(data)
	if total > MAX_RECORDING_BYTES:
		frappe.throw(_("Recording is too large."))

	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "wb") as fh:
		fh.write(data)
	manifest[key] = len(data)
	# Manifest first is conservative across a crash: it may temporarily count a
	# missing sequence, but it can never undercount bytes and exceed the cap.
	_write_chunk_manifest(att.name, prop.name, manifest)
	os.replace(tmp, path)
	return {"ok": True, "bytes": total, "seq": seq_n}


@frappe.whitelist()
def stream_recording(attempt: str, property: str):
	"""Inline the house's webm so <video> can play it (private files 403 in the tag).

	Must honour Range. Dumping the whole file as 200 with no Accept-Ranges makes
	Chrome treat it as a live stream — play works, scrub snaps back — even after
	ffmpeg has written duration + cues into the bytes."""
	_need()
	att = _get_attempt(attempt, write=False)
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	# Reading is also the recovery path for a browser that closed before the
	# explicit finish call: contiguous durable chunks can still become a take.
	_file_recording_url(att.name, prop.name)
	path = _ensure_seekable(_dest_path(att.name, prop.name))
	if not os.path.exists(path) or os.path.getsize(path) == 0:
		frappe.throw(_("No recording."), frappe.DoesNotExistError)
	return send_file(
		path,
		environ=frappe.local.request.environ,
		mimetype="video/webm",
		conditional=True,
		as_attachment=False,
		download_name=os.path.basename(path),
	)


@frappe.whitelist()
def finish_recording(attempt: str, property: str) -> dict:
	"""Finalize this house's on-disk stream and stamp it on the slot.

	Do NOT insert a Frappe File row here. A normal practice take is 40–150 MB,
	while File validates the whole assembled file against the site's 25 MB upload
	limit. The chunks already live in private/files and playback is our guarded
	Range endpoint, so the File row adds no access control and used to make long
	takes fail at the final stamp while leaving perfectly good bytes on disk.
	"""
	_need()
	att = _get_attempt(attempt, write=True)
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	dest, chunk_paths = _assemble_recording_chunks(att.name, prop.name)
	dest = dest or _dest_path(att.name, prop.name)
	part = _part_path(att.name, prop.name)
	try:
		part_size = os.path.getsize(part) if os.path.exists(part) else 0
		dest_size = os.path.getsize(dest) if os.path.exists(dest) else 0
	except OSError:
		part_size = dest_size = 0
	if part_size > dest_size:
		os.replace(part, dest)
	elif part_size:
		# A later finish (house revisit, submit) must not replace a real
		# take with the leftover crumbs still in .part.
		try:
			os.remove(part)
		except OSError:
			pass
	elif dest_size <= 0:
		return {"ok": False, "error": "no recording"}
	fname = os.path.basename(dest)
	file_url = f"/private/files/{fname}"
	results = _results(att)
	slot = _slot(results, prop.name)
	slot["recording_url"] = file_url
	_write_results(att, results)
	if chunk_paths:
		_cleanup_recording_chunks(att.name, prop.name, chunk_paths)
	return {"ok": True, "url": file_url, "property": prop.name}


# ---------------------------------------------------------------------------------
# Recording reactions (Loom-style emoji + comments at a timestamp)
# ---------------------------------------------------------------------------------
def _parse_reaction_content(raw) -> dict | None:
	text = (raw or "").strip()
	if not text:
		return None
	if "<" in text:
		i, j = text.find("{"), text.rfind("}")
		if i < 0 or j <= i:
			return None
		text = text[i : j + 1]
	try:
		data = json.loads(text)
	except Exception:
		return None
	if not isinstance(data, dict) or data.get("v") != _REACTION_V:
		return None
	return data


def _reaction_row(doc, data: dict, mine: str) -> dict:
	author = doc.comment_email or doc.owner
	return {
		"name": doc.name,
		"at_time": flt(data.get("at_time")),
		"emoji": (data.get("emoji") or "").strip(),
		"content": (data.get("text") or "").strip(),
		"author": author,
		"author_name": get_fullname(author) if author else "",
		"creation": str(doc.creation),
		"mine": author == mine,
	}


def _publish_reaction(attempt: str, property: str) -> None:
	frappe.publish_realtime(
		"crm_practice_reaction",
		{"attempt": attempt, "property": property},
		after_commit=True,
	)


@frappe.whitelist()
def get_recording_reactions(attempt: str, property: str) -> list:
	"""Timestamped emoji + comments on this house's take, oldest moment first."""
	_need()
	att = _get_attempt(attempt, write=False)
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": ATTEMPT,
			"reference_name": att.name,
			"comment_type": "Comment",
		},
		fields=["name", "content", "owner", "comment_email", "creation"],
		order_by="creation asc",
		limit_page_length=_MAX_REACTIONS,
	)
	mine = frappe.session.user
	out = []
	for r in rows:
		data = _parse_reaction_content(r.content)
		if not data or data.get("property") != prop.name:
			continue
		out.append(_reaction_row(frappe._dict(r), data, mine))
	out.sort(key=lambda x: (x["at_time"], x["creation"]))
	return out


@frappe.whitelist()
def add_recording_reaction(
	attempt: str,
	property: str,
	at_time=None,
	emoji: str = "",
	content: str = "",
) -> dict:
	"""Drop an emoji or a comment at `at_time` seconds. Anyone who can watch can react."""
	_need()
	att = _get_attempt(attempt, write=False)
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	emoji = (emoji or "").strip()
	text = (content or "").strip()
	if emoji and (len(emoji) > 16 or "<" in emoji or "\n" in emoji):
		frappe.throw(_("Invalid reaction."))
	if text:
		text = text[:_MAX_REACTION_TEXT]
	if not emoji and not text:
		frappe.throw(_("Add an emoji or a comment."))
	existing = get_recording_reactions(att.name, prop.name)
	if len(existing) >= _MAX_REACTIONS:
		frappe.throw(_("Too many reactions on this recording."))
	payload = {
		"v": _REACTION_V,
		"property": prop.name,
		"at_time": max(0.0, flt(at_time)),
		"emoji": emoji,
		"text": text,
	}
	doc = frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": ATTEMPT,
			"reference_name": att.name,
			"content": _dump(payload),
			"comment_email": frappe.session.user,
			"comment_by": get_fullname(frappe.session.user),
		}
	)
	doc.flags.skip_mention_notification = True
	doc.insert(ignore_permissions=True)
	_publish_reaction(att.name, prop.name)
	return _reaction_row(doc, payload, frappe.session.user)


@frappe.whitelist()
def delete_recording_reaction(name: str) -> dict:
	"""Delete your own reaction, or any if you're a manager."""
	_need()
	if not frappe.db.exists("Comment", name):
		frappe.throw(_("Reaction not found."), frappe.DoesNotExistError)
	doc = frappe.get_doc("Comment", name)
	data = _parse_reaction_content(doc.content)
	if not data or doc.reference_doctype != ATTEMPT:
		frappe.throw(_("Reaction not found."), frappe.DoesNotExistError)
	author = doc.comment_email or doc.owner
	if author != frappe.session.user and not _is_manager():
		frappe.throw(_("You can only delete your own reactions."), frappe.PermissionError)
	# Confirm the viewer can still see this take.
	_get_attempt(doc.reference_name, write=False)
	attempt = doc.reference_name
	prop = data.get("property") or ""
	doc.delete(ignore_permissions=True)
	_publish_reaction(attempt, prop)
	return {"deleted": name}


# ---------------------------------------------------------------------------------
# Comps sandbox
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def get_comps(
	attempt: str,
	property: str,
	radius_mi=None,
	limit=None,
	filters=None,
	auto: int | str = 0,
	include_hidden: int | str = 1,
	inventory=None,
	settle: int | str = 0,
) -> dict:
	"""The real comps map for this property, with THIS run's hides/picks."""
	_need()
	att = _get_attempt(attempt)
	mine = att.user == frappe.session.user
	if mine and att.status == "In Progress":
		att = _maybe_expire(_get_attempt(attempt, write=True))
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	if not prop.source_lead or not frappe.db.exists("CRM Lead", prop.source_lead):
		frappe.throw(_("The source lead for this property is gone."))
	results = _results(att)
	slot = results.get(property) if isinstance(results.get(property), dict) else {}
	if mine and att.status == "In Progress":
		slot = _slot(results, property)
		if not slot.get("opened_at"):
			slot["opened_at"] = str(_now())
			_write_results(att, results)
	data = get_lead_comps(
		prop.source_lead,
		radius_mi=radius_mi,
		limit=limit,
		filters=filters,
		auto=auto,
		include_hidden=include_hidden,
		state={"hidden": slot.get("hidden") or [], "selected": slot.get("selected") or []},
		inventory=inventory,
		settle=settle,
	)
	data["practice"] = True
	data["practice_locked"] = (not mine) or att.status != "In Progress"
	data["offer"] = slot.get("offer")
	data["source_lead"] = prop.source_lead
	return data


@frappe.whitelist()
def set_comp_state(attempt: str, property: str, comp: str, state: str) -> dict:
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	if att.status != "In Progress":
		frappe.throw(_("This practice run is finished."))
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	if state not in ("selected", "hidden", "none"):
		frappe.throw(_("Unknown comp state {0}").format(state))
	results = _results(att)
	slot = _slot(results, property)
	if not slot.get("opened_at"):
		slot["opened_at"] = str(_now())
	hidden = {str(x) for x in (slot.get("hidden") or [])}
	selected = {str(x) for x in (slot.get("selected") or [])}
	comp = str(comp)
	hidden.discard(comp)
	selected.discard(comp)
	if state == "hidden":
		hidden.add(comp)
	elif state == "selected":
		selected.add(comp)
	slot["hidden"] = sorted(hidden)
	slot["selected"] = sorted(selected)
	_write_results(att, results)
	return {"ok": True, "hidden": len(hidden), "selected": len(selected), "state": state}


@frappe.whitelist()
def save_offer(
	attempt: str,
	property: str,
	scenarios=None,
	comps=None,
	subject_sqft=None,
	notes: str = "",
) -> dict:
	"""Store the calc on the attempt. Never writes a Comment on the real lead.

	The owner can still save after they submit — fixing a number on review is
	a real move, and locking the calc at Submit made the button a lie.
	"""
	_need()
	att = _maybe_expire(_get_attempt(attempt, write=True))
	prop = _get_prop(property)
	if prop.practice_set != att.practice_set:
		frappe.throw(_("That property is not in this set."))
	from crm.api.cash_offer import _comps, _scene, _scene_payload

	if isinstance(scenarios, str):
		scenarios = json.loads(scenarios or "[]")
	if isinstance(comps, str):
		comps = json.loads(comps or "[]")
	sqft = flt(subject_sqft or 0)
	scenes = [s for s in (_scene(x, sqft) for x in (scenarios or [])) if s]
	if not scenes:
		frappe.throw(_("Type a value in at least one scenario first."))
	used = _comps(comps)
	kind = scenes[0]["kind"] if scenes else "cash"
	offer = {
		"kind": kind,
		"sqft": sqft,
		"notes": (notes or "").strip(),
		"scenarios": [_scene_payload(sc) for sc in scenes],
		"comps": used,
	}
	results = _results(att)
	slot = _slot(results, property)
	if not slot.get("opened_at"):
		slot["opened_at"] = str(_now())
	slot["offer"] = offer
	_write_results(att, results)
	return {"ok": True, "offer": offer}

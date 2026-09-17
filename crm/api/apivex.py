# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Apivex Realtor — gallery fallback photos, and the Realtor estimate.

Zillow remains the comps source (search, facts, priceHistory). Two holes this
fills, nothing else:

* **Photos.** Off-market solds often come back with a single Zillow frame;
  Realtor's listing photos stay up longer. One call, on an explicit gallery
  open, only when Zillow already returned ≤1 image.
* **The Realtor estimate** for the SUBJECT tile, beside the Zestimate and the
  Redfin Estimate — three independent AVMs on one line, so a rep pricing the
  house sees where the models disagree rather than anchoring on one. Two
  calls (`/property/details` by address → `property_id` → `/property/estimates`;
  the estimates endpoint does not resolve an address itself), ~1.5s total,
  measured 4/4 hits on real leads incl. an off-market one. Realtor publishes
  several AVMs (Cotality/CoreLogic, Quantarium, Collateral Analytics) and
  flags the one it headlines as `isbest_homevalue`; that is the number shown,
  the others ride along for the tooltip.

Same thread + join-budget + background-warm shape as `redfin.py`: the fetch
runs beside the Zillow refresh, the request never waits more than a second
for it, and a miss lands in Redis for the next load. Cached per lead in
Redis (prefix declared in `persistent_cache_keys`) — hits for 30 days, a
no-match for 7 (an address Realtor cannot resolve is otherwise re-billed on
every open), an error for 15 minutes.

Key: site_config `apivex_api_key`. Absent, everything here is a silent no-op.
"""

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

HOST = "api.apivex.com"
BASE = f"https://{HOST}"
TIMEOUT = 20
CONF_KEY = "apivex_api_key"

#: Bump when the cached estimate record's shape changes — the version in the
#: key is the ONLY invalidation path (same rule as the zillow_area caches).
ESTIMATE_CACHE_VERSION = 2  # v2: looser street match (directional suffix)
ESTIMATE_TTL_HIT = 30 * 86400
ESTIMATE_TTL_MISS = 7 * 86400
ESTIMATE_TTL_ERROR = 15 * 60
#: Thread-side budget for the two serial calls (measured ~0.5–0.9s each).
ESTIMATE_TIMEOUT = 8


def _api_key():
	import frappe

	return (frappe.conf.get(CONF_KEY) or "").strip()


def realtor_photo_urls(address: str, limit=60):
	"""Address -> list of Realtor CDN hrefs, or []. Never raises."""
	addr = (address or "").strip()
	if not addr:
		return []
	from crm.api import vendor_facts

	owned, env = vendor_facts.payload_or_fallback(vendor_facts.realtor_photos(addr))
	if owned:
		photos = ((env or {}).get("payload") or {}).get("photos") or []
		return [p for p in photos if isinstance(p, str) and p.startswith("http")][: int(limit)]
	key = _api_key()
	if not key:
		return []
	params = urllib.parse.urlencode({"address": addr})
	url = f"{BASE}/realtor/property/photos?{params}"
	req = urllib.request.Request(
		url,
		headers={
			"x-apivex-key": key,
			"Accept": "application/json",
		},
	)
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
			body = json.loads(resp.read().decode("utf-8", "replace"))
	except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
		return []
	except Exception:
		import frappe

		frappe.log_error(frappe.get_traceback(), "Apivex: realtor photos failed")
		return []
	return _hrefs(body, limit)


def _hrefs(body, limit):
	rows = body
	if isinstance(body, dict):
		rows = body.get("photos") or body.get("data") or body.get("results") or []
	if not isinstance(rows, list):
		return []
	out = []
	for row in rows:
		href = ""
		if isinstance(row, str):
			href = row
		elif isinstance(row, dict):
			href = (row.get("href") or row.get("url") or "").strip()
		if href.startswith("http") and href not in out:
			out.append(href)
		if len(out) >= int(limit):
			break
	return out


# ── Realtor estimate (subject tile) ──────────────────────────────────────────


def _estimate_cache_key(lead):
	return f"crm:realtor-estimate:v{ESTIMATE_CACHE_VERSION}:{lead}"


def _get_json(path, params, key, timeout=TIMEOUT):
	"""THREAD-SAFE, frappe-free GET. Returns (status, body|None)."""
	url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
	req = urllib.request.Request(url, headers={"x-apivex-key": key, "Accept": "application/json"})
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
	except urllib.error.HTTPError as e:
		return e.code, None


def _pick_current(current_values):
	"""Realtor's headline AVM (`isbest_homevalue`), else the first priced one."""
	rows = [r for r in (current_values or []) if isinstance(r, dict) and r.get("estimate")]
	if not rows:
		return None
	best = next((r for r in rows if r.get("isbest_homevalue") is True), rows[0])
	return best


def _same_street(a: str, b: str) -> bool:
	"""Street keys match, allowing one side to carry a trailing directional
	or suffix the other lacks ("4205 NC 210 S" vs our "4205 NC-210" — Realtor
	answered the right house and the exact-key test threw it away). The house
	number must agree; a prefix relationship covers the rest."""
	if not a or not b:
		return False
	if a == b:
		return True
	na = re.match(r"\d+", a)
	nb = re.match(r"\d+", b)
	if not na or not nb or na.group(0) != nb.group(0):
		return False
	return a.startswith(b) or b.startswith(a)


def _fetch_estimate(key, address, street_key_fn, holder):
	"""THREAD BODY — pure urllib, NOTHING frappe (`frappe.local` is a
	thread-local; see redfin._fetch_subject_record)."""
	try:
		status, body = _get_json("/realtor/property/details", {"address": address}, key, ESTIMATE_TIMEOUT)
		data = (body or {}).get("data") if isinstance(body, dict) else None
		if status != 200 or not isinstance(data, dict) or not data.get("property_id"):
			holder["result"] = {"matched": False, "status": status}
			return
		# Realtor resolves by fuzzy address search; the neighbour's estimate
		# would be worse than none, so the street line has to match ours.
		line = ((data.get("location") or {}).get("address") or {}).get("line") or ""
		if not _same_street(street_key_fn(line), street_key_fn(address)):
			holder["result"] = {"matched": False, "status": status, "resolved": line}
			return
		pid = str(data["property_id"])
		today = date.today()
		status, body = _get_json(
			"/realtor/property/estimates",
			{
				"property_id": pid,
				# Required by the endpoint; we only read `current_values`.
				"historical_years_min": (today - timedelta(days=365)).isoformat(),
				"historical_years_max": today.isoformat(),
				"forecasted_months_max": (today + timedelta(days=60)).isoformat(),
			},
			key,
			ESTIMATE_TIMEOUT,
		)
		est = (((body or {}).get("home") or {}).get("estimates") or {}) if isinstance(body, dict) else {}
		current = est.get("current_values") or []
		best = _pick_current(current)
		holder["result"] = {
			"matched": bool(best),
			"property_id": pid,
			"href": data.get("href"),
			"resolved": line,
			"estimate": (best or {}).get("estimate"),
			"low": (best or {}).get("estimate_low"),
			"high": (best or {}).get("estimate_high"),
			"as_of": (best or {}).get("date"),
			"source_name": ((best or {}).get("source") or {}).get("name"),
			"all": [
				{
					"name": (r.get("source") or {}).get("name"),
					"estimate": r.get("estimate"),
					"date": r.get("date"),
				}
				for r in current
				if isinstance(r, dict) and r.get("estimate")
			],
		}
	except Exception as e:
		holder["error"] = str(e)[:200]


def start_realtor_estimate(doc):
	"""Kick off the Realtor lookup for get_lead_comps, or answer from cache.

	Returns None (not configured / no address), {"cached_rec": record}, or a
	job dict holding the running thread. Everything frappe happens HERE."""
	import frappe

	from crm.api.redfin import street_key

	key = _api_key()
	address = str(doc.get("property_address") or "").strip()
	if not key or not address:
		return None
	ckey = _estimate_cache_key(doc.name)
	try:
		cached = frappe.cache().get_value(ckey)
	except Exception:
		cached = None
	if isinstance(cached, dict):
		return {"cached_rec": cached.get("rec")}

	holder = {}
	thread = threading.Thread(
		target=_fetch_estimate, args=(key, address, street_key, holder), daemon=True
	)
	thread.start()
	return {"thread": thread, "holder": holder, "key": ckey, "lead": doc.name}


def finish_realtor_estimate(job, budget=1.0):
	"""Collect the started lookup -> record or None. Never waits more than
	`budget`; a slow fetch is finished by the background warm for next time."""
	import frappe

	if not job:
		return None
	if "cached_rec" in job:
		return job["cached_rec"]
	job["thread"].join(timeout=max(0.05, float(budget)))
	holder = job["holder"]
	if job["thread"].is_alive():
		try:
			frappe.enqueue(
				"crm.api.apivex.warm_realtor_estimate",
				queue="short",
				job_name=f"realtor-estimate-{job['lead']}",
				lead=job["lead"],
			)
		except Exception:
			pass
		return None
	if "result" not in holder:
		frappe.cache().set_value(
			job["key"], {"rec": None, "error": holder.get("error")}, expires_in_sec=ESTIMATE_TTL_ERROR
		)
		return None
	rec = holder["result"]
	ttl = ESTIMATE_TTL_HIT if rec.get("matched") else ESTIMATE_TTL_MISS
	frappe.cache().set_value(job["key"], {"rec": rec}, expires_in_sec=ttl)
	return rec


def subject_estimate(rec):
	"""Shape a record for the subject tile, or None when nothing matched."""
	if not rec or not rec.get("matched") or not rec.get("estimate"):
		return None
	return {
		"value": rec.get("estimate"),
		"low": rec.get("low"),
		"high": rec.get("high"),
		"as_of": rec.get("as_of"),
		"source": rec.get("source_name"),
		"href": rec.get("href"),
		"all": rec.get("all") or [],
	}


def warm_realtor_estimate(lead):
	"""Background half of finish_realtor_estimate's timeout path."""
	import frappe

	from crm.api.comps import _load_subject, subject_doctype

	if not _api_key() or not frappe.db.exists(subject_doctype(lead), lead):
		return
	try:
		doc = _load_subject(lead)
		job = start_realtor_estimate(doc)
		if job:
			finish_realtor_estimate(job, budget=ESTIMATE_TIMEOUT * 2 + 2)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Apivex: warm_realtor_estimate failed")

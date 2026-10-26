"""State wholesaling regulations from the Titanium University state guide.

Dennis (2026-10-26, #bugs): Titanium keeps a state-by-state guide at
https://tumarketguide.com — wholesaling laws, enforcement severity, and the
forms each state needs (disclosures, state purchase contracts). The CRM should
flag a lead whose property sits in one of those states.

The guide is a Lovable / TanStack Start site with no public API. Its pages load
through two GET server functions (`/_serverFn/<sha256>`): one lists every state,
one returns a state with its companies and documents. The ids are content
hashes that change whenever Titanium redeploys, so they are read from the
site's main JS bundle on every sync instead of being hardcoded.

Storage: a daily job (`sync_guide`) writes the trimmed guide to Redis. If Redis
has nothing yet (fresh boot, the site is down) the snapshot committed next to
this file, `data/state_regulations.json`, is used. Lookups never call the site.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import frappe

GUIDE_URL = "https://tumarketguide.com"
CACHE_KEY = "crm_state_regulations_v1"
SNAPSHOT = os.path.join(os.path.dirname(__file__), "data", "state_regulations.json")
TIMEOUT = 20
UA = "Groundwork-CRM state-regulations sync (crm.groundworkpro.com)"

# Their severity codes. "UK" = unknown enforcement; blank = not rated.
SEVERITY_LABEL = {
	"Severe": "Severe enforcement",
	"High": "High enforcement",
	"Medium": "Medium enforcement",
	"Low": "Low known enforcement",
	"UK": "Unknown enforcement",
}

_STATE_NAMES = {
	"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
	"CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
	"FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
	"IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
	"ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
	"MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
	"NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
	"NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
	"OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
	"RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
	"TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
	"WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_NAME_TO_CODE = {v.lower(): k for k, v in _STATE_NAMES.items()}


def state_code(value) -> str:
	"""'IN', 'in', 'Indiana', ' indiana ' -> 'IN'; anything else -> ''."""
	s = (value or "").strip()
	if len(s) == 2 and s.upper() in _STATE_NAMES:
		return s.upper()
	return _NAME_TO_CODE.get(s.lower(), "")


# --------------------------------------------------------------------------- #
# Reading the site
# --------------------------------------------------------------------------- #
def _http(url: str) -> str:
	req = urllib.request.Request(url, headers={"User-Agent": UA, "x-tsr-serverFn": "true"})
	with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
		return r.read().decode("utf-8", "replace")


def decode_seroval(node):
	"""Decode the subset of seroval JSON these server functions return.

	t=10 object, t=9 array, t=1 string, t=0 number, t=2 constant
	(0 null, 1 undefined, 2 true, 3 false).
	"""
	if not isinstance(node, dict):
		return node
	t = node.get("t")
	if t == 10:
		p = node.get("p") or {}
		return {k: decode_seroval(v) for k, v in zip(p.get("k", []), p.get("v", []))}
	if t == 9:
		return [decode_seroval(x) for x in node.get("a", [])]
	if t in (0, 1):
		return node.get("s")
	if t == 2:
		return {0: None, 1: None, 2: True, 3: False}.get(node.get("s"))
	return None


def _slug_payload(slug: str) -> str:
	body = {
		"t": {"t": 10, "i": 0, "p": {"k": ["data"], "v": [
			{"t": 10, "i": 1, "p": {"k": ["slug"], "v": [{"t": 1, "s": slug}]}, "o": 0},
		]}, "o": 0},
		"f": 63,
		"m": [],
	}
	return urllib.parse.quote(json.dumps(body, separators=(",", ":")))


def find_server_fn_ids(bundle: str) -> tuple[str, str]:
	"""(list_states_id, state_by_slug_id) from the site's main JS bundle.

	The bundle declares each GET server function as
	`X=m({method:`GET`}).handler(u(`<64 hex>`))` and wires them up as
	`queryKey:[`states`],queryFn:()=>X()` and `queryKey:[`state`,e],queryFn:()=>Y({data:{slug:e}})`.
	"""
	fns = dict(re.findall(
		r"([\w$]+)=[\w$]+\(\{method:`GET`\}\)\.handler\([\w$]+\(`([a-f0-9]{64})`\)\)", bundle
	))
	m_list = re.search(r"queryKey:\[`states`\],queryFn:\(\)=>([\w$]+)\(\)", bundle)
	m_one = re.search(r"queryKey:\[`state`,[\w$]+\],queryFn:\(\)=>([\w$]+)\(\{data:\{slug", bundle)
	if not (m_list and m_one and m_list.group(1) in fns and m_one.group(1) in fns):
		raise ValueError("tumarketguide: could not locate the state server functions in the bundle")
	return fns[m_list.group(1)], fns[m_one.group(1)]


def _clean_text(s) -> str:
	return (s or "").replace("\\n", "\n").strip()


def trim_state(detail: dict) -> dict:
	"""Keep what the CRM shows; drop ids, sort order and HTML."""
	st = detail.get("state") or {}
	name = st.get("name") or ""
	docs = [
		{"title": (d.get("title") or "").strip(), "url": (d.get("url") or "").strip()}
		for d in (detail.get("documents") or [])
		if (d.get("url") or "").strip()
	]
	return {
		"code": state_code(name),
		"name": name,
		"slug": st.get("slug") or "",
		"kind": st.get("kind") or "",  # title | attorney | both
		"severity": st.get("severity") or "",
		"regulations": _clean_text(st.get("regulations")),
		"notes": _clean_text(st.get("general_notes")),
		"documents": docs,
		"updated_at": st.get("updated_at") or "",
		"url": f"{GUIDE_URL}/states/{st.get('slug') or ''}",
	}


def fetch_guide() -> dict:
	"""Pull every state from the live site. ~51 small GETs."""
	home = _http(GUIDE_URL + "/")
	m = re.search(r'src="(/assets/index-[\w-]+\.js)"', home) or re.search(
		r'href="(/assets/index-[\w-]+\.js)"', home
	)
	if not m:
		raise ValueError("tumarketguide: main bundle not found on the home page")
	list_id, one_id = find_server_fn_ids(_http(GUIDE_URL + m.group(1)))

	listing = decode_seroval(json.loads(_http(f"{GUIDE_URL}/_serverFn/{list_id}")))["result"] or []
	states = {}
	for row in listing:
		slug = row.get("slug")
		if not slug:
			continue
		raw = json.loads(_http(f"{GUIDE_URL}/_serverFn/{one_id}?payload={_slug_payload(slug)}"))
		detail = decode_seroval(raw).get("result")
		if not detail:
			continue
		s = trim_state(detail)
		if s["code"]:
			states[s["code"]] = s
	if len(states) < 40:  # the site lists all 50; a short answer is a broken pull
		raise ValueError(f"tumarketguide: only {len(states)} states came back")
	return {
		"source": GUIDE_URL,
		"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
		"states": states,
	}


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _load_snapshot() -> dict:
	try:
		with open(SNAPSHOT) as f:
			return json.load(f)
	except (OSError, ValueError):
		return {"states": {}}


def load_guide() -> dict:
	cached = frappe.cache().get_value(CACHE_KEY)
	if cached:
		try:
			return json.loads(cached) if isinstance(cached, str) else cached
		except ValueError:
			pass
	return _load_snapshot()


def changed_states(old: dict, new: dict) -> list[str]:
	"""State codes whose rules, severity or forms differ between two pulls."""
	keys = ("severity", "regulations", "documents", "kind")
	o, n = old.get("states") or {}, new.get("states") or {}
	return sorted(
		c for c in set(o) | set(n)
		if any((o.get(c) or {}).get(k) != (n.get(c) or {}).get(k) for k in keys)
	)


def sync_guide():
	"""Daily: refresh the Redis copy from the site. Logs, never raises."""
	try:
		new = fetch_guide()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "State regulations sync failed")
		return
	old = load_guide()
	diff = changed_states(old, new) if old.get("states") else []
	new["changed"] = diff
	frappe.cache().set_value(CACHE_KEY, json.dumps(new))
	if diff:
		frappe.logger("state_regulations").info(f"tumarketguide changed: {', '.join(diff)}")


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def has_rules(s: dict | None) -> bool:
	return bool(s and (s.get("regulations") or s.get("documents")))


def regulation_for_state(value, guide: dict | None = None) -> dict | None:
	"""The guide's entry for a state if it has regulations or forms, else None."""
	code = state_code(value)
	if not code:
		return None
	s = ((guide or load_guide()).get("states") or {}).get(code)
	if not has_rules(s):
		return None
	out = dict(s)
	out["severity_label"] = SEVERITY_LABEL.get(s.get("severity") or "", "")
	return out


@frappe.whitelist()
def get_lead_regulation(lead: str):
	"""What the lead page shows: the state's rules + forms, or None."""
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw("Not permitted", frappe.PermissionError)
	state = frappe.db.get_value("CRM Lead", lead, "property_state")
	guide = load_guide()
	reg = regulation_for_state(state, guide)
	if reg:
		reg["checked_at"] = guide.get("checked_at") or ""
	return reg

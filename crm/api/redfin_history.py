"""Sale history (and so flip warnings) from Redfin instead of Zillow.

Lance, 2026-09-29: the flip check should come from Redfin. The scraper's
`/history` reads one house's Redfin timeline (Listed / Price Changed / Pending /
Sold). It is free: measured 50 houses in 4.5s with 4 workers, every one direct
from our IP, none through the ZenRows fallback.

Redfin's event words are translated into the Zillow `priceHistory` vocabulary
`sale_history.parse` already understands, so the flip rule (resold within 18
months for 30% more) is the same code for both sources.

The one trap: Redfin often records ONE sale twice, as "Sold (MLS)" and a few
weeks later "Sold (Public Records)" when the deed lands. Read raw, that is a
second sale; the parser would take the duplicate as the previous purchase and
the real one (the flip's buy) would be hidden behind it. Sales within
`SAME_SALE_DAYS` of each other are collapsed to one.

Zillow stays only for houses Redfin does not know (a Zillow- or Realtor-only
comp), and only when Redfin was too thin to carry the board by itself.
"""

import datetime as dt

CACHE_VERSION = 1
#: A listing's timeline moves (price cuts, pending); a week is fresh enough for
#: a flip warning, which turns on a purchase that is months old.
CACHE_S = 7 * 86400
#: "Redfin has no events for this house" is worth remembering, but not as long.
EMPTY_CACHE_S = 86400
WORKERS = 4
TIMEOUT = 20
#: Fresh `/history` calls per page load. The rest read the cache and, if never
#: read, are marked unchecked. The scraper's own docstring warns that fanning
#: `/history` over a whole board is what saturates it; 50 is the measured-safe
#: batch, and a reopened lead reads the cache.
BUDGET = 50
SAME_SALE_DAYS = 90

_WORDS = {
	"sold": "Sold",
	"listed": "Listed for sale",
	"relisted": "Listed for sale",
	"coming soon": "Listed for sale",
	"price changed": "Price change",
	"pending": "Pending sale",
	"contingent": "Pending sale",
	"listing removed": "Listing removed",
	"delisted": "Listing removed",
}


def _key(pid):
	return f"crm:redfin-history:v{CACHE_VERSION}:{pid}"


def _iso(ms):
	try:
		n = float(ms)
	except (TypeError, ValueError):
		return None
	if n > 1e11:
		n /= 1000.0
	try:
		return dt.datetime.fromtimestamp(n, dt.timezone.utc).date().isoformat()
	except (OverflowError, OSError, ValueError):
		return None


def _word(description):
	"""Redfin event text -> Zillow's. Rentals keep their text ("rent" is what
	the parser looks for); anything unknown is passed through and ignored."""
	s = " ".join(str(description or "").lower().split())
	if "rent" in s:
		return str(description)
	if s.startswith("sold"):
		return "Sold"
	return _WORDS.get(s, str(description or ""))


def price_history(events):
	"""Redfin `/history` events -> Zillow-shaped priceHistory, newest first."""
	out = []
	for e in events or []:
		if not isinstance(e, dict):
			continue
		date = _iso(e.get("date"))
		if not date:
			continue
		out.append({
			"date": date,
			"event": _word(e.get("event")),
			"price": e.get("price"),
			"source": e.get("source") or "Redfin",
		})
	out.sort(key=lambda e: e["date"], reverse=True)
	return _one_sale_per_closing(out)


def _one_sale_per_closing(events):
	"""Collapse the MLS and public-record copies of the same sale.

	Keeps the MLS copy when there is one (it is the closing price the agent
	reported), else the priced one. Newest-first in, newest-first out.
	"""
	out = []
	for e in events:
		if e["event"] != "Sold":
			out.append(e)
			continue
		twin = next((o for o in out if o["event"] == "Sold"
					 and abs((dt.date.fromisoformat(o["date"])
							  - dt.date.fromisoformat(e["date"])).days) <= SAME_SALE_DAYS), None)
		if twin is None:
			out.append(e)
			continue
		mls = "public" not in str(e.get("source") or "").lower()
		twin_mls = "public" not in str(twin.get("source") or "").lower()
		if (mls and not twin_mls) or (not twin.get("price") and e.get("price")):
			out[out.index(twin)] = e
	return out


def home_status(row):
	"""The row's state in the words `sale_history.parse` takes."""
	return {
		"for_sale": "FOR_SALE",
		"pending": "PENDING",
		"auction": "FOR_SALE",
		"sold": "RECENTLY_SOLD",
	}.get(row.get("listing_state"), "")


def row_pid(row, index):
	"""Redfin property id for a board row, from its name or by address."""
	from crm.api import redfin

	name = str(row.get("name") or "")
	if name.startswith("redfin::"):
		return name.split("::", 1)[1] or None
	props = index.get(redfin.street_key(row.get("address")))
	pid = (props or {}).get("property_id")
	return str(pid) if pid else None


def _fetch(base, pid):
	"""Worker thread: HTTP only. -> events list, [] for none, None on failure."""
	import requests

	try:
		r = requests.get(f"{base}/history", params={"property_id": pid}, timeout=TIMEOUT)
		if r.status_code >= 400:
			return None
		body = r.json()
		if not isinstance(body, dict) or body.get("ok") is False:
			return None
		return body.get("events") or []
	except Exception:
		return None


def events_many(pids, fetch_budget=BUDGET):
	"""-> {pid: events}. Cache first; up to `fetch_budget` fresh reads, in order.

	A pid missing from the result was neither cached nor read (over budget or
	failed): its row is "unchecked", which the UI must not read as clean.
	"""
	import frappe
	from concurrent.futures import ThreadPoolExecutor

	from crm.api import redfin

	out, todo = {}, []
	cache = frappe.cache()
	for pid in dict.fromkeys(p for p in pids if p):
		try:
			hit = cache.get_value(_key(pid))
		except Exception:
			hit = None
		if isinstance(hit, list):
			out[pid] = hit
		elif len(todo) < fetch_budget:
			todo.append(pid)
	base = redfin._base_url() if todo else None
	if not base:
		return out
	with ThreadPoolExecutor(WORKERS) as pool:
		got = list(pool.map(lambda p: _fetch(base, p), todo))
	for pid, events in zip(todo, got):
		if events is None:
			continue
		out[pid] = events
		try:
			cache.set_value(_key(pid), events, expires_in_sec=CACHE_S if events else EMPTY_CACHE_S)
		except Exception:
			pass
	return out


def attach(rows, today, index, budget=BUDGET):
	"""Give each row Redfin's sale history. Mutates. -> (info, unknown, unread).

	`unknown`: rows Redfin has no property id for, untouched, for the caller's
	Zillow fallback. `unread`: rows Redfin knows but did not read this load
	(over budget, or it failed); marked unchecked, and the caller may still
	fill them from Zillow's free cache.
	"""
	from crm.api import sale_history

	info = {"checked": 0, "with_history": 0, "flips": 0, "missing": 0,
			"unchecked": 0, "source": "redfin"}
	known, unknown = [], []
	for row in rows or []:
		pid = row_pid(row, index) if row.get("address") else None
		(known if pid else unknown).append((row, pid))
	events = events_many([pid for _, pid in known], budget)
	unread = []
	for row, pid in known:
		if pid not in events:
			row["sale_history"] = None
			row["sale_history_missing"] = True
			row["sale_history_unchecked"] = True
			info["unchecked"] += 1
			unread.append(row)
			continue
		info["checked"] += 1
		history = sale_history.parse(price_history(events[pid]), today, home_status(row))
		if not history:
			row["sale_history"] = None
			row["sale_history_missing"] = True
			info["missing"] += 1
			continue
		row["sale_history"] = history
		row["sale_history_missing"] = False
		row["sale_history_source"] = "redfin"
		info["with_history"] += 1
		if history.get("flip"):
			info["flips"] += 1
	return info, [row for row, _ in unknown], unread

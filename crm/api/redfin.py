# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Redfin photos — third rung of the gallery ladder, via redfin-scraper-api.

The CRM does not talk to Redfin. redfin-scraper-api (formerly groundwork-geo)
owns the one Redfin client on this egress IP — the WAF budget is IP-keyed, so
several apps each running their own scraper would collectively exhaust it with
nobody able to tell which one did (that service's CLAUDE.md, "Why this is a
service and not a library"). The first cut of this module scraped Redfin's avm
endpoint directly from the CRM; it worked, and it was still the wrong place
for the traffic to live.

The service constructs CDN photo URLs from its rate-limited avm sweep (the
photo-bearing detail endpoints are WAF-403 from the box; see redfin_scraper/photos.py in
the redfin-scraper-api repo for the URL scheme and its verification). One GET
per lookup, fired only when Zillow AND Realtor are both empty, result cached
30 days by the comp detail cache.

Config-gated like crm/api/geo.py: reads `redfin_scraper_url` (pre-rename
fallback `geo_service_url`); absent both, every call is a silent no-op and
the ladder degrades exactly as before.
"""

import re
import threading

import requests

TIMEOUT = 15

#: Thread-side budget for one subject-facts fetch. The deployed service answers
#: /properties in ~5s (measured on prod — the KNN ordering, not the row count),
#: so this has to clear that; the REQUEST only ever waits `finish_subject_check`'s
#: much smaller join budget, and a slow fetch finishes in the background job.
FACTS_TIMEOUT = 8

#: Store-lookup circle for the /properties fallback, metres. Matches the
#: service's own /facts radius: wide enough that a street-interpolated geocode a
#: house or two off still contains the subject, tight enough to stay tens of rows.
FACTS_RADIUS_M = 150

#: Bump when the cached record's shape changes — the version in the key is
#: the ONLY invalidation path, same rule as the zillow_area caches. v2: the
#: cache holds the fetched Redfin RECORD, not the computed comparison — the
#: comparison is recomputed per request (it is pure and microseconds), so a
#: subject-sqft override applied AFTER the first fetch drops the flag on the
#: very next load instead of serving a stale verdict for 14 days. v3: the
#: record carries `estimate` (the Redfin Estimate) for the subject tile.
CHECK_CACHE_VERSION = 3

#: A record (matched or no-match) holds for days — beds and square
#: footage do not move. An ERROR is cached briefly so a service outage costs one
#: slow probe per window instead of one per filter change, and recovers itself.
CHECK_TTL_MATCHED = 14 * 86400
CHECK_TTL_UNMATCHED = 3 * 86400
CHECK_TTL_ERROR = 15 * 60

#: How far past materiality a difference must go before it flags. Sqft is
#: relative (a 5% tape-measure disagreement between listing feeds is normal);
#: baths tolerates a quarter-bath of rounding; year tolerates the off-by-one
#: that county records and MLS routinely disagree by. Beds is exact.
SQFT_REL_TOLERANCE = 0.05
BATHS_TOLERANCE = 0.25
YEAR_TOLERANCE = 1


def _base_url():
	from crm.api.geo import _base_url as geo_base

	return geo_base()


def redfin_photo_urls(address: str, lat=None, lng=None, limit=60):
	"""Compatibility wrapper for photo-only consumers."""
	return redfin_gallery(address, lat, lng, limit)["photos"]


def _point(address, lat, lng):
	addr = (address or "").strip()
	try:
		lat, lng = float(lat), float(lng)
	except (TypeError, ValueError):
		return None
	if not addr or not lat or not lng:
		return None
	return addr, lat, lng


def _fetch_listing_url(base, addr, lat, lng):
	"""The bare GET /url -> listing URL or None. Pure `requests`, no frappe: the
	comp gallery runs this on a worker THREAD (`comps._start_redfin_url`), where
	`frappe.local` -- config, cache, log_error -- does not exist. Raises on a
	transport/HTTP error; the callers decide whether that is worth logging."""
	r = requests.get(
		f"{base}/url",
		params={"address": addr, "lat": lat, "lng": lng},
		timeout=TIMEOUT,
	)
	if r.status_code == 404:
		# Service predates /url: /photos carries the observed path.
		return _fetch_gallery(base, addr, lat, lng, 1)["url"]
	r.raise_for_status()
	body = r.json() or {}
	url = body.get("url") if body.get("matched") else None
	return url if isinstance(url, str) and url.startswith("http") else None


def redfin_listing_url(address: str, lat=None, lng=None):
	"""Address + point -> absolute Redfin listing URL from GET /url, or None.

	Falls back to /photos' observed path if this service predates /url.
	"""
	base = _base_url()
	point = _point(address, lat, lng)
	if not base or not point:
		return None
	addr, lat, lng = point
	try:
		return _fetch_listing_url(base, addr, lat, lng)
	except Exception:
		import frappe

		frappe.log_error(frappe.get_traceback(), "Redfin: geo /url failed")
		return None


def redfin_gallery(address: str, lat=None, lng=None, limit=60):
	"""Address + point -> photos and the matched record's observed listing URL.

	The point finds the neighbourhood; the address picks the house (exact
	normalized street-line match, service-side — no nearest-row fallback,
	because the neighbour's gallery is worse than nothing).
	"""
	base = _base_url()
	point = _point(address, lat, lng)
	if not base or not point:
		return {"photos": [], "url": None}
	addr, lat, lng = point
	try:
		return _fetch_gallery(base, addr, lat, lng, limit)
	except Exception:
		import frappe

		frappe.log_error(frappe.get_traceback(), "Redfin: geo /photos failed")
		return {"photos": [], "url": None}


def _fetch_gallery(base, addr, lat, lng, limit):
	"""Bare GET /photos. Pure requests; raises on error (see _fetch_listing_url)."""
	r = requests.get(
		f"{base}/photos",
		params={"address": addr, "lat": lat, "lng": lng, "limit": int(limit)},
		timeout=TIMEOUT,
	)
	r.raise_for_status()
	body = r.json() or {}
	photos = body.get("photos") or []
	return {
		"photos": [p for p in photos if isinstance(p, str) and p.startswith("http")][: int(limit)],
		"url": body.get("url") if body.get("matched") else None,
	}


# ---------------------------------------------------------------------------------
# Subject cross-check: does Redfin agree with Zillow about this house?
#
# Best-effort by construction, like everything else in this module: no
# `redfin_scraper_url` -> silently absent; a timeout or miss never blocks the
# comps load. The fetch runs on a plain thread STARTED BEFORE the Zillow refresh
# in get_lead_comps and JOINED AFTER it with a small budget, so on a cold map it
# rides wall-clock the request was already spending. If the budget runs out, a
# background job computes and caches it for the next fetch instead.
# ---------------------------------------------------------------------------------

#: The same suffix collapse the service's photos.py uses, so "Seybert St" and
#: "Seybert Street" land on the same key whichever source spelled it.
_SUFFIXES = {
	"street": "st",
	"avenue": "ave",
	"boulevard": "blvd",
	"drive": "dr",
	"road": "rd",
	"lane": "ln",
	"court": "ct",
	"place": "pl",
	"terrace": "ter",
	"circle": "cir",
	"highway": "hwy",
	"parkway": "pkwy",
	"trail": "trl",
}


def street_key(text) -> str:
	"""Normalized street-line key: first comma chunk, lowercased, suffixes
	collapsed, everything non-alphanumeric dropped. Port of the service's
	photos.street_key so the two can never match differently."""
	street = str(text or "").split(",")[0].strip().lower()
	if not street:
		return ""
	parts = [_SUFFIXES.get(w, w) for w in re.split(r"[^a-z0-9]+", street) if w]
	return "".join(parts)


def _check_cache_key(lead):
	return f"redfin_subject:v{CHECK_CACHE_VERSION}:{lead}"


def _fetch_subject_record(base, address, lat, lng, holder):
	"""THREAD BODY — pure requests, NOTHING frappe. `frappe.local` is a
	thread-local: a worker thread has no site, no database and no cache, so
	`frappe.conf` / `frappe.cache()` / `frappe.log_error` RAISE here rather than
	degrade (the zillow._raw_get rule). Everything frappe happens on the request
	thread, before and after.

	Tries the service's /facts first (street-key matched service-side, store
	first with a live-avm fallback); a deployed service predating that endpoint
	404s, and the /properties store circle + a local street-key match covers it.
	"""
	try:
		try:
			r = requests.get(
				f"{base}/facts",
				params={"address": address, "lat": lat, "lng": lng},
				timeout=FACTS_TIMEOUT,
			)
			if r.status_code != 404:
				r.raise_for_status()
				body = r.json() or {}
				holder["result"] = body if body.get("ok") else {"matched": False}
				return
		except requests.RequestException:
			# /facts failing is not the end: the store circle below is a separate
			# route to the same answer.
			pass

		r = requests.get(
			f"{base}/properties",
			params={"lat": lat, "lng": lng, "radius": FACTS_RADIUS_M},
			timeout=FACTS_TIMEOUT,
		)
		r.raise_for_status()
		feats = (r.json() or {}).get("features") or []
		key = street_key(address)
		for f in feats:
			p = f.get("properties") or {}
			if street_key(p.get("address")) == key:
				holder["result"] = {
					"matched": True,
					"source": "store",
					"property_id": p.get("property_id"),
					"address": p.get("address"),
					"url": p.get("url"),
					"beds": p.get("beds"),
					"baths": p.get("baths"),
					"sqft": p.get("sqft"),
					"year_built": p.get("year_built"),
				}
				return
		holder["result"] = {"matched": False}
	except Exception as e:
		holder["error"] = str(e)[:200]


def _num(v):
	try:
		n = float(v)
	except (TypeError, ValueError):
		return None
	return n if n > 0 else None


def compare_subject_facts(subject, rec):
	"""Zillow-sourced subject facts vs a matched Redfin record -> discrepancy
	block, or None when they agree (or there is nothing to honestly compare).

	Only facts the subject holds as EXACT numbers SOURCED FROM ZILLOW take part:
	a seller pick-list band ("1000 - 2000") has no midpoint worth disputing, and
	a fact some other source (a human override, a listing record) outranked
	Zillow on is a fact a person has already settled — flagging it again would
	re-open a closed question. That source test is also what keeps this correct
	next to the editable-sqft override: an overridden sqft stops being labelled
	"zillow" and simply drops out of the comparison.
	"""
	if not rec or not rec.get("matched"):
		return None
	src = (subject or {}).get("source") or {}

	def zval(field):
		if src.get(field) != "zillow" or not (subject or {}).get(f"{field}_exact"):
			return None
		return _num((subject or {}).get(field))

	rows = []

	def check(field, label, differs):
		z, r = zval(field), _num(rec.get(field))
		if z is None or r is None:
			return
		if differs(z, r):
			rows.append({"field": field, "label": label, "zillow": z, "redfin": r})

	check("beds", "bd", lambda z, r: int(z) != int(r))
	check("baths", "ba", lambda z, r: abs(z - r) > BATHS_TOLERANCE)
	check("sqft", "sqft", lambda z, r: abs(z - r) / max(z, r) > SQFT_REL_TOLERANCE)
	check("year_built", "built", lambda z, r: abs(z - r) > YEAR_TOLERANCE)

	if not rows:
		return None
	return {
		"fields": rows,
		"property_id": rec.get("property_id"),
		"source": rec.get("source") or "store",
	}


def start_subject_check(doc, subject):
	"""Kick off the Redfin fetch for get_lead_comps, or answer from cache.

	Returns None (not applicable), {"cached_rec": record} (Redis already knows), or a
	job dict holding the running thread. Everything frappe — config, cache read —
	happens HERE, on the request thread, because the thread body cannot.
	"""
	import frappe

	base = _base_url()
	if not base or not subject or not subject.get("has_zillow"):
		# Without Zillow numbers there is nothing to disagree with.
		return None
	lat, lng = subject.get("lat"), subject.get("lng")
	address = str((doc.get("property_address") or "")).strip()
	if lat is None or lng is None or not address:
		return None

	key = _check_cache_key(doc.name)
	try:
		cached = frappe.cache().get_value(key)
	except Exception:
		cached = None
	if isinstance(cached, dict):
		# The RECORD is cached; the comparison is recomputed against the subject
		# on every request, so a later human override takes effect immediately.
		return {"cached_rec": cached.get("rec")}

	holder = {}
	thread = threading.Thread(
		target=_fetch_subject_record,
		args=(base, address, float(lat), float(lng), holder),
		daemon=True,
	)
	thread.start()
	return {"thread": thread, "holder": holder, "key": key, "lead": doc.name}


def finish_subject_record(job, budget=1.0):
	"""Collect the started fetch -> the Redfin RECORD (or None). Never waits
	more than `budget` past whatever wall-clock the Zillow refresh already
	spent — a slow or down service costs the map nothing, and the answer lands
	in Redis via the background job for the next fetch (every filter change is
	one) instead.

	The record is what gets cached; both consumers (the Zillow-vs-Redfin
	discrepancy flag and the Redfin Estimate on the subject tile) derive from
	it per request, so neither can go stale independently of the other."""
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
				"crm.api.redfin.warm_subject_check",
				queue="short",
				job_name=f"redfin-check-{job['lead']}",
				lead=job["lead"],
			)
		except Exception:
			pass
		return None

	if "result" not in holder:
		# The fetch errored. Cached briefly so an outage is one probe per window,
		# not one per filter change; the short TTL is the retry schedule.
		frappe.cache().set_value(job["key"], {"rec": None, "error": holder.get("error")},
		                         expires_in_sec=CHECK_TTL_ERROR)
		return None

	ttl = CHECK_TTL_MATCHED if holder["result"].get("matched") else CHECK_TTL_UNMATCHED
	frappe.cache().set_value(job["key"], {"rec": holder["result"]}, expires_in_sec=ttl)
	return holder["result"]


def finish_subject_check(job, subject, budget=1.0):
	"""Collect the started check -> discrepancy block or None. Thin wrapper
	kept for callers that only want the flag (the background warm)."""
	return compare_subject_facts(subject, finish_subject_record(job, budget))


def subject_estimate(rec):
	"""The Redfin Estimate out of a matched record, or None. Only a MATCHED
	record counts: the neighbour's estimate would be worse than none."""
	if not rec or not rec.get("matched"):
		return None
	return _num(rec.get("estimate"))


# ---------------------------------------------------------------------------------
# ISTL pool overlay: pictures + current MLS status from the Redfin ingest store.
#
# `CRM Comp` rows are RentCast last-asks with no imagery and a coarse
# Active/Inactive. Lead insert already warms the neighbourhood (`geo.warm_lead`
# → POST /coverage, ingest priority). This projects that stored sweep onto the
# ingested ISTL pins so the map does not wait on billed Zillow /property calls
# for a thumbnail and a live listing state.
# ---------------------------------------------------------------------------------

_FOR_SALE = {"active", "for sale", "coming soon", "new", "back on market"}
_PENDING = {
	"pending", "contingent", "active under contract", "under contract",
	"accepting backup offers",
}
_SOLD = {"sold", "recently sold", "closed"}
_RENT = {"for rent", "rented"}
_AUCTION = {"auction"}
_OFF = {"off market", "not for sale", "hold", "withdrawn", "expired", "cancelled", "canceled"}
COVERAGE_TIMEOUT = 8


def mls_listing_state(mls_status):
	"""Redfin mlsStatus → the listing_state token the comps map already paints."""
	s = " ".join(str(mls_status or "").lower().replace("-", " ").split())
	if not s:
		return None
	if s in _AUCTION or "auction" in s:
		return "auction"
	if s in _RENT or "rent" in s:
		return "for_rent"
	if s in _PENDING or "pending" in s or "contingent" in s:
		return "pending"
	if s in _FOR_SALE or s.startswith("active"):
		return "for_sale"
	if s in _SOLD or "sold" in s or s.startswith("closed"):
		return "sold"
	if s in _OFF or "off market" in s:
		return "off_market"
	return None


def _listing_url(url):
	if not isinstance(url, str):
		return None
	u = url.strip()
	if u.startswith("http"):
		return u
	if u.startswith("/"):
		return f"https://www.redfin.com{u}"
	return None


def _photos(props):
	out = []
	for p in props.get("photos") or []:
		if isinstance(p, str) and p.startswith("http"):
			out.append(p)
	return out


def is_istl_pool_row(row):
	"""Ingested CRM Comp pin — not a Zillow/BatchData extra, not an ADC sale."""
	name = str((row or {}).get("name") or "")
	if name.startswith(("zillow", "batchdata")):
		return False
	from crm.api.comp_provenance import is_adc

	return not is_adc(row or {})


def coverage_index(features):
	"""Street-key → Redfin store properties. First address wins."""
	by_key = {}
	for f in features or []:
		props = (f or {}).get("properties") or {}
		key = street_key(props.get("address"))
		if key and key not in by_key:
			by_key[key] = props
	return by_key


def apply_istl_comps(rows, features):
	"""Stamp Redfin ingest photo + listing_state onto ISTL pool rows. Mutates.

	A match with no status string still gets photos — Redfin knowing the house
	is not the same as having an MLS status. Unmatched rows are left alone so
	Zillow's overlay remains the fallback.
	"""
	index = coverage_index(features)
	matched = 0
	photos = 0
	for row in rows or []:
		if not is_istl_pool_row(row):
			continue
		props = index.get(street_key(row.get("address")))
		if not props:
			continue
		matched += 1
		shots = _photos(props)
		if shots:
			row["photo"] = shots[0]
			row["photos"] = shots
			photos += 1
		state = mls_listing_state(props.get("mls_status"))
		if state:
			row["listing_state"] = state
			row["redfin_status"] = props.get("mls_status") or ""
			row["current_status_source"] = "redfin"
			row["status"] = (
				"Active" if state in ("for_sale", "pending", "auction") else "Inactive"
			)
		url = _listing_url(props.get("url"))
		if url:
			row["redfin_url"] = url
	return {"matched": matched, "photos": photos, "homes": len(index)}


def _fetch_coverage(base, lat, lng, radius_m, holder):
	"""THREAD BODY — store read only. Never live-sweeps Redfin."""
	try:
		r = requests.get(
			f"{base}/properties",
			params={"lat": float(lat), "lng": float(lng), "radius": float(radius_m)},
			timeout=COVERAGE_TIMEOUT,
		)
		r.raise_for_status()
		body = r.json() or {}
		holder["features"] = body.get("features") or []
		holder["meta"] = body.get("meta") or {}
	except Exception as e:
		holder["error"] = str(e)[:200]


def start_istl_coverage(lat, lng, radius_mi):
	"""Kick off the stored-neighbourhood read beside the Zillow refresh."""
	base = _base_url()
	try:
		lat, lng, radius_mi = float(lat), float(lng), float(radius_mi)
	except (TypeError, ValueError):
		return None
	if not base or not lat or not lng:
		return None
	holder = {}
	thread = threading.Thread(
		target=_fetch_coverage,
		args=(base, lat, lng, radius_mi * 1609.344, holder),
		daemon=True,
	)
	thread.start()
	return {"thread": thread, "holder": holder, "lat": lat, "lng": lng, "radius_mi": radius_mi}


def finish_istl_coverage(job, budget=1.0):
	"""Collect the store read. Empty features on timeout/error — map still loads."""
	if not job:
		return [], {}
	job["thread"].join(timeout=max(0.05, float(budget)))
	holder = job["holder"]
	if job["thread"].is_alive() and "features" not in holder:
		return [], {"timed_out": True}
	return holder.get("features") or [], holder.get("meta") or {}


def maybe_rewarm(lead, meta):
	"""If ingest never covered this circle, kick the same warm lead-insert uses."""
	import frappe

	state = (meta or {}).get("coverage_state")
	if not state or state == "ready":
		return
	try:
		frappe.enqueue(
			"crm.api.geo.warm_lead",
			queue="long",
			job_name=f"geo-warm-comps-{lead}",
			enqueue_after_commit=True,
			lead=lead,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Redfin: rewarm ISTL coverage failed")


def warm_subject_check(lead):
	"""Background half of finish_subject_check's timeout path: same fetch, same
	compare, written to the same cache — just with the budget the request could
	not afford. Runs on a worker, so frappe is available normally here."""
	import frappe

	from crm.api.comps import subject_doctype

	if not _base_url() or not frappe.db.exists(subject_doctype(lead), lead):
		return
	try:
		from crm.api.comps import _load_subject, _subject_facts, _subject_point

		doc = _load_subject(lead)
		lat, lng, _cached = _subject_point(doc)
		if lat is None:
			return
		subject = {"lat": lat, "lng": lng}
		subject.update(_subject_facts(doc))
		job = start_subject_check(doc, subject)
		if job:
			finish_subject_check(job, subject, budget=FACTS_TIMEOUT + 2)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Redfin: warm_subject_check failed")

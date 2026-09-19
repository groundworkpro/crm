"""Client for the redfin-scraper-api service (neighbourhood properties + parcels).

(The service was named groundwork-geo until 2026-08-31; same box, same port,
same endpoints — only the name changed, because the abstract one hid the fact
that it is the fleet's one and only Redfin scraper.)

The service is source-agnostic — it takes coordinates and knows nothing about a
CRM Lead. This module owns the mapping in the other direction: lead -> point,
and lead -> "warm the neighbourhood".

WHY WARM AT PURCHASE. A dense two-mile sweep is ~75s across ~49 upstream calls,
and enriching its parcels is ~45 minutes at 6.6/s. Neither can happen while a
rep waits on a page. So the work starts when the lead lands and the desk reads
whatever is ready — the same shape as the Zillow facts cache, one order of
magnitude slower.

Config-gated like `contract_parser_url`: with no `redfin_scraper_url` in
site_config every entry point is a no-op, so the app is safe to deploy before
the service exists and degrades quietly if it goes away. The pre-rename
`geo_service_url` key still works as a fallback so config and code never have
to move in lockstep.
"""

import frappe
import requests
from frappe import _

DEFAULT_RADIUS_M = 1609.344 * 2  # 2 miles — the desk's outer ring
TIMEOUT = 10


def _base_url():
	url = frappe.conf.get("redfin_scraper_url") or frappe.conf.get("geo_service_url") or ""
	return url.strip().rstrip("/")


def _enabled():
	return bool(_base_url())


def _lead_point(lead):
	"""(lat, lng) for a lead, geocoding and caching on first use.

	Reuses comps._subject_point so the desk, the comps map and the geo warm can
	never disagree about where a lead is — and so a lead is geocoded once, not
	once per feature.

	NOTE `property_lat` is 0.0, not NULL, on an ungeocoded lead (3 of the 6
	newest on prod). 0.0 is a real latitude, so the "is it set" test has to be
	truthiness rather than `is not None`. _subject_point already gets this right;
	don't 'fix' it.
	"""
	from crm.api.comps import _load_subject, _subject_point

	doc = _load_subject(lead)
	lat, lng, _cached = _subject_point(doc)
	if lat is None or lng is None:
		return None, None
	return float(lat), float(lng)


#: Transient ingest-time failures (the scraper or propwarehouse being down when a
#: lead lands) retry this many times, then stop. A lead whose services are still
#: down hours later is recoverable via warm_backfill/resolve_now; a job that
#: retries forever is worse than one that gives up audibly.
INGEST_MAX_ATTEMPTS = 3
#: Seconds between attempts -- long enough for an egress outage to clear, short
#: enough that the neighbourhood is warm before a rep opens a lead bought today.
INGEST_RETRY_DELAY_S = 300


def _enqueue_ingest_retry(lead, radius_m, ingest_creation, attempt):
	"""Re-run this lead's ingest warm after a transient failure. Bounded.

	The creation token is forwarded UNCHANGED: this is still that lead's ingest --
	the same logical job, deferred -- so the retry keeps the one-time authority to
	move the circle. A token-less caller (manual warm, backfill) never reaches
	here, so a retry can never be minted out of a non-ingest job.
	"""
	try:
		frappe.enqueue(
			"crm.api.geo.warm_lead",
			queue="long",
			job_name=f"geo-warm-{lead}-retry-{attempt}",
			enqueue_after_commit=True,
			lead=lead,
			radius_m=radius_m,
			ingest_creation=ingest_creation,
			ingest_attempt=attempt,
			delay=INGEST_RETRY_DELAY_S,
		)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), "geo: ingest retry enqueue failed")
		return False


def warm_lead(lead, radius_m=None, ingest_creation=None, ingest_attempt=1):
	"""Ask the service to sweep this lead's neighbourhood. Returns a status dict.

	Best-effort by construction: every failure path returns rather than raises,
	because a geo outage must never be able to fail a lead insert.

	ALSO RESOLVES THE PROPERTY'S EXACT POINT FIRST, and the ordering is the
	reason it lives here instead of in a job of its own. This job already runs
	after commit on the `long` queue, and `address_resolve` writes the accurate
	coordinate into `property_lat/lng` at ingest -- which is the comps circle
	centre. Buy the circle first and the centre moves under a warm cache; resolve
	first and the sweep below, the Zillow circle and everything downstream all
	start from the right point at no extra cost.
	"""
	from crm.api.address_resolve import resolve, resolve_at_ingest

	# Only the after-insert job carries the creation token that authorizes moving
	# property_lat/lng. Every other caller (manual warm, Redfin refresh, backfill)
	# may populate parcel_* but must keep the existing circle centre untouched.
	resolved = (
		resolve_at_ingest(lead, ingest_creation)
		if ingest_creation else resolve(lead)
	)
	if not resolved.get("ok") or not resolved.get("exact"):
		retry_enqueued = False
		if (
			ingest_creation
			and resolved.get("retryable")
			and int(ingest_attempt or 1) < INGEST_MAX_ATTEMPTS
		):
			retry_enqueued = _enqueue_ingest_retry(
				lead, radius_m, ingest_creation, int(ingest_attempt or 1) + 1
			)
		return {
			"ok": False,
			"reason": resolved.get("reason") or "no exact parcel point",
			"retryable": bool(resolved.get("retryable")),
			"retry_enqueued": retry_enqueued,
			"resolved": resolved,
		}

	if not _enabled():
		return {"ok": False, "reason": "redfin_scraper_url not configured", "resolved": resolved}

	lat, lng = _lead_point(lead)
	if lat is None:
		return {"ok": False, "reason": "lead has no geocodable address", "lead": lead}

	try:
		r = requests.post(
			f"{_base_url()}/warm",
			json={"lat": lat, "lng": lng, "radius_m": float(radius_m or DEFAULT_RADIUS_M)},
			timeout=TIMEOUT,
		)
		r.raise_for_status()
		return {"ok": True, "lead": lead, "lat": lat, "lng": lng, **(r.json() or {})}
	except Exception:
		# Logged, not raised. The desk still works without a warm neighbourhood;
		# it just has less to draw. The reason is a stable string, never the
		# exception text: requests embeds the full internal service URL in its
		# exceptions, and a whitelisted response must not leak it. The operator
		# gets the traceback.
		frappe.log_error(frappe.get_traceback(), "geo: warm failed")
		return {"ok": False, "reason": "warm request failed", "lead": lead}


def on_lead_insert(doc, method=None):
	"""CRM Lead after_insert — warm this lead's neighbourhood in the background.

	Enqueued, never inline. The sweep takes ~75 seconds; doing it in the request
	would hold a webhook open long enough to time out and make iSpeedToLead retry,
	creating duplicate leads. The whole point of warming at purchase is that the
	rep never waits, so the insert must not wait either.
	"""
	if not _enabled():
		return
	try:
		frappe.enqueue(
			"crm.api.geo.warm_lead",
			queue="long",
			job_name=f"geo-warm-{doc.name}",
			enqueue_after_commit=True,
			lead=doc.name,
			ingest_creation=str(doc.get("creation") or ""),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "geo: enqueue warm failed")


#: What the map actually draws. The service returns the full Redfin record; a
#: dense neighbourhood is ~1,800 of them and none of the rest is rendered.
MAP_FIELDS = (
	"property_id", "address", "city", "state", "zipcode", "price",
	"beds", "baths", "sqft", "year_built", "property_type", "mls_status",
)

#: Ceiling on one response. Measured: a 2-mile Indianapolis sweep holds 17,287
#: homes, and nobody can read 17,000 dots -- past a point this is a heat map
#: pretending to be data. The client asks for the viewport it is showing.
MAX_FEATURES = 1500


def _trim(feature):
	"""One feature, reduced to what the map draws."""
	props = feature.get("properties") or {}
	coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
	out = {k: props.get(k) for k in MAP_FIELDS if props.get(k) not in (None, "")}
	out["lng"], out["lat"] = coords[0], coords[1]
	return out


def _in_bbox(feature, bbox):
	lng, lat = feature.get("lng"), feature.get("lat")
	if lng is None or lat is None:
		return False
	west, south, east, north = bbox
	return west <= lng <= east and south <= lat <= north


@frappe.whitelist()
def get_neighborhood(lead, radius_m=None, live=0, bbox=None, limit=None):
	"""Properties around a lead, trimmed for the desk map.

	This is CONTEXT, not comps: the off-market universe around the subject, most
	of which has no price (measured: 41% priced in Indianapolis). It is returned
	flat rather than as GeoJSON because the client draws circles, and shipping
	Redfin's whole record for 1,800 homes to render a dot is most of the payload
	for none of the information.

	`bbox` ('west,south,east,north') is what a zoomed-in rep is actually looking
	at; without it the whole warmed radius comes back, capped at MAX_FEATURES.
	"""
	if not _enabled():
		return {"ok": False, "reason": "geo service not configured", "features": []}

	lat, lng = _lead_point(lead)
	if lat is None:
		return {"ok": False, "reason": "lead has no geocodable address", "features": []}

	try:
		r = requests.get(
			f"{_base_url()}/properties",
			params={
				"lat": lat,
				"lng": lng,
				"radius": float(radius_m or DEFAULT_RADIUS_M),
				"live": "true" if str(live) not in ("0", "", "false", "False") else "false",
			},
			timeout=TIMEOUT if not live else 120,
		)
		r.raise_for_status()
		payload = r.json() or {}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "geo: get_neighborhood failed")
		# Stable reason, never the exception text -- it carries the internal URL.
		return {"ok": False, "reason": "properties request failed", "features": []}

	features = [_trim(f) for f in (payload.get("features") or [])]
	total = len(features)

	if bbox:
		try:
			box = [float(v) for v in str(bbox).split(",")]
			if len(box) == 4:
				features = [f for f in features if _in_bbox(f, box)]
		except ValueError:
			pass  # a malformed bbox shows the whole radius rather than nothing

	cap = min(int(limit or MAX_FEATURES), MAX_FEATURES)
	shown = features[:cap]
	return {
		"ok": True,
		"lat": lat,
		"lng": lng,
		"features": shown,
		"total": total,
		"in_view": len(features),
		# Said out loud rather than left for the eye to notice: a capped map is a
		# map that is not showing you everything, and it looks identical to one
		# that is.
		"truncated": len(shown) < len(features),
		"priced": sum(1 for f in shown if f.get("price")),
	}


@frappe.whitelist()
def get_parcels(lead, bbox=None):
	"""Lot-line polygons for a map viewport. `bbox` is 'west,south,east,north'."""
	if not _enabled():
		return {"ok": False, "reason": "geo service not configured", "parcels": []}
	if not bbox:
		return {"ok": False, "reason": "bbox required", "parcels": []}
	try:
		r = requests.get(f"{_base_url()}/parcels", params={"bbox": bbox}, timeout=TIMEOUT)
		r.raise_for_status()
		return {"ok": True, **(r.json() or {})}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "geo: get_parcels failed")
		# Stable reason, never the exception text -- it carries the internal URL.
		return {"ok": False, "reason": "parcels request failed", "parcels": []}


@frappe.whitelist()
def warm_backfill(dry_run=1, limit=None, radius_m=None):
	"""Warm the neighbourhoods of existing live leads.

	Staged on purpose. 362 leads x a few thousand parcels is ~1M rows, and the
	first run should be measured on a handful rather than turned loose:

	    bench execute crm.api.geo.warm_backfill --kwargs '{"dry_run":1}'
	    bench execute crm.api.geo.warm_backfill --kwargs '{"dry_run":0,"limit":10}'
	"""
	dry_run = str(dry_run) not in ("0", "false", "False", "")
	if not _enabled():
		return {"ok": False, "reason": "redfin_scraper_url not configured"}

	# Parked import leads are excluded. They are bulk-loaded inventory nobody is
	# working, and warming them would sweep ~500 neighbourhoods for no rep --
	# ~1.5M parcels of pure waste. Caught by a dry run reporting 876 leads when
	# only ~362 are live.
	#
	# The NULL-safe form matters: `import_hidden != 1` alone silently drops every
	# row where the column is NULL, which is most of them. Same trap documented
	# for leads_dashboard.live().
	Lead = frappe.qb.DocType("CRM Lead")
	q = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.property_address)
		.where(Lead.converted != 1)
		.orderby(Lead.creation, order=frappe.qb.desc)
	)
	if frappe.db.has_column("CRM Lead", "import_hidden"):
		q = q.where(Lead.import_hidden.isnull() | (Lead.import_hidden != 1))
	if limit:
		q = q.limit(int(limit))
	leads = q.run(as_dict=True)
	leads = [l for l in leads if (l.get("property_address") or "").strip()]
	if dry_run:
		return {"ok": True, "dry_run": True, "would_warm": len(leads),
		        "sample": [l["name"] for l in leads[:10]]}

	warmed, failed = 0, []
	for l in leads:
		res = warm_lead(l["name"], radius_m=radius_m)
		if res.get("ok"):
			warmed += 1
		else:
			failed.append({"lead": l["name"], "reason": res.get("reason")})
	return {"ok": True, "dry_run": False, "warmed": warmed,
	        "failed": len(failed), "failures": failed[:20]}

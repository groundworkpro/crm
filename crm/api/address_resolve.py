"""Accurate subject coordinates, address suggestions, and Street View heading.

This module is ORCHESTRATION ONLY. PropWarehouse owns every Google call, the
API key, durable lookup cache, rate controls, and spend accounting. CRM owns the
business decision: which exact point wins, whether a standardized address is
substantively different, and whether an ingest-time result may move the first
comp-circle centre.

The exact-point ladder is:

    Redfin exact point -> already-cached Zillow point -> PropWarehouse Google

Redfin is asked first. When Zillow is already cached we pass ``normalize=0`` to
Redfin `/facts`, because Zillow already answers the coordinate question and
D11's normalization fallback may reach paid Google. Without cached Zillow the
normal D11 path stays enabled: it can repair an address before resolving facts.

Only exact points are persisted as ``parcel_*``. Old Redfin servers omit
``point_exact`` and are rejected rather than guessed. Google must say non-partial
ROOFTOP. RANGE_INTERPOLATED and APPROXIMATE remain useful diagnostics and may
produce a human address suggestion, but can never move a circle or masquerade as
a parcel.

Circle movement is internal and one-shot: ``resolve_at_ingest`` may copy an
exact parcel point into ``property_lat/lng`` before ``geo.warm_lead`` buys the
first circle. Manual resolve, backfill, suggestion acceptance, and CRM Property
resolution never move an existing circle.
"""

from __future__ import annotations

import hashlib
import math
import os

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

SALES_ROLES = ("System Manager", "Sales Manager", "Sales User")
TIMEOUT = 20

REQUIRED_FIELDS = (
	"parcel_lat", "parcel_lng", "parcel_source", "parcel_address_key",
	"parcel_checked_at", "address_suggested", "address_suggestion_key",
	"address_suggestion_state",
)

#: Street-suffix and directional spellings that mean the same thing. Used only
#: to judge whether a provider rewrite is cosmetic; we never rewrite an address
#: ourselves.
SUFFIX = {
	"street": "st", "road": "rd", "avenue": "ave", "av": "ave", "drive": "dr",
	"lane": "ln", "court": "ct", "boulevard": "blvd", "circle": "cir",
	"place": "pl", "terrace": "ter", "highway": "hwy", "parkway": "pkwy",
	"trail": "trl", "square": "sq", "point": "pt", "crossing": "xing",
	"north": "n", "south": "s", "east": "e", "west": "w",
	"northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
	"apartment": "apt", "suite": "ste",
}
NOISE = {"usa", "us", "unitedstates"}


def _guard():
	if not any(role in SALES_ROLES for role in frappe.get_roles()):
		frappe.throw(_("Not permitted."), frappe.PermissionError)


# ---------------------------------------------------------------------------------
# Pure geometry and address comparison
# ---------------------------------------------------------------------------------
def bearing(lat1, lng1, lat2, lng2):
	"""Initial compass bearing from point 1 to point 2, clockwise from north."""
	p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
	dl = math.radians(float(lng2) - float(lng1))
	y = math.sin(dl) * math.cos(p2)
	x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
	return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def metres(lat1, lng1, lat2, lng2):
	r = 6371000.0
	p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
	dp = math.radians(float(lat2) - float(lat1))
	dl = math.radians(float(lng2) - float(lng1))
	a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
	return 2 * r * math.asin(math.sqrt(a))


def _tokens(line):
	out = []
	for raw in str(line or "").lower().replace(",", " ").split():
		tok = "".join(ch for ch in raw if ch.isalnum())
		if not tok or tok in NOISE:
			continue
		out.append(SUFFIX.get(tok, tok))
	return out


def _house_number(tokens):
	return tokens[0] if tokens and tokens[0].isdigit() else ""


def _zip5(text):
	for raw in str(text or "").replace(",", " ").split():
		digits = "".join(ch for ch in raw if ch.isdigit())
		if len(digits) >= 5:
			return digits[:5]
	return ""


def _is_subsequence(small, big):
	it = iter(big)
	return all(tok in it for tok in small)


def classify_address(ours, theirs):
	"""Classify a standardized address as same, cosmetic, or different.

	Only a substantive change interrupts a rep: different/missing house number,
	different street, or different ZIP. Country suffixes, punctuation, suffix
	spellings, omitted city/state, and vendor junk tails stay silent.
	"""
	a, b = _tokens(ours), _tokens(theirs)
	if not b or a == b:
		return "same"
	if _house_number(a) != _house_number(b):
		return "different"
	za, zb = _zip5(ours), _zip5(theirs)
	if za and zb and za != zb:
		return "different"
	if _is_subsequence(b, a) or _is_subsequence(a, b):
		return "cosmetic"
	return "different"


def pick_point(redfin=None, zillow=None, google=None):
	"""Return the first exact point as ``(lat, lng, source)``.

	Google diagnostic coordinates are deliberately rejected unless the endpoint
	explicitly says exact and independently carries a non-partial ROOFTOP result.
	"""
	if redfin:
		return float(redfin[0]), float(redfin[1]), "redfin"
	if zillow:
		return float(zillow[0]), float(zillow[1]), "zillow"
	if (
		google and google.get("exact") is True
		and str(google.get("location_type") or "").upper() == "ROOFTOP"
		and not google.get("partial_match")
		and google.get("lat") is not None and google.get("lng") is not None
	):
		return float(google["lat"]), float(google["lng"]), "google"
	return None, None, ""


# ---------------------------------------------------------------------------------
# Service boundaries
# ---------------------------------------------------------------------------------
def _propwarehouse_base():
	"""Strict PropWarehouse URL; NEVER inherit vendor_facts' Redfin fallback."""
	configured = ""
	try:
		configured = frappe.conf.get("propwarehouse_url") or ""
	except Exception:
		pass
	return str(configured or os.environ.get("PROPWAREHOUSE_URL") or "").strip().rstrip("/")


def _redfin_base():
	try:
		return str(frappe.conf.get("redfin_scraper_url") or "").strip().rstrip("/")
	except Exception:
		return ""


def _json_request(method, base, path, *, params=None, body=None):
	"""Return ``(payload, None)`` or ``(None, stable_reason)``.

	Never return/log response text or exception strings: requests includes full
	URLs in exceptions, and internal addresses or provider credentials must not
	leak into Frappe Error Log or an API response.
	"""
	if not base:
		return None, "not_configured"
	try:
		if method == "POST":
			r = requests.post(f"{base}{path}", json=body or {}, timeout=TIMEOUT)
		else:
			r = requests.get(f"{base}{path}", params=params or {}, timeout=TIMEOUT)
	except Exception:
		return None, "unreachable"
	if r.status_code != 200:
		return None, "unavailable"
	try:
		out = r.json()
	except Exception:
		return None, "invalid_response"
	return (out, None) if isinstance(out, dict) else (None, "invalid_response")


def _address_key(address):
	from crm.api.comps import address_key
	return address_key(address)


def _suggestion_key(source_address, suggested_address):
	raw = f"{_address_key(source_address)}->{_address_key(suggested_address)}"
	return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _exact_point(data):
	try:
		lat, lng = float(data.get("lat")), float(data.get("lng"))
	except (TypeError, ValueError, AttributeError):
		return None
	if not (-90 <= lat <= 90 and -180 <= lng <= 180):
		return None
	return lat, lng


def _cached_zillow_point(doc):
	"""Read Zillow's existing facts only when they belong to THIS address.

	The facts cache survives ordinary document edits. Without the address-key
	check, correcting a lead from house A to house B would let A's rooftop win the
	free rung, skip Redfin normalization and Google, and persist A as B's parcel.
	Legacy cache entries without `_queried_address` are therefore untrusted here.
	"""
	try:
		from crm.api import zillow as zillow_api
		facts = zillow_api._cached(doc)
		current = _full_address(doc)
	except Exception:
		return None
	if not facts or not current:
		return None
	queried = str(facts.get("_queried_address") or "").strip()
	if not queried or _address_key(queried) != _address_key(current):
		return None
	return _exact_point(facts)


def _redfin_facts(address, lat, lng, *, normalize):
	"""Redfin facts plus exact-point provenance. Old servers are non-exact."""
	body, error = _json_request(
		"GET", _redfin_base(), "/facts",
		params={"address": address, "lat": lat, "lng": lng,
		        "normalize": 1 if normalize else 0},
	)
	if error or not body or body.get("ok") is False:
		return {"transient": True, "reason": error or "redfin_error"}

	point = None
	# Strict `is True`: 1/string truthiness from an old or malformed server is
	# not provenance. Absent is false by design for backwards compatibility.
	point_source = str(body.get("point_source") or "").strip()
	if body.get("point_exact") is True and point_source == "redfin":
		point = _exact_point(body)
	else:
		# `seed` and `unknown` are intentionally not parcels, even when coordinates
		# are present for map compatibility. Old servers omit the contract entirely.
		point = None

	normalized = str(body.get("normalized_address") or "").strip()
	# D11 before point provenance returned the rescued canonical address as the
	# matched row's address plus normalization_source. Preserve compatibility.
	if not normalized and body.get("normalization_source") and body.get("address"):
		normalized = str(body.get("address") or "").strip()
	return {
		"transient": False,
		"matched": bool(body.get("matched")),
		"point": point,
		"point_source": point_source,
		"normalized": normalized,
		"normalization_source": body.get("normalization_source"),
		"normalization_billed": body.get("normalization_billed", 0),
	}


def _google_exact(address):
	"""One-address exact lookup through PropWarehouse; never calls Census."""
	body, error = _json_request(
		"POST", _propwarehouse_base(), "/address/exact",
		body={"street": address, "city": "", "state": "", "zip": ""},
	)
	if error:
		return {"status": "transient", "reason": error}
	if body.get("ok") is not True:
		return {"status": "transient", "reason": body.get("reason") or "unavailable"}
	out = {
		"normalized": str(body.get("normalized") or "").strip(),
		"location_type": body.get("location_type") or "",
		"partial_match": bool(body.get("partial_match")),
		"exact": body.get("exact") is True,
		"lat": body.get("lat"), "lng": body.get("lng"),
		"source": body.get("source") or "google",
		"cache": body.get("cache"), "billed": body.get("billed", 0),
	}
	if body.get("matched") is not True:
		return {**out, "status": "no_match", "reason": body.get("reason") or "no_match"}
	if not out["exact"]:
		return {**out, "status": "not_exact", "reason": "not_exact"}
	return {**out, "status": "exact", "reason": None}


# ---------------------------------------------------------------------------------
# Subject state
# ---------------------------------------------------------------------------------
def _supported(doctype):
	try:
		return all(frappe.db.has_column(doctype, field) for field in REQUIRED_FIELDS)
	except Exception:
		return False


def _full_address(doc):
	from crm.api.comps import _full_address as build
	return build(doc)


def _load_subject(subject):
	from crm.api.comps import _load_subject as load
	return load(subject)


def _field_values(doc, fields):
	return {field: doc.get(field) for field in fields}


def _filter_fields(doctype, values):
	return {k: v for k, v in values.items() if frappe.db.has_column(doctype, k)}


def _suggestion_fields(doc, source_address, normalized):
	"""Suggestion state scoped to this exact source-address/suggestion pair."""
	current_source_key = _address_key(source_address)
	existing_source_key = str(doc.get("parcel_address_key") or "")
	state = str(doc.get("address_suggestion_state") or "")
	existing_pair = str(doc.get("address_suggestion_key") or "")

	if normalized and classify_address(source_address, normalized) == "different":
		pair = _suggestion_key(source_address, normalized)
		if pair != existing_pair:
			return {
				"address_suggested": normalized,
				"address_suggestion_key": pair,
				"address_suggestion_state": "pending",
			}
		if state == "pending":
			return {"address_suggested": normalized}
		# accepted/dismissed suppresses only this exact pair.
		return {}

	# A new source address with no new disagreement invalidates an old decision;
	# otherwise the UI would claim an accepted/dismissed decision about a house
	# that is no longer on the document.
	if existing_source_key and existing_source_key != current_source_key:
		return {
			"address_suggested": "",
			"address_suggestion_key": "",
			"address_suggestion_state": "",
		}
	return {}


def _persist(doc, values):
	row = _filter_fields(doc.doctype, values)
	if not row:
		return False
	frappe.db.set_value(doc.doctype, doc.name, row, update_modified=False)
	return True


def _clear_resolution(doctype, name, *, keep_suggestion=False):
	values = {
		"parcel_lat": None, "parcel_lng": None, "parcel_source": "",
		"parcel_address_key": "", "parcel_checked_at": None,
	}
	if not keep_suggestion:
		values.update(
			address_suggested="", address_suggestion_key="",
			address_suggestion_state="",
		)
	row = _filter_fields(doctype, values)
	if row:
		frappe.db.set_value(doctype, name, row, update_modified=False)


def _resolve(subject, *, move_centre=False):
	"""Resolve one subject. Never raises; external failures stay retryable."""
	out = {"ok": False, "exact": False, "subject": subject}
	try:
		doc = _load_subject(subject)
	except Exception:
		return {**out, "reason": "no such subject", "retryable": False}
	if not _supported(doc.doctype):
		return {**out, "reason": "fields not installed", "retryable": True}

	address = _full_address(doc)
	if not address:
		return {**out, "reason": "no address", "retryable": False}
	address_key = _address_key(address)
	out.update(address=address, address_key=address_key)

	try:
		from crm.api.comps import _subject_point
		seed_lat, seed_lng, _ = _subject_point(doc)
	except Exception:
		seed_lat = seed_lng = None
	zillow = _cached_zillow_point(doc)
	if zillow:
		seed_lat, seed_lng = zillow

	redfin = {"transient": False, "point": None, "normalized": ""}
	if seed_lat is not None and seed_lng is not None:
		redfin = _redfin_facts(address, seed_lat, seed_lng, normalize=not bool(zillow))

	google = None
	redfin_point = redfin.get("point") if not redfin.get("transient") else None
	if not redfin_point and not zillow:
		google = _google_exact(address)

	lat, lng, source = pick_point(redfin=redfin_point, zillow=zillow, google=google)
	normalized = str(redfin.get("normalized") or "").strip()
	if google and google.get("normalized"):
		normalized = str(google["normalized"]).strip()
	suggestion = _suggestion_fields(doc, address, normalized)

	if lat is None:
		# A hard service failure says nothing about the address. Do not stamp the
		# address key/checked_at: the next call must retry rather than remember an
		# outage as an adjudicated miss.
		if google and google.get("status") == "transient":
			return {**out, "reason": google.get("reason"), "retryable": True}
		if not google and redfin.get("transient") and not zillow:
			return {**out, "reason": redfin.get("reason"), "retryable": True}

		values = {
			"parcel_lat": None, "parcel_lng": None, "parcel_source": "",
			"parcel_address_key": address_key, "parcel_checked_at": now_datetime(),
			**suggestion,
		}
		try:
			_persist(doc, values)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "address_resolve: persist miss failed")
			return {**out, "reason": "persist failed", "retryable": True}
		reason = (google or {}).get("reason") or "no exact point"
		return {
			**out, "reason": reason, "retryable": False, "adjudicated": True,
			"suggested": suggestion.get("address_suggested", ""),
		}

	values = {
		"parcel_lat": lat, "parcel_lng": lng, "parcel_source": source,
		"parcel_address_key": address_key, "parcel_checked_at": now_datetime(),
		**suggestion,
	}
	# Only two paths may move the circle, both Lead-only: the after-insert token
	# path and the human address-accept path (see resolve_after_address_correction).
	if move_centre and doc.doctype == "CRM Lead":
		values.update(property_lat=lat, property_lng=lng)
	try:
		_persist(doc, values)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "address_resolve: persist failed")
		return {**out, "reason": "persist failed", "retryable": True}

	return {
		**out, "ok": True, "exact": True, "lat": lat, "lng": lng,
		"source": source, "reason": None, "retryable": False,
		"centre_moved": bool(move_centre and doc.doctype == "CRM Lead"),
		"suggested": suggestion.get("address_suggested", doc.get("address_suggested") or ""),
		"google_billed": (google or {}).get("billed", 0),
		"google_cache": (google or {}).get("cache"),
	}


def resolve(subject):
	"""Manual/internal resolve that can NEVER move the existing comp circle."""
	return _resolve(subject, move_centre=False)


def resolve_at_ingest(lead, expected_creation):
	"""After-insert-only entry point; the only circle-moving path.

	The queue job carries the creation stamp captured by `on_lead_insert`. A
	manual/backfill caller has no token; a stale/replayed job whose name now points
	at a different row cannot move its circle. This is an internal Python method,
	not a whitelisted API.
	"""
	try:
		doc = _load_subject(lead)
		actual = str(doc.get("creation") or "")
		expected = str(expected_creation or "")
		if doc.doctype != "CRM Lead" or not expected or actual != expected:
			return {
				"ok": False, "exact": False, "subject": lead,
				"reason": "not an after-insert resolution", "retryable": False,
			}
		return _resolve(lead, move_centre=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "address_resolve: ingest resolve failed")
		return {"ok": False, "exact": False, "subject": lead, "reason": "exception", "retryable": True}


def resolve_after_address_correction(subject):
	"""The suggestion-accept path's resolve: MAY re-centre an existing circle.

	The only circle-moving path besides the after-insert token, and it needs no
	token: reaching it requires a valid pending suggestion for exactly this
	address, which only the resolver itself could have written -- that stored
	state IS the proof that a human compared the two addresses and chose the
	correction. A human deciding the address is the strongest signal the circle
	is wrong, and the longer it sits at the old centre the less true it is.

	A scratch property has no circle; `_resolve` already no-ops the move there.
	"""
	return _resolve(subject, move_centre=True)


# ---------------------------------------------------------------------------------
# Read APIs and human decisions
# ---------------------------------------------------------------------------------
def _status_from_doc(doc):
	address = _full_address(doc)
	key = _address_key(address) if address else ""
	stored_key = str(doc.get("parcel_address_key") or "")
	valid = bool(key and stored_key == key)
	return {
		"subject": doc.name,
		"address": address,
		"resolved": valid and bool(doc.get("parcel_checked_at")),
		"exact": valid and bool(doc.get("parcel_lat")) and doc.get("parcel_source") in ("redfin", "zillow", "google"),
		"source": doc.get("parcel_source") if valid else "",
		"suggested_address": doc.get("address_suggested") if valid else "",
		"suggestion_key": doc.get("address_suggestion_key") if valid else "",
		"suggestion_state": doc.get("address_suggestion_state") if valid else "",
	}


@frappe.whitelist()
def get_address_status(subject: str) -> dict:
	"""Read stored parcel/suggestion state only. Makes no external request."""
	_guard()
	doc = _load_subject(subject)
	doc.check_permission("read")
	if not _supported(doc.doctype):
		return {"subject": subject, "resolved": False, "exact": False, "reason": "fields not installed"}
	return _status_from_doc(doc)


@frappe.whitelist()
def get_street_view(subject: str) -> dict:
	"""Resolve lazily, then ask PropWarehouse for panorama metadata/heading."""
	_guard()
	doc = _load_subject(subject)
	doc.check_permission("read")
	if not _supported(doc.doctype):
		return {"ok": False, "available": False, "reason": "fields not installed"}
	status = _status_from_doc(doc)
	if not status["resolved"]:
		resolved = resolve(subject)
		doc = _load_subject(subject)
		status = _status_from_doc(doc)
		if resolved.get("retryable"):
			status["retryable"] = True
	if not status.get("exact"):
		return {**status, "ok": False, "available": False, "reason": "no exact point"}

	lat, lng = doc.get("parcel_lat"), doc.get("parcel_lng")
	body, error = _json_request(
		"GET", _propwarehouse_base(), "/streetview",
		params={"lat": lat, "lng": lng},
	)
	if error or not body or body.get("ok") is not True:
		return {**status, "ok": True, "available": False, "retryable": True,
		        "reason": error or "streetview unavailable", "lat": lat, "lng": lng}
	return {
		**status, "ok": True, "available": bool(body.get("available")),
		"lat": lat, "lng": lng,
		"heading": body.get("heading"), "pano_id": body.get("pano_id") or "",
		"camera_m": body.get("camera_m"), "captured": body.get("captured") or "",
		"reason": body.get("reason"),
	}


def _fresh_pending_suggestion(doc):
	"""Reload and validate a pending suggestion against the CURRENT address.

	Returns ``(suggested, pair, reason)``. The pair is recomputed rather than
	trusted: a concurrent address edit after the banner loaded must produce a
	clean stale response and zero mutation, not apply yesterday's correction to
	today's house.
	"""
	doc.reload()
	doc.check_permission("write")
	suggested = str(doc.get("address_suggested") or "").strip()
	pair = str(doc.get("address_suggestion_key") or "")
	if doc.get("address_suggestion_state") != "pending" or not suggested or not pair:
		return None, None, "nothing suggested"
	current = _full_address(doc)
	current_key = _address_key(current) if current else ""
	if (
		not current_key
		or str(doc.get("parcel_address_key") or "") != current_key
		or pair != _suggestion_key(current, suggested)
	):
		return None, None, "stale suggestion"
	return suggested, pair, None


def _save_human_decision(doc):
	"""Save with optimistic timestamp protection; name stale races cleanly."""
	try:
		doc.save()
		return None
	except Exception as exc:
		if type(exc).__name__ == "TimestampMismatchError":
			return "stale suggestion"
		raise


@frappe.whitelist()
def accept_address_suggestion(subject: str) -> dict:
	"""Accept the pending address with audit, then re-centre and re-warm.

	This is the one sanctioned place an existing lead's comp circle may move:
	a human has just asserted the old address was wrong. Every other path
	(manual warm, refresh, backfill) keeps the centre it was bought at.
	"""
	_guard()
	doc = _load_subject(subject)
	doc.check_permission("write")
	if not _supported(doc.doctype):
		frappe.throw(_("Address suggestions are not installed on this site."))
	suggested, pair, stale = _fresh_pending_suggestion(doc)
	if stale:
		return {"ok": False, "reason": stale}

	doc.property_address = suggested
	stale = _save_human_decision(doc)
	if stale:
		return {"ok": False, "reason": stale}
	_clear_resolution(doc.doctype, doc.name, keep_suggestion=True)
	frappe.db.set_value(
		doc.doctype, doc.name,
		{"address_suggested": "", "address_suggestion_key": pair,
		 "address_suggestion_state": "accepted"},
		update_modified=False,
	)

	# The accept has landed. The address the circle was bought around was wrong,
	# so re-centre and re-warm -- the ONLY time an existing lead's circle moves.
	# Ordering is the ingest ordering: move the centre FIRST, then enqueue the
	# warm, so the sweep never lands at the old point.
	resolved = resolve_after_address_correction(subject)
	out = {
		"ok": True,
		"address": suggested,
		"resolved": resolved,
		"circle_recentred": bool(resolved.get("centre_moved")),
		"warm_enqueued": False,
	}
	if not (resolved.get("ok") and resolved.get("exact")):
		# No exact point for the corrected address: keep the old centre rather
		# than move to a guess, and do not warm at a centre we know is stale.
		return out
	if resolved.get("centre_moved"):
		try:
			frappe.enqueue(
				"crm.api.geo.warm_lead",
				queue="long",
				job_name=f"geo-warm-accept-{subject}",
				enqueue_after_commit=True,
				lead=subject,
			)
			out["warm_enqueued"] = True
		except Exception:
			# The accept stands; the warm is recoverable via warm_backfill.
			frappe.log_error(
				frappe.get_traceback(), "address_resolve: re-warm enqueue failed"
			)
	return out


@frappe.whitelist()
def dismiss_address_suggestion(subject: str) -> dict:
	"""Dismiss only this exact source-address/suggestion pair."""
	_guard()
	doc = _load_subject(subject)
	doc.check_permission("write")
	if not _supported(doc.doctype):
		return {"ok": False, "reason": "fields not installed"}
	_suggested, pair, stale = _fresh_pending_suggestion(doc)
	if stale:
		return {"ok": False, "reason": stale}
	doc.address_suggested = ""
	doc.address_suggestion_key = pair
	doc.address_suggestion_state = "dismissed"
	stale = _save_human_decision(doc)
	return {"ok": not stale, "reason": stale} if stale else {"ok": True}


@frappe.whitelist()
def resolve_now(subject: str) -> dict:
	"""Manual re-resolve. There is intentionally no ingest/circle-moving flag."""
	_guard()
	doc = _load_subject(subject)
	doc.check_permission("write")
	return resolve(subject)


def backfill(dry_run=1, limit=None):
	"""Resolve existing leads without ever moving their canonical circle."""
	names = frappe.get_all(
		"CRM Lead", filters={"converted": ("!=", 1)},
		or_filters=[{"parcel_checked_at": ("is", "not set")}],
		pluck="name", limit=limit, order_by="creation desc",
	)
	if dry_run:
		return {"would_resolve": len(names), "sample": names[:10]}
	done = {"ok": 0, "failed": 0, "suggestions": 0}
	for name in names:
		res = resolve(name)
		done["ok" if res.get("ok") else "failed"] += 1
		if res.get("suggested"):
			done["suggestions"] += 1
	return done

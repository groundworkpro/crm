# Copyright (c) 2026, Groundwork and contributors
# For license information, please see license.txt

"""Comparable sales around a lead's property — the "View comps" map.

Ported from the LeadMarket (istl-buyer) comps view, with one deliberate change:
LeadMarket can only ever draw an ESTIMATED subject location, because iSpeedToLead
gates the real address until you buy the lead, so it plots the centroid of the comp
cloud. We own these leads, so we know the actual parcel — the map centers on the
subject's real geocoded address and the comps arrange themselves around it.

Where the data comes from
-------------------------
The comps themselves are iSpeedToLead's (RentCast-derived) last *asks*, imported
from the LeadMarket inventory by `import_comps_file`. ISTL uses RentCast internally;
we never call the RentCast API. They carry a full street address but no
coordinates, so the importer ships them pre-geocoded.

On every map open we then ask Zillow whether anything is more recent: a ZIP-level
RecentlySold + ForSale search (A), and `/property` on the nearest ISTL pins (B).
See `crm.api.zillow_comps`. Ingested ISTL pins also pick up photos and current
MLS status from the Redfin neighbourhood store (`crm.api.redfin.apply_istl_comps`)
that `geo.warm_lead` already queued at purchase. BatchData remains the last
resort when the pooled index is empty even after that. A ForRent circle is a
separate inventory (`inventory=rentals`) and never mixed into the sale map.

Two layers, because exact-per-lead coverage is thin:
  * a lead we BOUGHT and whose marketplace record we still hold has its own comps
  * everything else is served from the pooled AREA inventory — every comp we have
    anywhere near the subject, which covers ~92% of our leads

The pooled layer is what makes this useful today: matching a CRM lead back to its
marketplace record only works for the ~9% we scraped while they were still for
sale (see `crm.api.comps.LINKAGE_NOTE`).

Recency is a first-class signal, not decoration: a sale from last month says more
about today's value than one from last year, so the UI fades a comp by staleness.
The raw dates are returned untouched and the fade is computed client-side, so the
map and any future report can't disagree about what "recent" means.

Filters, and why they arrive PRE-SET
------------------------------------
An unfiltered radius dump is not a comp set — it is every roof within two miles,
and on a dense ZIP that is 200 pins of condos and mansions around a 900 sqft
bungalow. So the map opens with the filters already set AROUND THIS PROPERTY:
recent, and similar in beds / baths / size / age / type.

The hard part is that a tight, honest filter often matches nothing — our comp
inventory is a pooled area index, not a curated per-lead set. Silently showing an
empty map would be the worst outcome, and silently showing everything would be a
lie about fit. So `_preset_tiers` is a LADDER: the tightest tier that still yields
a usable set wins, and the response says which tier was used and whether it had to
loosen, so the UI can tell the user plainly rather than pretending.
"""

import hashlib
import json
import math
import re
import threading
import urllib.parse
import urllib.request

import frappe
from crm.api.comp_provenance import is_adc, shape_pool_row, qualified_address
from frappe import _

DOCTYPE = "CRM Comp"
SALES_ROLES = ("System Manager", "Sales Manager", "Sales User")

#: Fallback when a caller sends no radius. The comps PAGE starts at 0.5 mi and
#: walks out (0.5 → 1 → 2 → 5) until it has a usable set; this 2-mile default is
#: only for older callers that omit the argument entirely.
DEFAULT_RADIUS_MI = 2.0
#: Hard cap on returned pins, and since gw366 it is a DATA-COMPLETENESS promise
#: rather than only a rendering limit: every comp that reaches the map gets its
#: full sale history fetched (`_attach_sale_history`), so the board can be read
#: as "everything shown here, we know everything about". A two-tier board -- some
#: pins carrying a flip warning and some silently lacking one -- would be worse
#: than no warning at all, because absence would look like evidence of no flip.
#:
#: 50 is a SPEND decision, measured. Sale history is one billed RapidAPI call per
#: comp on a key shared with istl-buyer's ZIP job. At 25 leads/day against the
#: 57,000/cycle plan, ~53 comps/lead is what the budget affords once istl-buyer's
#: ~10,500 is reserved; every comp in a 1/2-mile circle (mean 126, and 253 in
#: Indianapolis) would need roughly double the plan. Raising this is a one-line
#: dial, but it is a bill, so turn it deliberately.
#:
#: It is applied AFTER filtering, never before -- capping first would take the 50
#: nearest and then filter those, hiding better-fitting comps slightly further
#: out. The tier ladder still runs over the whole filtered set, so what lands on
#: the board is unchanged; only how many of them are drawn.
MAX_COMPS = 50

#: How many matches make a tier "usable". Below this you are not comping, you are
#: reading anecdotes, so the ladder loosens instead of presenting 2 pins as an
#: answer. Five is the smallest set a reviewer can see a middle in.
MIN_USABLE_COMPS = 5

#: Default recency window for OFF-MARKET comps. A sale older than a year is a
#: different market, so the preset never reaches further back than this until the
#: ladder is forced to. Active listings are deliberately exempt — see `_matches`.
DEFAULT_WITHIN_DAYS = 365

#: Clicking a comp can spend two shared RapidAPI requests (property + photos), so
#: keep the normalized result rather than paying again every time another setter
#: opens the same house. A failed/partial lookup gets a short retry window.
DETAIL_CACHE_SECONDS = 30 * 24 * 60 * 60
DETAIL_RETRY_SECONDS = 60 * 60
DETAIL_CACHE_VERSION = 3  # v3 retries thin Zillow galleries via Realtor
#: The two BILLED calls behind a gallery (`/property` + `/photos`) are cached on
#: their own, for the full month, the moment Zillow ANSWERS -- even when the
#: answer is one leftover frame. The thin-gallery retry above exists so the
#: Realtor/Redfin rungs get another go; it used to re-bill Zillow every hour
#: too, for a house Zillow had already said has one photo. Off-market solds are
#: mostly one-photo houses, so that was most galleries, every hour, forever.
#: Only a FAILED call (no body at all) gets the short window. Shares the
#: `crm:comp-detail` prefix so `persistent_cache_keys` already keeps it.
DETAIL_ZILLOW_CACHE_VERSION = 1
#: Older gallery generations that can be served as the current one without a
#: refetch. {from_version: fn(cached) -> cached | None}; None refuses. v2->v3 only
#: changed what happens to a THIN gallery, so a full v2 gallery is still the answer.
DETAIL_MIGRATIONS = {
	2: lambda c: c if len((c or {}).get("photos") or []) > 1 else None,
}

#: Per-lead, TEAM-WIDE record of which comps a human hid or picked. Not per-user:
#: a junk comp is junk for everyone, and "the comps we used" is a deal artifact
#: the next person to open the lead needs to see.
HIDDEN_FIELD = "comps_hidden"
SELECTED_FIELD = "comps_selected"

#: A rep-entered subject square footage. Zillow is sometimes simply wrong about
#: the house being priced ("the square footage is off from what it actually is"),
#: and every downstream number — the preset ladder, the tray deltas, the repair
#: $/sf totals — keys off the subject's sqft. Team-wide like the comp picks: a
#: corrected measurement is correct for everyone.
SQFT_FIELD = "sqft_override"

#: Per-lead, TEAM-WIDE condition tag on a picked comp (comp docname -> label).
#: Optional on purpose — picking stays one click; the tag is what a rep adds when
#: the photos told them something the numbers do not carry.
TYPES_FIELD = "comps_types"
COMP_CONDITION_TYPES = (
	"New Build",
	"Fix and Flip",
	"Move-in Ready",
	"Remodel",
	"Full Gut",
	"Teardown",
)

#: The first cut shipped a different vocabulary for a few hours (gw433-gw437);
#: anything stored under it reads back as its nearest current label.
LEGACY_CONDITION_TYPES = {
	"Move-in ready": "Move-in Ready",
	"Fixed up": "Fix and Flip",
	"New build": "New Build",
	"Full rehab / as-is": "Remodel",
	"Full gut": "Full Gut",
}

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
UA = {"User-Agent": "groundwork-crm/1.0 (+groundworkpro.com; comps map)"}

LINKAGE_NOTE = (
	"CRM Lead.vendor_lead_id holds the iSpeedToLead ORDER id, not the lead id. "
	"The lead id lives at order.lead._id in GET /orders/all."
)


def _guard():
	if not any(role in SALES_ROLES for role in frappe.get_roles()):
		frappe.throw(_("Not permitted."), frappe.PermissionError)


def _available() -> bool:
	"""False until the ops setup script has run. Everything degrades to 'no comps'."""
	return bool(frappe.db.exists("DocType", DOCTYPE))


# ---------------------------------------------------------------------------------
# Subject dispatch: a CRM Lead, or a scratch CRM Property (comps without a lead)
# ---------------------------------------------------------------------------------
#: A scratch property is a house someone wants to comp and price WITHOUT making it
#: a lead — a deal being scouted, a neighbour's house, a buyer's ask. It carries
#: the same property/cache/comps columns a CRM Lead does (see ops
#: `setup_properties.py`), so every read and write in this module runs unchanged
#: against it. Dispatch is on the NAME prefix rather than a DB lookup, because
#: these functions sit on the hot path of every map open and a `frappe.db.exists`
#: per call would be a query for a fact the name already states.
SCRATCH_DOCTYPE = "CRM Property"
SCRATCH_PREFIX = "PROP-"


def subject_doctype(name) -> str:
	"""`CRM Property` for a scratch-property name, else `CRM Lead`."""
	return SCRATCH_DOCTYPE if str(name or "").startswith(SCRATCH_PREFIX) else "CRM Lead"


def _load_subject(name):
	"""The subject doc for a lead or scratch-property name; throws if absent."""
	dt = subject_doctype(name)
	if not frappe.db.exists(dt, name):
		frappe.throw(_("{0} {1} does not exist.").format(dt, name), frappe.DoesNotExistError)
	return frappe.get_doc(dt, name)


def address_key(address: str) -> str:
	"""Deterministic, collision-free docname for one property address.

	Deterministic so a re-import updates the same row instead of duplicating it;
	the md5 tail is what keeps two addresses that slugify identically (or that get
	truncated to the same 100 chars) from colliding on a `unique` field.
	"""
	norm = re.sub(r"\s+", " ", (address or "").strip().lower())
	slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")[:100]
	return f"{slug}-{hashlib.md5(norm.encode()).hexdigest()[:8]}"


def _haversine_mi(lat1, lng1, lat2, lng2):
	r = 3958.7613
	p1, p2 = math.radians(lat1), math.radians(lat2)
	dp = math.radians(lat2 - lat1)
	dl = math.radians(lng2 - lng1)
	a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
	return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------------
# Subject property geocoding
# ---------------------------------------------------------------------------------
def _full_address(doc) -> str:
	"""Street + city + state + zip, skipping anything already inside the street line.

	Webhook leads arrive with the whole address in `property_address`; hand-entered
	ones are street-only with the rest in separate fields. Same rule the agreement
	builder uses, and for the same reason: a bare "123 Main St" geocodes to the
	wrong state.
	"""
	parts = [(doc.get("property_address") or "").strip()]
	for key in ("property_city", "property_state", "property_zip"):
		val = (doc.get(key) or "").strip()
		if val and val.lower() not in parts[0].lower():
			parts.append(val)
	return ", ".join(p for p in parts if p)


def _census_geocode(address: str):
	"""One address -> (lat, lng), or None. Free, US-only, no API key."""
	qs = urllib.parse.urlencode(
		{"address": address, "benchmark": "Public_AR_Current", "format": "json"}
	)
	req = urllib.request.Request(f"{CENSUS_URL}?{qs}", headers=UA)
	try:
		with urllib.request.urlopen(req, timeout=20) as resp:
			body = json.loads(resp.read().decode("utf-8", "replace"))
		matches = (body.get("result") or {}).get("addressMatches") or []
		if not matches:
			return None
		coords = matches[0].get("coordinates") or {}
		return float(coords["y"]), float(coords["x"])
	except Exception:
		# A geocoder outage must not take the lead page with it.
		frappe.log_error(frappe.get_traceback(), "Comps: subject geocode failed")
		return None


def _subject_point(doc):
	"""The lead's real position, geocoded once and cached on the lead.

	Cached because the Census geocoder is free but slow (~1s), and this runs every
	time someone opens the modal. `update_modified=False` so caching a coordinate
	never looks like somebody edited the lead.
	"""
	has_cache = frappe.db.has_column(doc.doctype, "property_lat")
	if has_cache and doc.get("property_lat") and doc.get("property_lng"):
		return float(doc.property_lat), float(doc.property_lng), True

	address = _full_address(doc)
	if not address:
		return None, None, False
	point = _census_geocode(address)
	if not point:
		return None, None, False
	if has_cache:
		try:
			frappe.db.set_value(
				doc.doctype, doc.name,
				{"property_lat": point[0], "property_lng": point[1]},
				update_modified=False,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: caching subject point failed")
	return point[0], point[1], False


# ---------------------------------------------------------------------------------
# Subject property facts
# ---------------------------------------------------------------------------------
#: iSpeedToLead ships the subject's details as pick-list LABELS, not numbers.
#: Measured across all 764 prod leads, the entire vocabulary is:
#:   bedrooms   "3 Bedroom", "More than 5"
#:   bathrooms  "1.5 Bathroom", "More than 3", "None"
#:   square_footage "1000 - 2000", "5000+", "0 - 500"
#:   year_built "1900-1950", "2010-2022"
_RE_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|\u2013|to)\s*(\d+(?:\.\d+)?)\s*$", re.I)
_RE_PLUS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*\+\s*$")
_RE_MORE = re.compile(r"more\s*than\s*(\d+(?:\.\d+)?)", re.I)
_RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_band(value):
	"""One pick-list label -> (lo, hi, exact) in real numbers, or None.

	Returns the INTERVAL the source actually named, not a midpoint. "1000 - 2000"
	is a 2x band; collapsing it to 1500 would invent precision the data does not
	have and would then be widened again by a tolerance, compounding the fiction.
	`exact` says the source named a single value, which is what lets the preset be
	tight when we really know and loose when we are guessing.
	"""
	if value is None:
		return None
	text = str(value).strip()
	if not text or text.lower() in ("none", "n/a", "na", "not provided", "unknown", "0"):
		return None

	m = _RE_RANGE.match(text)
	if m:
		lo, hi = float(m.group(1)), float(m.group(2))
		return (min(lo, hi), max(lo, hi), False)
	# "5000+" / "More than 5" are open-ended: no upper bound, and NOT exact.
	m = _RE_PLUS.match(text) or _RE_MORE.search(text)
	if m:
		return (float(m.group(1)), None, False)
	m = _RE_NUM.search(text)
	if m:
		n = float(m.group(1))
		return (n, n, True)
	return None


def _band_mid(band):
	"""A single number to DISPLAY for a band (never used to filter)."""
	if not band:
		return None
	lo, hi = band[0], band[1]
	return lo if hi is None else (lo + hi) / 2.0


def _band_label(band, unit="", group=False):
	"""A band as the source named it. `group` adds thousand separators.

	Grouping is opt-in rather than automatic because the same helper labels a YEAR:
	"1,910" is not a year, it is a bug that looks like a price. Only sqft asks for
	it — a bare "1155 sqft" beside comps that all read "1,155 sqft" was the one
	number on the subject card written in a different language from its neighbours.
	"""
	if not band:
		return None
	lo, hi = band[0], band[1]

	def n(v):
		if float(v).is_integer():
			return f"{int(v):,}" if group else str(int(v))
		return f"{v:,g}" if group else f"{v:g}"

	if hi is None:
		return f"{n(lo)}+{unit}"
	if lo == hi:
		return f"{n(lo)}{unit}"
	return f"{n(lo)}–{n(hi)}{unit}"


def _self_listing(doc):
	"""The subject's OWN row in the comp inventory, if we happen to hold one.

	Present for ~5% of leads (13 of a 250-lead prod sample): a lead we bought was
	often scraped while it was itself on the market. When it exists it is the best
	thing we have about the property — real numbers instead of pick-list bands,
	plus what it last listed for and when it left the market.

	NOTE it is looked up by the same deterministic address_key the importer names
	rows with, so this is an exact-address match, never a fuzzy one.
	"""
	if not _available():
		return None
	candidates = []
	street = (doc.get("property_address") or "").strip()
	if street:
		candidates.append(street)
	full = _full_address(doc)
	if full and full != street:
		candidates.append(full)
	for addr in candidates:
		row = frappe.db.get_value(
			DOCTYPE, address_key(addr),
			["name", "address", "price", "status", "listed_date", "removed_date",
			 "days_on_market", "bedrooms", "bathrooms", "square_footage",
			 "year_built", "property_type"],
			as_dict=True,
		)
		if row:
			for key in ("listed_date", "removed_date"):
				if row.get(key):
					row[key] = str(row[key])
			return row
	return None


def _subject_facts(doc):
	"""Everything we can honestly say about the subject, best source first.

	1. ZILLOW (`crm.api.zillow`) — real numbers for beds/baths/sqft/year/type, and a
	   genuine last SALE out of priceHistory's Public Record rows
	2. its own listing in the comp inventory (real numbers, but a last ASK, and
	   present for only ~5% of leads)
	3. the iSpeedToLead pick-list fields on the lead (bands, not numbers)
	4. the BatchData tax pull (assessed value / annual tax), already on the lead

	Every fact carries the source it came from, because "3 bd" from Zillow, from a
	listing record, and typed into a web form by a motivated seller are not the same
	claim — and the preset filters widen according to which one it is.
	"""
	listing = _self_listing(doc)
	zillow = None
	try:
		from crm.api import zillow as zillow_api

		zillow = zillow_api.facts_for_lead(doc)
	except Exception:
		# A third-party lookup must never take the comps map down with it.
		frappe.log_error(frappe.get_traceback(), "Comps: Zillow facts failed")
	facts = {"source": {}}

	def take(key, lead_field, listing_field=None, zillow_key=None, unit="", group=False):
		z = (zillow or {}).get(zillow_key or key)
		listing_val = (listing or {}).get(listing_field or lead_field)
		if z:
			band = (float(z), float(z), True)
			facts["source"][key] = "zillow"
		elif listing_val:  # importer coerces missing numerics to 0, so 0 == unknown
			band = (float(listing_val), float(listing_val), True)
			facts["source"][key] = "listing"
		else:
			band = _parse_band(doc.get(lead_field))
			if band:
				facts["source"][key] = "lead"
		facts[f"{key}_band"] = list(band[:2]) if band else None
		facts[f"{key}_exact"] = bool(band[2]) if band else False
		facts[key] = _band_mid(band)
		facts[f"{key}_label"] = _band_label(band, unit, group)

	# Each fact falls through INDEPENDENTLY: Zillow returns null for a fact it lacks
	# (measured — a home with bathrooms but no bedrooms), so picking one source for
	# the whole set would throw away good data from the others.
	take("beds", "bedrooms")
	take("baths", "bathrooms")
	# Grouped: the only one of the four that routinely runs to four digits.
	take("sqft", "square_footage", zillow_key="sqft", group=True)
	take("year_built", "year_built")

	# A rep's manual sqft outranks every scraped source. Applied HERE, at the one
	# place subject facts are derived, so the ladder's tolerances, the tray's ±
	# deltas and the calc's repair totals all read the corrected number without any
	# of them knowing an override exists. 0 means "no override" (the column is NOT
	# NULL, so clearing writes 0 — same Int-coercion rule the importer documents).
	override = _sqft_override(doc)
	if override:
		facts["sqft_band"] = [override, override]
		facts["sqft_exact"] = True
		facts["sqft"] = override
		facts["sqft_label"] = _band_label((override, override, True), "", True)
		facts["source"]["sqft"] = "manual"
	facts["sqft_override"] = override or None
	facts["sqft_override_supported"] = _sqft_override_supported(doc.doctype)

	z_type = (zillow or {}).get("property_type")
	ptype = z_type or (listing or {}).get("property_type") or None
	facts["property_type"] = ptype
	if ptype:
		facts["source"]["property_type"] = "zillow" if z_type else "listing"

	# The headline: what it actually SOLD for, and when. A Public Record transaction
	# out of Zillow's priceHistory is a far stronger claim than the comp inventory's
	# last ask, so it is kept separate and labelled a sale rather than folded in.
	facts["last_sale"] = (zillow or {}).get("last_sale") or None
	facts["zestimate"] = (zillow or {}).get("zestimate") or None
	facts["rent_zestimate"] = (zillow or {}).get("rent_zestimate") or None
	facts["lot_size"] = (zillow or {}).get("lot_size") or None
	facts["zpid"] = (zillow or {}).get("zpid") or None
	# A cached miss is `{}` (plus `_queried_address`) and must not read as a hit.
	facts["has_zillow"] = bool(facts["zpid"])
	facts["zillow_queried_address"] = (zillow or {}).get("_queried_address") or ""
	# May be "" here: leads cached before `cover_photo` was carried have no key.
	# `get_lead_comps` fills the gap from the area search's self-match afterwards,
	# so neither path spends a request on a picture.
	facts["cover_photo"] = (zillow or {}).get("cover_photo") or ""
	# Rooftop, when Zillow has one. Census geocode is street-interpolated; the
	# pair is what lets Street View stand on the street and look at the house.
	zlat, zlng = _num((zillow or {}).get("lat")), _num((zillow or {}).get("lng"))
	facts["zillow_lat"] = zlat
	facts["zillow_lng"] = zlng

	# What it last asked, and when it left the market. Deliberately NOT called a
	# sale price: this inventory carries the last LIST price, and going off-market
	# is not a confirmed close. The UI repeats that caveat rather than burying it.
	if listing:
		facts["last_listing"] = {
			"price": listing.get("price") or None,
			"status": listing.get("status"),
			"listed_date": listing.get("listed_date"),
			"removed_date": listing.get("removed_date"),
			"days_on_market": listing.get("days_on_market") or None,
		}
		facts["self_comp_key"] = listing.get("name")
	else:
		facts["last_listing"] = None
		facts["self_comp_key"] = None

	# Straight off the lead — seller-stated or BatchData, both already displayed
	# elsewhere on the page, so the popup is not inventing a new source of truth.
	facts["condition"] = (doc.get("property_condition") or "").strip() or None
	for key in ("assessed_value", "annual_tax", "asking_price"):
		val = doc.get(key)
		if isinstance(val, str):
			val = val.strip()
			val = val if val and val.lower() not in ("not provided", "none", "n/a") else None
		facts[key] = val or None
	# BatchData's assessed value costs $0.22 a pull and is only fetched on demand;
	# Zillow ships one with the facts we already have, so it fills the gap for free.
	if not facts.get("assessed_value") and (zillow or {}).get("tax_assessed_value"):
		facts["assessed_value"] = zillow["tax_assessed_value"]
		facts["source"]["assessed_value"] = "zillow"
	return facts


# ---------------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------------
def _num(v):
	try:
		n = float(v)
	except (TypeError, ValueError):
		return None
	return n if math.isfinite(n) else None


def _is_active(status) -> bool:
	"""Exact match — a substring test would read "Inactive" as active."""
	return str(status or "").strip().lower() == "active"


def _recency_days(row, today):
	"""Days since this comp last said anything about the market.

	An off-market comp is dated by when it LEFT (the transaction); a live one by
	when it listed. Same rule the map's fade uses, so a comp that looks fresh can
	never be filtered out as stale, or vice versa.
	"""
	if _is_active(row.get("status")):
		if row.get("listed_date"):
			return max(0, frappe.utils.date_diff(today, row["listed_date"]))
		dom = _num(row.get("days_on_market"))
		return dom if dom is not None else None
	if row.get("removed_date"):
		return max(0, frappe.utils.date_diff(today, row["removed_date"]))
	age = _num(row.get("days_old"))
	if age is not None and age > 0:
		return age
	if row.get("listed_date"):
		return max(0, frappe.utils.date_diff(today, row["listed_date"]))
	return None


def _coerce_filters(filters):
	"""Accept a dict or a JSON string (frappe hands whitelisted args either way)."""
	if not filters:
		return None
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except Exception:
			return None
	return filters if isinstance(filters, dict) else None


def _matches(row, f, today):
	"""Does one comp pass the filter set? Unknown values never silently fail.

	The importer coerces a missing number to 0, so 0 means "not known", not "zero
	bedrooms". A comp with an unknown year built is not evidence that it fails a
	year filter — excluding it would quietly drop real sales for missing metadata,
	so an unknown passes and the pin carries no claim about that fact.
	"""
	status = f.get("status")
	if status == "sold" and _is_active(row.get("status")):
		return False
	if status == "active" and not _is_active(row.get("status")):
		return False

	# Recency gates OFF-MARKET comps ONLY. A sale from three years ago is a
	# different market, but a house that has sat on the market for 18 months is
	# live evidence about what is being asked TODAY — dropping it for being "old"
	# would hide exactly the stale listings that tell you the area is not moving.
	within = _num(f.get("within_days"))
	if within and not _is_active(row.get("status")):
		days = _recency_days(row, today)
		if days is not None and days > within:
			return False

	for key, field in (
		("beds", "bedrooms"), ("baths", "bathrooms"),
		("sqft", "square_footage"), ("year", "year_built"), ("price", "price"),
	):
		val = _num(row.get(field))
		if not val:  # 0 / None == unknown
			continue
		lo, hi = _num(f.get(f"{key}_min")), _num(f.get(f"{key}_max"))
		if lo is not None and val < lo:
			return False
		if hi is not None and val > hi:
			return False

	types = f.get("property_types")
	if types:
		if isinstance(types, str):
			types = [types]
		wanted = {str(t).strip().lower() for t in types if str(t).strip()}
		if wanted:
			actual = str(row.get("property_type") or "").strip().lower()
			if actual and actual not in wanted:
				return False
	return True


def _widen(band, pct=None, flat=None, floor=None):
	"""Grow an interval by a fraction of it and/or a flat amount.

	The band is the SOURCE's interval, so widening a wide band stays wide: a
	seller who said "1000 - 2000 sqft" gets a looser filter than a listing that
	said 1406, which is the correct amount of confidence in each.
	"""
	if not band:
		return None, None
	lo, hi = band[0], band[1]
	span = (hi - lo) if hi is not None else 0
	base = hi if hi is not None else lo
	pad = 0.0
	if pct:
		pad += base * pct
	if flat:
		pad += flat
	lo_out = max(floor, lo - pad) if floor is not None else lo - pad
	hi_out = None if hi is None else hi + pad
	return round(lo_out, 2), (None if hi_out is None else round(hi_out, 2))


def _preset_tiers(facts, radius):
	"""Filters pre-set around THIS property, tightest first.

	Each tier is a complete filter set, not a delta, so what the UI shows is
	exactly what ran. The ladder exists because a strict "recent and similar"
	filter genuinely matches nothing on plenty of leads — our comps are a pooled
	area index, not a curated per-lead set — and an empty map with no explanation
	is worse than a loose one that says so.
	"""
	beds, baths = facts.get("beds_band"), facts.get("baths_band")
	sqft, year = facts.get("sqft_band"), facts.get("year_built_band")
	ptype = facts.get("property_type")
	has_shape = any((beds, baths, sqft, year))

	tiers = []

	def tier(key, label, within, b_pad, ba_pad, sq_pct, yr_pad, use_type):
		f = {"status": "all", "within_days": within, "radius_mi": radius}
		if b_pad is not None:
			f["beds_min"], f["beds_max"] = _widen(beds, flat=b_pad, floor=0)
		if ba_pad is not None:
			f["baths_min"], f["baths_max"] = _widen(baths, flat=ba_pad, floor=0)
		if sq_pct is not None:
			f["sqft_min"], f["sqft_max"] = _widen(sqft, pct=sq_pct, floor=0)
		if yr_pad is not None:
			f["year_min"], f["year_max"] = _widen(year, flat=yr_pad)
		if use_type and ptype:
			f["property_types"] = [ptype]
		tiers.append({"key": key, "label": label, "filters": f})

	# The window opens at 12 months and STAYS there while the shape loosens: a
	# poorly-matching sale from this year beats a well-matching one from 2022, so
	# similarity is spent before recency is.
	if has_shape:
		tier("similar", _("Last 12 months · similar"), DEFAULT_WITHIN_DAYS, 1, 1, 0.25, 20, True)
		tier("wider", _("Last 12 months · wider"), DEFAULT_WITHIN_DAYS, 1, 1, 0.35, 30, True)
		tier("loose", _("Last 2 years · loosely similar"), 730, 2, None, 0.50, None, False)
	else:
		# Nothing is known about the subject, so "similar" has no meaning here and
		# pretending otherwise would filter on air. Recency is still real.
		tier("recent", _("Last 12 months"), DEFAULT_WITHIN_DAYS, None, None, None, None, False)
		tier("loose", _("Last 2 years"), 730, None, None, None, None, False)
	tiers.append({
		"key": "all",
		"label": _("Everything nearby"),
		"filters": {"status": "all", "radius_mi": radius},
	})
	return tiers


def _rental_preset_tiers(facts, radius):
	"""Rental ladder: similarity only. Recency is for sales; these are live asks."""
	beds, baths = facts.get("beds_band"), facts.get("baths_band")
	sqft, year = facts.get("sqft_band"), facts.get("year_built_band")
	ptype = facts.get("property_type")
	has_shape = any((beds, baths, sqft, year))

	tiers = []

	def tier(key, label, b_pad, ba_pad, sq_pct, yr_pad, use_type):
		f = {"status": "active", "radius_mi": radius}
		if b_pad is not None:
			f["beds_min"], f["beds_max"] = _widen(beds, flat=b_pad, floor=0)
		if ba_pad is not None:
			f["baths_min"], f["baths_max"] = _widen(baths, flat=ba_pad, floor=0)
		if sq_pct is not None:
			f["sqft_min"], f["sqft_max"] = _widen(sqft, pct=sq_pct, floor=0)
		if yr_pad is not None:
			f["year_min"], f["year_max"] = _widen(year, flat=yr_pad)
		if use_type and ptype:
			f["property_types"] = [ptype]
		tiers.append({"key": key, "label": label, "filters": f})

	if has_shape:
		tier("similar", _("Similar rentals"), 1, 1, 0.25, 20, True)
		tier("wider", _("Wider rentals"), 1, 1, 0.35, 30, True)
		tier("loose", _("Loosely similar rentals"), 2, None, 0.50, None, False)
	else:
		tier("nearby", _("Nearby rentals"), None, None, None, None, False)
	tiers.append({
		"key": "all",
		"label": _("Every rental nearby"),
		"filters": {"status": "active", "radius_mi": radius},
	})
	return tiers


# ---------------------------------------------------------------------------------
# Human judgement: hidden / selected comps
# ---------------------------------------------------------------------------------
def _state_supported(dt="CRM Lead") -> bool:
	"""False until the ops script adds the fields; everything degrades quietly.

	`dt` is the SUBJECT doctype — a scratch CRM Property carries the same
	columns from birth, so it must not be gated on whether the LEAD has them."""
	return frappe.db.has_column(dt, HIDDEN_FIELD) and frappe.db.has_column(dt, SELECTED_FIELD)


def _sqft_override_supported(dt="CRM Lead") -> bool:
	"""False until the ops script adds `sqft_override`; facts fall back to Zillow."""
	return frappe.db.has_column(dt, SQFT_FIELD)


def _sqft_override(doc):
	"""The rep-entered subject sqft, or None. 0/blank/garbage all mean none."""
	if not _sqft_override_supported(doc.doctype):
		return None
	val = _num(doc.get(SQFT_FIELD))
	return int(val) if val and val > 0 else None


@frappe.whitelist()
def set_subject_sqft(lead, sqft=None):
	"""Set (or clear) the subject's manual square footage. Team-wide, like picks.

	Blank/0 clears the override, reverting the facts to Zillow/listing/lead. The
	column is an Int and Frappe declares those NOT NULL, so "cleared" is stored as
	0 — which is falsy and never a real square footage, so nothing downstream can
	mistake it for a measurement.
	"""
	_guard()
	if not frappe.db.exists(subject_doctype(lead), lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if not _sqft_override_supported(subject_doctype(lead)):
		return {"ok": False, "error": "sqft_override field is missing"}

	val = 0
	if sqft not in (None, ""):
		n = _num(sqft)
		if n is None or n < 0 or n > 100000:
			frappe.throw(_("Square footage must be a positive number."))
		val = int(n)

	# db.set_value, not doc.save: same reasoning as set_comp_state — a fact
	# correction must not run SLA/assignment hooks or read as a lead edit.
	frappe.db.set_value(subject_doctype(lead), lead, SQFT_FIELD, val, update_modified=False)
	return {"ok": True, "sqft": val or None}


def _types_supported(dt="CRM Lead") -> bool:
	"""False until the ops script adds `comps_types`; tags degrade quietly."""
	return frappe.db.has_column(dt, TYPES_FIELD)


def _load_types(doc) -> dict:
	"""comp docname -> condition label for this lead. Unknown labels are dropped
	on read, so a bad historical write can never render an off-vocabulary chip."""
	if not _types_supported(doc.doctype):
		return {}
	raw = doc.get(TYPES_FIELD)
	if not raw:
		return {}
	try:
		val = json.loads(raw)
	except Exception:
		return {}
	if not isinstance(val, dict):
		return {}
	out = {}
	for k, v in val.items():
		label = LEGACY_CONDITION_TYPES.get(str(v), str(v))
		if label in COMP_CONDITION_TYPES:
			out[str(k)] = label
	return out


@frappe.whitelist()
def set_comp_type(lead, comp, comp_type=None):
	"""Tag one picked comp's condition (New Build … Teardown), or clear it.

	Optional by design — picking a comp stays one click and this is a second,
	separate judgement. Team-wide for the same reason the picks are: "the comps we
	priced off, and what state they were in" is a deal artifact, not a view setting.
	"""
	_guard()
	if not frappe.db.exists(subject_doctype(lead), lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if not _types_supported(subject_doctype(lead)):
		return {"ok": False, "error": "comps_types field is missing"}

	ct = (comp_type or "").strip()
	if ct and ct not in COMP_CONDITION_TYPES:
		frappe.throw(_("Unknown comp condition {0}").format(ct))

	doc = _load_subject(lead)
	types = _load_types(doc)
	comp = str(comp)
	if ct:
		types[comp] = ct
	else:
		types.pop(comp, None)

	frappe.db.set_value(
		subject_doctype(lead), lead, TYPES_FIELD, json.dumps(types, sort_keys=True), update_modified=False
	)
	return {"ok": True, "comp": comp, "comp_type": ct or None}


def _load_list(doc, field):
	raw = doc.get(field)
	if not raw:
		return []
	try:
		val = json.loads(raw)
		return [str(x) for x in val] if isinstance(val, list) else []
	except Exception:
		return []


def _comp_state(doc):
	"""(hidden, selected) as sets of comp docnames for this lead."""
	if not _state_supported(doc.doctype):
		return set(), set()
	return set(_load_list(doc, HIDDEN_FIELD)), set(_load_list(doc, SELECTED_FIELD))


def _parse_state_override(state):
	"""Optional hidden/selected override. None = use the lead's own lists.

	Practice passes a dict (even empty) so a trainee starts from a virgin map
	and their picks never touch the real lead. A missing/blank argument keeps
	every existing caller on the team-wide lists.
	"""
	if state in (None, ""):
		return None
	if isinstance(state, str):
		try:
			state = json.loads(state)
		except Exception:
			frappe.throw(_("Invalid comp state."))
	if not isinstance(state, dict):
		frappe.throw(_("Invalid comp state."))
	hidden = {str(x) for x in (state.get("hidden") or [])}
	selected = {str(x) for x in (state.get("selected") or [])}
	return hidden, selected


@frappe.whitelist()
def set_comp_state(lead, comp, state):
	"""Mark one comp as `selected`, `hidden`, or `none` for this lead.

	Deliberately TEAM-WIDE rather than per-user: a comp that is obviously not
	comparable is wrong for everyone, and the set someone actually priced the deal
	on is a deal artifact the next person needs to see, not a private view setting.

	The two states are mutually exclusive — picking a comp you hid un-hides it,
	which is the only sane reading of the two clicks.
	"""
	_guard()
	if state not in ("selected", "hidden", "none"):
		frappe.throw(_("Unknown comp state {0}").format(state))
	if not frappe.db.exists(subject_doctype(lead), lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if not _state_supported(subject_doctype(lead)):
		return {"ok": False, "error": "comps_hidden/comps_selected fields are missing"}

	doc = _load_subject(lead)
	hidden, selected = _comp_state(doc)
	comp = str(comp)
	hidden.discard(comp)
	selected.discard(comp)
	if state == "hidden":
		hidden.add(comp)
	elif state == "selected":
		selected.add(comp)

	# db.set_value, not doc.save: this is a view judgement on a lead, and running
	# the whole CRM Lead save path (SLA, hooks, assignment) for it would be absurd.
	frappe.db.set_value(
		subject_doctype(lead), lead,
		{
			HIDDEN_FIELD: json.dumps(sorted(hidden)),
			SELECTED_FIELD: json.dumps(sorted(selected)),
		},
		update_modified=False,
	)
	return {"ok": True, "hidden": len(hidden), "selected": len(selected), "state": state}


@frappe.whitelist()
def add_comp(lead, text):
	"""Add a house as a selected comparable for this subject (lead or PROP-).

	`text` is a street address or a Zillow / Redfin / Realtor / Auction.com
	listing URL. We geocode it (Census, free), create a CRM Comp if this
	address is not already in the pool, and mark it selected. Price/beds are
	left blank unless we already had the row — never invented.
	"""
	from crm.api.listing_url import looks_like_url, parse_listing_url

	_guard()
	if not frappe.db.exists(subject_doctype(lead), lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if not _state_supported(subject_doctype(lead)):
		return {"ok": False, "error": "comps_hidden/comps_selected fields are missing"}
	text = " ".join(str(text or "").split())
	if not text:
		frappe.throw(_("Paste an address or a listing URL first."))

	city = state = zip_code = ""
	if looks_like_url(text):
		parsed = parse_listing_url(text)
		if not parsed:
			frappe.throw(
				_(
					"Could not read an address from that link. Paste a Zillow, Redfin, "
					"Realtor or Auction.com property page, or type the street address."
				)
			)
		address = parsed["address"]
		city = parsed.get("city") or ""
		state = (parsed.get("state") or "").upper()[:2]
		zip_code = parsed.get("zip") or ""
	else:
		address = text

	line = ", ".join(p for p in (address, city, state, zip_code) if p)
	point = _census_geocode(line)
	if not point:
		frappe.throw(_("Could not find that address on the map. Check the street, city and ZIP."))
	key = address_key(line)
	existing = frappe.db.get_value(DOCTYPE, {"address_key": key}, "name")
	if existing:
		name = existing
		created = False
	else:
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"address_key": key,
				"address": address,
				"city": city,
				"state": state,
				"zip": zip_code,
				"lat": point[0],
				"lng": point[1],
				"status": "Inactive",
				"source_lead": f"manual:{lead}",
			}
		)
		doc.insert(ignore_permissions=True)
		name = doc.name
		created = True
	mark = set_comp_state(lead, name, "selected")
	if mark.get("ok") is False:
		return mark
	return {"ok": True, "name": name, "created": created, "address": address}


# ---------------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------------
def _detail_cache_key(comp, version=None):
	return f"crm:comp-detail:v{version or DETAIL_CACHE_VERSION}:{comp}"


def _detail_cached(comp):
	"""The current-version gallery, or an older generation carried forward.

	Same idea as `zillow_comps._cache_get_migrated`: a version bump should not
	re-bill every gallery anyone has opened when the old blob still answers. A
	migrated entry is re-written under the current key for the retry window only,
	not the full month, because Redis owns these TTLs and the old one is unknown.
	"""
	cache = frappe.cache()
	cached = cache.get_value(_detail_cache_key(comp))
	if isinstance(cached, dict):
		return cached
	steps, v = [], DETAIL_CACHE_VERSION
	while (v - 1) in DETAIL_MIGRATIONS:
		v -= 1
		steps.append(DETAIL_MIGRATIONS[v])
		old = cache.get_value(_detail_cache_key(comp, v))
		if not isinstance(old, dict):
			continue
		for step in reversed(steps):
			old = step(old)
			if old is None:
				return None
		try:
			cache.set_value(_detail_cache_key(comp), old, expires_in_sec=DETAIL_RETRY_SECONDS)
		except Exception:
			pass
		return old
	return None


def _zillow_detail(row, zpid):
	"""`(details, zillow_photos)` for one house -- the billed half of a gallery.

	Cached for the month as soon as Zillow answers; a failed CALL (no body) is
	remembered for the retry window only, so a timeout does not lock the house
	out for 30 days. Keyed on the comp name plus zpid so the subject (whose zpid
	comes from its cached facts) and a `zillow::` pin cannot collide.

	Billed calls go through PropWarehouse when the facts API is up; RapidAPI is
	the fallback for an old scraper image or a missing key on that box.
	"""
	from crm.api import vendor_facts
	from crm.api import zillow as zillow_api

	name = str(row.get("name") or "")
	key = f"crm:comp-detail:zillow:v{DETAIL_ZILLOW_CACHE_VERSION}:{name}|{zpid or ''}"
	cached = frappe.cache().get_value(key)
	if isinstance(cached, dict) and "details" in cached:
		return cached.get("details"), list(cached.get("photos") or [])

	raw = photo_raw = None
	answered = False
	owned, env = vendor_facts.payload_or_fallback(
		vendor_facts.zillow_property(address=row.get("address"), zpid=zpid, photos=True)
	)
	if owned:
		raw = (env or {}).get("payload")
		photo_raw = (env or {}).get("photos")
		# Warehouse already remembers a miss for 7d. Don't pin an empty gallery
		# in Redis for a month on top of that.
	elif zpid:
		raw = zillow_api._request("/property", {"zpid": zpid}, "Zillow: zpid lookup failed")
		answered = raw is not None
	else:
		raw = zillow_api.property_details(row.get("address"))
		answered = raw is not None
	details = zillow_api.normalize_detail(raw) if raw else None
	# Zillow's /property returns an EMPTY SHELL (every field null, even zpid) for
	# some listings its own /search happily returned — observed on a pending Philly
	# row, by zpid AND by address. When the zpid path came back hollow and we know
	# the address, one address retry is worth the spend before giving up on Zillow.
	if not owned and not details and zpid and (row.get("address") or "").strip():
		raw = zillow_api.property_details(row.get("address"))
		answered = answered or raw is not None
		details = zillow_api.normalize_detail(raw) if raw else None
	if not owned:
		photo_raw = zillow_api.property_photos(details.get("zpid")) if details else None
	photos = zillow_api.photo_urls(photo_raw)
	if details and details.get("cover_photo") and not photos:
		photos = [details["cover_photo"]]
	if owned:
		answered = bool(details)
	try:
		frappe.cache().set_value(
			key,
			{"details": details, "photos": photos},
			expires_in_sec=DETAIL_CACHE_SECONDS if answered else DETAIL_RETRY_SECONDS,
		)
	except Exception:
		pass
	return details, photos


def _detail_address(row, details=None):
	"""Street + city/state/zip the Realtor photos lookup can resolve."""
	parts = []
	if details:
		parts = [
			details.get("address"),
			details.get("city"),
			details.get("state"),
			details.get("zip"),
		]
	if not any(parts):
		parts = [
			row.get("address"),
			row.get("city"),
			row.get("state"),
			row.get("zip"),
		]
	return " ".join(str(p).strip() for p in parts if p and str(p).strip())


def _shape_detail(row, zpid=None):
	"""Fetch and normalize one property only after a person explicitly opens it.

	`zpid` short-circuits address resolution when the caller already holds one (the
	subject does, from its cached facts). A zpid is an exact identifier where an
	address string is a guess Zillow has to re-resolve, so it is both cheaper and
	more reliable when available.
	"""
	row = shape_pool_row(row)
	if is_adc(row) and not qualified_address(row):
		return {"available": False, "comp": row, "details": None, "photos": [],
			"photos_available": False, "photo_source": "", "redfin_url": None,
			"message": _("ADC comp lacks a full locality-qualified address; lookup skipped.")}
	name = str(row.get("name") or "")
	if not zpid and name.startswith("zillow::"):
		zpid = name.split("::", 1)[1]
	# The Redfin LINK is the slowest thing in a gallery open and the least
	# important thing in it. Measured on prod (819 Rosehill, 2026-09-10): Zillow
	# facts + photos 0.39s, then `/url` on redfin-scraper-api 6.3s -- the KNN
	# ordering that service documents -- run SERIALLY after them, so a rep waited
	# ~7s for photos that were ready in under half a second, to get a link. Start
	# it FIRST on a thread and wait only REDFIN_URL_BUDGET past Zillow; a late
	# answer is written into the cached entry by a background job, so the second
	# open has the link and the first one has its photos.
	url_job = _start_redfin_url(_detail_address(row), row.get("lat"), row.get("lng"))
	details, photos = _zillow_detail(row, zpid)
	# Dedupe BEFORE the thin-gallery check: a photo-less home whose "gallery" is
	# two sizes of the same synthesized Street View frame is really ONE photo, and
	# counting it as two suppressed the Realtor fallback that could do better.
	photos = _dedupe_streetview(photos)
	# Off-market Zillow galleries often have a single leftover frame. The photo
	# ladder is Zillow -> Realtor (apivex) -> Redfin: each rung fires only while
	# the gallery is still ≤1 image, on an explicit open, never for the tray, and
	# the winner rides the 30-day detail cache. Absent its key, a rung no-ops.
	photo_source = "zillow" if photos else ""
	from crm.api import redfin

	# Coordinates come from the row (CRM Comp / BatchData / client-passed for
	# zillow:: pins), falling back to the fresh Zillow detail's own point.
	addr = _detail_address(row, details)
	lat = row.get("lat") or (details or {}).get("lat")
	lng = row.get("lng") or (details or {}).get("lng")
	# Listing URL is independent of the photo ladder: a 45-photo Zillow gallery
	# still needs a Redfin link. Thin galleries reuse /photos (url + CDN).
	redfin_url, url_pending = None, False
	if len(photos) <= 1:
		from crm.api import apivex

		realtor = apivex.realtor_photo_urls(addr)
		if len(realtor) > len(photos):
			photos = realtor
			photo_source = "realtor"
	if len(photos) <= 1:
		rf = redfin.redfin_gallery(addr, lat=lat, lng=lng)
		redfin_url = rf.get("url")
		if len(rf["photos"]) > len(photos):
			photos = rf["photos"]
			photo_source = "redfin"
	else:
		redfin_url, url_pending = _finish_redfin_url(url_job, addr, lat, lng)

	comp = dict(row)
	if is_adc(comp) and details:
		from crm.api.zillow_comps import _state_from_facts
		state = _state_from_facts(details)
		if not state:
			state = {"SOLD": "sold", "RECENTLY_SOLD": "sold", "OFF_MARKET": "off_market"}.get(
				str(details.get("home_status") or "").upper())
		if state:
			comp["listing_state"] = state
			comp["current_status_source"] = "zillow"
	for key in ("listed_date", "removed_date"):
		if comp.get(key):
			comp[key] = str(comp[key])
	return {
		"available": bool(details),
		"comp": comp,
		"details": details,
		"photos": photos,
		"photos_available": len(photos) > 0,
		"photo_source": photo_source,
		"redfin_url": redfin_url,
		"redfin_url_pending": url_pending,
		"redfin_url_point": [addr, lat, lng] if url_pending else None,
		"message": "" if details else _("Zillow details are unavailable for this property."),
	}


#: How long past the Zillow calls a gallery open waits for the Redfin link. The
#: link is a courtesy; the photos are the click. Same shape as the subject's
#: `finish_subject_record` budget.
REDFIN_URL_BUDGET = 1.0


def _start_redfin_url(address, lat, lng):
	"""Kick off the Redfin listing-URL lookup on a thread, or None when the row
	cannot be looked up (no point, no service). The thread is pure `requests`:
	`frappe.local` is thread-local, so nothing frappe may run in there -- the
	wrapper swallows everything, including the `frappe.log_error` the inline
	path would have written, because a slow link service is not an Error Log
	row per gallery open."""
	from crm.api import redfin

	# Config is read HERE, on the request thread; the thread gets plain values.
	base = redfin._base_url()
	point = redfin._point(address, lat, lng)
	if not base or not point:
		return None
	addr, plat, plng = point
	holder = {}

	def _run():
		try:
			holder["url"] = redfin._fetch_listing_url(base, addr, plat, plng)
		except Exception as e:  # noqa: BLE001 -- thread must never raise
			holder["error"] = str(e)

	thread = threading.Thread(target=_run, daemon=True)
	thread.start()
	return {"thread": thread, "holder": holder}


def _finish_redfin_url(job, addr, lat, lng, budget=None):
	"""-> (url, pending). Waits `budget` for the started thread; a job that
	could not start (row had no point) falls back to the old inline lookup so
	nothing that resolved before stops resolving."""
	from crm.api import redfin

	if job is None:
		return redfin.redfin_listing_url(addr, lat, lng), False
	job["thread"].join(timeout=max(0.05, float(REDFIN_URL_BUDGET if budget is None else budget)))
	if job["thread"].is_alive():
		return None, True
	return job["holder"].get("url"), False


def _enqueue_redfin_url_fill(result, key, ttl):
	"""After a cache write: if the Redfin link was still in flight, have a worker
	finish it and patch the cached entry. Never raises -- the gallery is already
	on its way to the browser."""
	if not result.get("redfin_url_pending"):
		return
	point = result.get("redfin_url_point") or [None, None, None]
	try:
		frappe.enqueue(
			"crm.api.comps.fill_detail_redfin_url",
			queue="short",
			job_name=f"redfin-url-{key}",
			key=key,
			address=point[0],
			lat=point[1],
			lng=point[2],
			ttl=ttl,
		)
	except Exception:
		pass


def fill_detail_redfin_url(key, address, lat, lng, ttl):
	"""Background half of `_finish_redfin_url`'s timeout path: the same lookup
	with the full service timeout, written into the entry the request cached.
	The entry keeps its remaining TTL, so a link does not extend a gallery's
	month."""
	from crm.api import redfin

	cache = frappe.cache()
	cached = cache.get_value(key)
	if not isinstance(cached, dict) or not cached.get("redfin_url_pending"):
		return
	url = redfin.redfin_listing_url(address, lat, lng)
	cached = {**cached, "redfin_url": url, "redfin_url_pending": False, "redfin_url_point": None}
	try:
		# `ttl` is raw redis, so it needs the site-prefixed key get/set_value use.
		remaining = int(cache.ttl(cache.make_key(key)) or 0)
	except Exception:
		remaining = 0
	cache.set_value(key, cached, expires_in_sec=remaining if remaining > 0 else ttl)


def _dedupe_streetview(photos):
	"""Collapse provider-synthesized Street View frames of the same spot.

	The RapidAPI Zillow provider fabricates `maps.googleapis.com/…/streetview`
	URLs for photo-less homes — signed with ITS OWN shared, quota-limited Google
	key — and serves the SAME shot at several sizes, so a subject gallery read
	"2 / 2" with both frames identical. Keep the largest per location; the rest
	add nothing but extra rolls of the quota dice.
	"""
	from urllib.parse import parse_qs, urlparse

	best = {}
	keyed = []
	for i, url in enumerate(photos or []):
		loc = None
		try:
			p = urlparse(url)
			if p.netloc == "maps.googleapis.com" and "/streetview" in p.path:
				q = parse_qs(p.query)
				loc = (q.get("location") or [""])[0] or None
				size = (q.get("size") or ["0x0"])[0]
				try:
					w, h = size.lower().split("x")
					area = int(w) * int(h)
				except Exception:
					area = 0
		except Exception:
			loc = None
		keyed.append(loc)
		if loc is not None and (loc not in best or area > best[loc][0]):
			best[loc] = (area, i)
	return [
		url
		for i, url in enumerate(photos or [])
		if keyed[i] is None or best[keyed[i]][1] == i
	]


def _batchdata_row(lead, comp):
	"""Resolve a `batchdata::` comp back to its shaped row.

	BatchData fallback comps are not CRM Comp docs — they live in the lead's
	`batchdata_comps` cache blob. Looking the name up in CRM Comp therefore threw
	DoesNotExistError, which broke BOTH photo surfaces for every BatchData comp:
	the tray's hover prefetch and the detail modal ("Comparable property
	batchdata::… does not exist"). `fetch_for_lead` reads the cached blob for free;
	on a cache miss it re-runs the paid search, which is the documented no-cache
	contract ("works but re-bills") and cannot normally happen right after the map
	just rendered the comp.
	"""
	from crm.api import batchdata_comps

	try:
		doc = _load_subject(lead)
		rows = batchdata_comps.fetch_for_lead(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "BatchData comp detail lookup failed")
		return None
	for r in rows or []:
		if r.get("name") == comp:
			return frappe._dict(r)
	return None


@frappe.whitelist()
def get_comp_details(lead, comp, address=None, lat=None, lng=None):
	"""On-demand Zillow facts + scrollable photos for one comp.

	The compact Today view already has the comparison facts from `CRM Comp`; this
	endpoint is deliberately lazy so merely opening a lead never spends one or two
	third-party calls PER HOUSE. Results are cached by the immutable comp name for
	30 days. Access remains sales-role gated and the lead anchor must be real.
	"""
	_guard()
	if not frappe.db.exists(subject_doctype(lead), lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if not _available():
		return {"available": False, "comp": None, "details": None, "photos": []}

	if str(comp).startswith("zillow::") or str(comp).startswith("zillow-rent::"):
		# Area-search pins are not CRM Comp rows. _shape_detail looks them up by zpid.
		# The caller's address matters more than it looks: when Zillow's /property
		# returns an empty shell for the zpid (it does, on some pending listings),
		# the address is the only route left — a Zillow address retry, then the
		# Realtor photo fallback, both of which no-op on an empty string. The
		# coordinates are what the Redfin rung sweeps around.
		row = frappe._dict({"name": comp, "address": str(address or "").strip(), "lat": lat, "lng": lng})
	elif str(comp).startswith("batchdata::"):
		# BatchData fallback pins are not CRM Comp rows either — resolve from the
		# lead's cached blob; _shape_detail then looks Zillow up by address.
		row = _batchdata_row(lead, comp)
	else:
		row = frappe.db.get_value(
			DOCTYPE,
			comp,
			[
				"name", "address", "city", "state", "zip", "price", "status",
				"listed_date", "removed_date", "days_on_market", "days_old",
				"bedrooms", "bathrooms", "square_footage", "year_built", "property_type",
				"lat", "lng", "source_lead",
			],
			as_dict=True,
		)
	if not row:
		frappe.throw(_("Comparable property {0} does not exist.").format(comp), frappe.DoesNotExistError)

	cache_comp = f"{comp}:adc-v1" if is_adc(row) else comp
	key = _detail_cache_key(cache_comp)
	cached = _detail_cached(cache_comp)
	if isinstance(cached, dict):
		# A remembered MISS written before the caller supplied an address is worth
		# one address-armed retry — otherwise Retry serves the same failure for the
		# whole TTL. `address_tried` stops a genuinely photo-less comp from being
		# re-billed on every hover: after one armed attempt the miss caches again.
		retry_with_address = (
			str(address or "").strip()
			and not (cached.get("photos") or [])
			and not cached.get("address_tried")
		)
		if not retry_with_address:
			# Dedupe on READ so entries cached before _dedupe_streetview existed
			# get the fix without a version bump (which would re-bill every gallery).
			return {**cached, "photos": _dedupe_streetview(cached.get("photos")), "cached": True}

	result = _shape_detail(row)
	result["cached"] = False
	result["address_tried"] = bool(str(address or "").strip() or row.get("address"))
	# A full gallery is stable. One leftover Zillow frame (or a failed Realtor
	# fallback) is not — retry soon rather than freezing a thin gallery for a month.
	# A Realtor gallery WITHOUT Zillow details (hollow-shell zpid) still counts:
	# the photos are what the click was for, and the facts panel has the comp row.
	full = len(result.get("photos") or []) > 1
	ttl = DETAIL_CACHE_SECONDS if full else DETAIL_RETRY_SECONDS
	frappe.cache().set_value(key, result, expires_in_sec=ttl)
	_enqueue_redfin_url_fill(result, key, ttl)
	# Do not read the cache again in this request: Frappe memoizes cache misses in
	# `frappe.local.cache`, and an expiring set does not replace that local miss.
	return result


@frappe.whitelist()
def get_photo_date(url=None):
	"""DateTimeOriginal for one gallery JPEG. The pictures themselves do not wait."""
	_guard()
	from crm.api.photo_exif import date_for_url

	return {"exif": date_for_url(url)}


#: The radius the client actually opens with (`CompsView`'s `radius` ref). Warming
#: any other circle would populate a cache key nobody reads.
WARM_RADIUS_MI = 0.5


def warm_lead_area(lead):
	"""Pull one lead's Zillow AREA search into cache before anybody asks for it.

	The AREA SEARCH ONLY, and that is the whole design. Measured on prod, a cold
	comps open is ~8.3 RapidAPI calls: roughly 3.3 of them are this circle and the
	rest are per-pin `/property` lookups. The circle is the half worth prewarming
	twice over --

	  * it is the SLOW half: it pages and price-splits SERIALLY, because each step
	    needs the previous one's `totalPages` before it can ask for anything;
	  * the pins are the EXPENSIVE half: more calls, on a key shared with
	    istl-buyer, and they are already fast (parallel: 21.3s -> 0.6s).

	So this buys most of the wait for about a third of the spend. A cold open goes
	from ~14s worst case to ~5s, and from ~5s typical to well under one.

	Returns what it did, so the sweep can report real numbers rather than claiming
	success for a lead it skipped.
	"""
	from crm.api import zillow_comps

	if not _available():
		return {"warmed": False, "reason": "no_comps_doctype"}
	doc = _load_subject(lead)
	# Geocoding is Census, not RapidAPI — free, and cached on the lead — so a lead
	# that has never been located gets that out of the way here too.
	lat, lng, _cached_point = _subject_point(doc)
	if lat is None:
		return {"warmed": False, "reason": "no_location"}
	if zillow_comps.area_is_cached(lat, lng, WARM_RADIUS_MI):
		return {"warmed": False, "reason": "already_warm"}

	area = zillow_comps.area_comps(lat, lng, WARM_RADIUS_MI)
	return {
		"warmed": True,
		"sold": len(area.get("sold") or []),
		"for_sale": len(area.get("for_sale") or []),
		# False means the circle came back partial, so it is cached for a day rather
		# than a week and a later sweep will retry it.
		"complete": bool(area.get("cached")),
	}


@frappe.whitelist()
def warm_lead_comps(lead):
	"""Manual/whitelisted wrapper, for testing a single lead by hand."""
	_guard()
	if not frappe.db.exists(subject_doctype(lead), lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	return warm_lead_area(lead)


@frappe.whitelist()
def get_subject_details(lead):
	"""The SUBJECT's own photos and Zillow facts, in the comp detail shape.

	The subject pin only ever offered a popup of numbers, so the one house a rep is
	actually being asked to price was the one house on the board they could not
	look at. Every comp opens a gallery; this makes the subject open the same one.

	Deliberately the SAME lazy contract as `get_comp_details` — two billed calls,
	on an explicit click, cached 30 days — rather than folding photos into
	`get_lead_comps`, which every comps open pays for whether anyone looks or not.

	It reuses `_shape_detail`, so the panel that renders a comp renders this too and
	the two cannot drift apart. The zpid comes from the lead's already-cached facts,
	which skips re-resolving an address Zillow has resolved before.
	"""
	_guard()
	doc = _load_subject(lead)

	from crm.api import zillow as zillow_api

	# Free past the first open: cached on the lead for 30 days.
	facts = zillow_api.facts_for_lead(doc) or {}
	zpid = str(facts.get("zpid") or "")
	address = _full_address(doc)
	if not zpid and not address:
		return {
			"available": False,
			"comp": None,
			"details": None,
			"photos": [],
			"message": _("This lead has no property address yet."),
		}

	# Keyed on the zpid where we have one so two leads on the same house share the
	# lookup, and on the lead otherwise — never on the raw address, which changes
	# whenever somebody tidies up the punctuation on a lead.
	key = _detail_cache_key(f"subject::{zpid or lead}")
	cached = _detail_cached(f"subject::{zpid or lead}")
	if isinstance(cached, dict):
		# Same read-side dedupe as get_comp_details — pre-fix cached subjects hold
		# the same Street View shot twice.
		return {**cached, "photos": _dedupe_streetview(cached.get("photos")), "cached": True}

	row = frappe._dict(
		{
			"name": f"subject::{lead}",
			"address": address,
			"city": doc.get("property_city") or "",
			"state": doc.get("property_state") or "",
			"zip": doc.get("property_zip") or "",
			# For the Redfin photo rung. Cached coords only — 0.0 means ungeocoded
			# (truthiness, the _subject_point rule), and geocoding here would spend
			# a Census call on a gallery open.
			"lat": doc.get("property_lat") or None,
			"lng": doc.get("property_lng") or None,
			# The subject is not for sale and has no comp price; the panel reads its
			# money off `details` (Zestimate, last sale) exactly as it does for a comp
			# whose own price is unknown.
			"price": None,
			"status": "",
			"bedrooms": facts.get("beds"),
			"bathrooms": facts.get("baths"),
			"square_footage": facts.get("sqft"),
			"year_built": facts.get("year_built"),
			"property_type": facts.get("property_type"),
			"is_subject": True,
		}
	)
	result = _shape_detail(row, zpid=zpid or None)
	cover = facts.get("cover_photo") or ""
	photos = list(result.get("photos") or [])
	if cover and cover not in photos:
		# The cover is often the 400px variant of a Street View frame the gallery
		# already holds at 1152px — dedupe after prepending, not just string-compare.
		photos = _dedupe_streetview([cover] + photos)
		result["photos"] = photos
		result["photos_available"] = bool(photos)
	result["cached"] = False
	full = result.get("available") and len(result.get("photos") or []) > 1
	ttl = DETAIL_CACHE_SECONDS if full else DETAIL_RETRY_SECONDS
	frappe.cache().set_value(key, result, expires_in_sec=ttl)
	_enqueue_redfin_url_fill(result, key, ttl)
	return result


def _zillow_match(doc, subject=None):
	"""Did Zillow resolve this lead's address? The UI uses this to ask the rep.

	`tried` is True once we have asked (a fetch stamp, or a zpid). A Census hit
	with no Zillow zpid is the 310 Asbury case: the street exists, the house does
	not, and the setter needs to hear that out loud.
	"""
	zpid = str((subject or {}).get("zpid") or doc.get("zillow_zpid") or "")
	queried = (subject or {}).get("zillow_queried_address") or ""
	if not queried and doc.get("zillow_facts"):
		try:
			blob = json.loads(doc.get("zillow_facts") or "")
		except Exception:
			blob = {}
		if isinstance(blob, dict):
			queried = blob.get("_queried_address") or ""
	tried = bool(zpid) or bool(doc.get("zillow_fetched_at"))
	# Zillow gave no answer in THIS request (subscription lapsed, quota floor,
	# outage hold). Not a verdict on the address, and the UI must not read it as
	# one: `tried` stays False because nothing was stamped on the lead.
	from crm.api import zillow as zillow_api

	unavailable = None if zpid else zillow_api.unavailable_reason()
	return {
		"matched": bool(zpid),
		"tried": tried,
		"zpid": zpid,
		"queried_address": queried,
		"unavailable": unavailable,
	}


def _is_rentals(inventory):
	return str(inventory or "sale").strip().lower() in (
		"rent", "rental", "rentals", "for_rent",
	)


@frappe.whitelist()
def get_lead_comps(
	lead, radius_mi=None, limit=None, filters=None, auto=0, include_hidden=0, state=None,
	inventory=None,
):
	"""Comps near a lead, nearest first, with the subject's real position.

	The bounding box is a cheap indexed pre-filter; haversine then trims the
	corners of that box to a true circle. Doing it the other way round would mean
	a full-table distance computation over every comp we hold.

	`filters` is the explicit filter set (the user has touched the controls).
	With none, and `auto`, the filters are DERIVED from the subject and walked down
	`_preset_tiers` until a tier yields a usable set — the response reports which
	tier that was so the UI can say so out loud.

	`auto` defaults OFF so this stays byte-compatible for any caller that predates
	the filters: no argument means the old "everything in radius" answer. Presets
	are opt-in by a caller that knows how to show and explain them, which is what
	lets this deploy independently of the frontend.
	"""
	_guard()
	doc = _load_subject(lead)

	try:
		radius = max(0.25, min(10.0, float(radius_mi or DEFAULT_RADIUS_MI)))
	except (TypeError, ValueError):
		radius = DEFAULT_RADIUS_MI
	try:
		cap = max(1, min(MAX_COMPS, int(limit or MAX_COMPS)))
	except (TypeError, ValueError):
		cap = MAX_COMPS

	lat, lng, cached = _subject_point(doc)
	subject = {"lat": lat, "lng": lng} if lat is not None else None
	if subject:
		# The facts the subject pin shows when clicked. Merged onto the same dict the
		# map already reads for lat/lng, so no caller has to learn a second shape.
		subject.update(_subject_facts(doc))
	base = {
		"lead": lead,
		"address": _full_address(doc),
		"subject": subject,
		"radius_mi": radius,
		"cached_point": cached,
		"comps": [],
		# Comps a human discarded. Never merged into `comps` — see the pool filter
		# below. Present as [] so the client can render it without a guard.
		"discarded": [],
		"available": _available(),
		"zillow_match": _zillow_match(doc, subject),
	}
	if doc.doctype == SCRATCH_DOCTYPE:
		# Same contract practice uses: the latest saved calc rides on the comps
		# response and seeds the calculator, so a scratch property reopens on the
		# numbers that were saved rather than on a localStorage draft.
		from crm.api.properties import latest_offer

		base["offer"] = latest_offer(doc)
	rental = _is_rentals(inventory)
	base["inventory"] = "rentals" if rental else "sale"
	# Rentals are a Zillow ForRent circle. They do not need the ISTL pooled
	# index, so an unprovisioned CRM Comp doctype must not block the map.
	if not rental and not base["available"]:
		base["message"] = _("Comps have not been imported yet.")
		return base
	if lat is None:
		base["message"] = _("Could not locate this property's address on the map.")
		return base

	# 1 degree of latitude is ~69 miles; longitude shrinks with the cosine of it.
	dlat = radius / 69.0
	cos_lat = max(math.cos(math.radians(lat)), 0.01)
	dlng = radius / (69.0 * cos_lat)
	# Explicit >=/<= rather than `between`: Frappe's `between` operator routes
	# through get_between_date_filter and treats its bounds as DATES, which turns a
	# numeric bounding box into malformed SQL.
	rows = []
	if not rental:
		rows = frappe.get_all(
			DOCTYPE,
			filters=[
				[DOCTYPE, "lat", ">=", lat - dlat],
				[DOCTYPE, "lat", "<=", lat + dlat],
				[DOCTYPE, "lng", ">=", lng - dlng],
				[DOCTYPE, "lng", "<=", lng + dlng],
			],
			fields=[
				"name", "address", "city", "state", "zip", "lat", "lng", "price", "status",
				"listed_date", "removed_date", "days_on_market", "days_old",
				"bedrooms", "bathrooms", "square_footage", "year_built", "property_type", "source_lead",
			],
			limit_page_length=5000,
		)

	# Does Redfin agree with Zillow about this house? Started HERE so the fetch
	# rides the same wall-clock as the Zillow refresh below; collected after it
	# with a small join budget, so a slow or absent service costs the map nothing
	# (a miss lands in Redis via a background job for the next fetch instead).
	redfin_check_job = None
	redfin_istl_job = None
	realtor_job = None
	if subject is not None:
		try:
			from crm.api import redfin

			redfin_check_job = redfin.start_subject_check(doc, subject)
			if not rental:
				redfin_istl_job = redfin.start_istl_coverage(lat, lng, radius)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: Redfin check start failed")
		# Third AVM for the subject tile, same thread-beside-the-refresh shape.
		try:
			from crm.api import apivex

			realtor_job = apivex.start_realtor_estimate(doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: Realtor estimate start failed")

	today = frappe.utils.today()
	self_key = (subject or {}).get("self_comp_key")
	override = _parse_state_override(state)
	hidden, selected = override if override is not None else _comp_state(doc)
	base["selected"] = sorted(selected)
	base["hidden"] = sorted(hidden)
	base["state_supported"] = _state_supported(doc.doctype)
	out = []
	for row in rows:
		if row.lat is None or row.lng is None:
			continue
		# The subject's own listing is not a comparable for itself. It sits at
		# distance 0 and used to render as a pill directly under the subject dot,
		# quietly inflating the count and comping the house against itself.
		if self_key and row.name == self_key:
			continue
		dist = _haversine_mi(lat, lng, row.lat, row.lng)
		if dist > radius:
			continue
		row = shape_pool_row(row)
		row["distance_mi"] = round(dist, 2)
		row["source"] = row.get("source") or "istl"
		# The pooled index holds no imagery. The key exists from the start so the
		# client never has to special-case its absence; the Zillow merge fills it in
		# for any pin it can match, and the rest render a placeholder.
		row.setdefault("photo", "")
		# Whether a house is merely listed or already under contract is something
		# only Zillow tells us; the pooled index carries a last ASK and no more. The
		# key is always present so the client renders one grammar, and the honest
		# value for an unrefreshed ISTL pin is the coarse one it actually has.
		#
		# "off_market", NOT "sold": this inventory is the last LIST price, and a
		# listing disappearing is not a confirmed close. Only a Zillow-recorded
		# transaction earns the word sold, which is the same line the pin popup has
		# always drawn -- now drawn once, on the server, instead of re-derived by
		# each surface that shows a comp.
		if not is_adc(row):
			row["listing_state"] = "for_sale" if _is_active(row.get("status")) else "off_market"
		row["selected"] = row["name"] in selected
		row["hidden"] = row["name"] in hidden
		# Computed while the dates are still dates, and returned so the client can
		# show "3 mo ago" without re-deriving "recent" a second, divergent way.
		row["recency_days"] = _recency_days(row, today)
		# Dates are returned raw; the client derives the recency fade so the map and
		# any future report can't disagree about what "recent" means.
		for key in ("listed_date", "removed_date"):
			if row.get(key):
				row[key] = str(row[key])
		out.append(row)

	# ISTL asks go stale. Check Zillow for newer sales/listings around the
	# subject, and refresh the nearest ISTL pins' sale dates, before we count
	# or filter. Soft: an outage leaves `out` as the pooled index.
	# Rentals skip the sale circle entirely — ForRent is a different number.
	try:
		from crm.api import zillow_comps

		if rental:
			base["zillow"] = zillow_comps.apply_rentals(doc, out, lat, lng, radius)
		else:
			base["zillow"] = zillow_comps.apply(doc, out, lat, lng, radius)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Comps: Zillow refresh failed")
		base["zillow"] = {"used": False, "reason": "error"}

	# Redfin ingest overlay on ISTL pool pins. After Zillow so extra zillow::
	# solds stay Zillow's, but ingested CRM Comp rows take photos + MLS status
	# from the store that purchase already warmed.
	if redfin_istl_job is not None:
		try:
			from crm.api import redfin

			features, meta = redfin.finish_istl_coverage(redfin_istl_job)
			base["redfin"] = redfin.apply_istl_comps(out, features)
			redfin.maybe_rewarm(lead, meta)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: Redfin ISTL overlay failed")

	# Subject photo, cheapest source first: the facts we already cached, else the
	# self-match the area search threw away. Both are free; neither is worth a
	# billed lookup, so an unmatched subject simply shows no photo.
	if base.get("subject") is not None and not base["subject"].get("cover_photo"):
		base["subject"]["cover_photo"] = (base.get("zillow") or {}).get("subject_photo") or ""

	# Redfin cross-check, started above. None when it does not apply, was not
	# ready in time, or the two sources agree — the flag only exists to disagree.
	# The same record also carries the Redfin Estimate for the subject tile.
	if redfin_check_job is not None:
		try:
			from crm.api import redfin

			rec = redfin.finish_subject_record(redfin_check_job)
			base["subject"]["redfin_check"] = redfin.compare_subject_facts(base["subject"], rec)
			base["subject"]["redfin_estimate"] = redfin.subject_estimate(rec)
			base["subject"]["redfin_url"] = rec.get("url") if rec and rec.get("matched") else None
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: Redfin check failed")
	if realtor_job is not None:
		try:
			from crm.api import apivex

			base["subject"]["realtor_estimate"] = apivex.subject_estimate(
				apivex.finish_realtor_estimate(realtor_job)
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: Realtor estimate failed")

	for row in out:
		row["selected"] = row["name"] in selected
		row["hidden"] = row["name"] in hidden

	out.sort(key=lambda r: r["distance_mi"])

	# BatchData when Zillow RecentlySold produced no priced sales (Louisiana and
	# the other non-disclosure states). Zillow for-sale listings do not count —
	# an ask is not a sale. ISTL last-asks also do not count: they are last LISTs,
	# labelled off-market, and treating them as solds is how LA maps went yellow
	# without a single Sold pin. Pin-refreshed ISTL rows keep their ISTL name even
	# after `source` flips to zillow, so only `zillow::` solds are real search hits.
	had_pins = bool(out)
	if rental:
		# BatchData is recorded sales. A rent is not a sale.
		base["fallback"] = {
			"source": "batchdata", "used": False,
			"reason": "rentals", "had_pins": had_pins,
		}
	else:
		zillow_solds = [
			r for r in out
			if r.get("price")
			and str(r.get("name") or "").startswith("zillow::")
			and r.get("listing_state") == "sold"
		]
		if zillow_solds:
			base["fallback"] = {
				"source": "batchdata", "used": False,
				"reason": "zillow_has_prices", "had_pins": had_pins,
			}
		else:
			base["fallback"] = _batchdata_fallback(doc, base, merge_into=out)
			base["fallback"]["had_pins"] = had_pins
			out.sort(key=lambda r: r["distance_mi"])

	# Condition tags, stamped once over the final pool (ISTL + Zillow + BatchData
	# rows alike) so every surface — tray, gallery, discard drawer — reads the same
	# value. Practice runs pass a state override and get none: the tags are a
	# judgement about the REAL deal, not part of a training rerun.
	comp_types = {} if override is not None else _load_types(doc)
	base["types_supported"] = _types_supported(doc.doctype)
	for row in out:
		row["comp_type"] = comp_types.get(row["name"])

	base["total_in_radius"] = len(out)

	# A comp a human hid is gone from every count and every tier decision — leaving
	# it in the pool would let junk keep a tier "usable" and suppress the widening
	# that the rep actually needs.
	hidden_here = [r for r in out if r["hidden"]]
	base["hidden_count"] = len(hidden_here)
	# ALWAYS removed from the pool, whatever the caller asked for. `include_hidden`
	# used to merge them back in here, which quietly let discarded junk keep a tier
	# "usable" and suppress the widening the rep needed. Discards now travel in
	# their own list so the tray can gray them out and offer an undo without ever
	# touching the ladder, the counts, or what gets underwritten.
	out = [r for r in out if not r["hidden"]]
	if int(include_hidden or 0):
		base["discarded"] = sorted(hidden_here, key=lambda r: r["distance_mi"])[:cap]

	explicit = _coerce_filters(filters)
	if explicit is not None:
		# The user drove the controls: run exactly what they asked for, even if it
		# matches nothing. Quietly widening someone's deliberate filter is how a
		# tool stops being trusted.
		matched = [r for r in out if _matches(r, explicit, today)]
		base["filters"] = explicit
		base["preset"] = None
		base["relaxed"] = False
	elif int(auto or 0):
		tiers = (
			_rental_preset_tiers(base["subject"] or {}, radius)
			if rental
			else _preset_tiers(base["subject"] or {}, radius)
		)
		matched, chosen = [], tiers[-1]
		for t in tiers:
			hits = [r for r in out if _matches(r, t["filters"], today)]
			# Take the first tier that clears the bar. A looser tier is a superset,
			# so this is the tightest usable set by construction.
			if len(hits) >= MIN_USABLE_COMPS:
				matched, chosen = hits, t
				break
			if t is tiers[-1]:
				matched, chosen = hits, t
		base["filters"] = chosen["filters"]
		base["preset"] = {"key": chosen["key"], "label": chosen["label"]}
		# "Relaxed" means we could NOT honour the recent-and-similar default, which
		# is the thing the user has to be told rather than left to infer from a map
		# full of comps that do not resemble the subject.
		base["relaxed"] = chosen["key"] != tiers[0]["key"]
		base["fell_through"] = chosen["key"] == "all"
	else:
		matched = out
		base["filters"] = {"status": "all", "radius_mi": radius}
		base["preset"] = None
		base["relaxed"] = False

	# An explicit human pick outranks any derived filter: a comp someone marked as
	# one they are pricing off must not vanish because the preset later tightened.
	# Same principle as the Today board — the machine decides what LANDS, the human
	# owns it afterwards.
	if selected:
		seen = {r["name"] for r in matched}
		pinned = [r for r in out if r["selected"] and r["name"] not in seen]
		if pinned:
			matched = sorted(matched + pinned, key=lambda r: r["distance_mi"])
	base["selected_count"] = sum(1 for r in matched if r["selected"])

	base["total_matched"] = len(matched)
	base["comps"] = matched[:cap]

	# LAST, and only on the capped set. This is the first point at which we know
	# which comps a person will actually see, and the promise is that every one of
	# them has complete information -- so this must not run before the filter (it
	# would bill for comps nobody looks at) nor before the cap (same, and far
	# worse: a 1/2-mile circle holds 126 comps on average and 253 in Indianapolis).
	#
	# Best-effort by construction: `attach_sale_history` marks a row it could not
	# resolve rather than raising, because a comps map that renders without flip
	# warnings beats a lead page that 500s.
	if rental:
		# Sale history is billed `/property` per pin and answers a sale question.
		base["sale_history"] = {
			"checked": 0, "with_history": 0, "flips": 0, "missing": 0,
		}
	else:
		try:
			# Imported again rather than relying on the binding from the refresh block
			# above: that one lives inside a `try` whose `except` swallows an ImportError,
			# so the name is not guaranteed to exist down here.
			from crm.api import zillow_comps as _zc

			base["sale_history"] = _zc.attach_sale_history(base["comps"], today)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Comps: sale history failed")
			base["sale_history"] = {"checked": 0, "with_history": 0, "flips": 0, "missing": len(base["comps"])}
			for row in base["comps"]:
				row.setdefault("sale_history", None)
				row.setdefault("sale_history_missing", True)
	return base


def _batchdata_fallback(doc, base, merge_into=None):
	"""Fill an empty comps map from BatchData. Returns a small status dict.

	Split out so the paid path is one obvious, greppable place rather than an inline
	branch someone later widens by accident.
	"""
	from crm.api import batchdata_comps

	if not batchdata_comps.available():
		return {"source": "batchdata", "used": False, "reason": "not_configured"}

	try:
		comps = batchdata_comps.fetch_for_lead(doc)
	except Exception:
		# A comps map that renders without the fallback beats a 500 on lead detail.
		frappe.log_error(frappe.get_traceback(), "BatchData comps fallback failed")
		return {"source": "batchdata", "used": False, "reason": "error"}

	if not comps:
		return {"source": "batchdata", "used": True, "count": 0}

	lat, lng = base["subject"]["lat"], base["subject"]["lng"]
	for c in comps:
		c["distance_mi"] = round(_haversine_mi(lat, lng, c["lat"], c["lng"]), 2)
		c["selected"] = False
		c["hidden"] = False
		# BatchData sells records, not imagery.
		c.setdefault("photo", "")
		c["recency_days"] = _recency_days(c, frappe.utils.today())

	# BatchData applies no radius and returns no similarity score, so both are ours
	# to impose. Drop first: a comp 2.8mi away is not a comp here, and padding the
	# list with one is worse than showing five.
	comps = [c for c in comps if c["distance_mi"] <= batchdata_comps.MAX_MILES]

	# Then rank on shape, not just proximity. Distance still dominates because it is
	# the one fact always present; sqft and beds are frequently missing, and a
	# missing fact must not score as a bad match or every sparse row sinks.
	subj = base.get("subject") or {}
	s_sqft, s_beds, s_year = subj.get("sqft"), subj.get("beds"), subj.get("year_built")

	def _fit(c):
		score = c["distance_mi"] * 2.0
		if s_sqft and c.get("square_footage"):
			score += abs(c["square_footage"] - s_sqft) / max(s_sqft, 1) * 1.5
		if s_beds and c.get("bedrooms"):
			score += abs(c["bedrooms"] - s_beds) * 0.3
		if s_year and c.get("year_built"):
			score += min(abs(c["year_built"] - s_year) / 50.0, 1.0) * 0.3
		return score

	comps.sort(key=_fit)
	comps = comps[: batchdata_comps.KEEP]
	comps.sort(key=lambda r: r["distance_mi"])

	if merge_into is not None:
		try:
			from crm.api.zillow_comps import merge_key
		except Exception:
			merge_key = lambda a: (a or "").strip().lower()
		seen = {merge_key(r.get("address") or "") for r in merge_into}
		for c in comps:
			k = merge_key(c.get("address") or "")
			if k in seen:
				continue
			seen.add(k)
			merge_into.append(c)
	else:
		# Pre-merge callers (none left) still get a standalone list.
		base["comps"] = comps
		base["total_matched"] = len(comps)
		base["total_in_radius"] = len(comps)
	return {
		"source": "batchdata",
		"used": True,
		"count": len(comps),
		"basis": batchdata_comps.WINDOW_LABEL,
	}


# ---------------------------------------------------------------------------------
# Import (bench-executable)
# ---------------------------------------------------------------------------------
@frappe.whitelist()
def import_comps_file(path, dry_run=1, chunk=2000):
	"""Bulk-load geocoded comps from a JSONL file produced by the build script.

	Raw SQL upsert rather than `doc.insert()`: this is ~37k rows of immutable
	reference data, and the ORM path costs minutes plus a Version row apiece for an
	audit trail nobody will read. `ON DUPLICATE KEY UPDATE` keyed on the
	deterministic address_key is what makes a re-import idempotent.

	    bench execute crm.api.comps.import_comps_file \\
	        --kwargs '{"path": "/tmp/comps_geocoded.jsonl", "dry_run": 0}'
	"""
	dry_run = int(dry_run)
	if not _available():
		return {"ok": False, "error": f"{DOCTYPE} doctype does not exist — run setup_comps.py"}

	cols = [
		"address", "city", "state", "zip", "lat", "lng", "price", "status",
		"listed_date", "removed_date", "days_on_market", "days_old",
		"bedrooms", "bathrooms", "square_footage", "year_built", "property_type",
		"correlation", "source_lead",
	]
	# Frappe declares Int/Float/Currency columns NOT NULL, but plenty of comps are
	# missing a year built or a lot size. Coerce to 0 rather than dropping the row:
	# a comp with an unknown year is still a valid sale at a known price and place,
	# and 0 is falsy so the popup simply omits the fact instead of printing "0".
	NUMERIC = {
		"lat", "lng", "price", "days_on_market", "days_old",
		"bedrooms", "bathrooms", "square_footage", "year_built", "correlation",
	}
	# Dates stay nullable (a live listing has no removal date); text stays "".
	NULLABLE = {"listed_date", "removed_date"}

	def cell(col, value):
		if value is None:
			return None if col in NULLABLE else (0 if col in NUMERIC else "")
		return value
	seen, rows, stats = set(), [], {"read": 0, "skipped": 0, "written": 0}

	def flush(batch):
		if not batch or dry_run:
			return
		placeholders = ", ".join(["(" + ", ".join(["%s"] * (len(cols) + 6)) + ")"] * len(batch))
		flat = []
		for r in batch:
			flat.extend(r)
		updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
		frappe.db.sql(
			f"""INSERT INTO `tab{DOCTYPE}`
			    (`name`, `address_key`, `creation`, `modified`, `owner`, `modified_by`, {', '.join(f'`{c}`' for c in cols)})
			    VALUES {placeholders}
			    ON DUPLICATE KEY UPDATE {updates}, `modified`=VALUES(`modified`)""",
			flat,
		)
		frappe.db.commit()

	now = frappe.utils.now()
	user = frappe.session.user or "Administrator"
	with open(path) as fh:
		for line in fh:
			line = line.strip()
			if not line:
				continue
			stats["read"] += 1
			try:
				rec = json.loads(line)
			except Exception:
				stats["skipped"] += 1
				continue
			if rec.get("lat") is None or rec.get("lng") is None or not rec.get("address"):
				stats["skipped"] += 1
				continue
			key = address_key(rec["address"])
			if key in seen:          # same address twice in one file
				stats["skipped"] += 1
				continue
			seen.add(key)
			rows.append([key, key, now, now, user, user] + [cell(c, rec.get(c)) for c in cols])
			stats["written"] += 1
			if len(rows) >= int(chunk):
				flush(rows)
				rows = []
	flush(rows)
	stats["ok"] = True
	stats["dry_run"] = bool(dry_run)
	stats["total_in_db"] = frappe.db.count(DOCTYPE) if not dry_run else None
	return stats

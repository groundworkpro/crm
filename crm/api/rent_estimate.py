"""What the subject would rent for: Zillow's rent estimate plus nearby rentals we hold.

Dennis (2026-11-17): "get average rental rates for a property". Two sources,
both free per lookup:

* Zillow's Rent Zestimate — already in the subject facts (`rent_zestimate`),
  fetched with the Zillow record the comps page loads anyway. The client sends
  it; this module only passes it through so the card has one shape to read.
* Rental listings in PropWarehouse (`GET /rentals` on propwarehouse-api):
  Redfin and Zillow for-rent houses, plus Trulia, Rent.com and Craigslist.
  One row per (house, source); a house two sources carry comes back twice, so
  rows are grouped on `rental_id` and the most recently seen one wins.

The estimate from listings is the median monthly rent of houses like the
subject (same class; beds ±1 and sqft ±30% when the subject has them),
widening the circle 1 → 2 → 3 mi until there are MIN_COMPS. If the strict
filter still finds too few, the beds/sqft filters are dropped and the answer
says so. Delisted listings count: a house that stopped being listed in the last
few weeks was still asking that rent, and the store is only weeks old.

* Redfin's Rental Estimate (Exe, 2026-11-18) — through the Redfin scraper's
  `GET /rental-estimate?property_id=` (never Redfin directly), for the
  propertyId of the subject's matched Redfin record. Absent when the house has
  no Redfin match, Redfin shows none, or the scraper lacks the endpoint.
"""

from __future__ import annotations

import hashlib
import json
import statistics

import frappe
from frappe import _

RADII_MI = (1.0, 2.0, 3.0)
MIN_COMPS = 5
#: Rows shown in the card's tooltip / list.
SHOW = 8
CACHE_TTL = 6 * 3600
#: Redfin recomputes its Rental Estimate rarely; a miss is not cached.
REDFIN_TTL = 24 * 3600
BED_SLACK = 1
SQFT_SLACK = 0.30
#: A monthly rent outside this is a typo, a weekly/room price or a sale price.
RENT_MIN, RENT_MAX = 300, 20000


def _num(v):
	try:
		n = float(v)
	except (TypeError, ValueError):
		return None
	return n if n == n else None


def _classes(property_type):
	"""PropWarehouse `property_class` values to search for this subject."""
	t = str(property_type or "").lower()
	if "town" in t:
		return ("townhouse", "sfr")
	if "condo" in t or "apartment" in t:
		return ("condo", "apartment")
	return ("sfr",)


def _fetch(lat, lng, radius_mi, property_class):
	from crm.api import vendor_facts

	body = vendor_facts._get(
		"/rentals",
		{
			"lat": lat,
			"lng": lng,
			"radius_mi": radius_mi,
			"property_class": property_class,
			"status": "all",
			"limit": 1000,
		},
		timeout=15,
	)
	if not body or not body.get("ok"):
		return None
	return body.get("rentals") or []


def _dedupe(rows):
	"""One row per house: the most recently seen observation, with its sources."""
	best = {}
	for r in rows:
		rent = _num(r.get("rent"))
		if rent is None or not (RENT_MIN <= rent <= RENT_MAX):
			continue
		key = r.get("rental_id") or (r.get("address_key"), r.get("unit"))
		cur = best.get(key)
		sources = set(cur["sources"]) if cur else set()
		sources.add(r.get("source") or "")
		if cur is None or str(r.get("last_seen") or "") > str(cur.get("last_seen") or ""):
			cur = dict(r)
		cur["sources"] = sorted(s for s in sources if s)
		best[key] = cur
	# The same listing under two rental_ids: one source wrote the unit
	# ("10990 Dusk Sky Point #3248"), the other did not ("10990 Dusk Sky Pt").
	# Same house number, rent, beds and sqft is one listing.
	out = {}
	for r in sorted(best.values(), key=lambda r: str(r.get("last_seen") or ""), reverse=True):
		number = str(r.get("street") or "").split(" ")[0]
		key = (number, r.get("zip") or r.get("city"), _num(r.get("rent")),
			   _num(r.get("beds")), _num(r.get("sqft")))
		if key in out:
			out[key]["sources"] = sorted(set(out[key]["sources"]) | set(r["sources"]))
		else:
			out[key] = r
	return list(out.values())


def _shape(r):
	beds = _num(r.get("beds")) if r.get("beds") is not None else _num(r.get("rental_beds"))
	baths = _num(r.get("baths")) if r.get("baths") is not None else _num(r.get("rental_baths"))
	sqft = _num(r.get("sqft")) if r.get("sqft") is not None else _num(r.get("rental_sqft"))
	street = " ".join(x for x in (r.get("street"), r.get("unit")) if x)
	return {
		"address": ", ".join(x for x in (street, r.get("city"), r.get("state")) if x),
		"rent": round(_num(r.get("rent"))),
		"beds": beds,
		"baths": baths,
		"sqft": round(sqft) if sqft else None,
		"dist_mi": round((_num(r.get("dist_m")) or 0) / 1609.344, 2),
		"status": r.get("status"),
		"last_seen": (r.get("last_seen") or "")[:10] or None,
		"sources": r.get("sources") or [r.get("source")],
		"url": _url(r.get("url")),
		"lat": _num(r.get("lat")),
		"lng": _num(r.get("lng")),
	}


def _url(url):
	"""Redfin stores a path ("/CO/Colorado-Springs/…/home/34350066"); the rest full URLs."""
	url = str(url or "").strip()
	if url.startswith("/"):
		return "https://www.redfin.com" + url
	return url if url.startswith("http") else None


def _like(c, beds, sqft):
	if beds and (c["beds"] is None or abs(c["beds"] - beds) > BED_SLACK):
		return False
	if sqft and (not c["sqft"] or abs(c["sqft"] - sqft) > SQFT_SLACK * sqft):
		return False
	return True


def _quantile(sorted_vals, q):
	if not sorted_vals:
		return None
	i = (len(sorted_vals) - 1) * q
	lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
	return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def estimate_from_listings(lat, lng, beds=None, sqft=None, property_type=None, fetch=_fetch):
	"""The nearby-rentals estimate, or {"count": 0, ...} when none were found."""
	beds = _num(beds) or None
	sqft = _num(sqft) or None
	classes = _classes(property_type)
	reachable = False
	pool = []
	for radius in RADII_MI:
		rows = []
		for cls in classes:
			got = fetch(lat, lng, radius, cls)
			if got is None:
				continue
			reachable = True
			rows.extend(got)
		pool = [_shape(r) for r in _dedupe(rows)]
		similar = [c for c in pool if _like(c, beds, sqft)]
		if len(similar) >= MIN_COMPS:
			return _summary(similar, radius, beds, sqft, filtered=bool(beds or sqft))
	if not reachable:
		return {"count": 0, "unavailable": True}
	similar = [c for c in pool if _like(c, beds, sqft)]
	if len(similar) >= 3 or (similar and len(similar) == len(pool)):
		return _summary(similar, RADII_MI[-1], beds, sqft, filtered=bool(beds or sqft))
	if pool:
		# Too few houses like the subject: say so, and use every rental in the circle.
		return _summary(pool, RADII_MI[-1], beds, sqft, filtered=False, loose=bool(beds or sqft))
	return {"count": 0, "radius_mi": RADII_MI[-1]}


def _summary(comps, radius, beds, sqft, filtered, loose=False):
	comps = sorted(comps, key=lambda c: c["dist_mi"])
	rents = sorted(c["rent"] for c in comps)
	per_sqft = sorted(c["rent"] / c["sqft"] for c in comps if c["sqft"])
	out = {
		"count": len(comps),
		"radius_mi": radius,
		"median": round(statistics.median(rents)),
		"low": round(_quantile(rents, 0.25)),
		"high": round(_quantile(rents, 0.75)),
		"filtered": filtered,
		# Too few houses like the subject: every rental in the circle was used.
		"loose": loose,
		"beds": beds,
		"sqft": sqft,
		"listings": comps[:SHOW],
	}
	if sqft and len(per_sqft) >= 3:
		ps = statistics.median(per_sqft)
		out["per_sqft"] = round(ps, 2)
		out["by_sqft"] = round(ps * sqft)
	return out


@frappe.whitelist()
def get_rent_estimate(
	lat, lng, beds=None, sqft=None, property_type=None, rent_zestimate=None, redfin_property_id=None
):
	"""Rent estimate for one house: Zillow's, Redfin's, and the median of nearby rentals.

	Takes the subject facts the comps page already holds rather than a lead name,
	so it works the same for a lead, a scratch property and an address search.
	"""
	from crm.api.comps import _guard

	_guard()
	lat, lng = _num(lat), _num(lng)
	if lat is None or lng is None:
		frappe.throw(_("No location for this property."))
	key_src = json.dumps([round(lat, 5), round(lng, 5), _num(beds), _num(sqft), property_type])
	key = "crm:rent_estimate:" + hashlib.sha1(key_src.encode()).hexdigest()
	cached = frappe.cache().get_value(key)
	if not cached:
		cached = estimate_from_listings(lat, lng, beds, sqft, property_type)
		if not cached.get("unavailable"):
			frappe.cache().set_value(key, cached, expires_in_sec=CACHE_TTL)
	zillow = _num(rent_zestimate)
	return {
		"zillow": round(zillow) if zillow else None,
		"redfin": _redfin(redfin_property_id),
		"listings": cached,
	}


def _redfin(property_id, lookup=None):
	"""{"rent", "low", "high"} from Redfin's Rental Estimate, or None."""
	try:
		pid = int(property_id or 0)
	except (TypeError, ValueError):
		return None
	if pid <= 0:
		return None
	key = f"crm:rent_estimate:redfin:{pid}"
	cached = frappe.cache().get_value(key)
	if cached is None:
		if lookup is None:
			from crm.api.redfin import rental_estimate as lookup
		got = lookup(pid)
		if got is None:
			return None
		cached = {"rent": got.get("rent"), "low": got.get("rent_low"), "high": got.get("rent_high")}
		cached = {k: round(v) if v else None for k, v in cached.items()}
		frappe.cache().set_value(key, cached, expires_in_sec=REDFIN_TTL)
	return cached if cached.get("rent") else None

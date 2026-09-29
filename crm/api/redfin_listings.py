"""Redfin for-sale, pending and last-12-months sold homes around a lead.

Redfin is the FIRST comps source (Lance, 2026-09-29). Its prices and living
areas are the most accurate of the three providers (benchmark: odd one out on
sqft 9% of the time against Zillow's 52%), and this read costs nothing: the
scraper's `/listings` asks Redfin's own map endpoint directly from our box.

The stored Redfin sweep (`redfin.start_istl_coverage`) only ever holds SALES,
so before this the board's listings and pendings came from Zillow alone. This
module adds Redfin's on-market set (active, coming soon, contingent, pending)
and its last-12-months solds, and decides whether the paid-ish fill-in sources
are needed at all:

    Zillow's area search and Realtor's search run only when Redfin has fewer
    than `ENOUGH` listings OR fewer than `ENOUGH` recent sales in the circle,
    or when Redfin did not answer.

Cost note: when Redfin is blocking our IP the scraper re-sends the map call
through ZenRows (its own budgeted egress), so "free" is "free almost always".
Answers are cached here for `CACHE_S` so reopening a lead does not ask again.
"""

import threading

#: Five of each is the smallest board a rep can price from (Lance).
ENOUGH = 5
#: The scraper refuses a wider circle (`MAX_LISTING_RADIUS_MI`). A wider board
#: gets Redfin's inner 2 miles, so its counts are a floor, never an overcount.
MAX_RADIUS_MI = 2.0
#: A listing that went pending this morning shows by this afternoon.
CACHE_S = 6 * 3600
CACHE_VERSION = 1
TIMEOUT = 20
#: How long the request waits for the thread. Measured 0.6s at 1/2 mile and
#: 1.1s at 1 mile; the budget is for a slow day, not the normal one.
BUDGET = 8


def _key(lat, lng, radius_mi):
	return f"crm:redfin-listings:v{CACHE_VERSION}:{round(lat, 4)}|{round(lng, 4)}|{radius_mi:.2f}"


def _radius(radius_mi):
	return min(MAX_RADIUS_MI, float(radius_mi))


def _fetch(base, lat, lng, radius_mi, holder):
	"""Worker thread: HTTP only, no Frappe (a thread has no site)."""
	import requests

	try:
		r = requests.get(
			f"{base}/listings",
			params={"lat": lat, "lng": lng, "radius_mi": _radius(radius_mi), "kind": "both"},
			timeout=TIMEOUT,
		)
		if r.status_code >= 400:
			holder["error"] = f"http_{r.status_code}"
			return
		body = r.json()
		if not isinstance(body, dict):
			holder["error"] = "bad_body"
			return
		holder["body"] = body
	except Exception as e:
		holder["error"] = type(e).__name__


def start(lat, lng, radius_mi):
	"""Cached answer, or a thread already asking. None when it cannot apply."""
	import frappe

	from crm.api import redfin

	try:
		lat, lng, radius_mi = float(lat), float(lng), float(radius_mi)
	except (TypeError, ValueError):
		return None
	if not lat or not lng:
		return None
	key = _key(lat, lng, radius_mi)
	try:
		hit = frappe.cache().get_value(key)
	except Exception:
		hit = None
	if isinstance(hit, dict) and "on_market" in hit:
		return {"key": key, "body": hit, "cached": True}
	base = redfin._base_url()
	if not base:
		return None
	holder = {}
	thread = threading.Thread(target=_fetch, args=(base, lat, lng, radius_mi, holder), daemon=True)
	thread.start()
	return {"key": key, "thread": thread, "holder": holder, "cached": False}


def finish(job, budget=BUDGET):
	"""-> (homes, meta). Homes is [] on timeout/error; meta says which."""
	if not job:
		return [], {"error": "not_configured"}
	body = job.get("body")
	if body is None:
		job["thread"].join(timeout=max(0.05, float(budget)))
		holder = job["holder"]
		if "body" not in holder:
			if job["thread"].is_alive():
				return [], {"error": "timed_out"}
			return [], {"error": holder.get("error") or "upstream"}
		body = holder["body"]
		# Only a clean answer is remembered: a half-failed one would hide the
		# missing half for six hours.
		if body.get("ok") is not False and _block_ok(body, "on_market") and _block_ok(body, "sold"):
			try:
				import frappe

				frappe.cache().set_value(job["key"], body, expires_in_sec=CACHE_S)
			except Exception:
				pass
	homes = []
	for label in ("on_market", "sold"):
		block = body.get(label)
		if isinstance(block, dict):
			for h in block.get("homes") or []:
				if isinstance(h, dict):
					homes.append(dict(h, _block=label))
	meta = {
		"cached": bool(job.get("cached")),
		"on_market_ok": _block_ok(body, "on_market"),
		"sold_ok": _block_ok(body, "sold"),
		"region_source": body.get("region_source"),
	}
	return homes, meta


def _block_ok(body, label):
	block = body.get(label)
	return isinstance(block, dict) and not block.get("error")


def _mls_status(home):
	"""The status word `redfin.mls_listing_state` reads.

	`mls_status` is the MLS's own label and is often blank on a search row;
	`status` is Redfin's search status (Active / Pending / Contingent / Sold /
	ComingSoon). A sold-block row with neither is still a sale.
	"""
	if home.get("is_pending"):
		return "Pending"
	for v in (home.get("mls_status"), home.get("status")):
		s = str(v or "").strip()
		if s:
			return "Active" if s.lower() == "comingsoon" else s
	return "Sold" if home.get("_block") == "sold" else "Active"


def _iso_date(v):
	"""Redfin search rows carry `soldDate` as epoch MILLISECONDS; the store
	features and every consumer here read an ISO date."""
	import datetime as dt

	if v is None or v == "":
		return None
	try:
		n = float(v)
	except (TypeError, ValueError):
		return str(v)[:10] or None
	if n > 1e11:
		n /= 1000.0
	try:
		return dt.datetime.fromtimestamp(n, dt.timezone.utc).date().isoformat()
	except (OverflowError, OSError, ValueError):
		return None


def features(homes):
	"""Listings homes -> the store-feature shape `comp_merge.apply_redfin` reads.

	On-market homes come first so a house that sold years ago and is listed
	again today boards as the listing: `apply_redfin` keeps the first copy of
	an address it sees.
	"""
	ordered = [h for h in homes if h.get("_block") == "on_market"]
	ordered += [h for h in homes if h.get("_block") != "on_market"]
	out = []
	for h in ordered:
		sold = h.get("_block") == "sold"
		out.append({
			"properties": {
				"property_id": h.get("property_id"),
				"address": h.get("address"),
				"city": h.get("city"),
				"state": h.get("state"),
				"zipcode": h.get("zip"),
				"lat": h.get("lat"),
				"lng": h.get("lng"),
				"price": h.get("price"),
				"beds": h.get("beds"),
				"baths": h.get("baths"),
				"sqft": h.get("sqft"),
				"year_built": h.get("year_built"),
				"mls_status": _mls_status(h),
				"sold_date": _iso_date(h.get("sold_date")) if sold else None,
				"url": h.get("url"),
				"photos": h.get("photos") or [],
			}
		})
	return out


def counts(homes, lat, lng, radius_mi):
	"""(listings, recent_sales) inside the circle. Pending counts as a listing."""
	from crm.api import comps

	listings = sales = 0
	seen = set()
	for h in homes:
		pid = h.get("property_id")
		if pid in seen:
			continue
		seen.add(pid)
		hlat, hlng = comps._num(h.get("lat")), comps._num(h.get("lng"))
		if hlat is None or hlng is None:
			continue
		if comps._haversine_mi(lat, lng, hlat, hlng) > radius_mi:
			continue
		if h.get("_block") == "on_market":
			listings += 1
		else:
			sales += 1
	return listings, sales


def decide(homes, meta, lat, lng, radius_mi):
	"""Does the board need Zillow and Realtor? -> dict the response carries.

	`fill_in` is True when Redfin is thin or did not answer. A half answer
	(one block failed) is treated as thin: a missing listings block must not
	read as "no listings here".
	"""
	listings, sales = counts(homes, lat, lng, radius_mi)
	out = {"listings": listings, "recent_sales": sales, "enough": ENOUGH}
	if meta.get("error"):
		out.update(fill_in=True, reason=meta["error"])
	elif not (meta.get("on_market_ok") and meta.get("sold_ok")):
		out.update(fill_in=True, reason="partial")
	elif listings < ENOUGH or sales < ENOUGH:
		out.update(fill_in=True, reason="thin")
	else:
		out.update(fill_in=False, reason="redfin_enough")
	return out

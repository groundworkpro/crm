"""Merge comp inventory from Redfin, Zillow and Realtor into one board.

WHY MERGE RATHER THAN FALL BACK. Measured over 100 real subjects
(`redfin-scraper-api/bench/README.md`): a fallback chain rescues **3 of 100**
subjects, because whichever provider you ask first nearly always returns
something. The merge is worth **x1.37 on sold comps**, because no provider
holds more than about two thirds of the union:

    only Redfin has it     544   10%
    only Zillow has it     616   11%
    only Realtor has it  1,192   21%
    all three have it    1,882   33%

Falling back therefore optimises the wrong thing. The question is not "did
someone answer" but "how much of the block did we miss", and the answer for
any single provider is a fifth to a third of it.

FIELD AUTHORITY IS REDFIN, ALWAYS, and it is not a preference. Of the houses
more than one provider returned, where all three carry a value:

    sold price   agree 98%   odd one out: Realtor 45% . Zillow 42% . REDFIN 12%
    sqft         agree 89%   odd one out: Zillow 52% . Realtor 39% . REDFIN 9%

So on the rare row where they disagree, Redfin is almost never the one that is
wrong. Zillow and Realtor are here to ADD HOUSES, never to overwrite a field
Redfin has an opinion about.

WHAT THIS MODULE DOES NOT DO: it never renames a row. Hides and picks are
persisted on `CRM Lead` as JSON arrays of comp NAMES (`comps_hidden`,
`comps_selected`), so a house that was `zillow::123` must stay `zillow::123`
for as long as the lead lives. New houses get a new name; existing houses get
better field values under the name they already had. That split is the whole
safety property of this file.
"""

from __future__ import annotations

#: Name prefixes minted by a provider search, as opposed to an ISTL pool
#: docname. A row with one of these names came from a vendor that told us
#: about a TRANSACTION; an ISTL docname is a last ASK and means nothing about
#: whether the house sold. The BatchData gate turns on exactly that difference
#: -- see `has_recorded_sale`.
VENDOR_PREFIXES = ("zillow::", "zillow-rent::", "redfin::", "realtor::")

#: Fields Redfin owns on a row that more than one provider returned. Ordered
#: for readability only. `photo` is deliberately ABSENT: Redfin carries none on
#: a comp row by construction, and the benchmark puts Zillow at 99.9% there, so
#: the photo ladder stays where it is and is not this module's business.
REDFIN_AUTHORITY_FIELDS = (
	"price",
	"square_footage",
	"bedrooms",
	"bathrooms",
	"year_built",
	"property_type",
)


def _comps():
	# Late import, same reason zillow_comps does it: comps.py calls this module
	# from get_lead_comps, so importing it at module scope would cycle.
	from crm.api import comps as comps_mod

	return comps_mod


def _merge_key(address):
	from crm.api.zillow_comps import merge_key

	return merge_key(address or "")


def is_vendor_row(row) -> bool:
	return str((row or {}).get("name") or "").startswith(VENDOR_PREFIXES)


# ── half-baths ─────────────────────────────────────────────────────────────

def baths_conflict(redfin_val, vendor_val) -> bool:
	"""Do two bath counts disagree for a reason a rep could act on?

	THIS IS THE HALF-BATH NORMALISATION, and it belongs at the comparison
	rather than on the value. Zillow ROUNDS A HALF-BATH UP: measured over the
	benchmark's matched houses, 3,030 pairs where both carried a count, 2,338
	agreed exactly, **586 had Zillow exactly 0.5 higher and only 14 had it 0.5
	lower** -- 42:1, a convention rather than a spread. Redfin 2.5 / Zillow 3.0
	and Redfin 1.5 / Zillow 2.0 are the two shapes it takes.

	Without this, merging manufactures disagreement that does not exist: 586
	houses per hundred subjects would read as a provider conflict when the two
	providers are describing the same bathroom.

	Normalising the VALUE instead would be worse, because the half is not
	recoverable from a single vendor row -- 3.0 is 3.0 under either convention,
	and turning it into 2.5 would invent a half-bath that may not exist. Where
	we have both numbers the Redfin one simply wins (`_take_authority`); where
	we only have the vendor's, it is reported as given.

	Delegates to `redfin.baths_agree`, which is the one place that 586:14
	evidence is written down. A second copy of the rule here would be a second
	thing to keep in step with it.
	"""
	from crm.api import redfin as redfin_api

	if redfin_val is None or vendor_val is None:
		return False
	return not redfin_api.baths_agree(vendor_val, redfin_val)


# ── shaping ────────────────────────────────────────────────────────────────

def _listing_state_from_redfin(props):
	from crm.api import redfin as redfin_api

	state = redfin_api.mls_listing_state(props.get("mls_status"))
	if state:
		return state
	# A sold date with no usable MLS status is still a recorded sale. Redfin
	# serves public-record sales with a null mlsStatus (measured: 31 of 98 rows
	# on one Milwaukee box), and dropping them loses the no-agent cash and
	# distressed transactions this desk exists to find.
	return "sold" if props.get("sold_date") else None


def redfin_row(props, distance_mi):
	"""One Redfin store feature -> the row shape `get_lead_comps` emits.

	Returns None for a feature with no address or no position: a comp that
	cannot be placed on the map is not a comp, and a blank address would
	collide with every other blank address under `merge_key`.
	"""
	comps = _comps()
	address = str(props.get("address") or "").strip()
	lat, lng = comps._num(props.get("lat")), comps._num(props.get("lng"))
	if not address or lat is None or lng is None:
		return None
	pid = str(props.get("property_id") or "").strip()
	if not pid:
		return None
	state = _listing_state_from_redfin(props)
	active = state in ("for_sale", "pending", "auction")
	sold_date = str(props.get("sold_date") or "").strip() or None
	photos = [p for p in (props.get("photos") or []) if isinstance(p, str) and p.startswith("http")]
	return {
		"name": f"redfin::{pid}",
		"address": address,
		"city": props.get("city") or "",
		"state": props.get("state") or "",
		"zip": props.get("zipcode") or props.get("zip") or "",
		"lat": lat,
		"lng": lng,
		"price": comps._num(props.get("price")),
		"status": "Active" if active else "Inactive",
		"listing_state": state or "off_market",
		"listed_date": str(props.get("listing_date") or "").strip() or None if active else None,
		"removed_date": None if active else sold_date,
		"days_on_market": None,
		"days_old": None,
		"bedrooms": comps._num(props.get("beds")),
		"bathrooms": comps._num(props.get("baths")),
		"square_footage": comps._num(props.get("sqft")),
		"year_built": comps._num(props.get("year_built")),
		"lot_size": None,
		"property_type": props.get("property_type") or None,
		"source": "redfin",
		# Marks every field on this row as Redfin's, which is what stops the
		# billed `/property` pass in `attach_sale_history` overwriting them
		# later. A blank here can still be filled by Zillow -- see `_apply_facts`.
		"field_authority": "redfin",
		"photo": photos[0] if photos else "",
		"photos": photos or None,
		"redfin_status": props.get("mls_status") or "",
		"redfin_url": _redfin_url(props.get("url")),
		"distance_mi": round(distance_mi, 2),
		"selected": False,
		"hidden": False,
		# The sale is Redfin's, recorded, and that is what lets the BatchData
		# gate tell it apart from an ISTL last-ask. See `has_recorded_sale`.
		"sale_source": "redfin" if state == "sold" else None,
	}


def _redfin_url(url):
	from crm.api import redfin as redfin_api

	return redfin_api._listing_url(url)


def realtor_row(prop, distance_mi, kind):
	"""One Apivex search property -> a comp row, or None if unplaceable.

	`kind` is "forsold" or "forsale" and decides which price is the row's
	price: a sold row's `list_price` is what it was ASKING, which is not what
	it fetched, and putting one on a sold pin is how a board starts lying.

	Realtor's search rows carry NO year_built at all -- it exists only on
	`/property/details`, one billed call per comp. The key is present and None
	so no consumer has to special-case its absence.
	"""
	comps = _comps()
	loc = ((prop.get("location") or {}).get("address") or {})
	coord = loc.get("coordinate") or {}
	desc = prop.get("description") or {}
	flags = prop.get("flags") or {}
	address = str(loc.get("line") or "").strip()
	lat, lng = comps._num(coord.get("lat")), comps._num(coord.get("lon"))
	pid = str(prop.get("property_id") or "").strip()
	if not address or lat is None or lng is None or not pid:
		return None

	sold = kind == "forsold"
	sold_date = str(desc.get("sold_date") or prop.get("last_sold_date") or "").strip() or None
	sale_price = comps._num(desc.get("sold_price")) or comps._num(prop.get("last_sold_price"))
	list_price = comps._num(prop.get("list_price"))
	# `baths` is empty on an Apivex search row; `baths_consolidated` is the
	# populated one and arrives as a STRING ("4", sometimes "2.5").
	baths = (comps._num(desc.get("baths"))
			 or comps._num(desc.get("baths_consolidated"))
			 or comps._num(desc.get("baths_total")))

	if sold:
		state = "sold"
	elif flags.get("is_pending") or flags.get("is_contingent"):
		# Realtor carries pending and contingent inline on the for-sale set;
		# the flags are the only way to separate them, because `pending=true`
		# as a search parameter returns nothing usable.
		state = "pending"
	else:
		state = "for_sale"
	active = state in ("for_sale", "pending")
	price = sale_price if sold else list_price
	if not price:
		# A sold row with no sale price tells us nothing a rep can price off,
		# and an unpriced for-sale row is not a comparable either.
		return None

	photo = ""
	primary = prop.get("primary_photo") or {}
	if isinstance(primary, dict):
		photo = str(primary.get("href") or "").strip()

	return {
		"name": f"realtor::{pid}",
		"address": address,
		"city": loc.get("city") or "",
		"state": loc.get("state_code") or "",
		"zip": loc.get("postal_code") or "",
		"lat": lat,
		"lng": lng,
		"price": price,
		"status": "Active" if active else "Inactive",
		"listing_state": state,
		"listed_date": str(prop.get("list_date") or "").strip() or None if active else None,
		"removed_date": None if active else sold_date,
		"days_on_market": None,
		"days_old": None,
		"bedrooms": comps._num(desc.get("beds")),
		"bathrooms": baths,
		"square_footage": comps._num(desc.get("sqft")),
		"year_built": comps._num(desc.get("year_built")),
		"lot_size": None,
		"property_type": (str(desc.get("type") or "").replace("_", " ").title() or None),
		"source": "realtor",
		"photo": photo,
		"distance_mi": round(distance_mi, 2),
		"selected": False,
		"hidden": False,
		"sale_source": "realtor" if sold else None,
	}


# ── the merge ──────────────────────────────────────────────────────────────

def _index(rows):
	"""address key -> row. First row under a key wins, same as zillow_comps."""
	by_key = {}
	for row in rows:
		key = _merge_key(row.get("address"))
		if key and key not in by_key:
			by_key[key] = row
	return by_key


def apply_redfin(rows, features, lat, lng, radius_mi, today, self_keys=None):
	"""Redfin takes authority on every row it matches, and ADDS the rest.

	Mutates `rows`. Returns a status dict the response carries so the desk can
	say where its comps came from.

	ADC ROWS ARE EXEMPT FROM AUTHORITY and that is deliberate. An Auction.com
	pin is a recorded auction sale carrying `price_basis: adc_sale` and a
	deliberately `Unknown` status; letting an MLS row overwrite its price would
	replace the number the house actually fetched at auction with a listing
	figure. Redfin still stamps photos and a listing_state on them through the
	existing `apply_istl_comps` overlay, which is a different claim.
	"""
	from crm.api.comp_provenance import is_adc

	comps = _comps()
	info = {
		"used": False, "added": 0, "authority": 0, "conflicts": 0,
		"homes": 0, "skipped_adc": 0,
	}
	self_keys = self_keys or set()
	by_key = _index(rows)
	seen_incoming = set()

	for feature in features or []:
		props = (feature or {}).get("properties") or {}
		# The store's GeoJSON keeps the point in `geometry`; the flattened
		# properties may or may not repeat it, so read both.
		geom = (feature or {}).get("geometry") or {}
		coords = geom.get("coordinates") or []
		if len(coords) == 2 and props.get("lat") is None:
			props = dict(props, lng=coords[0], lat=coords[1])
		plat, plng = comps._num(props.get("lat")), comps._num(props.get("lng"))
		if plat is None or plng is None:
			continue
		dist = comps._haversine_mi(lat, lng, plat, plng)
		if dist > radius_mi:
			continue
		info["homes"] += 1
		key = _merge_key(props.get("address"))
		if not key or key in self_keys or key in seen_incoming:
			continue
		seen_incoming.add(key)
		incoming = redfin_row(props, dist)
		if incoming is None:
			continue

		existing = by_key.get(key)
		if existing is None:
			incoming["recency_days"] = comps._recency_days(incoming, today)
			rows.append(incoming)
			by_key[key] = incoming
			info["added"] += 1
			continue

		if is_adc(existing):
			info["skipped_adc"] += 1
			continue
		if _take_authority(existing, incoming, today, info):
			info["authority"] += 1

	info["used"] = bool(info["added"] or info["authority"])
	return info


def _take_authority(existing, incoming, today, info):
	"""Overwrite `existing` with Redfin's values. Returns True if anything moved.

	Only fields Redfin actually HAS. A Redfin row with a null sqft is Redfin
	declining to answer, not Redfin asserting that the house has no floor area,
	so a blank must never erase a value another provider supplied.
	"""
	comps = _comps()
	changed = False
	if baths_conflict(incoming.get("bathrooms"), existing.get("bathrooms")):
		info["conflicts"] += 1
	for field in REDFIN_AUTHORITY_FIELDS:
		val = incoming.get(field)
		if val in (None, "", 0):
			continue
		if existing.get(field) != val:
			existing[field] = val
			changed = True
	state = incoming.get("listing_state")
	if state and state != "off_market":
		existing["listing_state"] = state
		existing["status"] = incoming["status"]
		existing["current_status_source"] = "redfin"
		if state == "sold":
			existing["sale_source"] = "redfin"
			if incoming.get("removed_date"):
				existing["removed_date"] = incoming["removed_date"]
		changed = True
	if incoming.get("redfin_url") and not existing.get("redfin_url"):
		existing["redfin_url"] = incoming["redfin_url"]
	if incoming.get("redfin_status"):
		existing["redfin_status"] = incoming["redfin_status"]
	if changed:
		existing["field_authority"] = "redfin"
		existing["recency_days"] = comps._recency_days(existing, today)
	return changed


def apply_realtor(rows, results, lat, lng, radius_mi, today, kind, self_keys=None):
	"""Realtor ADDS houses nobody else returned. It never overwrites a field.

	Realtor is the biggest single contributor to the union (21% sole source)
	and simultaneously the worst field authority measured (outlier on price 45%
	of the time). Both facts point the same way: take its houses, ignore its
	opinions about houses we already have.
	"""
	comps = _comps()
	info = {"used": False, "added": 0, "duplicate": 0, "unplaceable": 0}
	self_keys = self_keys or set()
	by_key = _index(rows)
	for prop in results or []:
		if not isinstance(prop, dict):
			continue
		loc = ((prop.get("location") or {}).get("address") or {})
		coord = loc.get("coordinate") or {}
		plat, plng = comps._num(coord.get("lat")), comps._num(coord.get("lon"))
		if plat is None or plng is None:
			info["unplaceable"] += 1
			continue
		dist = comps._haversine_mi(lat, lng, plat, plng)
		if dist > radius_mi:
			continue
		key = _merge_key(loc.get("line"))
		if not key or key in self_keys:
			continue
		if key in by_key:
			info["duplicate"] += 1
			continue
		row = realtor_row(prop, dist, kind)
		if row is None:
			continue
		row["recency_days"] = comps._recency_days(row, today)
		rows.append(row)
		by_key[key] = row
		info["added"] += 1
	info["used"] = bool(info["added"])
	return info


def fetch_and_apply_realtor(rows, doc, lat, lng, radius_mi, today, self_keys=None):
	"""Ask the warehouse for Realtor's sold circle and add what is new.

	ONE warehouse call per comps-open, for the SOLD window only. For-sale is
	deliberately not fetched: Zillow and Redfin both already cover live listings
	well, the union gain the benchmark measured is on SOLD comps, and a second
	call would double this feature's share of a 50k/month Apivex ceiling for the
	half of the board that needs it least.

	The ZIP is passed whenever the lead has one -- see `vendor_facts.realtor_search`
	for why that one parameter is worth 35 points of recall. With no ZIP the call
	still runs on coordinates alone and the warehouse says so in `coverage`, which
	is carried through rather than dropped: an answer that is a third of the
	market must not be indistinguishable from a complete one.
	"""
	from crm.api import vendor_facts

	info = {"used": False, "added": 0, "reason": None, "coverage": None, "calls": 1}
	zip_code = str(doc.get("property_zip") or "").strip()
	envelope = vendor_facts.realtor_search(
		lat=lat, lng=lng, radius_mi=radius_mi, zip_code=zip_code, kind="forsold",
	)
	if envelope is None:
		return {**info, "calls": 0, "reason": "not_configured"}
	if envelope.get("ok") is False:
		return {**info, "reason": envelope.get("error") or "error"}
	payload = envelope.get("payload") or {}
	info["coverage"] = payload.get("coverage")
	info["source"] = envelope.get("source")
	# A cached answer cost no vendor call at all. Counting it as one would
	# overstate the quota this feature actually burns.
	if envelope.get("source") == "store":
		info["calls"] = 0
	info.update(apply_realtor(
		rows, payload.get("results"), lat, lng, radius_mi, today,
		kind="forsold", self_keys=self_keys,
	))
	if not zip_code:
		# Not an error, but the caller is looking at roughly a third of the
		# market and is entitled to know which third.
		info["reason"] = info.get("reason") or "no_zip"
	return info


# ── the BatchData gate ─────────────────────────────────────────────────────

def has_recorded_sale(row) -> bool:
	"""Is this row a real, priced, recorded SALE?

	This is the question the paid BatchData fallback turns on, and getting it
	wrong costs $0.15 a lead. Three things must all hold:

	  * a price -- an unpriced sold row prices nothing;
	  * `listing_state == "sold"` -- a for-sale ask is not a sale, and an ISTL
	    row labelled `off_market` is a last LIST that simply disappeared, which
	    is not a confirmed close (this is how Louisiana maps went yellow
	    without a single Sold pin);
	  * a VENDOR saying so -- either the row came from a provider search
	    (`zillow::`, `redfin::`, `realtor::`) or a provider stamped the sale
	    onto an existing pin, which `sale_source` records.

	The third clause is the one the merge added. Before it, the gate tested
	`name.startswith("zillow::")`, so a board full of `redfin::` solds read as
	"no priced sales" and fired BatchData on comps we already had.
	"""
	if not (row or {}).get("price"):
		return False
	if row.get("listing_state") != "sold":
		return False
	return bool(is_vendor_row(row) or row.get("sale_source"))

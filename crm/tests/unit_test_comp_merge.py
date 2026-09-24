"""The merged comp board: union arithmetic, Redfin authority, and the paid gate.

WHY MERGE AT ALL. Measured over 100 real subjects
(`redfin-scraper-api/bench/README.md`), a fallback chain rescues 3 of 100
subjects while the merge is worth x1.37 on sold comps, because no provider
holds more than about two thirds of the union: Redfin is sole source for 10%
of it, Zillow 11%, Realtor 21%. Falling back therefore answers the wrong
question -- not "did someone answer" but "how much of the block did we miss".

Four things are pinned here, and three of them cost money or correctness if
they regress:

  1. UNION ARITHMETIC. A house two providers both返 return must appear ONCE, and
     a house only one provider has must appear at all. Getting the first wrong
     double-counts the board; getting the second wrong is the 21% Realtor
     contribution silently going missing again.
  2. REDFIN IS FIELD AUTHORITY. On the rare row where providers disagree,
     Redfin is the outlier on price 12% of the time against Zillow's 42%, and
     on sqft 9% against 52%. Its values win; the other two may only ADD.
  3. HALF-BATHS ARE A CONVENTION, NOT A CONFLICT. Zillow rounds up -- 586
     pairs where Zillow was exactly 0.5 higher against 14 the other way. Merging
     without normalising manufactures hundreds of disagreements that do not
     exist.
  4. THE BATCHDATA GATE. It decides whether to spend $0.15 on a paid fallback.
     It used to ask `name.startswith("zillow::")`, so a board full of
     `redfin::` solds read as "nobody has a price" and billed for comps already
     in hand.

Comp NAMES are never rewritten by any of this, and that is load-bearing: hides
and picks live on `CRM Lead` as JSON arrays of comp names, so a house that was
`zillow::123` has to stay `zillow::123` for the life of the lead.
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import comp_merge, vendor_facts

TODAY = "2026-09-07"


def redfin_feature(pid, address, lat=43.0, lng=-87.9, **props):
	base = {
		"property_id": pid, "address": address, "lat": lat, "lng": lng,
		"price": 200000, "beds": 3, "baths": 2.0, "sqft": 1500,
		"year_built": 1950, "mls_status": "Sold", "sold_date": "2026-06-01",
		"property_type": "Single Family", "photos": [], "url": "/home/1",
	}
	base.update(props)
	return {"type": "Feature",
			"geometry": {"type": "Point", "coordinates": [lng, lat]},
			"properties": base}


def realtor_prop(pid, address, lat=43.0, lng=-87.9, **over):
	desc = {"beds": 3, "baths_consolidated": "2", "sqft": 1400,
			"sold_price": 210000, "sold_date": "2026-05-01", "type": "single_family"}
	desc.update(over.pop("description", {}))
	prop = {
		"property_id": pid,
		"location": {"address": {"line": address, "city": "Milwaukee",
								 "state_code": "WI", "postal_code": "53206",
								 "coordinate": {"lat": lat, "lon": lng}}},
		"description": desc,
		"flags": {},
	}
	prop.update(over)
	return prop


def pool_row(name, address, lat=43.0, lng=-87.9, **over):
	row = {
		"name": name, "address": address, "lat": lat, "lng": lng,
		"price": 111111, "status": "Inactive", "listing_state": "off_market",
		"bedrooms": 2, "bathrooms": 1.0, "square_footage": 900,
		"year_built": 1900, "source": "istl", "photo": "",
		"distance_mi": 0.1, "selected": False, "hidden": False,
	}
	row.update(over)
	return row


class UnionArithmetic(unittest.TestCase):
	"""A house both providers hold appears once; a house only one holds appears."""

	def test_a_redfin_house_nobody_else_has_is_added(self):
		rows = [pool_row("ISTL-1", "100 N First St")]
		info = comp_merge.apply_redfin(
			rows, [redfin_feature("R9", "500 W Other Ave")], 43.0, -87.9, 1.0, TODAY)
		self.assertEqual(info["added"], 1)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[1]["name"], "redfin::R9")

	def test_a_house_both_hold_is_not_duplicated(self):
		rows = [pool_row("ISTL-1", "100 N First St")]
		info = comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St")], 43.0, -87.9, 1.0, TODAY)
		self.assertEqual(info["added"], 0)
		self.assertEqual(len(rows), 1, "the same house must not appear twice")

	def test_street_suffix_spelling_still_collides(self):
		"""ISTL writes `St`, a vendor writes `STREET`. Same house, one pin.

		This is exactly why the merge reuses `zillow_comps.merge_key` instead of
		comparing raw strings -- a second normalisation would drift from it and
		start duplicating pins.
		"""
		rows = [pool_row("ISTL-1", "100 N First St")]
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First STREET")], 43.0, -87.9, 1.0, TODAY)
		self.assertEqual(len(rows), 1)

	def test_zillow_full_address_collides_with_street_line(self):
		"""Zillow appends `, City, ST ZIP`; Realtor and Redfin do not.

		The exact shapes from CRM-LEAD-2026-01471 (2026-09-24), where every one
		of these landed twice on the board.
		"""
		pairs = [
			("6544 N 67th St, Milwaukee, WI 53223", "6544 N 67th St"),
			("6650 W Clovernook COURT, Milwaukee, WI 53223", "6650 W Clovernook Ct"),
			("6579 N 66th STREET, Milwaukee, WI 53223", "6579 N 66th St"),
			("6526 North 68th Street", "6526 N. 68th St"),
		]
		for zillow_addr, realtor_addr in pairs:
			rows = [pool_row("zillow::1", zillow_addr)]
			info = comp_merge.apply_realtor(
				rows, [realtor_prop("A1", realtor_addr)], 43.0, -87.9, 1.0, TODAY, kind="forsold")
			self.assertEqual((info["added"], info["duplicate"]), (0, 1), zillow_addr)

	def test_subject_full_address_excludes_its_street_line_echo(self):
		"""The lead stores the full address; Redfin echoes the street line."""
		from crm.api.zillow_comps import merge_key

		self_keys = {merge_key("6526 N 68th St, Milwaukee, WI 53223")}
		rows = []
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "6526 N 68th St")], 43.0, -87.9, 1.0, TODAY,
			self_keys=self_keys)
		self.assertEqual(rows, [], "the subject must not be its own comp")

	def test_units_stay_distinct_and_spellings_collide(self):
		from crm.api.zillow_comps import merge_key

		self.assertEqual(merge_key("10 Oak Ave, Apt 4, Milwaukee, WI"), merge_key("10 Oak Ave #4"))
		self.assertEqual(merge_key("10 Oak Ave Unit 4"), merge_key("10 Oak Avenue Apt 4"))
		self.assertNotEqual(merge_key("10 Oak Ave #4"), merge_key("10 Oak Ave #5"))
		self.assertNotEqual(merge_key("10 Oak Ave"), merge_key("12 Oak Ave"))
		self.assertEqual(merge_key(", Milwaukee, WI"), merge_key(""))

	def test_realtor_adds_only_what_is_new(self):
		rows = [pool_row("zillow::7", "100 N First St")]
		info = comp_merge.apply_realtor(
			rows,
			[realtor_prop("A1", "100 N First St"), realtor_prop("A2", "22 S Park Rd")],
			43.0, -87.9, 1.0, TODAY, kind="forsold")
		self.assertEqual(info["added"], 1)
		self.assertEqual(info["duplicate"], 1)
		self.assertEqual({r["name"] for r in rows}, {"zillow::7", "realtor::A2"})

	def test_the_union_is_the_sum_of_the_sole_sources(self):
		"""Three providers, one shared house: 1 + 1 + 1 distinct = 3 pins."""
		rows = [pool_row("zillow::7", "1 Shared St")]
		comp_merge.apply_redfin(
			rows,
			[redfin_feature("R1", "1 Shared St"), redfin_feature("R2", "2 Redfin Ave")],
			43.0, -87.9, 1.0, TODAY)
		comp_merge.apply_realtor(
			rows,
			[realtor_prop("A1", "1 Shared St"), realtor_prop("A2", "3 Realtor Blvd")],
			43.0, -87.9, 1.0, TODAY, kind="forsold")
		self.assertEqual(len(rows), 3)
		self.assertEqual(
			{r["name"] for r in rows},
			{"zillow::7", "redfin::R2", "realtor::A2"})

	def test_the_subject_is_never_its_own_comp(self):
		rows = []
		keys = comp_merge._index([{"address": "9 Subject Way"}])
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "9 Subject Way")], 43.0, -87.9, 1.0, TODAY,
			self_keys=set(keys))
		self.assertEqual(rows, [])

	def test_a_house_outside_the_radius_is_dropped(self):
		rows = []
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "Far Away Rd", lat=44.0)],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows, [])

	def test_an_unplaceable_row_is_never_a_comp(self):
		"""No coordinates means no pin. A blank address would also collide with
		every other blank address under `merge_key`, silently merging them."""
		rows = []
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "", lat=None, lng=None)], 43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows, [])


class RedfinIsFieldAuthority(unittest.TestCase):
	"""Redfin's values win on a shared row. Vendors ADD houses, never overwrite."""

	def test_redfin_price_and_sqft_overwrite_the_vendor(self):
		rows = [pool_row("zillow::7", "100 N First St", price=250000, square_footage=1200)]
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St", price=199000, sqft=1500)],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows[0]["price"], 199000)
		self.assertEqual(rows[0]["square_footage"], 1500)
		self.assertEqual(rows[0]["field_authority"], "redfin")

	def test_the_name_is_never_rewritten(self):
		"""Hides and picks are stored by NAME on the lead. Renaming a row would
		silently un-hide a comp a rep discarded."""
		rows = [pool_row("zillow::7", "100 N First St")]
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St")], 43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows[0]["name"], "zillow::7")

	def test_a_blank_redfin_field_never_erases_a_vendor_value(self):
		"""Redfin declining to answer is not Redfin asserting the house has no
		floor area. A null must not wipe a number another provider supplied."""
		rows = [pool_row("zillow::7", "100 N First St", square_footage=1200, year_built=1965)]
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St", sqft=None, year_built=None)],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows[0]["square_footage"], 1200)
		self.assertEqual(rows[0]["year_built"], 1965)

	def test_realtor_never_overwrites_a_field(self):
		rows = [pool_row("zillow::7", "100 N First St", price=250000, square_footage=1200)]
		comp_merge.apply_realtor(
			rows, [realtor_prop("A1", "100 N First St",
								description={"sold_price": 999999, "sqft": 4242})],
			43.0, -87.9, 1.0, TODAY, kind="forsold")
		self.assertEqual(rows[0]["price"], 250000)
		self.assertEqual(rows[0]["square_footage"], 1200)

	def test_an_auction_com_row_keeps_its_own_sale_price(self):
		"""An ADC pin is a recorded AUCTION sale carrying `price_basis: adc_sale`.
		Letting an MLS row overwrite it replaces what the house actually fetched
		with a listing figure."""
		rows = [pool_row("ADC-1", "100 N First St", price=75000,
						 source_lead="auction:551", price_basis="adc_sale")]
		info = comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St", price=199000)],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows[0]["price"], 75000)
		self.assertEqual(info["skipped_adc"], 1)

	def test_a_redfin_sale_is_recorded_on_the_row_it_lands_on(self):
		rows = [pool_row("zillow::7", "100 N First St", listing_state="for_sale", status="Active")]
		comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St",
								  mls_status="Sold", sold_date="2026-07-04")],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(rows[0]["listing_state"], "sold")
		self.assertEqual(rows[0]["sale_source"], "redfin")
		self.assertEqual(rows[0]["removed_date"], "2026-07-04")


class HalfBaths(unittest.TestCase):
	"""Zillow rounds a half-bath up. 586 pairs high against 14 low -- 42:1."""

	def test_a_half_bath_is_not_a_conflict(self):
		self.assertFalse(comp_merge.baths_conflict(2.5, 3.0))
		self.assertFalse(comp_merge.baths_conflict(1.5, 2.0))

	def test_a_real_whole_bath_gap_still_is_one(self):
		self.assertTrue(comp_merge.baths_conflict(2.0, 3.0))

	def test_a_missing_count_is_never_a_conflict(self):
		self.assertFalse(comp_merge.baths_conflict(None, 3.0))
		self.assertFalse(comp_merge.baths_conflict(2.5, None))

	def test_the_convention_does_not_inflate_the_conflict_count(self):
		rows = [pool_row("zillow::7", "100 N First St", bathrooms=3.0)]
		info = comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St", baths=2.5)],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(info["conflicts"], 0,
						 "2.5 vs 3.0 is the Zillow convention, not a disagreement")
		self.assertEqual(rows[0]["bathrooms"], 2.5, "Redfin still wins the value")

	def test_a_genuine_disagreement_is_still_counted(self):
		rows = [pool_row("zillow::7", "100 N First St", bathrooms=4.0)]
		info = comp_merge.apply_redfin(
			rows, [redfin_feature("R1", "100 N First St", baths=2.0)],
			43.0, -87.9, 1.0, TODAY)
		self.assertEqual(info["conflicts"], 1)

	def test_realtors_string_bath_count_is_read(self):
		"""`baths` is empty on an Apivex search row; `baths_consolidated` is the
		populated one and arrives as a STRING."""
		row = comp_merge.realtor_row(
			realtor_prop("A1", "1 X St", description={"baths_consolidated": "2.5"}),
			0.1, "forsold")
		self.assertEqual(row["bathrooms"], 2.5)


class BatchDataGate(unittest.TestCase):
	"""The $0.15 question: does this board already have a recorded sale price?"""

	def test_a_redfin_sold_counts_as_a_price(self):
		self.assertTrue(comp_merge.has_recorded_sale(
			{"name": "redfin::1", "price": 100, "listing_state": "sold"}))

	def test_a_realtor_sold_counts_as_a_price(self):
		self.assertTrue(comp_merge.has_recorded_sale(
			{"name": "realtor::1", "price": 100, "listing_state": "sold"}))

	def test_a_zillow_sold_still_counts(self):
		self.assertTrue(comp_merge.has_recorded_sale(
			{"name": "zillow::1", "price": 100, "listing_state": "sold"}))

	def test_a_stamped_sale_on_an_istl_pin_counts(self):
		"""Redfin can confirm a sale on a pin that keeps its ISTL docname. The
		name carries no prefix, so `sale_source` is the only evidence."""
		self.assertTrue(comp_merge.has_recorded_sale(
			{"name": "ISTL-1", "price": 100, "listing_state": "sold",
			 "sale_source": "redfin"}))

	def test_an_istl_last_ask_is_not_a_sale(self):
		"""`off_market` means a listing disappeared, which is not a close. This
		is how Louisiana maps went yellow without a single Sold pin."""
		self.assertFalse(comp_merge.has_recorded_sale(
			{"name": "ISTL-1", "price": 100, "listing_state": "off_market"}))

	def test_a_for_sale_ask_is_not_a_sale(self):
		self.assertFalse(comp_merge.has_recorded_sale(
			{"name": "zillow::1", "price": 100, "listing_state": "for_sale"}))

	def test_an_unpriced_sold_is_not_a_price(self):
		self.assertFalse(comp_merge.has_recorded_sale(
			{"name": "redfin::1", "price": None, "listing_state": "sold"}))

	def test_a_bare_istl_sold_with_no_vendor_behind_it_is_not_trusted(self):
		self.assertFalse(comp_merge.has_recorded_sale(
			{"name": "ISTL-1", "price": 100, "listing_state": "sold"}))

	def test_a_full_redfin_board_does_not_fire_the_paid_fallback(self):
		"""The regression this change exists to prevent. Before the fix the gate
		asked `name.startswith("zillow::")`, so a board of Redfin solds looked
		empty and billed $0.15 for comps already in hand."""
		board = [
			{"name": "redfin::1", "price": 210000, "listing_state": "sold"},
			{"name": "redfin::2", "price": 185000, "listing_state": "sold"},
		]
		self.assertTrue(any(comp_merge.has_recorded_sale(r) for r in board))

	def test_a_board_with_no_recorded_sale_still_fires_it(self):
		"""The other half: a non-disclosure market really does need BatchData,
		and a gate that never fires is as broken as one that always does."""
		board = [
			{"name": "ISTL-1", "price": 100000, "listing_state": "off_market"},
			{"name": "zillow::9", "price": 250000, "listing_state": "for_sale"},
		]
		self.assertFalse(any(comp_merge.has_recorded_sale(r) for r in board))


class RealtorSearchCall(unittest.TestCase):
	"""The ZIP half is 35 points of recall and is passed as `zip`, not `zip_code`."""

	def test_zip_is_sent_under_the_parameter_name_the_endpoint_binds(self):
		with patch.object(vendor_facts, "_get", return_value={"ok": True}) as get:
			vendor_facts.realtor_search(lat=43.0, lng=-87.9, radius_mi=0.5,
										zip_code="53206", kind="forsold")
		path, params = get.call_args[0]
		self.assertEqual(path, "/realtor/search")
		self.assertEqual(params["zip"], "53206")
		self.assertNotIn("zip_code", params,
						 "FastAPI binds by name; `zip_code` is silently ignored")

	def test_the_circle_is_sent_too(self):
		with patch.object(vendor_facts, "_get", return_value={"ok": True}) as get:
			vendor_facts.realtor_search(lat=43.0, lng=-87.9, radius_mi=0.5,
										zip_code="53206")
		params = get.call_args[0][1]
		self.assertEqual((params["lat"], params["lng"], params["radius_mi"]),
						 (43.0, -87.9, 0.5))

	def test_a_lead_with_no_zip_still_searches_the_circle(self):
		with patch.object(vendor_facts, "_get", return_value={"ok": True}) as get:
			vendor_facts.realtor_search(lat=43.0, lng=-87.9, radius_mi=0.5, zip_code="")
		params = get.call_args[0][1]
		self.assertNotIn("zip", params)
		self.assertIn("lat", params)

	def test_no_circle_and_no_zip_is_not_a_call(self):
		with patch.object(vendor_facts, "_get") as get:
			self.assertIsNone(vendor_facts.realtor_search())
		get.assert_not_called()

	def test_the_lead_zip_reaches_the_search(self):
		doc = {"property_zip": "53206"}
		envelope = {"ok": True, "source": "live",
					"payload": {"results": [realtor_prop("A1", "5 New St")]}}
		with patch.object(vendor_facts, "realtor_search", return_value=envelope) as rs:
			rows = []
			comp_merge.fetch_and_apply_realtor(rows, doc, 43.0, -87.9, 0.5, TODAY)
		self.assertEqual(rs.call_args.kwargs["zip_code"], "53206")
		self.assertEqual(len(rows), 1)

	def test_a_partial_coverage_block_is_carried_not_discarded(self):
		"""Coordinates-only recall is 33%. An answer that is a third of the
		market must not look identical to a complete one."""
		coverage = {"partial": True, "skipped": ["zip"], "expected_recall": 0.33}
		envelope = {"ok": True, "source": "live",
					"payload": {"results": [], "coverage": coverage}}
		with patch.object(vendor_facts, "realtor_search", return_value=envelope):
			info = comp_merge.fetch_and_apply_realtor([], {}, 43.0, -87.9, 0.5, TODAY)
		self.assertEqual(info["coverage"], coverage)
		self.assertEqual(info["reason"], "no_zip")

	def test_an_unreachable_warehouse_costs_nothing_and_says_so(self):
		with patch.object(vendor_facts, "realtor_search", return_value=None):
			info = comp_merge.fetch_and_apply_realtor([], {}, 43.0, -87.9, 0.5, TODAY)
		self.assertEqual(info["calls"], 0)
		self.assertEqual(info["reason"], "not_configured")
		self.assertFalse(info["used"])

	def test_a_cached_answer_is_not_counted_as_a_vendor_call(self):
		envelope = {"ok": True, "source": "store", "payload": {"results": []}}
		with patch.object(vendor_facts, "realtor_search", return_value=envelope):
			info = comp_merge.fetch_and_apply_realtor([], {"property_zip": "1"},
													  43.0, -87.9, 0.5, TODAY)
		self.assertEqual(info["calls"], 0, "a store hit spends no Apivex quota")

	def test_a_live_answer_is_exactly_one_call(self):
		envelope = {"ok": True, "source": "live", "payload": {"results": []}}
		with patch.object(vendor_facts, "realtor_search", return_value=envelope):
			info = comp_merge.fetch_and_apply_realtor([], {"property_zip": "1"},
													  43.0, -87.9, 0.5, TODAY)
		self.assertEqual(info["calls"], 1)


class RowShaping(unittest.TestCase):
	def test_a_sold_realtor_row_prices_off_the_SALE_not_the_ask(self):
		"""A sold row's `list_price` is what it was asking, which is not what it
		fetched. Putting one on a sold pin is how a board starts lying."""
		row = comp_merge.realtor_row(
			realtor_prop("A1", "1 X St", list_price=999999,
						 description={"sold_price": 210000}),
			0.1, "forsold")
		self.assertEqual(row["price"], 210000)
		self.assertEqual(row["listing_state"], "sold")

	def test_a_pending_realtor_row_is_flagged_from_the_for_sale_set(self):
		prop = realtor_prop("A1", "1 X St", list_price=300000)
		prop["flags"] = {"is_pending": True}
		row = comp_merge.realtor_row(prop, 0.1, "forsale")
		self.assertEqual(row["listing_state"], "pending")
		self.assertEqual(row["status"], "Active")

	def test_an_unpriced_realtor_row_is_dropped(self):
		row = comp_merge.realtor_row(
			realtor_prop("A1", "1 X St", description={"sold_price": None}),
			0.1, "forsold")
		self.assertIsNone(row)

	def test_a_public_record_sale_with_no_mls_status_is_still_sold(self):
		"""Redfin serves public-record sales with a null mlsStatus -- 31 of 98
		rows on one measured box. Dropping them loses the no-agent cash and
		distressed transactions this desk exists to find."""
		row = comp_merge.redfin_row(
			{"property_id": "R1", "address": "1 X St", "lat": 43.0, "lng": -87.9,
			 "price": 90000, "mls_status": None, "sold_date": "2026-01-05"},
			0.1)
		self.assertEqual(row["listing_state"], "sold")
		self.assertEqual(row["sale_source"], "redfin")

	def test_a_redfin_row_carries_its_photos(self):
		row = comp_merge.redfin_row(
			{"property_id": "R1", "address": "1 X St", "lat": 43.0, "lng": -87.9,
			 "photos": ["https://a/1.jpg", "https://a/2.jpg"]},
			0.1)
		self.assertEqual(row["photo"], "https://a/1.jpg")
		self.assertEqual(len(row["photos"]), 2)


if __name__ == "__main__":
	unittest.main()


class AuthoritySurvivesTheBilledPass(unittest.TestCase):
	"""`attach_sale_history` runs AFTER the merge with a billed /property payload.

	It calls `zillow_comps._apply_facts` on the final board, which used to
	overwrite unconditionally. That would have silently undone Redfin authority
	at the very last step -- the merge would look correct in isolation and be
	wrong on screen, which is the worst shape a bug can take.
	"""

	def test_zillow_cannot_overwrite_a_redfin_field_afterwards(self):
		from crm.api import zillow_comps

		row = {"field_authority": "redfin", "square_footage": 1500, "bathrooms": 2.5}
		zillow_comps._apply_facts(row, {"square_footage": 1200, "bathrooms": 3.0})
		self.assertEqual(row["square_footage"], 1500)
		self.assertEqual(row["bathrooms"], 2.5)

	def test_zillow_may_still_fill_a_blank_redfin_left(self):
		"""Redfin returning no year is Redfin declining to answer, not an
		assertion the house has none. Zillow's number beats nothing."""
		from crm.api import zillow_comps

		row = {"field_authority": "redfin", "square_footage": 1500, "year_built": None}
		zillow_comps._apply_facts(row, {"year_built": 1962})
		self.assertEqual(row["year_built"], 1962)

	def test_a_non_redfin_row_is_untouched_by_the_guard(self):
		"""The pre-existing ISTL-vs-Zillow behaviour must not change."""
		from crm.api import zillow_comps

		row = {"square_footage": 900, "bedrooms": 2}
		zillow_comps._apply_facts(row, {"square_footage": 1400, "bedrooms": 3})
		self.assertEqual(row["square_footage"], 1400)
		self.assertEqual(row["bedrooms"], 3)

	def test_a_redfin_added_row_carries_the_marker(self):
		row = comp_merge.redfin_row(
			{"property_id": "R1", "address": "1 X St", "lat": 43.0, "lng": -87.9,
			 "sqft": 1500}, 0.1)
		self.assertEqual(row["field_authority"], "redfin")

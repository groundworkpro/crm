"""Zillow answering for a DIFFERENT house, and Redfin standing in when it does.

The case (2026-11-05): "112 Highland Ave 13203" (Syracuse) went to Zillow and
came back as 112 Highland Ave, North Syracuse 13212, 5.8 mi away. The subject
card printed that house's 2 bd / 1 ba / 1,224 sqft / 1955, while Redfin's own
exact record for the right house (4,116 sqft) sat unused, and a twin record
saying only "Zillow doesn't recognize this address" sent the rep to the seller.
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install
install()

from crm.api import comps, properties


def doc(**kw):
	d = {
		"doctype": "CRM Property", "name": "PROP-00023",
		"property_address": "112 Highland Ave", "property_city": "Syracuse",
		"property_state": "NY", "property_zip": "13203",
		"property_lat": 43.05882, "property_lng": -76.13968,
		"parcel_lat": None, "parcel_lng": None, "parcel_source": "",
		"bedrooms": "", "bathrooms": "", "square_footage": "", "year_built": "",
		"creation": "2026-11-05 10:00:00",
	}
	d.update(kw)
	return type("Doc", (), {
		"doctype": d["doctype"], "name": d["name"],
		"get": lambda self, k, default=None: d.get(k, default),
	})()


#: What Zillow actually sent back for 112 Highland Ave 13203.
NORTH_SYRACUSE = {
	"zpid": "31694805", "address": "112 Highland Ave", "city": "North Syracuse",
	"state": "NY", "zip": "13212", "lat": 43.14158, "lng": -76.126144,
	"beds": 2, "baths": 1, "sqft": 1224, "year_built": 1955, "zestimate": 199200,
	"_queried_address": "112 Highland Ave, Syracuse, NY 13203",
}
#: The same blob as cached before city/zip were carried.
NORTH_SYRACUSE_OLD = {k: v for k, v in NORTH_SYRACUSE.items() if k not in ("city", "state", "zip")}

REDFIN = {
	"matched": True, "property_id": 72863687, "point_exact": True,
	"lat": 43.0588091, "lng": -76.139362, "sqft": "4116",
	"beds": None, "baths": None, "year_built": None,
}


def subject(d, zillow, rec=None):
	with patch.object(comps, "_self_listing", return_value=None), \
		 patch.object(comps, "_sqft_override", return_value=0), \
		 patch.object(comps, "_sqft_override_supported", return_value=True), \
		 patch.object(comps, "_redfin_first_for", return_value=False):
		import crm.api.zillow as zillow_api
		with patch.object(zillow_api, "facts_for_lead", return_value=zillow):
			return comps._subject_facts(d, rec)


class WrongHouseIsRejected(unittest.TestCase):
	def test_redfin_exact_point_rejects(self):
		v = comps._zillow_wrong_house(doc(), NORTH_SYRACUSE_OLD, REDFIN)
		self.assertEqual(v["checked_against"], "redfin")
		self.assertEqual(v["distance_mi"], 5.8)

	def test_other_zip_rejects_against_a_geocode(self):
		v = comps._zillow_wrong_house(doc(), NORTH_SYRACUSE, None)
		self.assertEqual(v["checked_against"], "geocode")
		self.assertIn("North Syracuse", v["address"])
		self.assertIn("13212", v["address"])

	def test_zip_read_off_the_street_line(self):
		d = doc(property_address="112 Highland Ave 13203", property_city="",
		        property_state="", property_zip="")
		self.assertIsNotNone(comps._zillow_wrong_house(d, NORTH_SYRACUSE, None))

	def test_other_street_rejects_against_a_geocode(self):
		d = doc(property_address="138 S Benbrook Rd", property_city="Butler",
		        property_state="PA", property_zip="16001",
		        property_lat=40.0, property_lng=-80.0)
		z = {"zpid": "1", "address": "138 N Benbrook Rd", "lat": 40.0146, "lng": -80.0}
		self.assertIsNotNone(comps._zillow_wrong_house(d, z, None))

	def test_exact_parcel_rejects(self):
		d = doc(parcel_lat=43.0588, parcel_lng=-76.1394, parcel_source="google",
		        property_lat=None, property_lng=None)
		v = comps._zillow_wrong_house(d, NORTH_SYRACUSE_OLD, None)
		self.assertEqual(v["checked_against"], "parcel")


class RightHouseIsKept(unittest.TestCase):
	def test_close_hit_is_kept(self):
		z = dict(NORTH_SYRACUSE, lat=43.0590, lng=-76.1394, zip="13203", city="Syracuse")
		self.assertIsNone(comps._zillow_wrong_house(doc(), z, REDFIN))

	def test_bad_geocode_does_not_overrule_a_same_address_hit(self):
		"""CRM-LEAD-2026-00939: the Census pin was 238 mi off, Zillow was right."""
		d = doc(property_lat=40.0, property_lng=-80.0)
		self.assertIsNone(comps._zillow_wrong_house(d, NORTH_SYRACUSE_OLD, None))

	def test_parcel_copied_from_zillow_is_not_a_witness(self):
		d = doc(parcel_lat=43.14158, parcel_lng=-76.126144, parcel_source="zillow",
		        property_lat=None, property_lng=None)
		self.assertIsNone(comps._zillow_wrong_house(d, NORTH_SYRACUSE_OLD, None))

	def test_unmatched_redfin_record_is_not_a_witness(self):
		rec = dict(REDFIN, matched=False)
		d = doc(property_lat=None, property_lng=None)
		self.assertIsNone(comps._zillow_wrong_house(d, NORTH_SYRACUSE_OLD, rec))

	def test_a_miss_is_left_alone(self):
		self.assertIsNone(comps._zillow_wrong_house(doc(), {"_queried_address": "x"}, REDFIN))


class SubjectFacts(unittest.TestCase):
	def test_rejected_hit_shows_none_of_its_numbers(self):
		out = subject(doc(), NORTH_SYRACUSE_OLD, REDFIN)
		self.assertIsNone(out["zpid"])
		self.assertFalse(out["has_zillow"])
		self.assertIsNone(out["zestimate"])
		self.assertIsNone(out["beds"])
		self.assertEqual(out["zillow_rejected"]["zpid"], "31694805")

	def test_redfin_answers_when_zillow_has_no_house_even_before_cutover(self):
		out = subject(doc(), NORTH_SYRACUSE_OLD, REDFIN)
		self.assertEqual(out["sqft"], 4116)
		self.assertEqual(out["source"]["sqft"], "redfin")
		out = subject(doc(), {"_queried_address": "x"}, REDFIN)
		self.assertEqual(out["sqft"], 4116)

	def test_pre_cutover_zillow_hit_still_wins(self):
		z = dict(NORTH_SYRACUSE, lat=43.0590, lng=-76.1394, zip="13203", city="Syracuse")
		out = subject(doc(), z, REDFIN)
		self.assertEqual(out["sqft"], 1224)
		self.assertIsNone(out["zillow_rejected"])

	def test_a_miss_opens_the_redfin_fetch_gate(self):
		from crm.api import redfin
		out = subject(doc(), {"_queried_address": "x"}, None)
		self.assertTrue(redfin.subject_has_vendor_facts(out))


class ZillowMatchBanner(unittest.TestCase):
	def _match(self, d, subj):
		import crm.api.zillow as zillow_api
		with patch.object(zillow_api, "unavailable_reason", return_value=None):
			return comps._zillow_match(d, subj)

	def test_rejection_reads_as_a_miss_with_redfin_cover(self):
		d = doc(zillow_zpid="31694805", zillow_fetched_at="2026-11-05")
		m = self._match(d, subject(d, NORTH_SYRACUSE_OLD, REDFIN))
		self.assertFalse(m["matched"])
		self.assertTrue(m["tried"])
		self.assertTrue(m["redfin_matched"])
		self.assertEqual(m["rejected"]["zpid"], "31694805")

	def test_redfin_parcel_counts_on_a_cold_load(self):
		d = doc(zillow_fetched_at="2026-11-05", parcel_source="redfin")
		m = self._match(d, subject(d, {"_queried_address": "x"}, None))
		self.assertTrue(m["redfin_matched"])

	def test_cold_load_fill(self):
		s = subject(doc(), {"_queried_address": "x"}, None)
		comps._fill_from_redfin(s, REDFIN)
		self.assertEqual(s["sqft"], 4116)
		self.assertEqual(s["sqft_label"], "4,116")
		self.assertTrue(s["has_redfin"])


class CompleteAddress(unittest.TestCase):
	HIT = {"city": "SYRACUSE", "state": "NY", "zip": "13203", "lat": 43.0588, "lng": -76.1397}

	def run_it(self, text, hit=HIT, **kw):
		parts = {"address": text, "city": "", "state": "", "zip": "", **kw}
		with patch.object(properties, "_census_parts", return_value=hit) as m:
			return properties._complete_address(parts), m

	def test_street_and_zip_gets_city_and_state(self):
		(out, point), _ = self.run_it("112 Highland Ave 13203")
		self.assertEqual(out["address"], "112 Highland Ave")
		self.assertEqual((out["city"], out["state"], out["zip"]), ("Syracuse", "NY", "13203"))
		self.assertEqual(point, (43.0588, -76.1397))

	def test_full_line_is_split(self):
		(out, _), _ = self.run_it("112 Highland Ave, Syracuse, NY 13203")
		self.assertEqual(out["address"], "112 Highland Ave")
		self.assertEqual(out["city"], "Syracuse")

	def test_census_zip_must_agree(self):
		(out, point), _ = self.run_it("112 Highland Ave 13212")
		self.assertIsNone(point)
		self.assertEqual(out["address"], "112 Highland Ave 13212")

	def test_bare_street_is_not_guessed(self):
		(out, point), m = self.run_it("112 Highland Ave")
		m.assert_not_called()
		self.assertIsNone(point)

	def test_city_named_in_text_must_match(self):
		(out, point), _ = self.run_it("112 Highland Ave, Liverpool")
		self.assertIsNone(point)

	def test_complete_parts_skip_the_lookup(self):
		(_, point), m = self.run_it("112 Highland Ave", city="Syracuse", state="NY", zip="13203")
		m.assert_not_called()

	def test_geocoder_down_keeps_what_was_typed(self):
		(out, point), _ = self.run_it("112 Highland Ave 13203", hit=None)
		self.assertIsNone(point)
		self.assertEqual(out["address"], "112 Highland Ave 13203")

	def test_street_named_like_the_city_keeps_it(self):
		hit = dict(self.HIT, city="WASHINGTON", state="DC", zip="20001")
		(out, _), _ = self.run_it("12 Washington, DC 20001", hit=hit)
		self.assertEqual(out["address"], "12 Washington")


if __name__ == "__main__":
	unittest.main()

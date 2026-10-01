"""Opening a Realtor or Redfin pin must not look it up as a CRM Comp.

Those names (`realtor::1146311171`, `redfin::…`) are minted by the vendor
search. They are not doctypes. `get_comp_details` used to fall through to
`CRM Comp` and throw "Comparable property … does not exist", so the gallery
never reached the photo ladder. Dennis hit this on every Realtor pin,
2026-09-22.
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

shim = install()

from crm.api import comps


def _lead_exists():
	shim.db.doctypes.add("CRM Comp")
	shim.db.values[("CRM Lead", "CRM-LEAD-1")] = True
	shim.db.get_value.reset_mock()


class DetailPinRows(unittest.TestCase):
	def test_caller_pin_keeps_locality(self):
		row = comps._caller_pin_row(
			"realtor::9", "114 Main", 41.1, -87.2, "Joliet", "IL", "60435",
		)
		self.assertEqual(row["name"], "realtor::9")
		self.assertEqual(row["address"], "114 Main")
		self.assertEqual(row["city"], "Joliet")
		self.assertEqual(row["state"], "IL")
		self.assertEqual(row["zip"], "60435")
		self.assertEqual((row["lat"], row["lng"]), (41.1, -87.2))

	def test_realtor_and_redfin_skip_the_comp_table(self):
		_lead_exists()
		seen = []

		def shape(row, zpid=None):
			seen.append(dict(row))
			return {"available": True, "comp": dict(row), "photos": ["https://cdn/a.jpg", "https://cdn/b.jpg"],
					"details": {"address": "114 Main"}, "redfin_url_pending": False}

		with patch.object(comps, "_shape_detail", side_effect=shape), \
			 patch.object(comps, "_detail_cached", return_value=None):
			for name in ("realtor::1146311171", "redfin::R9", "zillow::7"):
				out = comps.get_comp_details(
					"CRM-LEAD-1", name,
					address="114 Main", lat=41.1, lng=-87.2,
					city="Joliet", state="IL", zip="60435",
				)
				self.assertTrue(out["photos"])
		shim.db.get_value.assert_not_called()
		self.assertEqual([r["name"] for r in seen], ["realtor::1146311171", "redfin::R9", "zillow::7"])
		self.assertEqual(seen[0]["city"], "Joliet")
		self.assertEqual(seen[0]["zip"], "60435")

	def test_pool_docname_still_reads_crm_comp(self):
		_lead_exists()
		shim.db.get_value.return_value = {"name": "5-main-st-abcd", "address": "5 Main"}
		with patch.object(comps, "_shape_detail", return_value={"photos": ["a", "b"], "redfin_url_pending": False}), \
			 patch.object(comps, "_detail_cached", return_value=None):
			comps.get_comp_details("CRM-LEAD-1", "5-main-st-abcd")
		shim.db.get_value.assert_called()


class ZillowLookupLocality(unittest.TestCase):
	"""Exe, 2026-09-30: a Redfin pin at 1635 Oregon Ave S, St Louis Park MN
	opened as 1635 Oregon Ave, Steubenville OH — Zillow got the street only."""

	def test_pin_street_gets_city_state_zip(self):
		row = comps._caller_pin_row("redfin::50038980", "1635 Oregon Ave S", 44.9, -93.3,
			"Saint Louis Park", "MN", "55426")
		self.assertEqual(comps._zillow_lookup_address(row), "1635 Oregon Ave S, Saint Louis Park, MN 55426")

	def test_full_crm_comp_line_is_left_alone(self):
		row = {"address": "1611 Oregon Ave S, Saint Louis Park, MN 55426", "city": "Saint Louis Park", "state": "MN"}
		self.assertEqual(comps._zillow_lookup_address(row), row["address"])

	def test_other_state_is_a_mismatch(self):
		row = {"city": "Rockford", "state": "IL", "zip": "61108"}
		self.assertTrue(comps._locality_mismatch(row, {"city": "West Palm Beach", "state": "FL", "zip": "33407"}))
		self.assertFalse(comps._locality_mismatch(row, {"city": "Rockford", "state": "IL", "zip": "61108"}))
		# Same town, spelled differently, or a missing field: not a mismatch.
		self.assertFalse(comps._locality_mismatch({"city": "St. Louis Park", "state": "MN", "zip": "55426"},
			{"city": "Saint Louis Park", "state": "MN", "zip": "55426"}))
		self.assertFalse(comps._locality_mismatch({"state": ""}, {"state": "OH"}))

	def test_zillow_detail_sends_full_address_and_drops_wrong_town(self):
		from crm.api import vendor_facts
		shim.cache_store = {}
		sent = []

		def prop(address=None, zpid=None, photos=False):
			sent.append(address)
			return {"payload": {"zpid": 1}, "photos": {"photos": [1]}}

		wrong = {"zpid": "1", "address": "1635 Oregon Ave", "city": "Steubenville", "state": "OH", "zip": "43952", "price": 55000}
		row = {"name": "redfin::50038980", "address": "1635 Oregon Ave S", "city": "Saint Louis Park", "state": "MN", "zip": "55426"}
		with patch.object(vendor_facts, "zillow_property", side_effect=prop), \
			 patch.object(vendor_facts, "payload_or_fallback", side_effect=lambda env: (True, env)), \
			 patch("crm.api.zillow.normalize_detail", return_value=wrong), \
			 patch("crm.api.zillow.photo_urls", side_effect=lambda r: ["https://x/1.jpg"] if r else []):
			details, photos = comps._zillow_detail(row, None)
		self.assertEqual(sent, ["1635 Oregon Ave S, Saint Louis Park, MN 55426"])
		self.assertIsNone(details)
		self.assertEqual(photos, [])

	def test_cached_wrong_town_gallery_is_not_carried_forward(self):
		bad = {"comp": {"city": "Saint Louis Park", "state": "MN"}, "details": {"city": "Steubenville", "state": "OH"},
			"photos": ["a"] * 12}
		good = {"comp": {"city": "Rockford", "state": "IL"}, "details": {"city": "Rockford", "state": "IL"},
			"photos": ["a"] * 12}
		self.assertIsNone(comps.DETAIL_MIGRATIONS[4](bad))
		self.assertIs(comps.DETAIL_MIGRATIONS[4](good), good)

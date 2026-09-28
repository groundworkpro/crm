"""Contractor metro matching for the Photos & Lockbox board badge."""
import importlib.util
import os
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import contractors, dispo_buyers

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _builder():
	path = os.path.join(ROOT, "crm", "api", "data", "build_metro_counties.py")
	spec = importlib.util.spec_from_file_location("build_metro_counties", path)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


class LeadMetro(unittest.TestCase):
	def test_plain_county(self):
		self.assertEqual(contractors.lead_metro("Pueblo", "CO"), "Pueblo, CO")

	def test_full_state_name_and_case(self):
		self.assertEqual(contractors.lead_metro("ANOKA", "Minnesota"),
		                 "Minneapolis-St. Paul-Bloomington, MN-WI")

	def test_independent_city_falls_back(self):
		# Leads write "Norfolk", Census writes "Norfolk city" (no Norfolk County in VA).
		self.assertEqual(contractors.lead_metro("Norfolk", "VA"),
		                 "Virginia Beach-Chesapeake-Norfolk, VA-NC")

	def test_county_wins_over_same_named_city(self):
		# Baltimore County and Baltimore city are both in the same metro, but the
		# county key must be the one tried first.
		self.assertEqual(contractors.lead_metro("Baltimore County", "MD"),
		                 "Baltimore-Columbia-Towson, MD")

	def test_saint_abbreviation(self):
		self.assertEqual(contractors.lead_metro("St. Louis", "MO"), "St. Louis, MO-IL")

	def test_rural_county_is_none(self):
		self.assertIsNone(contractors.lead_metro("Klamath", "OR"))
		self.assertIsNone(contractors.lead_metro("", "OR"))
		self.assertIsNone(contractors.lead_metro("Pueblo", ""))

	def test_builder_normalises_like_the_runtime(self):
		# The builder carries a copy of dispo_buyers' normaliser (no frappe there).
		b = _builder()
		self.assertEqual(b._SUFFIX.pattern, dispo_buyers._SUFFIX.pattern)
		self.assertEqual(b._ABBR, dispo_buyers._ABBR)
		for raw in ("Doña Ana", "St. Mary's", "Prince George's", "Miami-Dade"):
			self.assertEqual(b._norm(raw), dispo_buyers._norm(raw))


class CardBadges(unittest.TestCase):
	CONTRACTORS = [
		contractors._shape({"name": "c1", "contractor_name": "Ana", "phone": "1",
		                    "does_photos": 1, "does_lockbox": 1,
		                    "metro_areas": '["Pueblo, CO"]'}),
		contractors._shape({"name": "c2", "contractor_name": "Bo", "phone": "2",
		                    "does_photos": 1, "does_lockbox": 0,
		                    "metro_areas": '["Pueblo, CO", "Denver-Aurora-Centennial, CO"]'}),
	]

	def badges(self, leads):
		with patch.object(contractors, "_active_contractors", return_value=self.CONTRACTORS):
			return contractors.card_badges(leads)

	def test_only_photos_lockbox_cards(self):
		out = self.badges([
			{"name": "L1", "status": "Photos & Lockbox In Progress",
			 "property_county": "Pueblo", "property_state": "CO"},
			{"name": "L2", "status": "New",
			 "property_county": "Pueblo", "property_state": "CO"},
		])
		self.assertEqual(list(out), ["L1"])
		self.assertEqual(out["L1"]["metro"], "Pueblo, CO")
		self.assertEqual([c["name"] for c in out["L1"]["contractors"]], ["c1", "c2"])
		self.assertEqual(out["L1"]["contractors"][0]["services"], ["Photos", "Lockbox"])

	def test_uncovered_metro_is_an_empty_list(self):
		out = self.badges([{"name": "L3", "status": "Photos & Lockbox In Progress",
		                    "property_county": "Pima", "property_state": "AZ"}])
		self.assertEqual(out["L3"], {"metro": "Tucson, AZ", "contractors": []})

	def test_no_photos_cards_skips_the_query(self):
		with patch.object(contractors, "_active_contractors") as q:
			self.assertEqual(contractors.card_badges([{"name": "x", "status": "New"}]), {})
			q.assert_not_called()


class Clean(unittest.TestCase):
	def test_metros_json_and_checks(self):
		v = contractors._clean({
			"contractor_name": "  Ana ", "email": "A@B.COM", "does_photos": True,
			"metro_areas": ["Pueblo, CO", "Pueblo, CO", ""], "bogus": "x",
		})
		self.assertEqual(v["contractor_name"], "Ana")
		self.assertEqual(v["email"], "a@b.com")
		self.assertEqual(v["does_photos"], 1)
		self.assertEqual(v["metro_areas"], '["Pueblo, CO"]')
		self.assertNotIn("bogus", v)


if __name__ == "__main__":
	unittest.main()

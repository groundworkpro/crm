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

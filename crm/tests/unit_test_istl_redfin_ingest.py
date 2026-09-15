"""ISTL pool comps take photos and MLS status from Redfin ingest coverage."""
import unittest

from crm.tests.frappe_shim import install

install()

from crm.api import redfin


def feature(address, mls_status="Sold", photos=None, url="/MN/x/home/1"):
	return {
		"type": "Feature",
		"properties": {
			"address": address,
			"mls_status": mls_status,
			"photos": photos or ["https://ssl.cdn-redfin.com/a.jpg", "https://ssl.cdn-redfin.com/b.jpg"],
			"url": url,
		},
	}


def istl(address, name="abc123"):
	return {
		"name": name,
		"address": address,
		"source": "istl",
		"status": "Inactive",
		"listing_state": "off_market",
		"photo": "",
		"source_lead": "CRM-LEAD-1",
	}


class MlsListingState(unittest.TestCase):
	def test_closed_sale_is_sold(self):
		self.assertEqual(redfin.mls_listing_state("Closed Sale"), "sold")

	def test_pending(self):
		self.assertEqual(redfin.mls_listing_state("Active Under Contract"), "pending")

	def test_active_listing(self):
		self.assertEqual(redfin.mls_listing_state("Active"), "for_sale")

	def test_blank_is_none(self):
		self.assertIsNone(redfin.mls_listing_state(""))


class ApplyIstlComps(unittest.TestCase):
	def test_matched_istl_gets_photo_and_status(self):
		row = istl("123 Main Street, Denver, CO 80211")
		stats = redfin.apply_istl_comps(
			[row],
			[feature("123 Main St", "Pending")],
		)
		self.assertEqual(stats["matched"], 1)
		self.assertEqual(stats["photos"], 1)
		self.assertEqual(row["photo"], "https://ssl.cdn-redfin.com/a.jpg")
		self.assertEqual(row["listing_state"], "pending")
		self.assertEqual(row["status"], "Active")
		self.assertEqual(row["current_status_source"], "redfin")
		self.assertEqual(row["redfin_url"], "https://www.redfin.com/MN/x/home/1")

	def test_suffix_collapse_matches_street_to_st(self):
		row = istl("9 Seybert Street, Philadelphia, PA 19123")
		redfin.apply_istl_comps([row], [feature("9 Seybert St", "Sold")])
		self.assertEqual(row["listing_state"], "sold")
		self.assertEqual(row["status"], "Inactive")

	def test_zillow_extras_are_left_alone(self):
		row = {
			"name": "zillow::99",
			"address": "123 Main St, Denver, CO 80211",
			"source": "zillow",
			"photo": "https://zillow/p.jpg",
			"listing_state": "for_sale",
			"status": "Active",
		}
		redfin.apply_istl_comps([row], [feature("123 Main St", "Sold")])
		self.assertEqual(row["photo"], "https://zillow/p.jpg")
		self.assertEqual(row["listing_state"], "for_sale")

	def test_adc_rows_are_left_alone(self):
		row = {
			"name": "adc-1",
			"address": "123 Main St",
			"source_lead": "auction:2012961",
			"listing_state": "unknown",
			"photo": "",
		}
		redfin.apply_istl_comps([row], [feature("123 Main St", "Sold")])
		self.assertEqual(row["listing_state"], "unknown")
		self.assertEqual(row["photo"], "")

	def test_unmatched_istl_keeps_rentcast_status(self):
		row = istl("1 Other Ave, Denver, CO 80211")
		redfin.apply_istl_comps([row], [feature("123 Main St", "Active")])
		self.assertEqual(row["listing_state"], "off_market")
		self.assertEqual(row["photo"], "")

	def test_match_without_mls_status_still_gets_photos(self):
		row = istl("123 Main St")
		redfin.apply_istl_comps(
			[row],
			[feature("123 Main St", mls_status="", photos=["https://ssl.cdn-redfin.com/a.jpg"])],
		)
		self.assertEqual(row["photo"], "https://ssl.cdn-redfin.com/a.jpg")
		self.assertEqual(row["listing_state"], "off_market")
		self.assertNotIn("current_status_source", row)

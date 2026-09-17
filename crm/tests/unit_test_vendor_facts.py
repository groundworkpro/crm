"""Gallery Zillow/Realtor calls go through PropWarehouse when it answers."""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import apivex, comps, vendor_facts


class PayloadOrFallback(unittest.TestCase):
	def test_none_and_not_configured_fall_back(self):
		self.assertEqual(vendor_facts.payload_or_fallback(None), (False, None))
		owned, env = vendor_facts.payload_or_fallback(
			{"ok": False, "error": "not_configured", "payload": None}
		)
		self.assertFalse(owned)
		self.assertIsNone(env)

	def test_miss_and_quota_are_owned(self):
		miss = {"ok": True, "matched": False, "payload": None}
		self.assertTrue(vendor_facts.payload_or_fallback(miss)[0])
		quota = {"ok": False, "error": "quota", "payload": None}
		self.assertTrue(vendor_facts.payload_or_fallback(quota)[0])


class GalleryUsesWarehouse(unittest.TestCase):
	def test_store_hit_skips_rapidapi(self):
		env = {
			"ok": True,
			"matched": True,
			"payload": {
				"zpid": 9,
				"streetAddress": "5 Main St",
				"homeStatus": "RECENTLY_SOLD",
				"imgSrc": "https://zillow/cover.jpg",
				"photos": [],
			},
			"photos": {"photos": []},
		}
		with patch.object(vendor_facts, "zillow_property", return_value=env), \
			 patch("crm.api.zillow._request") as billed:
			details, photos = comps._zillow_detail(
				{"name": "zillow::9", "address": "5 Main St"}, "9"
			)
		billed.assert_not_called()
		self.assertEqual(details["zpid"], 9)
		self.assertEqual(photos, ["https://zillow/cover.jpg"])

	def test_unreachable_falls_back(self):
		with patch.object(vendor_facts, "zillow_property", return_value=None), \
			 patch("crm.api.zillow._request", return_value={"zpid": 2, "streetAddress": "x"}) as billed, \
			 patch("crm.api.zillow.property_photos", return_value={"photos": []}):
			details, _photos = comps._zillow_detail(
				{"name": "zillow::2", "address": "x"}, "2"
			)
		billed.assert_called()
		self.assertEqual(details["zpid"], 2)


class RealtorUsesWarehouse(unittest.TestCase):
	def test_store_hit_skips_apivex(self):
		env = {"ok": True, "matched": True, "payload": {"photos": ["https://ap.rdcpix.com/a.jpg"]}}
		with patch.object(vendor_facts, "realtor_photos", return_value=env), \
			 patch.object(apivex, "_api_key") as key:
			urls = apivex.realtor_photo_urls("5 Main St")
		key.assert_not_called()
		self.assertEqual(urls, ["https://ap.rdcpix.com/a.jpg"])

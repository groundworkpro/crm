"""Gallery Zillow/Realtor calls go through PropWarehouse when it answers."""
import os
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import apivex, comps, vendor_facts


class BaseUrlResolution(unittest.TestCase):
	"""The vendor read-through can be pointed at propwarehouse-api by config.

	Defaulting to the scraper is what makes the split deployable in two steps:
	stand the new service up, flip one key, and unset it to roll back.
	"""

	def setUp(self):
		self.env = patch.dict(os.environ, {}, clear=False)
		self.env.start()
		os.environ.pop("PROPWAREHOUSE_URL", None)

	def tearDown(self):
		self.env.stop()

	def test_unset_falls_back_to_the_scraper(self):
		# Today's behaviour, and the thing that must not change on deploy.
		with patch("crm.api.geo._base_url", return_value="http://scraper:8110"):
			self.assertEqual(vendor_facts._base_url(), "http://scraper:8110")

	def test_site_config_wins(self):
		import frappe

		with patch.dict(frappe.conf, {"propwarehouse_url": "http://warehouse:8120"}), \
			 patch("crm.api.geo._base_url", return_value="http://scraper:8110"):
			self.assertEqual(vendor_facts._base_url(), "http://warehouse:8120")

	def test_env_var_is_used_when_site_config_is_silent(self):
		os.environ["PROPWAREHOUSE_URL"] = "http://warehouse-env:8120"
		with patch("crm.api.geo._base_url", return_value="http://scraper:8110"):
			self.assertEqual(vendor_facts._base_url(), "http://warehouse-env:8120")

	def test_trailing_slash_is_stripped(self):
		# `_get` builds f"{base}{path}", so a trailing slash would double it.
		os.environ["PROPWAREHOUSE_URL"] = "http://warehouse:8120/"
		with patch("crm.api.geo._base_url", return_value="http://scraper:8110"):
			self.assertEqual(vendor_facts._base_url(), "http://warehouse:8120")

	def test_blank_config_is_not_a_base_url(self):
		os.environ["PROPWAREHOUSE_URL"] = "   "
		with patch("crm.api.geo._base_url", return_value="http://scraper:8110"):
			self.assertEqual(vendor_facts._base_url(), "http://scraper:8110")


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

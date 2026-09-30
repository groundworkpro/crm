"""Redfin calls go through PropWarehouse's /redfin passthrough (Lance, 2026-09-29)."""
import unittest
from unittest.mock import MagicMock, patch

from crm.tests.frappe_shim import install

install()

import frappe

from crm.api import geo

CONF = {"redfin_scraper_url": "http://scraper:8110/", "propwarehouse_url": "http://pw:8120"}


class Route(unittest.TestCase):
	def setUp(self):
		self.cache = {}
		c = MagicMock()
		c.get_value.side_effect = self.cache.get
		c.set_value.side_effect = lambda k, v, expires_in_sec=None: self.cache.__setitem__(k, v)
		self.p_cache = patch.object(frappe, "cache", return_value=c, create=True)
		self.p_cache.start()

	def tearDown(self):
		self.p_cache.stop()

	def url(self, conf, health=200):
		resp = MagicMock(status_code=health)
		with patch.object(frappe, "conf", frappe._dict(conf), create=True), \
			 patch.object(geo.requests, "get", return_value=resp) as get:
			return geo._base_url(), get

	def test_through_propwarehouse_when_it_is_up(self):
		url, get = self.url(CONF)
		self.assertEqual(url, "http://pw:8120/redfin")
		self.assertEqual(get.call_args[0][0], "http://pw:8120/health")

	def test_health_is_remembered(self):
		self.url(CONF)
		_, get = self.url(CONF)
		get.assert_not_called()

	def test_direct_when_propwarehouse_is_down(self):
		url, _ = self.url(CONF, health=503)
		self.assertEqual(url, "http://scraper:8110")

	def test_direct_when_not_configured_or_switched_off(self):
		self.assertEqual(self.url({"redfin_scraper_url": "http://scraper:8110"})[0], "http://scraper:8110")
		self.assertEqual(self.url(dict(CONF, redfin_via_propwarehouse=0))[0], "http://scraper:8110")

	def test_no_scraper_url_still_means_redfin_is_off(self):
		self.assertEqual(self.url({"propwarehouse_url": "http://pw:8120"})[0], "")


if __name__ == "__main__":
	unittest.main()

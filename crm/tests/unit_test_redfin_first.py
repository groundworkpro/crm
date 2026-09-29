"""Redfin first: Zillow and Realtor fill in only when Redfin has fewer than
five listings or fewer than five recent sales in the circle (Lance, 2026-09-29).
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import redfin_listings as rl
from crm.api import zillow_comps

LAT, LNG = 45.166, -93.0393
OK = {"on_market_ok": True, "sold_ok": True}


def home(i, block="sold", **kw):
	return dict({"property_id": i, "address": f"{i} Oak St", "lat": LAT + (i % 50) * 1e-5,
				 "lng": LNG, "price": 300000, "_block": block}, **kw)


def board(listings, sales):
	return [home(i, "on_market") for i in range(listings)] + \
		[home(100 + i) for i in range(sales)]


class Decide(unittest.TestCase):
	def test_five_and_five_is_enough(self):
		d = rl.decide(board(5, 5), OK, LAT, LNG, 0.5)
		self.assertFalse(d["fill_in"])
		self.assertEqual((d["listings"], d["recent_sales"]), (5, 5))

	def test_four_listings_fills_in(self):
		self.assertTrue(rl.decide(board(4, 40), OK, LAT, LNG, 0.5)["fill_in"])

	def test_four_sales_fills_in(self):
		self.assertTrue(rl.decide(board(40, 4), OK, LAT, LNG, 0.5)["fill_in"])

	def test_no_answer_fills_in(self):
		d = rl.decide([], {"error": "timed_out"}, LAT, LNG, 0.5)
		self.assertTrue(d["fill_in"])
		self.assertEqual(d["reason"], "timed_out")

	def test_a_missing_half_is_not_an_empty_market(self):
		d = rl.decide(board(9, 9), {"on_market_ok": False, "sold_ok": True}, LAT, LNG, 0.5)
		self.assertTrue(d["fill_in"])
		self.assertEqual(d["reason"], "partial")

	def test_homes_outside_the_circle_do_not_count(self):
		far = [home(200 + i, "on_market", lat=LAT + 1) for i in range(10)]
		d = rl.decide(far + board(0, 9), OK, LAT, LNG, 0.5)
		self.assertEqual(d["listings"], 0)
		self.assertTrue(d["fill_in"])


class Features(unittest.TestCase):
	def test_listings_come_first_so_they_win_the_address(self):
		f = rl.features([home(1), home(2, "on_market")])
		self.assertEqual(f[0]["properties"]["property_id"], 2)

	def test_pending_reads_as_pending(self):
		from crm.api import redfin
		f = rl.features([home(1, "on_market", is_pending=True, status="Pending")])
		self.assertEqual(redfin.mls_listing_state(f[0]["properties"]["mls_status"]), "pending")

	def test_active_and_coming_soon_read_as_for_sale(self):
		from crm.api import redfin
		for st in ("Active", "ComingSoon"):
			f = rl.features([home(1, "on_market", status=st)])
			self.assertEqual(redfin.mls_listing_state(f[0]["properties"]["mls_status"]), "for_sale")

	def test_a_public_record_sale_with_no_status_is_still_sold(self):
		from crm.api import redfin
		f = rl.features([home(1, sold_date=1760400000000)])
		p = f[0]["properties"]
		self.assertEqual(redfin.mls_listing_state(p["mls_status"]), "sold")
		self.assertEqual(p["sold_date"], "2025-10-14")

	def test_listing_rows_carry_no_sale_date(self):
		f = rl.features([home(1, "on_market", sold_date=1760400000000)])
		self.assertIsNone(f[0]["properties"]["sold_date"])


class Finish(unittest.TestCase):
	def test_a_cached_answer_needs_no_thread(self):
		body = {"on_market": {"homes": [{"property_id": 1}]}, "sold": {"homes": []}}
		homes, meta = rl.finish({"key": "k", "body": body, "cached": True})
		self.assertEqual(homes[0]["_block"], "on_market")
		self.assertTrue(meta["cached"] and meta["on_market_ok"] and meta["sold_ok"])

	def test_no_job_is_not_configured(self):
		self.assertEqual(rl.finish(None)[1]["error"], "not_configured")

	def test_a_failed_block_is_reported(self):
		body = {"ok": False, "on_market": {"error": "X", "homes": []}, "sold": {"homes": []}}
		_, meta = rl.finish({"key": "k", "body": body, "cached": True})
		self.assertFalse(meta["on_market_ok"])


class ZillowAreaSkipped(unittest.TestCase):
	def test_enough_redfin_skips_the_area_search_but_not_the_pin_refresh(self):
		import frappe

		doc = frappe._dict(property_address="1 Main St")
		with patch.object(zillow_comps.zillow_api, "_api_key", return_value="k"), \
			 patch.object(zillow_comps, "area_comps") as area, \
			 patch.object(zillow_comps, "refresh_pins", return_value={"checked": 2, "updated": 1}) as pins, \
			 patch.object(zillow_comps, "_comps") as c:
			c.return_value._full_address.return_value = "1 Main St"
			info = zillow_comps.apply(doc, [], LAT, LNG, 0.5, area=False)
		area.assert_not_called()
		pins.assert_called_once()
		self.assertEqual(info["reason"], "redfin_enough")
		self.assertEqual(info["pins_checked"], 2)


if __name__ == "__main__":
	unittest.main()

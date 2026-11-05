"""Ask AI's comps board as normal comps (Exe, 2026-11-05).

"Make it so that AI comps can get through the filter and be treated as a normal comp so
the crm can update the information and reps can add it as a comp." 7041 S Throop St's pool
row was a June $190k delisting, so the filters hid the house the run priced off ($205k, 7/2).
"""

import types
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

install()

from crm.api import comps

SUBJECT = (41.7600, -87.6600)
LEAD = types.SimpleNamespace(
	doctype="CRM Lead", name="CRM-LEAD-1",
	get=lambda k, d=None: {"property_address": "1468 W 73rd St, Chicago, IL 60636"}.get(k, d),
)


def ai(address, lat, lng, **kw):
	return {"address": address, "lat": lat, "lng": lng, **kw}


def pool(name, address, lat, lng, price=190000):
	return {
		"name": name, "address": address, "lat": lat, "lng": lng, "price": price,
		"status": "Inactive", "listed_date": "2026-05-31", "removed_date": "2026-06-01",
		"source_lead": "x",
	}


class Missing(unittest.TestCase):
	def test_street_key_matches_across_formats(self):
		rows = [{"address": "7041 S Throop Street, Chicago, IL 60636"}]
		got = comps._ai_missing([ai("7041 S Throop St", 41.766, -87.656), ai("1 Main St", 41.76, -87.66)], rows)
		self.assertEqual([a["address"] for a in got], ["1 Main St"])

	def test_far_only_splits_on_radius(self):
		near, far = ai("1 A St", 41.7601, -87.6601), ai("2 B St", 41.80, -87.66)
		kw = dict(radius=0.5, lat=SUBJECT[0], lng=SUBJECT[1])
		self.assertEqual(comps._ai_missing([near, far], [], far_only=True, **kw), [far])
		self.assertEqual(comps._ai_missing([near, far], [], far_only=False, **kw), [near])


class PoolRow(unittest.TestCase):
	def test_existing_pool_row_is_used_not_duplicated(self):
		row = pool("7041-throop", "7041 S Throop St, Chicago, IL 60636", 41.766085, -87.656492)
		with mock.patch.object(comps.frappe, "get_all", create=True, return_value=[row]), \
			mock.patch.object(comps.frappe, "get_doc", create=True) as gd:
			got = comps._ai_pool_row(ai("7041 S Throop St", 41.7661, -87.6565, price=205000),
			                         LEAD, *SUBJECT, {"7041-throop"}, set(), "2026-11-05")
		gd.assert_not_called()
		self.assertEqual(got["name"], "7041-throop")
		self.assertTrue(got["ai_comp"])
		self.assertTrue(got["selected"])
		# The pool's facts; the board's Zillow/Redfin refresh corrects them.
		self.assertEqual(got["price"], 190000)

	def test_missing_house_is_created_from_the_runs_sale(self):
		made = {}

		class Doc:
			def __init__(self, f):
				made.update(f)

			def insert(self, **kw):
				return self

		stored = pool("x", "", 41.7661, -87.6565, price=205000)
		with mock.patch.object(comps.frappe, "get_all", create=True, return_value=[]), \
			mock.patch.object(comps.frappe, "get_doc", create=True, side_effect=Doc), \
			mock.patch.object(comps.frappe.db, "exists", create=True, return_value=False), \
			mock.patch.object(comps.frappe.db, "get_value", create=True, return_value=stored):
			got = comps._ai_pool_row(
				ai("7041 S Throop St", 41.7661, -87.6565, price=205000, date="2026-07-02",
				   beds=3, baths=1, sqft=987, year_built=1893),
				LEAD, *SUBJECT, set(), set(), "2026-11-05")
		self.assertEqual(made["address"], "7041 S Throop St, Chicago, IL")
		self.assertEqual((made["price"], made["removed_date"], made["bedrooms"], made["square_footage"]),
		                 (205000, "2026-07-02", 3, 987))
		self.assertEqual(made["source_lead"], "ai:CRM-LEAD-1")
		self.assertEqual(got["listing_state"], "sold")
		self.assertEqual(got["sale_source"], "ask_ai")


class RunComps(unittest.TestCase):
	def test_practice_reads_nothing(self):
		self.assertEqual(comps._ai_run_comps(types.SimpleNamespace(doctype="CRM Practice", name="P")), [])

	def test_comps_without_a_point_are_dropped(self):
		from crm.api import ai_pricer

		run = {"comps": [ai("1 A St", 1, 2), {"address": "2 B St"}]}
		with mock.patch.object(ai_pricer, "_allowed", return_value=True), \
			mock.patch.object(ai_pricer, "latest_run", return_value=run):
			self.assertEqual([c["address"] for c in comps._ai_run_comps(LEAD)], ["1 A St"])


if __name__ == "__main__":
	unittest.main()

"""A picked comp stays on the board when it sits outside the radius.

Dennis, 2026-10-29 (CRM-LEAD-2026-01927): "Add as comp" on 27 Booker St
(0.52 mi) and 95 Arcadia Cir (0.77 mi) saved both as picked, but the default
0.5 mi board dropped them, so the map and the offer calc never showed them.
"""

import unittest
from unittest import mock

from crm.tests.frappe_shim import install

install()

from crm.api import comps

SUBJECT = (33.7555, -84.4130)


def pool(name, lat, lng, price=300000):
	return {
		"name": name, "address": name, "lat": lat, "lng": lng, "price": price,
		"status": "Inactive", "listed_date": "2026-07-03", "removed_date": "2026-08-20",
		"source_lead": "x",
	}


class PickedOutsideRadius(unittest.TestCase):
	def run_it(self, selected, have=(), rows=(), self_key=None):
		with mock.patch.object(comps.frappe, "get_all", create=True, return_value=list(rows)) as ga:
			out = comps._picked_outside_radius(
				set(selected), set(have), *SUBJECT, self_key, set(), "2026-10-29"
			)
		return out, ga

	def test_picked_pool_row_outside_circle_comes_back_selected(self):
		row = pool("27-booker", 33.75525739, -84.42032459)
		out, ga = self.run_it({"27-booker", "on-board"}, have={"on-board"}, rows=[row])
		self.assertEqual(len(out), 1)
		got = out[0]
		self.assertTrue(got["selected"])
		self.assertTrue(got["outside_radius"])
		self.assertFalse(got["hidden"])
		self.assertGreater(got["distance_mi"], 0.3)
		self.assertEqual(got["listing_state"], "off_market")
		self.assertEqual(got["removed_date"], "2026-08-20")
		# Only the missing pick was asked for.
		self.assertEqual(ga.call_args.kwargs["filters"][0][3], ["27-booker"])

	def test_nothing_missing_means_no_query(self):
		out, ga = self.run_it({"a"}, have={"a"})
		self.assertEqual(out, [])
		ga.assert_not_called()

	def test_source_pins_and_subject_are_not_looked_up(self):
		out, ga = self.run_it({"zillow::123", "self"}, self_key="self")
		self.assertEqual(out, [])
		ga.assert_not_called()

	def test_row_without_point_is_skipped(self):
		out, _ = self.run_it({"nopoint"}, rows=[pool("nopoint", None, None)])
		self.assertEqual(out, [])


if __name__ == "__main__":
	unittest.main()

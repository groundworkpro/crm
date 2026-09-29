"""The 50-comp cap reserves slots for live listings.

2027 Willow Cir, Centerville MN: 421 comps within 1/2 mile, and the nearest 50
were all sales in the townhouse cluster, so the board never drew one of the 8
listings nearby even with every filter cleared.
"""

import unittest

from crm.tests.frappe_shim import install

install()

from crm.api import comps


def row(i, state="sold", selected=False):
	return {"name": f"r{i}", "distance_mi": i / 100, "listing_state": state, "selected": selected}


class CapBoard(unittest.TestCase):
	def test_under_cap_is_untouched(self):
		m = [row(i) for i in range(10)]
		self.assertEqual(comps._cap_board(m, 50), m)

	def test_far_listings_still_board(self):
		m = [row(i) for i in range(100)] + [row(100 + i, "for_sale") for i in range(5)] + [row(200, "pending")]
		out = comps._cap_board(m, 50)
		self.assertEqual(len(out), 50)
		self.assertEqual(sum(r["listing_state"] != "sold" for r in out), 6)
		self.assertEqual([r["distance_mi"] for r in out], sorted(r["distance_mi"] for r in out))

	def test_reserve_is_bounded(self):
		m = [row(i) for i in range(100)] + [row(100 + i, "for_sale") for i in range(40)]
		out = comps._cap_board(m, 50)
		self.assertEqual(sum(r["listing_state"] == "for_sale" for r in out), comps.LISTING_RESERVE)
		self.assertEqual(len(out), 50)

	def test_picked_comp_is_not_evicted(self):
		m = [row(i) for i in range(100)] + [row(150, selected=True)] + [row(200 + i, "for_sale") for i in range(20)]
		out = comps._cap_board(m, 50)
		self.assertIn("r150", {r["name"] for r in out})


if __name__ == "__main__":
	unittest.main()

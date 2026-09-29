"""The board is uncapped; only the billed sale-history lookups are budgeted,
and listings get the budget first.

2027 Willow Cir, Centerville MN: 421 comps within 1/2 mile, and the old
nearest-50 board was all sales, so none of the listings nearby ever drew.
"""

import unittest

from crm.tests.frappe_shim import install

install()

from crm.api import comps


def row(i, state="sold", selected=False):
	return {"name": f"r{i}", "distance_mi": i / 100, "listing_state": state, "selected": selected}


class HistoryOrder(unittest.TestCase):
	def test_small_board_all_billed(self):
		m = [row(i) for i in range(10)]
		paid, free = comps._history_order(m, 50)
		self.assertEqual((len(paid), free), (10, []))

	def test_listings_first_then_picked_then_nearest(self):
		m = [row(i) for i in range(100)] + [row(150, selected=True)]
		m += [row(200, "for_sale"), row(201, "pending"), row(202, "auction")]
		paid, free = comps._history_order(m, 10)
		self.assertEqual([r["name"] for r in paid[:4]], ["r200", "r201", "r202", "r150"])
		self.assertEqual([r["name"] for r in paid[4:]], [f"r{i}" for i in range(6)])
		self.assertEqual(len(paid) + len(free), len(m))
		self.assertFalse({id(r) for r in paid} & {id(r) for r in free})

	def test_more_listings_than_budget(self):
		m = [row(i) for i in range(10)] + [row(100 + i, "for_sale") for i in range(60)]
		paid, _ = comps._history_order(m, 50)
		self.assertTrue(all(r["listing_state"] == "for_sale" for r in paid))
		self.assertEqual(len(paid), 50)


if __name__ == "__main__":
	unittest.main()

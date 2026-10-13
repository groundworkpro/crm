"""Redfin withholds living area in some markets (Memphis, 2026-10-13: Dennis's
board read "Sq ft —" while each comp card showed Zillow's number). Blank sqft
rows are filled from Zillow: detail cache, pin cache, then a billed budget."""

import unittest
from unittest import mock

from crm.tests.frappe_shim import install

install()

from crm.api import comps, zillow_comps


def _row(name, sqft=None, **kw):
	return {"name": name, "address": f"{name} St", "city": "Memphis", "state": "TN",
			"zip": "38104", "square_footage": sqft, **kw}


class FillMissingSqft(unittest.TestCase):
	def run_fill(self, rows, detail=None, cache=None, fetch=None, budget=6, lead=None):
		detail, cache, fetch = detail or {}, cache or {}, fetch or {}
		calls = []

		def pins(addresses, fetch=True):
			calls.append((list(addresses), fetch))
			src = globals_fetch if fetch else cache
			return {a: src[a] for a in addresses if a in src}

		globals_fetch = fetch
		with mock.patch.object(comps, "_detail_cached", lambda n: detail.get(n)), \
				mock.patch.object(zillow_comps, "_pin_facts_many", pins):
			info = comps.fill_missing_sqft(rows, lead, picked_budget=budget)
		return info, calls

	def test_keeps_redfin_value_and_fills_blank_from_detail_cache(self):
		rows = [_row("1", 1500), _row("2")]
		info, calls = self.run_fill(rows, detail={"2": {"details": {"sqft": 1827.0}}})
		self.assertEqual(rows[0]["square_footage"], 1500)
		self.assertNotIn("sqft_source", rows[0])
		self.assertEqual((rows[1]["square_footage"], rows[1]["sqft_source"]), (1827, "zillow"))
		self.assertEqual(info, {"blank": 1, "filled": 1, "billed": 0, "queued": 0})
		self.assertEqual(calls, [])

	def test_pin_cache_then_billed_only_when_picked(self):
		rows = [_row("a"), _row("b", selected=True), _row("c")]
		key = lambda n: f"{n} St, Memphis, TN 38104"
		info, calls = self.run_fill(rows, cache={key("a"): {"sqft": 900}}, fetch={key("b"): {"sqft": 1200}})
		self.assertEqual([r["square_footage"] for r in rows], [900, 1200, None])
		self.assertEqual(calls[-1], ([key("b")], True))
		self.assertEqual(info["billed"], 1)

	def test_remembered_miss_is_not_rebilled(self):
		rows = [_row("a", selected=True)]
		info, calls = self.run_fill(rows, cache={"a St, Memphis, TN 38104": None})
		self.assertIsNone(rows[0]["square_footage"])
		self.assertEqual(len(calls), 1)
		self.assertEqual(info["billed"], 0)

	def test_warm_order_prefers_live_then_nearest(self):
		rows = [_row("near"), _row("live", listing_state="for_sale"), _row("far")]
		self.assertEqual([r["name"] for r in comps._sqft_order(rows, 2)], ["live", "near"])


if __name__ == "__main__":
	unittest.main()

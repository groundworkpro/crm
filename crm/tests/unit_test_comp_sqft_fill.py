"""Redfin's map layer withholds living area in some markets (Memphis,
2026-10-13: Dennis's board read "Sq ft —" while each comp card showed it). The
board fills blanks from caches only; the page then asks `fill_comp_sqft`,
which reads the house's own Redfin page (`/history`) in the background."""

import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

import frappe  # the shim

from crm.api import comps, redfin, redfin_history, zillow_comps


def _row(name, sqft=None, **kw):
	return {"name": name, "address": f"{name} St", "city": "Memphis", "state": "TN",
			"zip": "38104", "square_footage": sqft, **kw}


class FillMissingSqft(unittest.TestCase):
	def setUp(self):
		frappe.cache().store.clear()

	def run_fill(self, rows, detail=None, pins=None):
		detail, pins = detail or {}, pins or {}
		calls = []

		def pin_facts(addresses, fetch=True):
			calls.append(fetch)
			return {a: pins[a] for a in addresses if a in pins}

		with mock.patch.object(comps, "_detail_cached", lambda n: detail.get(n)), \
				mock.patch.object(zillow_comps, "_pin_facts_many", pin_facts):
			info = comps.fill_missing_sqft(rows, "LEAD")
		return info, calls

	def test_keeps_board_value_and_prefers_redfin_cache(self):
		frappe.cache().set_value(redfin_history._sqft_key("7"), 2053)
		rows = [_row("redfin::1", 1500), _row("redfin::7")]
		info, calls = self.run_fill(rows, detail={"redfin::7": {"details": {"sqft": 1827}}})
		self.assertEqual(rows[0]["square_footage"], 1500)
		self.assertNotIn("sqft_source", rows[0])
		self.assertEqual((rows[1]["square_footage"], rows[1]["sqft_source"]), (2053, "redfin"))
		self.assertEqual(info, {"blank": 1, "filled": 1, "pending": 0})
		self.assertEqual(calls, [])

	def test_falls_back_to_free_zillow_caches_never_bills(self):
		rows = [_row("redfin::2"), _row("zillow::3"), _row("redfin::4")]
		info, calls = self.run_fill(
			rows, detail={"redfin::2": {"details": {"sqft": 900}}},
			pins={"zillow::3 St, Memphis, TN 38104": {"sqft": 1200}},
		)
		self.assertEqual([r["square_footage"] for r in rows], [900, 1200, None])
		self.assertEqual(calls, [False])
		self.assertEqual(info["pending"], 1)

	def test_remembered_redfin_miss_is_not_pending(self):
		frappe.cache().set_value(redfin_history._sqft_key("5"), 0)
		rows = [_row("redfin::5")]
		info, _ = self.run_fill(rows)
		self.assertIsNone(rows[0]["square_footage"])
		self.assertEqual(info["pending"], 0)


class SqftMany(unittest.TestCase):
	def setUp(self):
		frappe.cache().store.clear()

	def test_fetches_unread_caches_both_and_respects_budget(self):
		frappe.cache().set_value(redfin_history._sqft_key("1"), 1500)
		bodies = {"2": {"ok": True, "events": [{"date": 1}], "sqft": 2408},
				  "3": {"ok": True, "events": [], "sqft": None}}
		fetched = []

		def fetch(base, pid):
			fetched.append(pid)
			return bodies.get(pid)

		with mock.patch.object(redfin, "_base_url", return_value="http://svc"), \
				mock.patch.object(redfin_history, "_fetch", fetch):
			out = redfin_history.sqft_many(["1", "2", "3", "4"], fetch_budget=2)
		self.assertEqual(fetched, ["2", "3"])
		self.assertEqual(out, {"1": 1500, "2": 2408, "3": 0})
		store = frappe.cache().store
		self.assertEqual(store[redfin_history._sqft_key("2")], 2408)
		self.assertEqual(store[redfin_history._key("2")], [{"date": 1}])

	def test_old_scraper_without_sqft_key_caches_nothing(self):
		with mock.patch.object(redfin, "_base_url", return_value="http://svc"), \
				mock.patch.object(redfin_history, "_fetch", lambda b, p: {"ok": True, "events": []}):
			out = redfin_history.sqft_many(["9"])
		self.assertEqual(out, {})
		self.assertNotIn(redfin_history._sqft_key("9"), frappe.cache().store)


class FillCompSqftEndpoint(unittest.TestCase):
	def test_only_redfin_names_capped_and_mapped_back(self):
		names = [f"redfin::{i}" for i in range(40)] + ["zillow::x"]
		seen = {}

		def many(pids, budget):
			seen["pids"] = pids
			return {p: (1000 + int(p) if int(p) % 2 else 0) for p in pids}

		with mock.patch.object(comps, "_guard"), mock.patch.object(comps, "_load_subject"), \
				mock.patch.object(redfin_history, "sqft_many", many):
			out = comps.fill_comp_sqft("LEAD", names)
		self.assertEqual(len(seen["pids"]), comps.SQFT_FETCH_BUDGET)
		self.assertEqual(out["redfin::1"], 1001)
		self.assertNotIn("redfin::2", out)


if __name__ == "__main__":
	unittest.main()

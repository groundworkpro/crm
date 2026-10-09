"""The comps Sources card and the BatchData race it replaced.

The board used to be decided in one request that gave Redfin 1 second. On a
cold area Redfin's store read takes 5-8s, so Redfin silently dropped out, and
in a non-disclosure state (no Zillow sale prices) the empty board then BOUGHT
BatchData — while Redfin was about to deliver priced solds. Myesha Moore's
lead, 2026-09-24.

These pin:

1. Redfin's state is reported, never silently missing (loading / queued /
    ready / partial / error / off).
2. BatchData is NOT bought while Redfin is still on its way, unless the page
    says it has given up waiting (`settle`).
3. Each source says where its data came from, for the card.
"""

import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import comps, redfin


class RedfinStatus(unittest.TestCase):
	def test_timeout_is_loading_not_absent(self):
		self.assertEqual(comps._redfin_status({"timed_out": True}, None, False, {})["state"], "loading")

	def test_service_error(self):
		self.assertEqual(comps._redfin_status({"error": "x"}, None, False, {})["state"], "error")

	def test_ready_carries_collected_at_and_added(self):
		st = comps._redfin_status(
			{"coverage_state": "ready", "coverage_cells": 8, "ready_cells": 8, "collected_at": "2026-09-24T14:34:00+00:00"},
			{"merge": {"added": 7}}, False, {},
		)
		self.assertEqual((st["state"], st["added"], st["collected_at"]), ("ready", 7, "2026-09-24T14:34:00+00:00"))

	def test_queued_carries_queue(self):
		q = {"ours": 5, "running": 1, "ahead": 32, "per_min": 9.0, "eta_seconds": 253}
		st = comps._redfin_status({"coverage_state": "partial", "coverage_cells": 8, "ready_cells": 3, "queue": q}, {}, False, {})
		self.assertEqual((st["state"], st["queue"], st["ready_cells"]), ("queued", q, 3))

	def test_failed_tiles_are_partial_not_waited_on(self):
		st = comps._redfin_status({"coverage_state": "failed"}, {}, False, {})
		self.assertEqual(st["state"], "partial")
		self.assertNotIn("partial", comps.REDFIN_STILL_COMING)

	def test_rentals_and_unconfigured_are_off(self):
		self.assertEqual(comps._redfin_status({}, None, True, {})["state"], "off")
		self.assertEqual(comps._redfin_status(None, None, False, None)["state"], "off")


class Sources(unittest.TestCase):
	def _base(self, fallback):
		return {
			"zillow": {"used": True, "added": 3, "sold": 0, "for_sale": 4, "pending": 1, "checked_at": 1790000000.0, "cached": True},
			"realtor": {"used": True, "added": 2, "source": "store", "fetched_at": "2026-09-28T16:21:47+00:00", "reason": None},
			"fallback": fallback,
		}

	def test_waiting_on_redfin_polls(self):
		out = comps._sources(self._base({"reason": "waiting_on_redfin", "used": False}), {"state": "loading"}, False)
		self.assertEqual(out["batchdata"]["state"], "waiting")
		self.assertTrue(out["pending"])

	def test_saved_vs_bought(self):
		saved = comps._sources(self._base({"used": True, "count": 2, "saved_at": 1790000000.0, "bought_now": False}), {"state": "ready"}, False)
		bought = comps._sources(self._base({"used": True, "count": 2, "saved_at": None, "bought_now": True}), {"state": "ready"}, False)
		self.assertEqual((saved["batchdata"]["state"], bought["batchdata"]["state"]), ("saved", "bought"))
		self.assertTrue(saved["batchdata"]["saved_at"].startswith("2026-09-21"))
		self.assertFalse(saved["pending"])

	def test_realtor_and_zillow_say_where_from(self):
		out = comps._sources(self._base({"reason": "merged_has_prices", "priced_solds": 7}), {"state": "ready"}, False)
		self.assertEqual(out["realtor"]["state"], "ready")
		self.assertFalse(out["realtor"]["live"])
		self.assertEqual(out["zillow"]["for_sale"], 5)
		self.assertTrue(out["zillow"]["checked_at"])
		self.assertEqual(out["batchdata"], {"state": "skipped", "priced_solds": 7})

	def test_rentals_turn_sale_sources_off(self):
		out = comps._sources(self._base({}), {"state": "off", "reason": "rentals"}, True)
		self.assertEqual((out["realtor"]["state"], out["batchdata"]["state"]), ("off", "off"))


class Rewarm(unittest.TestCase):
	def test_no_second_push_while_work_is_in_line(self):
		import frappe

		with patch.object(frappe, "enqueue", create=True) as enq:
			redfin.maybe_rewarm("L1", {"coverage_state": "queued", "queue": {"ours": 3, "running": 0}})
		enq.assert_not_called()

	def test_rewarm_collects_the_boards_circle_first(self):
		import frappe

		with patch.object(frappe, "enqueue", create=True) as enq, \
		     patch.object(frappe, "cache", create=True) as cache:
			cache.return_value.get_value.return_value = None
			redfin.maybe_rewarm("L1", {"coverage_state": "missing", "queue": {}}, 1)
		kw = enq.call_args.kwargs
		self.assertEqual(kw["priority"], "new_lead")
		self.assertAlmostEqual(kw["radius_m"], 1609.344)

	def test_rewarm_never_below_half_a_mile(self):
		import frappe

		with patch.object(frappe, "enqueue", create=True) as enq, \
		     patch.object(frappe, "cache", create=True) as cache:
			cache.return_value.get_value.return_value = None
			redfin.maybe_rewarm("L2", {"coverage_state": "missing", "queue": {}}, 0.25)
		self.assertAlmostEqual(enq.call_args.kwargs["radius_m"], 804.672)


class WarmPriority(unittest.TestCase):
	"""New leads warm at `new_lead`; a service without the class still warms."""

	def _run(self, codes, **kw):
		from crm.api import geo

		sent = []

		class R:
			def __init__(self, code): self.status_code = code
			def raise_for_status(self):
				if self.status_code >= 400: raise RuntimeError(self.status_code)
			def json(self): return {"queued": True}

		it = iter(codes)
		with patch.object(geo, "_enabled", return_value=True), \
		     patch.object(geo, "_lead_point", return_value=(1.0, 2.0)), \
		     patch("crm.api.address_resolve.resolve", return_value={"ok": True, "exact": True}), \
		     patch.object(geo.requests, "post", side_effect=lambda url, json, timeout: sent.append(json) or R(next(it))):
			out = geo.warm_lead("LEAD-1", **kw)
		return out, sent

	def test_default_is_new_lead(self):
		out, sent = self._run([200])
		self.assertTrue(out["ok"])
		self.assertEqual(sent[0]["priority"], "new_lead")

	def test_new_lead_collects_half_a_mile(self):
		out, sent = self._run([200])
		self.assertAlmostEqual(sent[0]["radius_m"], 804.672)

	def test_old_service_falls_back_to_default(self):
		out, sent = self._run([422, 200])
		self.assertTrue(out["ok"])
		self.assertNotIn("priority", sent[1])

	def test_backfill_stays_ingest(self):
		_, sent = self._run([200], priority="ingest")
		self.assertEqual(sent[0]["priority"], "ingest")


class CoverageRead(unittest.TestCase):
	def test_reads_dated_rows_only(self):
		class R:
			def raise_for_status(self): pass
			def json(self): return {"features": [], "meta": {}}

		with patch.object(redfin.requests, "get", return_value=R()) as get:
			redfin._fetch_coverage("http://x", 1.0, 2.0, 3000.0, {})
		self.assertEqual(get.call_args.kwargs["params"]["dated_only"], "true")


if __name__ == "__main__":
	unittest.main()

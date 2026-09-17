"""Zillow outage handling: a failed call is not a verdict on the address.

Background (2026-09-16/17): the RapidAPI subscription lapsed — 429 then 403
"You are not subscribed to this API" on every call. `facts_for_lead` cached
each failed lookup as `{}`, i.e. "Zillow doesn't recognize this address", for
30 days, and the comps page told a rep to ask the seller to confirm a perfectly
good address. These pin the three rules that came out of it.
"""

import time
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import zillow  # noqa: E402


def _http_error(code):
	return f"Traceback...\nurllib.error.HTTPError: HTTP Error {code}: Whatever\n"


class _Doc(dict):
	doctype = "CRM Lead"
	name = "CRM-LEAD-2026-01360"


def _lead(**kw):
	d = _Doc(
		property_address="3401 Bonds Lake Rd Nw, Conyers, GA 30012",
		property_city="Conyers",
		property_state="GA",
		property_zip="30012",
		zillow_facts="",
		zillow_fetched_at=None,
		zillow_zpid="",
	)
	d.update(kw)
	return d


class Base(unittest.TestCase):
	def setUp(self):
		shim.conf["rapidapi_zillow_key"] = "k"
		# Other suites replace `db.set_value` / `db.has_column` on the shared shim;
		# own both here, or the cache silently no-ops and "not cached" passes for
		# the wrong reason.
		shim.db.has_column = lambda doctype, column: column in zillow.CACHE_FIELDS
		shim.db.set_value = mock.MagicMock()
		shim._cache.store.clear()
		shim.errors.clear()
		shim.flags.zillow_unavailable = None


class FactsForLeadTests(Base):
	def test_http_failure_is_not_cached_as_a_miss(self):
		with mock.patch.object(zillow, "_raw_get", return_value=(None, None, _http_error(403))):
			out = zillow.facts_for_lead(_lead())
		self.assertIsNone(out)
		shim.db.set_value.assert_not_called()
		self.assertEqual(zillow.unavailable_reason(), "not_subscribed")

	def test_throttled_failure_is_not_cached_either(self):
		with mock.patch.object(zillow, "_raw_get", return_value=(None, None, _http_error(429))):
			out = zillow.facts_for_lead(_lead())
		self.assertIsNone(out)
		shim.db.set_value.assert_not_called()
		self.assertEqual(zillow.unavailable_reason(), "throttled")

	def test_real_miss_is_still_cached(self):
		# Zillow ANSWERED (HTTP 200) with nothing usable: that IS "no such house".
		with mock.patch.object(zillow, "_raw_get", return_value=({"message": "no match"}, 100, None)):
			out = zillow.facts_for_lead(_lead())
		self.assertEqual(out.get("zpid"), None)
		shim.db.set_value.assert_called_once()
		self.assertIsNone(zillow.unavailable_reason())

	def test_no_key_is_unavailable_not_a_miss(self):
		shim.conf["rapidapi_zillow_key"] = ""
		out = zillow.facts_for_lead(_lead())
		self.assertIsNone(out)
		shim.db.set_value.assert_not_called()
		self.assertEqual(zillow.unavailable_reason(), "not_configured")


class OutageHoldTests(Base):
	def test_403_opens_a_hold_that_skips_calls_without_a_request(self):
		with mock.patch.object(zillow, "_raw_get", return_value=(None, None, _http_error(403))) as raw:
			zillow._request("/property", {"address": "x"}, "t")
			self.assertEqual(raw.call_count, 1)
			self.assertEqual(zillow.outage_reason(), "not_subscribed")
			# Held: no second HTTP call, and fetch_many yields all-None.
			self.assertIsNone(zillow._request("/property", {"address": "y"}, "t"))
			self.assertEqual(zillow.fetch_many([("/search", {"a": 1}), ("/search", {"a": 2})]), [None, None])
			self.assertEqual(raw.call_count, 1)
		# One loud log line for the outage itself, then silence.
		titles = [a[1] for a, _ in shim.errors]
		self.assertEqual(titles.count("Zillow: subscription unavailable"), 1)

	def test_hold_expires(self):
		shim._cache.store[zillow._OUTAGE_KEY] = {
			"reason": "not_subscribed", "t": time.time() - zillow._OUTAGE_HOLD - 1,
		}
		self.assertIsNone(zillow.outage_reason())

	def test_a_single_429_does_not_open_a_hold(self):
		with mock.patch.object(zillow, "_raw_get", return_value=(None, None, _http_error(429))):
			zillow._request("/property", {"address": "x"}, "t")
		self.assertIsNone(zillow.outage_reason())
		self.assertEqual(zillow.unavailable_reason(), "throttled")

	def test_batch_failures_mark_the_request(self):
		results = [({"props": []}, 50, None), (None, None, _http_error(429))]
		with mock.patch.object(zillow, "_raw_get", side_effect=results):
			bodies = zillow.fetch_many([("/search", {"a": 1}), ("/search", {"a": 2})])
		self.assertEqual(bodies[1], None)
		self.assertEqual(zillow.unavailable_reason(), "throttled")


class RefreshTests(Base):
	def test_refresh_reports_unavailable(self):
		from crm.api import comps

		doc = _lead()
		doc.reload = lambda: None
		with mock.patch.object(comps, "_guard", lambda: None), mock.patch.object(
			comps, "_load_subject", lambda lead: doc
		), mock.patch.object(zillow, "_raw_get", return_value=(None, None, _http_error(403))):
			res = zillow.refresh_lead_facts("CRM-LEAD-2026-01360")
		self.assertFalse(res["matched"])
		self.assertEqual(res["unavailable"], "not_subscribed")

	def test_zillow_match_carries_unavailable_without_claiming_tried(self):
		from crm.api import comps

		shim.flags.zillow_unavailable = "not_subscribed"
		m = comps._zillow_match(_lead(), {"zpid": None, "zillow_queried_address": ""})
		self.assertFalse(m["tried"])
		self.assertFalse(m["matched"])
		self.assertEqual(m["unavailable"], "not_subscribed")
		# A resolved lead never reports an outage — there is nothing to warn about.
		m = comps._zillow_match(_lead(zillow_zpid="123"), None)
		self.assertIsNone(m["unavailable"])


if __name__ == "__main__":
	unittest.main()

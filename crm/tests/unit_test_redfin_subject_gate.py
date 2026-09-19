"""The Redfin subject record must not be gated on Zillow specifically.

`start_subject_check` fetches ONE record that feeds three unrelated things:
the Zillow-vs-Redfin discrepancy flag, the Redfin Estimate on the subject tile
(`subject_estimate`) and the listing URL. It used to refuse to fetch unless
`subject["has_zillow"]` was set, and `compare_subject_facts` only argued with
facts whose source string was the literal "zillow".

Under a Redfin-first subject cascade both of those become permanently false,
so all three features would have gone dark at once — silently, because a
missing amber flag looks exactly like agreement.

These tests pin two things:

1. **Nothing changes today.** With a Zillow-sourced subject the flag fires on
    the same fields, with the same row keys the frontend reads, and the same
    exclusions (human override, listing record, inexact band) still apply.
2. **The generalisation works.** A subject resolved by a different vendor
    still gets its record, and a fact sourced from the record's OWN provider
    never argues with itself.
"""

import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import redfin


def subject(source, **facts):
	"""A subject dict shaped like `comps._subject_facts` output.

	`source` maps field -> provider string; every fact is marked exact unless
	explicitly overridden, because an inexact band is excluded for a separate
	reason that has its own test below.
	"""
	out = {"source": dict(source), "has_zillow": True, "lat": 45.0, "lng": -93.0}
	for field, value in facts.items():
		out[field] = value
		out.setdefault(f"{field}_exact", True)
	return out


def record(**fields):
	rec = {"matched": True, "property_id": 77, "source": "store"}
	rec.update(fields)
	return rec


class _Doc(dict):
	"""A lead: dict access for fields, attribute access for `name`."""

	doctype = "CRM Lead"
	name = "CRM-LEAD-2026-00001"


class CompareIsUnchangedToday(unittest.TestCase):
	"""Zillow-sourced subject vs Redfin record: same answers as before."""

	def test_zillow_fact_that_differs_still_flags(self):
		check = redfin.compare_subject_facts(
			subject({"beds": "zillow"}, beds=3), record(beds=5)
		)
		self.assertIsNotNone(check)
		self.assertEqual([r["field"] for r in check["fields"]], ["beds"])
		self.assertEqual(check["property_id"], 77)

	def test_row_keeps_the_keys_the_frontend_reads(self):
		# CompDiscrepancyFlag.vue filters on `r.zillow != null && r.redfin != null`
		# and renders both. Renaming either key blanks the flag with no error.
		row = redfin.compare_subject_facts(
			subject({"sqft": "zillow"}, sqft=1000), record(sqft=2000)
		)["fields"][0]
		self.assertEqual(row["zillow"], 1000)
		self.assertEqual(row["redfin"], 2000)
		self.assertEqual(row["source"], "zillow")

	def test_agreement_is_still_none(self):
		self.assertIsNone(
			redfin.compare_subject_facts(subject({"beds": "zillow"}, beds=3), record(beds=3))
		)

	def test_unmatched_record_is_still_none(self):
		self.assertIsNone(
			redfin.compare_subject_facts(
				subject({"beds": "zillow"}, beds=3), {"matched": False}
			)
		)

	def test_tolerances_are_unchanged(self):
		# Inside tolerance -> silent; outside -> flags. Pins the four thresholds.
		agree = subject(
			{"baths": "zillow", "sqft": "zillow", "year_built": "zillow"},
			baths=2.0, sqft=1000, year_built=1990,
		)
		self.assertIsNone(
			redfin.compare_subject_facts(
				agree, record(baths=2.25, sqft=1050, year_built=1991)
			)
		)
		differ = redfin.compare_subject_facts(
			agree, record(baths=2.5, sqft=1100, year_built=1992)
		)
		self.assertEqual(
			sorted(r["field"] for r in differ["fields"]),
			["baths", "sqft", "year_built"],
		)

	def test_human_override_still_drops_out(self):
		# A rep's manual sqft is a settled question; re-flagging it re-opens it.
		self.assertIsNone(
			redfin.compare_subject_facts(
				subject({"sqft": "manual"}, sqft=1000), record(sqft=9999)
			)
		)

	def test_listing_and_lead_sources_still_drop_out(self):
		for provider in ("listing", "lead"):
			with self.subTest(provider=provider):
				self.assertIsNone(
					redfin.compare_subject_facts(
						subject({"sqft": provider}, sqft=1000), record(sqft=9999)
					)
				)

	def test_inexact_band_still_drops_out(self):
		s = subject({"sqft": "zillow"}, sqft=1500)
		s["sqft_exact"] = False  # a seller pick-list band has no midpoint to dispute
		self.assertIsNone(redfin.compare_subject_facts(s, record(sqft=9999)))


class CompareIsProviderAgnostic(unittest.TestCase):
	"""The part that makes a Redfin-first subject survivable."""

	def test_a_record_never_argues_with_its_own_provider(self):
		# The whole point: under Redfin-first these facts ARE the record's, so
		# comparing them would flag every subject on every load.
		self.assertIsNone(
			redfin.compare_subject_facts(
				subject({"beds": "redfin", "sqft": "redfin"}, beds=3, sqft=1000),
				record(beds=5, sqft=9999),
			)
		)

	def test_another_vendor_still_argues_and_is_named(self):
		row = redfin.compare_subject_facts(
			subject({"beds": "realtor"}, beds=3), record(beds=5)
		)["fields"][0]
		self.assertEqual(row["source"], "realtor")
		self.assertEqual((row["zillow"], row["redfin"]), (3, 5))

	def test_mixed_sources_compare_only_the_foreign_ones(self):
		check = redfin.compare_subject_facts(
			subject(
				{"beds": "redfin", "sqft": "zillow", "year_built": "manual"},
				beds=3, sqft=1000, year_built=1990,
			),
			record(beds=5, sqft=9999, year_built=1800),
		)
		self.assertEqual([r["field"] for r in check["fields"]], ["sqft"])

	def test_record_provider_is_a_parameter_not_a_constant(self):
		# Same facts, different record provider -> the exclusion moves with it.
		s = subject({"beds": "zillow"}, beds=3)
		self.assertIsNone(redfin.compare_subject_facts(s, record(beds=5), "zillow"))
		self.assertIsNotNone(redfin.compare_subject_facts(s, record(beds=5), "redfin"))

	def test_comparable_sources_excludes_the_record(self):
		self.assertNotIn("redfin", redfin.comparable_sources("redfin"))
		self.assertIn("zillow", redfin.comparable_sources("redfin"))


class VendorPresenceGate(unittest.TestCase):
	"""`start_subject_check` must fetch for ANY vendor, not just Zillow."""

	def test_has_zillow_still_opens_the_gate(self):
		self.assertTrue(redfin.subject_has_vendor_facts({"has_zillow": True}))

	def test_a_redfin_first_subject_opens_the_gate(self):
		# No zpid, so `has_zillow` is False. Before this change the record was
		# never fetched and the Redfin Estimate + listing URL went dark with it.
		self.assertTrue(redfin.subject_has_vendor_facts({"has_redfin": True}))
		self.assertTrue(redfin.subject_has_vendor_facts({"has_realtor": True}))

	def test_no_vendor_keeps_the_gate_shut(self):
		self.assertFalse(redfin.subject_has_vendor_facts({}))
		self.assertFalse(redfin.subject_has_vendor_facts(None))
		self.assertFalse(redfin.subject_has_vendor_facts({"has_zillow": False}))


class StartSubjectCheckGate(unittest.TestCase):
	"""End-to-end on the gate, so the wiring is pinned and not just the helper."""

	def doc(self):
		return _Doc(property_address="5 Main St, Minneapolis, MN 55401")

	def start(self, subject_dict, base="http://svc"):
		with patch.object(redfin, "_base_url", return_value=base), \
			 patch.object(redfin, "_fetch_subject_record") as fetch:
			job = redfin.start_subject_check(self.doc(), subject_dict)
		return job, fetch

	def test_no_vendor_does_not_fetch(self):
		job, fetch = self.start({"lat": 45.0, "lng": -93.0})
		self.assertIsNone(job)
		fetch.assert_not_called()

	def test_no_base_url_does_not_fetch(self):
		job, fetch = self.start({"has_zillow": True, "lat": 45.0, "lng": -93.0}, base=None)
		self.assertIsNone(job)
		fetch.assert_not_called()

	def test_zillow_subject_fetches(self):
		job, _ = self.start({"has_zillow": True, "lat": 45.0, "lng": -93.0})
		self.assertIsNotNone(job)
		job["thread"].join(timeout=5)

	def test_redfin_first_subject_fetches(self):
		job, _ = self.start({"has_redfin": True, "lat": 45.0, "lng": -93.0})
		self.assertIsNotNone(job)
		job["thread"].join(timeout=5)

	def test_missing_point_still_refuses(self):
		job, fetch = self.start({"has_zillow": True, "lat": None, "lng": None})
		self.assertIsNone(job)
		fetch.assert_not_called()


class EstimateRidesTheSameRecord(unittest.TestCase):
	"""The reason the gate matters: two features that are not the flag."""

	def test_estimate_survives_a_subject_with_no_zillow(self):
		rec = record(estimate=412000)
		self.assertTrue(redfin.subject_has_vendor_facts({"has_redfin": True}))
		self.assertEqual(redfin.subject_estimate(rec), 412000)

	def test_estimate_still_requires_a_match(self):
		self.assertIsNone(redfin.subject_estimate({"matched": False, "estimate": 1}))


if __name__ == "__main__":
	unittest.main()

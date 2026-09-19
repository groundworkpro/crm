"""The subject's facts are Redfin-first, per field, for the fields Redfin wins.

The evidence is about ACCURACY, not coverage, and the distinction matters.
Where all three providers carry a value and disagree, Redfin is the odd one out
least often -- 12% on sold price against Zillow's 42%, 9% on sqft against
Zillow's 52% (`redfin-scraper-api/bench/README.md`). So Redfin's number wins
when it HAS one.

Coverage runs the other way for a subject: Zillow resolves more subjects than
Redfin (77% vs 67%), because a subject is usually an off-market house. That is
why this is a per-field fallthrough rather than a provider switch -- Zillow
still answers for every field Redfin missed. (The "year built 93.8% vs 0%"
figure is about Zillow's COMP SEARCH rows, which carry no year; the subject's
Zillow facts come from `/property`, which does.)

Three things this must NOT do, each of which would be a silent regression:

* invert the fields Redfin structurally cannot answer -- taxes (0% vs 90%),
  `lot_size` (the service's /facts does not return it), the Zestimate (a
  different AVM, shown beside the Redfin Estimate, not instead of it), and the
  subject `cover_photo` (Zillow 94% vs Redfin 27% -- the one place Zillow wins
  on photos, opposite to the comp rule);
* read an UNMATCHED record as fact. "Redfin does not know this house" is not
  "this house has no bedrooms";
* print a live ASK under a "sold for" label. `price` on a /facts row is the
  last sale for an off-market house and the current ask for a listed one, and
  only `sold_date` distinguishes them.
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install
install()

from crm.api import comps, redfin


def lead(**kw):
	doc = {
		"doctype": "CRM Lead", "name": "CRM-LEAD-1",
		"property_address": "5 Main St", "property_city": "Minneapolis",
		"property_state": "MN", "property_zip": "55401",
		"bedrooms": "", "bathrooms": "", "square_footage": "", "year_built": "",
	}
	doc.update(kw)
	return type("Doc", (), {
		"doctype": doc["doctype"], "name": doc["name"],
		"get": lambda self, k, d=None: doc.get(k, d),
	})()


def rec(**kw):
	base = {"matched": True, "property_id": 123, "source": "store"}
	base.update(kw)
	return base


ZILLOW = {
	"beds": 3, "baths": 2, "sqft": 1500, "year_built": 1960,
	"property_type": "Single Family", "zpid": "z1",
	"zestimate": 250000, "rent_zestimate": 1800, "lot_size": "0.25 ac",
	"cover_photo": "https://zillow/cover.jpg",
	"tax_assessed_value": 190000,
	"last_sale": {"price": 200000, "date": "2019-01-01"},
	"lat": 45.0, "lng": -93.0,
}


def facts(redfin_rec=None, zillow=ZILLOW, doc=None):
	"""Subject facts with the Redfin-first cascade ON.

	Which leads get the cascade is a CREATION CUTOVER (`redfin_first_cutover`,
	unset by default) because it moves the comp set — see `_redfin_first_for`.
	Every test in THIS file is about what the cascade does once a lead qualifies,
	so the helper forces the gate open. Who qualifies is covered in
	`unit_test_subject_cascade_cutover.py`, which is where a regression in the
	default would surface.
	"""
	with patch.object(comps, "_redfin_first_for", return_value=True), \
		 patch.object(comps, "_self_listing", return_value=None), \
		 patch.object(comps, "_sqft_override", return_value=0), \
		 patch.object(comps, "_sqft_override_supported", return_value=True):
		import crm.api.zillow as zillow_api
		with patch.object(zillow_api, "facts_for_lead", return_value=zillow):
			return comps._subject_facts(doc or lead(), redfin_rec)


class RedfinWinsItsFields(unittest.TestCase):
	def test_beds_baths_sqft_year_come_from_redfin(self):
		out = facts(rec(beds=4, baths=2.5, sqft=1800, year_built=1972))
		self.assertEqual((out["beds"], out["baths"], out["sqft"], out["year_built"]),
		                 (4, 2.5, 1800, 1972))
		for field in ("beds", "baths", "sqft", "year_built"):
			self.assertEqual(out["source"][field], "redfin", field)

	def test_property_type_prefers_redfin(self):
		out = facts(rec(property_type="Townhouse"))
		self.assertEqual(out["property_type"], "Townhouse")
		self.assertEqual(out["source"]["property_type"], "redfin")

	def test_each_field_falls_through_independently(self):
		"""A record with beds but no year must not drag the year down with it."""
		out = facts(rec(beds=4))
		self.assertEqual((out["beds"], out["source"]["beds"]), (4, "redfin"))
		self.assertEqual((out["year_built"], out["source"]["year_built"]), (1960, "zillow"))

	def test_redfin_covers_a_field_zillow_lacks(self):
		"""The fallthrough in the useful direction: Zillow has no year here."""
		out = facts(rec(year_built=1972), zillow=dict(ZILLOW, year_built=None))
		self.assertEqual(out["year_built"], 1972)
		self.assertEqual(out["source"]["year_built"], "redfin")

	def test_zillow_covers_a_field_redfin_lacks(self):
		"""And in the other direction, which is the COMMON case for a subject:
		Zillow resolves 77% of subjects against Redfin's 67%."""
		out = facts(rec(beds=4))
		self.assertEqual((out["sqft"], out["source"]["sqft"]), (1500, "zillow"))
		self.assertEqual((out["baths"], out["source"]["baths"]), (2, "zillow"))


class ZillowKeepsWhatRedfinCannotAnswer(unittest.TestCase):
	def test_taxes_photo_avm_and_lot_size_stay_zillow(self):
		"""Each is structural, not a preference -- see the module docstring."""
		out = facts(rec(beds=4, sqft=1800))
		self.assertEqual(out["assessed_value"], 190000)
		self.assertEqual(out["source"]["assessed_value"], "zillow")
		self.assertEqual(out["zestimate"], 250000)
		self.assertEqual(out["rent_zestimate"], 1800)
		self.assertEqual(out["lot_size"], "0.25 ac")
		self.assertEqual(out["cover_photo"], "https://zillow/cover.jpg")

	def test_zillow_identity_keys_survive(self):
		"""zpid and the rooftop point are read by the frontend by name."""
		out = facts(rec(beds=4))
		self.assertEqual(out["zpid"], "z1")
		self.assertTrue(out["has_zillow"])
		self.assertEqual((out["zillow_lat"], out["zillow_lng"]), (45.0, -93.0))

	def test_manual_sqft_override_still_outranks_redfin(self):
		with patch.object(comps, "_self_listing", return_value=None), \
			 patch.object(comps, "_sqft_override", return_value=2222), \
			 patch.object(comps, "_sqft_override_supported", return_value=True):
			import crm.api.zillow as zillow_api
			with patch.object(zillow_api, "facts_for_lead", return_value=ZILLOW):
				out = comps._subject_facts(lead(), rec(sqft=1800))
		self.assertEqual(out["sqft"], 2222)
		self.assertEqual(out["source"]["sqft"], "manual")


class UnmatchedRecordIsNotAFact(unittest.TestCase):
	def test_unmatched_record_is_ignored_entirely(self):
		out = facts({"matched": False, "beds": 99})
		self.assertEqual(out["beds"], 3)
		self.assertEqual(out["source"]["beds"], "zillow")
		self.assertFalse(out["has_redfin"])

	def test_no_record_is_zillow_first(self):
		"""The cold-lead path: nothing cached yet, so one Zillow-first request."""
		out = facts(None)
		self.assertEqual(out["source"]["beds"], "zillow")
		self.assertFalse(out["has_redfin"])

	def test_has_redfin_is_set_on_a_matched_record(self):
		self.assertTrue(facts(rec(beds=4))["has_redfin"])


class LastSaleNeedsASoldDate(unittest.TestCase):
	def test_redfin_sale_wins_when_it_has_a_date(self):
		out = facts(rec(price=275000, sold_date="2024-06-01"))
		self.assertEqual(out["last_sale"], {"price": 275000, "date": "2024-06-01"})
		self.assertEqual(out["source"]["last_sale"], "redfin")

	def test_a_live_ask_is_never_printed_as_a_sale(self):
		"""`price` with no `sold_date` is the current ask on a listed house."""
		out = facts(rec(price=499000, mls_status="Active"))
		self.assertEqual(out["last_sale"], {"price": 200000, "date": "2019-01-01"})
		self.assertEqual(out["source"]["last_sale"], "zillow")


class CachedRecordAccessor(unittest.TestCase):
	"""`cached_subject_record` must READ ONLY -- it is called before the fetch.

	The cache is patched with `patch.object` rather than assigned: the shim's
	`frappe.cache()` returns ONE shared object for the whole run, so assigning
	to it leaks into every later test file. It did -- it broke the Zillow outage
	hold's `outage_reason()` until this was fixed.
	"""

	def _cache_returning(self, value):
		import frappe
		return patch.object(frappe.cache(), "get_value", return_value=value)

	def test_missing_key_is_none(self):
		with self._cache_returning(None):
			self.assertIsNone(redfin.cached_subject_record("CRM-LEAD-1"))

	def test_cached_error_entry_is_not_a_miss(self):
		"""`{"rec": None}` means the service failed, not that Redfin said no."""
		with self._cache_returning({"rec": None, "error": "boom"}):
			self.assertIsNone(redfin.cached_subject_record("CRM-LEAD-1"))

	def test_cached_record_is_returned(self):
		with self._cache_returning({"rec": {"matched": True, "beds": 4}}):
			self.assertEqual(redfin.cached_subject_record("CRM-LEAD-1"),
			                 {"matched": True, "beds": 4})

	def test_a_non_dict_entry_is_none(self):
		with self._cache_returning("garbage"):
			self.assertIsNone(redfin.cached_subject_record("CRM-LEAD-1"))

	def test_no_lead_is_none(self):
		self.assertIsNone(redfin.cached_subject_record(None))


class HalfBathConvention(unittest.TestCase):
	"""Zillow rounds a half-bath up; Redfin reports the half.

	Measured over the benchmark's matched houses (3,030 pairs carrying a bath
	count on both sides): 2,338 agreed exactly, 586 had Zillow exactly 0.5
	HIGHER, and 14 had Zillow 0.5 lower. 42:1 is a convention, not a spread.
	"""

	def test_the_convention_gap_does_not_flag(self):
		self.assertTrue(redfin.baths_agree(3.0, 2.5))
		self.assertTrue(redfin.baths_agree(2.0, 1.5))

	def test_a_real_whole_bath_gap_still_flags(self):
		self.assertFalse(redfin.baths_agree(2.0, 3.0))
		self.assertFalse(redfin.baths_agree(1.0, 3.0))

	def test_the_rare_reverse_case_still_flags(self):
		"""14 of 3,030. Kept honest rather than swallowed with the 586."""
		self.assertFalse(redfin.baths_agree(2.5, 2.0))

	def test_ordinary_rounding_is_unaffected(self):
		self.assertTrue(redfin.baths_agree(2.0, 2.0))
		self.assertTrue(redfin.baths_agree(2.0, 2.2))

	def test_a_missing_side_never_flags(self):
		self.assertTrue(redfin.baths_agree(None, 2.0))
		self.assertTrue(redfin.baths_agree(2.0, None))

	def test_the_flag_uses_it(self):
		"""End-to-end: a Redfin-2.5 / Zillow-3.0 subject shows no bath row."""
		subject = {
			"baths": 3.0, "baths_exact": True,
			"source": {"baths": "zillow"},
		}
		self.assertIsNone(redfin.compare_subject_facts(subject, rec(baths=2.5)))


class RecordSourcedFactsDropOutOfTheCompare(unittest.TestCase):
	"""A record cannot disagree with itself. Under Redfin-first this is the
	whole ballgame -- without it every load would flag Redfin-vs-Redfin."""

	def test_redfin_sourced_fact_is_not_compared(self):
		subject = {"beds": 4, "beds_exact": True, "source": {"beds": "redfin"}}
		self.assertIsNone(redfin.compare_subject_facts(subject, rec(beds=9)))

	def test_a_zillow_sourced_fact_still_flags(self):
		subject = {"beds": 3, "beds_exact": True, "source": {"beds": "zillow"}}
		out = redfin.compare_subject_facts(subject, rec(beds=9))
		self.assertIsNotNone(out)
		self.assertEqual(out["fields"][0]["source"], "zillow")


if __name__ == "__main__":
	unittest.main()

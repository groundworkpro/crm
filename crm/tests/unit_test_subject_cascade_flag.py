"""`redfin_first_subject_enabled` decides whether the subject cascade reads
Redfin, and therefore whether the COMP SET can move.

WHY THIS FILE EXISTS. The Redfin-first cascade is more accurate per field
(Redfin is the odd one out on sqft 9% of the time against Zillow's 52%), but
the subject's numbers are not display-only: beds/baths/sqft/year become
`*_band`, `_preset_tiers` turns bands into filter windows, and `_matches`
excludes comps outside them. Redfin sqft 1,800 where Zillow said 1,500 moves a
±25% window from 1,125–1,875 to 1,350–2,250, so comps at the edges appear and
disappear on a rep's board.

That is why the cascade is opt-in rather than on. And because
`redfin.cached_subject_record` is a pure cache read, an ungated cascade was
also NON-DETERMINISTIC: a cold lead was Zillow-first for exactly one request
and Redfin-first after, so the same lead could show different comps on a first
and second open with nobody touching anything.

The tests below are the proof in both directions:

* OFF (the default) — a Redfin record that disagrees with Zillow on every
  field changes NOTHING: not a fact, not a band, not a source label, not a
  filter window, not which comps pass. Any leak fails here.
* ON — the cascade applies, and the comp set is allowed to move. The moving is
  the point; it is a choice, made deliberately, applied going forward.

Two things are deliberately OUTSIDE the flag and are pinned here so a later
"consistency" pass does not sweep them in:

* `has_redfin` — a PRESENCE flag for `redfin.subject_has_vendor_facts` (the
  fetch gate widened in 3447bda04). It says a record exists, never what is in
  it, and reaches no band, window or displayed number.
* the comp PHOTO ladder in `_shape_detail` — changes which pictures show on a
  comp, never which comps exist.
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install
install()

from crm.api import comps


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


#: Zillow and Redfin disagree on EVERY field the cascade can take, so a single
#: leaked value is visible as a changed number, not masked by an accidental
#: agreement.
ZILLOW = {
	"beds": 3, "baths": 2, "sqft": 1500, "year_built": 1960,
	"property_type": "Single Family", "zpid": "z1",
	"zestimate": 250000, "rent_zestimate": 1800, "lot_size": "0.25 ac",
	"cover_photo": "https://zillow/cover.jpg",
	"last_sale": {"price": 200000, "date": "2019-01-01"},
	"lat": 45.0, "lng": -93.0,
}

REDFIN = {
	"matched": True, "property_id": 123, "source": "store",
	"beds": 4, "baths": 2.5, "sqft": 1800, "year_built": 1972,
	"property_type": "Townhouse",
	"price": 305000, "sold_date": "2023-06-01",
}


def facts(enabled, redfin_rec=REDFIN, zillow=ZILLOW, doc=None):
	with patch.object(comps, "redfin_first_subject_enabled", return_value=enabled), \
		 patch.object(comps, "_self_listing", return_value=None), \
		 patch.object(comps, "_sqft_override", return_value=0), \
		 patch.object(comps, "_sqft_override_supported", return_value=True):
		import crm.api.zillow as zillow_api
		with patch.object(zillow_api, "facts_for_lead", return_value=zillow):
			return comps._subject_facts(doc or lead(), redfin_rec)


class DefaultIsOff(unittest.TestCase):
	"""The flag is opt-in. A fresh site must not move anybody's comps."""

	def test_absent_config_means_off(self):
		import frappe
		frappe.conf.pop("redfin_first_subject_enabled", None)
		self.assertFalse(comps.redfin_first_subject_enabled())

	def test_explicit_truthy_config_turns_it_on(self):
		import frappe
		try:
			frappe.conf["redfin_first_subject_enabled"] = 1
			self.assertTrue(comps.redfin_first_subject_enabled())
		finally:
			frappe.conf.pop("redfin_first_subject_enabled", None)

	def test_explicit_falsey_config_stays_off(self):
		import frappe
		try:
			frappe.conf["redfin_first_subject_enabled"] = 0
			self.assertFalse(comps.redfin_first_subject_enabled())
		finally:
			frappe.conf.pop("redfin_first_subject_enabled", None)


class OffLeaksNothing(unittest.TestCase):
	"""The regression guard. Every one of these fails if a Redfin value leaks."""

	def test_every_fact_is_zillows(self):
		out = facts(enabled=False)
		self.assertEqual(
			(out["beds"], out["baths"], out["sqft"], out["year_built"]),
			(3, 2, 1500, 1960),
			"a Redfin fact reached the subject with the cascade off",
		)

	def test_no_source_label_says_redfin(self):
		out = facts(enabled=False)
		self.assertNotIn("redfin", set(out["source"].values()))

	def test_bands_are_zillows(self):
		"""The bands ARE the comp filter. This is the assertion that matters."""
		out = facts(enabled=False)
		self.assertEqual(out["beds_band"], [3, 3])
		self.assertEqual(out["baths_band"], [2, 2])
		self.assertEqual(out["sqft_band"], [1500, 1500])
		self.assertEqual(out["year_built_band"], [1960, 1960])

	def test_property_type_is_zillows(self):
		out = facts(enabled=False)
		self.assertEqual(out["property_type"], "Single Family")
		self.assertEqual(out["source"]["property_type"], "zillow")

	def test_last_sale_is_zillows(self):
		"""Redfin's record here carries a sold_date AND a price, so it would win
		outright if the gate leaked."""
		out = facts(enabled=False)
		self.assertEqual(out["last_sale"], {"price": 200000, "date": "2019-01-01"})
		self.assertEqual(out["source"]["last_sale"], "zillow")

	def test_identical_to_having_no_redfin_record_at_all(self):
		"""The strongest form: off with a full record must be byte-identical to
		the same lead with nothing cached. Catches any field added later that
		reads the record without checking the gate."""
		with_record = facts(enabled=False, redfin_rec=REDFIN)
		without = facts(enabled=False, redfin_rec=None)
		with_record.pop("has_redfin")
		without.pop("has_redfin")
		self.assertEqual(with_record, without)


class OffCannotMoveTheCompSet(unittest.TestCase):
	"""Closing the loop on the P0: prove the FILTER WINDOWS are unchanged, not
	merely that a dict field is. This is the chain the flag exists to break."""

	def _windows_for(self, f):
		"""Just the shape windows out of every preset tier."""
		return [
			{k: v for k, v in t["filters"].items()
			 if k.split("_")[0] in ("beds", "baths", "sqft", "year")}
			for t in comps._preset_tiers(f, radius=1.0)
		]

	def _windows(self, enabled):
		return self._windows_for(facts(enabled=enabled))

	def test_filter_windows_match_a_zillow_only_subject(self):
		self.assertEqual(self._windows(enabled=False),
		                 self._windows_for(facts(enabled=False, redfin_rec=None)))

	def test_turning_it_on_does_move_the_windows(self):
		"""The flag would be pointless if both states produced the same filters.
		This is the control: it proves the OFF assertions above are meaningful."""
		self.assertNotEqual(self._windows(enabled=False), self._windows(enabled=True))

	def test_a_boundary_comp_survives_with_the_cascade_off(self):
		"""A 1,150 sqft comp sits inside Zillow's ±25% window (1,125–1,875) and
		outside Redfin's (1,350–2,250). With the cascade off it must still pass,
		which is precisely the comp that would vanish from a rep's board."""
		import datetime
		today = datetime.date.today()
		row = {"square_footage": 1150, "bedrooms": 3, "bathrooms": 2,
		       "year_built": 1960, "property_type": "Single Family",
		       "distance_mi": 0.1, "status": "sold",
		       "sold_date": str(today - datetime.timedelta(days=30))}

		off = comps._preset_tiers(facts(enabled=False), radius=1.0)[0]["filters"]
		on = comps._preset_tiers(facts(enabled=True), radius=1.0)[0]["filters"]

		self.assertTrue(comps._matches(row, off, today),
		                "the cascade is off, so this comp must still be on the board")
		self.assertFalse(comps._matches(row, on, today),
		                 "control: with the cascade on this comp legitimately drops")


class OnAppliesTheCascade(unittest.TestCase):
	def test_redfin_wins_every_field_it_carries(self):
		out = facts(enabled=True)
		self.assertEqual(
			(out["beds"], out["baths"], out["sqft"], out["year_built"]),
			(4, 2.5, 1800, 1972),
		)
		for field in ("beds", "baths", "sqft", "year_built"):
			self.assertEqual(out["source"][field], "redfin", field)

	def test_zillow_still_answers_what_redfin_lacks(self):
		"""On is still a per-field fallthrough, not a provider switch: Zillow
		resolves more subjects than Redfin (77% vs 67%)."""
		out = facts(enabled=True, redfin_rec={"matched": True, "property_id": 9, "beds": 4})
		self.assertEqual((out["beds"], out["source"]["beds"]), (4, "redfin"))
		self.assertEqual((out["sqft"], out["source"]["sqft"]), (1500, "zillow"))

	def test_structurally_zillow_fields_are_untouched_either_way(self):
		"""Taxes, the Zestimate, lot_size and the subject cover photo are Zillow's
		by construction, not by preference \u2014 the flag must not disturb them."""
		for enabled in (False, True):
			out = facts(enabled=enabled)
			self.assertEqual(out["zestimate"], 250000, enabled)
			self.assertEqual(out["lot_size"], "0.25 ac", enabled)
			self.assertEqual(out["cover_photo"], "https://zillow/cover.jpg", enabled)
			self.assertEqual(out["zpid"], "z1", enabled)


class PresenceFlagIsNotGated(unittest.TestCase):
	"""`has_redfin` feeds `redfin.subject_has_vendor_facts` \u2014 the fetch gate,
	not the fact cascade. It must survive the flag being off, or turning the
	cascade off would also narrow the gate 3447bda04 widened."""

	def test_has_redfin_is_true_even_with_the_cascade_off(self):
		self.assertTrue(facts(enabled=False)["has_redfin"])

	def test_has_redfin_is_true_with_the_cascade_on(self):
		self.assertTrue(facts(enabled=True)["has_redfin"])

	def test_an_unmatched_record_is_still_not_a_presence(self):
		"""Ungated is not unconditional: an unmatched record means 'Redfin does
		not know this house', in either flag state."""
		unmatched = {"matched": False, "property_id": 123}
		for enabled in (False, True):
			self.assertFalse(facts(enabled=enabled, redfin_rec=unmatched)["has_redfin"], enabled)

	def test_no_record_is_not_a_presence(self):
		for enabled in (False, True):
			self.assertFalse(facts(enabled=enabled, redfin_rec=None)["has_redfin"], enabled)


if __name__ == "__main__":
	unittest.main()

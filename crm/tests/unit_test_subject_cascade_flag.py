"""Who gets the Redfin-first subject cascade, and therefore whose COMP SET may
move.

WHY THIS FILE EXISTS. The Redfin-first cascade is more accurate per field
(Redfin is the odd one out on sqft 9% of the time against Zillow's 52%), but
the subject's numbers are not display-only: beds/baths/sqft/year become
`*_band`, `_preset_tiers` turns bands into filter windows, and `_matches`
excludes comps outside them. Redfin sqft 1,800 where Zillow said 1,500 moves a
±25% window from 1,125–1,875 to 1,350–2,250, so comps at the edges appear and
disappear on a rep's board.

THE RULE IS TWO-LEVEL, and both levels are pinned below.

    `redfin_first_subject_enabled`  master switch, DEFAULT TRUE. Off, nobody
                                    gets Redfin-first — a full kill switch.
    `_comp_set_frozen(doc)`         per lead. A lead carrying recorded human
                                    judgement keeps its Zillow-first facts
                                    permanently.

The per-lead half is what makes a default of TRUE safe. The improvement lands
on new work automatically, with no flag to remember, while a lead somebody has
already priced or hand-picked comps on cannot have those comps move underneath
them. Recorded judgement is `price_determination_at`, `comps_selected` or
`comps_hidden` — any one is enough, and each is tested independently here
because a rep who picks comps without saving a price is just as exposed.

The saved NUMBERS were never the exposure: a determination snapshots its own
comps. The exposure is that picks and hides store comp DOCNAMES, so a shifted
band removes a hand-picked comp from the board while the saved calc still
cites it.

DETERMINISM. `redfin.cached_subject_record` is a pure cache read, so anything
deciding the cascade from cache warmth is non-deterministic by construction —
the same lead would answer differently on a first and second open with nobody
touching it. The freeze decision reads PERSISTED FIELDS ONLY, and
`FreezeIsDeterministic` pins that.

Two things are deliberately OUTSIDE this gate and are pinned here so a later
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
	"""Subject facts with the GATE FORCED, for testing what the cascade does
	once a decision has been made. `ungated_facts` exercises the real decision."""
	with patch.object(comps, "_redfin_first_for", return_value=enabled):
		return ungated_facts(doc or lead(), redfin_rec=redfin_rec, zillow=zillow)


def ungated_facts(doc, redfin_rec=REDFIN, zillow=ZILLOW):
	"""Subject facts with the REAL gate: master flag plus per-lead freeze."""
	with patch.object(comps, "_self_listing", return_value=None), \
		 patch.object(comps, "_sqft_override", return_value=0), \
		 patch.object(comps, "_sqft_override_supported", return_value=True):
		import crm.api.zillow as zillow_api
		with patch.object(zillow_api, "facts_for_lead", return_value=zillow):
			return comps._subject_facts(doc, redfin_rec)


def took_redfin(out) -> bool:
	"""Did the subject actually read Redfin? Asserted on the BAND, not a label:
	the band is what `_preset_tiers` turns into a comp filter window."""
	return out["sqft_band"] == [1800, 1800]


class _ColumnsMixin:
	"""The three judgement markers are CUSTOM FIELDS. Which ones a site has is
	part of what is under test, so every test states it rather than inheriting
	it.

	`has_column` is assigned rather than driven through `shim.db.columns`
	because other suites REPLACE that method outright on the shared shim and do
	not restore it (see the same note at unit_test_zillow_unavailable.py:47), so
	populating `columns` here would be silently ignored depending on test order.
	The previous callable is put back in tearDown so this file is not the next
	one to leak.
	"""

	def setUp(self):
		import frappe
		frappe.conf.pop("redfin_first_subject_enabled", None)
		self._has_column = frappe.db.has_column
		self.install_fields()

	def tearDown(self):
		import frappe
		frappe.conf.pop("redfin_first_subject_enabled", None)
		frappe.db.has_column = self._has_column

	def install_fields(self, *names):
		"""Pretend the ops repo has added exactly these columns to CRM Lead."""
		import frappe
		installed = set(names)
		frappe.db.has_column = lambda dt, col: col in installed

	def all_fields(self):
		self.install_fields(comps.PRICED_AT_FIELD, comps.SELECTED_FIELD, comps.HIDDEN_FIELD)


class MasterSwitchDefaultsOn(unittest.TestCase):
	"""DEFAULT TRUE, deliberately: an opt-in default leaves the improvement
	waiting on somebody remembering to flip it. Safe because the per-lead freeze
	below is what protects worked leads, not the flag."""

	def test_absent_config_means_on(self):
		import frappe
		frappe.conf.pop("redfin_first_subject_enabled", None)
		self.assertTrue(comps.redfin_first_subject_enabled())

	def test_explicit_truthy_config_keeps_it_on(self):
		import frappe
		try:
			frappe.conf["redfin_first_subject_enabled"] = 1
			self.assertTrue(comps.redfin_first_subject_enabled())
		finally:
			frappe.conf.pop("redfin_first_subject_enabled", None)

	def test_explicit_falsey_config_turns_it_off(self):
		"""The kill switch still kills."""
		import frappe
		try:
			frappe.conf["redfin_first_subject_enabled"] = 0
			self.assertFalse(comps.redfin_first_subject_enabled())
		finally:
			frappe.conf.pop("redfin_first_subject_enabled", None)


class VirginLeadGetsTheCascade(_ColumnsMixin, unittest.TestCase):
	"""The point of the whole change: no flag to flip, no lead to migrate."""

	def test_a_lead_nobody_has_worked_reads_redfin(self):
		self.all_fields()
		self.assertTrue(took_redfin(ungated_facts(lead())))

	def test_and_says_so_in_the_source_labels(self):
		self.all_fields()
		out = ungated_facts(lead())
		self.assertEqual(out["source"]["sqft"], "redfin")
		self.assertEqual(out["sqft"], 1800)


class RecordedJudgementFreezesTheLead(_ColumnsMixin, unittest.TestCase):
	"""Each marker INDEPENDENTLY. A rep who picks comps without ever saving a
	price is exactly as exposed as one who priced the deal, so testing only the
	saved price would miss the commoner case."""

	def test_a_saved_price_determination_freezes_it(self):
		self.all_fields()
		worked = lead(price_determination_at="2026-09-01 10:00:00")
		self.assertTrue(comps._comp_set_frozen(worked))
		self.assertFalse(took_redfin(ungated_facts(worked)))

	def test_a_hand_picked_comp_freezes_it(self):
		self.all_fields()
		worked = lead(comps_selected='["CRM-COMP-1"]')
		self.assertTrue(comps._comp_set_frozen(worked))
		self.assertFalse(took_redfin(ungated_facts(worked)))

	def test_a_hidden_comp_freezes_it(self):
		self.all_fields()
		worked = lead(comps_hidden='["CRM-COMP-2"]')
		self.assertTrue(comps._comp_set_frozen(worked))
		self.assertFalse(took_redfin(ungated_facts(worked)))

	def test_a_frozen_lead_is_byte_identical_to_the_old_behaviour(self):
		"""The strongest form of the promise: a worked lead's facts are the same
		dict it would have had with no Redfin record at all."""
		self.all_fields()
		worked = lead(comps_selected='["CRM-COMP-1"]')
		frozen = ungated_facts(worked, redfin_rec=REDFIN)
		never_had_one = ungated_facts(worked, redfin_rec=None)
		frozen.pop("has_redfin")
		never_had_one.pop("has_redfin")
		self.assertEqual(frozen, never_had_one)

	def test_an_empty_pick_list_is_not_a_judgement(self):
		"""`set_comp_state` rewrites BOTH fields on every call, so a rep who picked
		and then unpicked leaves "[]" behind. An empty list records no surviving
		judgement, so it must not freeze — otherwise one stray click would pin a
		lead to the worse provider forever."""
		self.all_fields()
		self.assertFalse(comps._comp_set_frozen(lead(comps_selected="[]", comps_hidden="[]")))

	def test_garbage_in_the_field_does_not_freeze_or_raise(self):
		"""`_load_list` already swallows bad JSON; pinned so the freeze inherits
		that rather than taking the comps map down on one corrupt row."""
		self.all_fields()
		self.assertFalse(comps._comp_set_frozen(lead(comps_selected="not json")))


class MissingCustomFieldsAreNotAJudgement(_ColumnsMixin, unittest.TestCase):
	"""All three markers are installed by the ops repo, so a site can genuinely
	not have them. Absent must read as "nobody has worked this lead" — never as
	an error, and never as a freeze that would quietly disable the cascade
	estate-wide on a site that is simply behind on migrations."""

	def test_no_columns_at_all_does_not_raise(self):
		self.install_fields()
		self.assertFalse(comps._comp_set_frozen(lead()))

	def test_no_columns_still_gets_the_cascade(self):
		self.install_fields()
		self.assertTrue(took_redfin(ungated_facts(lead())))

	def test_a_value_in_an_uninstalled_column_is_ignored(self):
		"""The guard is the COLUMN, not the value: a doc carrying a stale attribute
		for a field this site has never installed must not freeze anything."""
		self.install_fields()
		stale = lead(price_determination_at="2026-09-01 10:00:00",
		             comps_selected='["CRM-COMP-1"]')
		self.assertFalse(comps._comp_set_frozen(stale))

	def test_the_price_field_alone_is_enough_to_freeze(self):
		"""Partial installs are real. The pick fields being absent must not stop a
		saved determination from freezing the lead."""
		self.install_fields(comps.PRICED_AT_FIELD)
		self.assertTrue(comps._comp_set_frozen(lead(price_determination_at="2026-09-01 10:00:00")))

	def test_the_pick_fields_alone_are_enough_to_freeze(self):
		self.install_fields(comps.SELECTED_FIELD, comps.HIDDEN_FIELD)
		self.assertTrue(comps._comp_set_frozen(lead(comps_selected='["CRM-COMP-1"]')))


class MasterSwitchOverridesEverything(_ColumnsMixin, unittest.TestCase):
	"""Off means off. The per-lead freeze can only ever take the cascade AWAY,
	never grant it — otherwise the kill switch would not be one."""

	def test_off_denies_a_virgin_lead(self):
		import frappe
		self.all_fields()
		frappe.conf["redfin_first_subject_enabled"] = 0
		self.assertFalse(comps._redfin_first_for(lead()))
		self.assertFalse(took_redfin(ungated_facts(lead())))

	def test_off_also_denies_a_worked_lead(self):
		import frappe
		self.all_fields()
		frappe.conf["redfin_first_subject_enabled"] = 0
		self.assertFalse(comps._redfin_first_for(lead(comps_hidden='["CRM-COMP-2"]')))

	def test_on_is_the_only_state_that_can_grant_it(self):
		import frappe
		self.all_fields()
		frappe.conf["redfin_first_subject_enabled"] = 1
		self.assertTrue(comps._redfin_first_for(lead()))
		self.assertFalse(comps._redfin_first_for(lead(comps_selected='["CRM-COMP-1"]')))


class FreezeIsDeterministic(_ColumnsMixin, unittest.TestCase):
	"""THE NON-DETERMINISM PIN.

	`redfin.cached_subject_record` is a pure cache read: it returns None until
	something warms it. Any gate that consulted it would answer differently on a
	first and second open of the SAME lead, moving comps on a rep's board with
	nobody touching anything. The freeze therefore reads persisted fields only,
	and these tests fail if a future edit reaches for the record.
	"""

	def test_the_decision_ignores_whether_a_record_is_cached(self):
		"""`_comp_set_frozen` cannot even see the record — pinned by signature, so
		the only way to break it is a change this test will catch."""
		self.all_fields()
		import inspect
		self.assertEqual(list(inspect.signature(comps._comp_set_frozen).parameters), ["doc"])

	def test_a_worked_lead_answers_the_same_cold_and_warm(self):
		self.all_fields()
		worked = lead(price_determination_at="2026-09-01 10:00:00")
		cold = ungated_facts(worked, redfin_rec=None)
		warm = ungated_facts(worked, redfin_rec=REDFIN)
		cold.pop("has_redfin")
		warm.pop("has_redfin")
		self.assertEqual(cold, warm, "a frozen lead moved when the cache warmed")

	def test_repeated_calls_agree(self):
		"""No memoisation, no first-call special case, in either direction."""
		self.all_fields()
		for doc in (lead(), lead(comps_selected='["CRM-COMP-1"]')):
			answers = {comps._comp_set_frozen(doc) for _ in range(3)}
			self.assertEqual(len(answers), 1, doc.get("comps_selected"))


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

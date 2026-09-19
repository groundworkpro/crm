"""Who gets the Redfin-first subject cascade, and therefore whose COMP SET may
move.

WHY THIS FILE EXISTS. The Redfin-first cascade is more accurate per field
(Redfin is the odd one out on sqft 9% of the time against Zillow's 52%), but
the subject's numbers are not display-only: beds/baths/sqft/year become
`*_band`, `_preset_tiers` turns bands into filter windows, and `_matches`
excludes comps outside them. Redfin sqft 1,800 where Zillow said 1,500 moves a
±25% window from 1,125–1,875 to 1,350–2,250, so comps at the edges appear and
disappear on a rep's board.

THE RULE IS A CREATION CUTOVER, and one knob sets it.

    `redfin_first_cutover`   a datetime in site_config.
                             unset -> OFF for everybody (the default)
                             set   -> `creation >= cutover` is Redfin-first,
                                      `creation <  cutover` is Zillow-first,
                                      both permanently.

WHY NOT A FREEZE ON RECORDED JUDGEMENT, which this file used to test. That rule
gave a post-cutover lead the Redfin bands, let a rep pick a comp out of them,
and then — because the pick was itself the freeze marker — flipped that same
lead back to ZILLOW bands. `_matches` does not consult selection (comps.py's
`selected_count` counts picks among rows that already matched), so the comp the
rep had just hand-picked could fall outside the new window and vanish. The
freeze caused the exact harm it existed to prevent, one cohort later.

`creation` cannot do that, because it never changes. A lead's answer does not
depend on rep activity, on deploy order, or on cache warmth — which is the
other bug this replaces. `redfin.cached_subject_record` is a pure cache read,
so a gate consulting it would have answered differently on a lead's first and
second open with nobody touching anything. `GateIsDeterministic` pins both.

WHAT THE DEFAULT BUYS. Unset means deploying this code changes nothing at all,
for anybody. Turning it on is then a dated, reversible decision: a past date
opts in everything since then, today's date opts in new work only, and deleting
the key rolls back instantly because the decision is never written down
anywhere.

Two things are deliberately OUTSIDE this gate and are pinned here so a later
"consistency" pass does not sweep them in:

* `has_redfin` — a PRESENCE flag for `redfin.subject_has_vendor_facts` (the
  fetch gate widened in 3447bda04). It says a record exists, never what is in
  it, and reaches no band, window or displayed number.
* the comp PHOTO ladder in `_shape_detail` — changes which pictures show on a
  comp, never which comps exist.
"""
import datetime
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install
install()

from crm.api import comps


#: The instant the estate splits, for every test here.
CUTOVER = "2026-09-19 00:00:00"
BEFORE = "2026-09-18 23:59:59"
AFTER = "2026-09-19 00:00:01"


def lead(**kw):
	doc = {
		"doctype": "CRM Lead", "name": "CRM-LEAD-1",
		"property_address": "5 Main St", "property_city": "Minneapolis",
		"property_state": "MN", "property_zip": "55401",
		"bedrooms": "", "bathrooms": "", "square_footage": "", "year_built": "",
		# Post-cutover unless a test says otherwise: the interesting default is
		# the one where a leak is visible.
		"creation": AFTER,
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
	"""Subject facts with the REAL gate: whatever `redfin_first_cutover` says."""
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


class _ConfMixin:
	"""`frappe.conf` is shared across the whole suite, so the knob is set and
	removed per test rather than left lying around for the next file."""

	def setUp(self):
		import frappe
		frappe.conf.pop("redfin_first_cutover", None)

	def tearDown(self):
		import frappe
		frappe.conf.pop("redfin_first_cutover", None)

	def set_cutover(self, value):
		import frappe
		frappe.conf["redfin_first_cutover"] = value


class UnsetMeansOffForEverybody(_ConfMixin, unittest.TestCase):
	"""THE DEFAULT, and the reason shipping this is a no-op: with no cutover
	configured there is no lead, of any age, that reads Redfin first."""

	def test_no_config_at_all_is_none(self):
		self.assertIsNone(comps.redfin_first_cutover())

	def test_a_brand_new_lead_does_not_get_it(self):
		self.assertFalse(comps._redfin_first_for(lead(creation=AFTER)))
		self.assertFalse(took_redfin(ungated_facts(lead(creation=AFTER))))

	def test_an_old_lead_does_not_get_it(self):
		self.assertFalse(comps._redfin_first_for(lead(creation=BEFORE)))

	def test_a_lead_with_no_creation_does_not_get_it(self):
		"""Off is off. The missing-creation rule is about which SIDE of a cutover
		a doc falls on, and with no cutover there are no sides."""
		self.assertFalse(comps._redfin_first_for(lead(creation=None)))

	def test_an_empty_string_is_the_same_as_unset(self):
		"""A key left in site_config with the value cleared out. Whitespace counts,
		because a human editing JSON leaves some behind."""
		for blank in ("", "   "):
			self.set_cutover(blank)
			self.assertIsNone(comps.redfin_first_cutover(), repr(blank))
			self.assertFalse(comps._redfin_first_for(lead(creation=AFTER)), repr(blank))

	def test_emptiness_is_checked_before_get_datetime_is_called(self):
		"""THE TRAP, pinned against REAL frappe rather than the shim.

		`frappe.utils.get_datetime(None)` returns NOW. So a missing or blanked key
		handed straight to it reads as "the cutover is this instant" — silently ON
		for all new work, which is precisely what the default exists to prevent.
		The shim raises on None instead, so without this test the guard would be
		protected only by an accident of the stub; here the real behaviour is
		substituted so the guard is what is actually under test.
		"""
		import frappe
		now = datetime.datetime(2026, 9, 7, 12, 0, 0)

		def real_frappe_get_datetime(v=None):
			if v is None:
				return now
			return v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v))

		with patch.object(frappe.utils, "get_datetime", real_frappe_get_datetime):
			for blank in (None, "", "   "):
				if blank is None:
					frappe.conf.pop("redfin_first_cutover", None)
				else:
					self.set_cutover(blank)
				self.assertIsNone(comps.redfin_first_cutover(), repr(blank))
				self.assertFalse(
					comps._redfin_first_for(lead(creation=AFTER)),
					f"{blank!r} was read as a cutover of NOW, silently enabling the cascade",
				)


class TheCutoverSplitsTheEstate(_ConfMixin, unittest.TestCase):
	"""The rule itself, asserted on the BAND rather than a label because the band
	is what decides which comps are on the board."""

	def setUp(self):
		super().setUp()
		self.set_cutover(CUTOVER)

	def test_a_lead_created_after_it_reads_redfin(self):
		self.assertTrue(comps._redfin_first_for(lead(creation=AFTER)))
		self.assertTrue(took_redfin(ungated_facts(lead(creation=AFTER))))

	def test_a_lead_created_before_it_stays_zillow(self):
		self.assertFalse(comps._redfin_first_for(lead(creation=BEFORE)))
		self.assertFalse(took_redfin(ungated_facts(lead(creation=BEFORE))))

	def test_the_old_lead_is_identical_to_having_no_redfin_record(self):
		"""The promise to every lead already on the board: its facts are the dict
		it would have had if Redfin had never been asked."""
		old = lead(creation=BEFORE)
		gated = ungated_facts(old, redfin_rec=REDFIN)
		never_had_one = ungated_facts(old, redfin_rec=None)
		gated.pop("has_redfin")
		never_had_one.pop("has_redfin")
		self.assertEqual(gated, never_had_one)

	def test_and_says_so_in_the_source_labels(self):
		out = ungated_facts(lead(creation=AFTER))
		self.assertEqual(out["source"]["sqft"], "redfin")
		self.assertEqual(out["sqft"], 1800)


class TheBoundaryIsInclusive(_ConfMixin, unittest.TestCase):
	"""AT the cutover instant is IN. Pinned explicitly because ">=" and ">" are a
	one-character difference that no other test in this file would catch, and
	because the answer has to be stated somewhere rather than inferred."""

	def setUp(self):
		super().setUp()
		self.set_cutover(CUTOVER)

	def test_created_exactly_at_the_cutover_is_post_cutover(self):
		self.assertTrue(comps._redfin_first_for(lead(creation=CUTOVER)))
		self.assertTrue(took_redfin(ungated_facts(lead(creation=CUTOVER))))

	def test_one_microsecond_before_is_pre_cutover(self):
		self.assertFalse(comps._redfin_first_for(lead(creation="2026-09-18 23:59:59.999999")))

	def test_one_microsecond_after_is_post_cutover(self):
		self.assertTrue(comps._redfin_first_for(lead(creation="2026-09-19 00:00:00.000001")))


class CreationIsReadInEveryShapeItArrivesIn(_ConfMixin, unittest.TestCase):
	"""`creation` is a string off the database and a datetime on a doc held in
	memory. The two must not disagree, or the same lead would answer differently
	depending on how it was loaded — which is the non-determinism this rule
	exists to remove, reintroduced by the back door."""

	def setUp(self):
		super().setUp()
		self.set_cutover(CUTOVER)

	def test_a_string_and_a_datetime_agree_after_the_cutover(self):
		as_str = lead(creation="2026-09-20 08:30:00")
		as_dt = lead(creation=datetime.datetime(2026, 9, 20, 8, 30, 0))
		self.assertTrue(comps._redfin_first_for(as_str))
		self.assertTrue(comps._redfin_first_for(as_dt))

	def test_a_string_and_a_datetime_agree_before_the_cutover(self):
		as_str = lead(creation="2026-09-01 08:30:00")
		as_dt = lead(creation=datetime.datetime(2026, 9, 1, 8, 30, 0))
		self.assertFalse(comps._redfin_first_for(as_str))
		self.assertFalse(comps._redfin_first_for(as_dt))

	def test_the_facts_are_identical_either_way(self):
		"""Not just the gate — the whole subject dict, because that is what a rep
		actually sees."""
		as_str = ungated_facts(lead(creation="2026-09-20 08:30:00"))
		as_dt = ungated_facts(lead(creation=datetime.datetime(2026, 9, 20, 8, 30, 0)))
		self.assertEqual(as_str, as_dt)

	def test_a_date_only_cutover_works(self):
		"""What a human will actually type into site_config. Midnight that day."""
		self.set_cutover("2026-09-19")
		self.assertTrue(comps._redfin_first_for(lead(creation=AFTER)))
		self.assertFalse(comps._redfin_first_for(lead(creation=BEFORE)))

	def test_a_datetime_in_the_config_works(self):
		"""site_config is JSON so this cannot happen from a file, but `frappe.conf`
		is a plain dict that other code can write to."""
		self.set_cutover(datetime.datetime(2026, 9, 19, 0, 0, 0))
		self.assertTrue(comps._redfin_first_for(lead(creation=AFTER)))
		self.assertFalse(comps._redfin_first_for(lead(creation=BEFORE)))


class ADocWithNoCreationIsPostCutover(_ConfMixin, unittest.TestCase):
	"""A doc that has never been inserted is being created NOW, which is after any
	cutover that has already passed. Treating it as pre-cutover would instead give
	an unsaved lead the OLD provider and then silently change its comps the moment
	it was saved."""

	def setUp(self):
		super().setUp()
		self.set_cutover(CUTOVER)

	def test_missing_creation_reads_redfin(self):
		self.assertTrue(comps._redfin_first_for(lead(creation=None)))
		self.assertTrue(took_redfin(ungated_facts(lead(creation=None))))

	def test_empty_creation_reads_redfin(self):
		self.assertTrue(comps._redfin_first_for(lead(creation="")))

	def test_it_does_not_raise(self):
		"""The comps map must never 500 on a doc shape, whatever else it does."""
		self.assertIsInstance(comps._redfin_first_for(lead(creation=None)), bool)


class BadConfigFailsSafeAndLoud(_ConfMixin, unittest.TestCase):
	"""A typo means OFF plus an error log. Never an exception: a desk that cannot
	price a lead because somebody fat-fingered site_config is a worse outcome than
	the cascade staying off. And never ON: a value nobody can read must not be
	interpreted as permission to move every comp set in the estate."""

	def _errors(self):
		import frappe
		return frappe.errors

	def test_garbage_is_off_and_does_not_raise(self):
		for junk in ("not a date", "2026-13-45", "yesterday", True, 12345, [], {"a": 1}):
			self.set_cutover(junk)
			self.assertIsNone(comps.redfin_first_cutover(), repr(junk))
			self.assertFalse(comps._redfin_first_for(lead(creation=AFTER)), repr(junk))

	def test_garbage_logs(self):
		before = len(self._errors())
		self.set_cutover("not a date")
		comps.redfin_first_cutover()
		self.assertEqual(len(self._errors()), before + 1)
		self.assertIn("redfin_first_cutover", str(self._errors()[-1]))

	def test_a_valid_value_logs_nothing(self):
		"""The control: proves the assertion above is about the failure and not
		about logging in general."""
		before = len(self._errors())
		self.set_cutover(CUTOVER)
		self.assertIsNotNone(comps.redfin_first_cutover())
		self.assertEqual(len(self._errors()), before)

	def test_an_unset_knob_logs_nothing(self):
		"""Not configured is the intended state, not a fault."""
		before = len(self._errors())
		self.assertIsNone(comps.redfin_first_cutover())
		self.assertEqual(len(self._errors()), before)

	def test_the_whole_subject_still_renders_with_bad_config(self):
		"""End to end: bad config degrades to Zillow-first facts, not a traceback."""
		self.set_cutover("not a date")
		out = ungated_facts(lead(creation=AFTER))
		self.assertFalse(took_redfin(out))
		self.assertEqual(out["sqft"], 1500)


class GateIsDeterministic(_ConfMixin, unittest.TestCase):
	"""THE NON-DETERMINISM PIN.

	`redfin.cached_subject_record` is a pure cache read: it returns None until
	something warms it. A gate consulting it would answer differently on a first
	and second open of the SAME lead, moving comps on a rep's board with nobody
	touching anything. The gate therefore sees one immutable field and one config
	value, and these tests fail if a future edit reaches for anything else.
	"""

	def setUp(self):
		super().setUp()
		self.set_cutover(CUTOVER)

	def test_the_decision_ignores_whether_a_record_is_cached(self):
		"""`_redfin_first_for` cannot even see the record — pinned by signature, so
		the only way to break it is a change this test will catch."""
		import inspect
		self.assertEqual(list(inspect.signature(comps._redfin_first_for).parameters), ["doc"])

	def test_the_same_lead_decides_the_same_cold_and_warm(self):
		for creation in (BEFORE, AFTER, None):
			doc = lead(creation=creation)
			cold = comps._redfin_first_for(doc)
			with patch.object(comps, "_self_listing", return_value=None):
				ungated_facts(doc, redfin_rec=REDFIN)
			self.assertEqual(comps._redfin_first_for(doc), cold, repr(creation))

	def test_a_pre_cutover_lead_answers_the_same_cold_and_warm(self):
		"""The facts too, not just the decision: an old lead must not move when
		the Redfin cache fills behind it."""
		old = lead(creation=BEFORE)
		cold = ungated_facts(old, redfin_rec=None)
		warm = ungated_facts(old, redfin_rec=REDFIN)
		cold.pop("has_redfin")
		warm.pop("has_redfin")
		self.assertEqual(cold, warm, "a pre-cutover lead moved when the cache warmed")

	def test_repeated_calls_agree(self):
		"""No memoisation, no first-call special case, in either direction."""
		for creation in (BEFORE, AFTER):
			doc = lead(creation=creation)
			answers = {comps._redfin_first_for(doc) for _ in range(3)}
			self.assertEqual(len(answers), 1, repr(creation))

	def test_rep_activity_does_not_change_the_answer(self):
		"""THE BUG THIS RULE REPLACES. The previous gate froze a lead the moment a
		rep picked or hid a comp or saved a price — so a post-cutover lead handed a
		rep the Redfin bands, and their first pick flipped it back to Zillow bands,
		which could drop the comp they had just picked. `creation` is immutable, so
		none of these markers can move a lead now."""
		worked = lead(creation=AFTER, comps_selected='["CRM-COMP-1"]',
		              comps_hidden='["CRM-COMP-2"]',
		              price_determination_at="2026-09-20 10:00:00")
		self.assertTrue(comps._redfin_first_for(worked))
		self.assertTrue(took_redfin(ungated_facts(worked)))

	def test_rep_activity_does_not_rescue_a_pre_cutover_lead_either(self):
		"""Symmetrically: judgement cannot grant the cascade any more than it can
		take it away. The cutover is the only input."""
		worked = lead(creation=BEFORE, comps_selected='["CRM-COMP-1"]')
		self.assertFalse(comps._redfin_first_for(worked))


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
	merely that a dict field is. This is the chain the cutover exists to break."""

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
		"""The cutover would be pointless if both sides produced the same filters.
		This is the control: it proves the OFF assertions above are meaningful."""
		self.assertNotEqual(self._windows(enabled=False), self._windows(enabled=True))

	def test_a_boundary_comp_survives_with_the_cascade_off(self):
		"""A 1,150 sqft comp sits inside Zillow's ±25% window (1,125–1,875) and
		outside Redfin's (1,350–2,250). With the cascade off it must still pass,
		which is precisely the comp that would vanish from a rep's board."""
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
		by construction, not by preference \u2014 the cutover must not disturb them."""
		for enabled in (False, True):
			out = facts(enabled=enabled)
			self.assertEqual(out["zestimate"], 250000, enabled)
			self.assertEqual(out["lot_size"], "0.25 ac", enabled)
			self.assertEqual(out["cover_photo"], "https://zillow/cover.jpg", enabled)
			self.assertEqual(out["zpid"], "z1", enabled)


class PresenceFlagIsNotGated(unittest.TestCase):
	"""`has_redfin` feeds `redfin.subject_has_vendor_facts` \u2014 the fetch gate,
	not the fact cascade. It must survive the cascade being off, or turning the
	cascade off would also narrow the gate 3447bda04 widened."""

	def test_has_redfin_is_true_even_with_the_cascade_off(self):
		self.assertTrue(facts(enabled=False)["has_redfin"])

	def test_has_redfin_is_true_with_the_cascade_on(self):
		self.assertTrue(facts(enabled=True)["has_redfin"])

	def test_an_unmatched_record_is_still_not_a_presence(self):
		"""Ungated is not unconditional: an unmatched record means 'Redfin does
		not know this house', on either side of the cutover."""
		unmatched = {"matched": False, "property_id": 123}
		for enabled in (False, True):
			self.assertFalse(facts(enabled=enabled, redfin_rec=unmatched)["has_redfin"], enabled)

	def test_no_record_is_not_a_presence(self):
		for enabled in (False, True):
			self.assertFalse(facts(enabled=enabled, redfin_rec=None)["has_redfin"], enabled)


if __name__ == "__main__":
	unittest.main()

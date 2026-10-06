"""A rep-entered comp square footage (Dennis, 2026-10-01: a sold comp with no
living area could not feed the $/sf average). Stored per lead in `comps_sqft`,
stamped over the row, and the scraped value kept as `sqft_original`."""

import json
import unittest

from crm.tests.frappe_shim import install

shim = install()

from crm.api import comps


class Doc(dict):
	doctype = "CRM Lead"


def _doc(raw):
	shim.db.columns = {"CRM Lead": {comps.COMP_SQFT_FIELD}}
	return Doc({comps.COMP_SQFT_FIELD: raw})


class LoadCompSqft(unittest.TestCase):
	def test_reads_map_and_drops_garbage(self):
		raw = json.dumps({"zillow::1": 950, "redfin::2": "1200", "x": 0, "y": "abc", "z": -5})
		self.assertEqual(comps._load_comp_sqft(_doc(raw)), {"zillow::1": 950, "redfin::2": 1200})

	def test_missing_column_reads_nothing(self):
		doc = _doc(json.dumps({"a": 900}))
		shim.db.columns = {}
		self.assertEqual(comps._load_comp_sqft(doc), {})

	def test_bad_json(self):
		self.assertEqual(comps._load_comp_sqft(_doc("{nope")), {})
		self.assertEqual(comps._load_comp_sqft(_doc("[1,2]")), {})


class ApplyCompSqft(unittest.TestCase):
	def test_fills_blank_and_marks_manual(self):
		rows = [{"name": "a", "square_footage": None}, {"name": "b", "square_footage": 1000}]
		comps._apply_comp_sqft(rows, {"a": 900})
		self.assertEqual(rows[0]["square_footage"], 900)
		self.assertEqual(rows[0]["sqft_source"], "manual")
		self.assertIsNone(rows[0]["sqft_original"])
		self.assertEqual(rows[1], {"name": "b", "square_footage": 1000})

	def test_second_pass_keeps_first_original(self):
		# The end-of-request pass runs after Zillow may have rewritten the row.
		row = {"name": "a", "square_footage": 1100}
		comps._apply_comp_sqft([row], {"a": 900})
		row["square_footage"] = 1150
		comps._apply_comp_sqft([row], {"a": 900})
		self.assertEqual((row["square_footage"], row["sqft_original"]), (900, 1100))

	def test_none_rows_or_map(self):
		comps._apply_comp_sqft(None, {"a": 1})
		comps._apply_comp_sqft([{"name": "a"}], {})


if __name__ == "__main__":
	unittest.main()

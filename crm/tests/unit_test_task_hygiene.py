"""Cadence pile-up: only the newest 'day N of 10' task survives."""

import unittest

from crm.tests.frappe_shim import install

shim = install()

from crm.api import task_hygiene as th  # noqa: E402


def task(name, title, due=None):
	return {"name": name, "title": title, "due_date": due, "status": "Todo"}


class CadenceDayTests(unittest.TestCase):
	def test_reads_the_day_number(self):
		self.assertEqual(th.cadence_day("Text Jere& Robin — day 4 of 10"), 4)
		self.assertEqual(th.cadence_day("Text Bob — day 10 of 10"), 10)

	def test_hyphen_and_en_dash_variants(self):
		self.assertEqual(th.cadence_day("Text Bob - day 2 of 10"), 2)
		self.assertEqual(th.cadence_day("Text Bob – day 2 of 10"), 2)

	def test_human_typed_tasks_are_not_cadence_tasks(self):
		for t in ("Follow up", "Call Jere& Robin Walkow", "", None, "day 4 of 10 something"):
			self.assertIsNone(th.cadence_day(t), t)


class SupersededTests(unittest.TestCase):
	def test_keeps_only_the_latest_day(self):
		rows = [
			task("T1", "Text Bob — day 4 of 10"),
			task("T2", "Text Bob — day 5 of 10"),
			task("T3", "Text Bob — day 6 of 10"),
		]
		self.assertEqual([r["name"] for r in th.superseded_tasks(rows)], ["T1", "T2"])

	def test_single_open_day_is_left_alone(self):
		rows = [task("T3", "Text Bob — day 6 of 10")]
		self.assertEqual(th.superseded_tasks(rows), [])

	def test_never_touches_non_cadence_tasks(self):
		rows = [
			task("H1", "Follow up"),
			task("H2", "Call Bob Smith"),
			task("T1", "Text Bob — day 4 of 10"),
			task("T2", "Text Bob — day 5 of 10"),
		]
		self.assertEqual([r["name"] for r in th.superseded_tasks(rows)], ["T1"])

	def test_lead_with_no_cadence_tasks(self):
		self.assertEqual(th.superseded_tasks([task("H1", "Follow up")]), [])
		self.assertEqual(th.superseded_tasks([]), [])

	def test_out_of_order_rows_still_keep_the_highest_day(self):
		rows = [
			task("T6", "Text Bob — day 6 of 10"),
			task("T2", "Text Bob — day 2 of 10"),
			task("T9", "Text Bob — day 9 of 10"),
		]
		self.assertEqual(sorted(r["name"] for r in th.superseded_tasks(rows)), ["T2", "T6"])


if __name__ == "__main__":
	unittest.main()

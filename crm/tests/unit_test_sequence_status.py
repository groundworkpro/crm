"""Sequence status gating (pure parts) + the Today cadence's daily closer card."""

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from crm.tests.frappe_shim import install

shim = install()

from crm.api import daily_standup as ds  # noqa: E402
from crm.api import sequence_status as ss  # noqa: E402


class StatusGateTests(unittest.TestCase):
	def test_blank_means_any_status(self):
		self.assertIsNone(ss.parse_statuses(""))
		self.assertIsNone(ss.parse_statuses(None))
		self.assertIsNone(ss.parse_statuses("  \n \n"))
		self.assertFalse(ss.status_excluded(None, "Dead Lead"))

	def test_one_per_line_trimmed(self):
		allowed = ss.parse_statuses(" New \nCalled No Answer\n\n")
		self.assertEqual(allowed, frozenset({"New", "Called No Answer"}))
		self.assertFalse(ss.status_excluded(allowed, "New"))
		self.assertTrue(ss.status_excluded(allowed, "Follow Up"))
		self.assertTrue(ss.status_excluded(allowed, ""))
		self.assertTrue(ss.status_excluded(allowed, None))

	def test_pause_only_the_gated_sequences(self):
		shim.db.has_column = lambda dt, col: True
		values = {"Seq A": "New\nCalled No Answer", "Seq B": ""}
		shim.db.get_value = lambda dt, name, field=None, *a, **k: values.get(name)
		shim.get_all = lambda *a, **k: [
			SimpleNamespace(name="ENR-A", sequence="Seq A"),
			SimpleNamespace(name="ENR-B", sequence="Seq B"),
		]
		writes = []
		shim.db.set_value = lambda dt, name, vals, **k: writes.append((name, vals))
		paused = ss.pause_excluded_enrollments("LEAD-1", "Follow Up")
		self.assertEqual(paused, ["ENR-A"])
		self.assertEqual(writes[0][0], "ENR-A")
		self.assertEqual(writes[0][1]["status"], "Paused")
		self.assertIn("auto-paused", writes[0][1]["last_log"])
		self.assertIn("Follow Up", writes[0][1]["last_log"])

	def test_hook_ignores_saves_that_did_not_change_status(self):
		shim.db.has_column = lambda dt, col: True
		calls = []
		shim.get_all = lambda *a, **k: calls.append(1) or []
		doc = SimpleNamespace(name="LEAD-1", status="New", has_value_changed=lambda f: False)
		ss.on_lead_update(doc)
		self.assertEqual(calls, [])

	def test_unprovisioned_column_is_a_noop(self):
		shim.db.has_column = lambda dt, col: False
		shim.get_all = lambda *a, **k: self.fail("must not query enrollments")
		self.assertEqual(ss.pause_excluded_enrollments("LEAD-1", "Dead Lead"), [])
		self.assertTrue(
			ss.check_before_step(SimpleNamespace(name="E", sequence="S", lead="L"))
		)


def _row(status, **kw):
	base = dict(
		name="LEAD-1",
		lead_name="Test",
		status=status,
		creation=datetime(2026, 8, 3, 9, 0),
		first_call=datetime(2026, 8, 3, 10, 0),
		last_call=datetime(2026, 8, 3, 10, 0),  # weeks ago → monthly-sweep territory
		calls_today=0,
		next_future_due=None,
		tasks_due_now=0,
		due_task_title=None,
		last_contact=None,
		in_sequence=False,
	)
	base.update(kw)
	return SimpleNamespace(**base)


class CloserCardTests(unittest.TestCase):
	today = date(2026, 9, 7)

	def test_closer_status_without_task_is_due_daily(self):
		for status in ds.CLOSER_STATUSES:
			phase, need, due, reason = ds._classify(_row(status, last_call=datetime(2026, 9, 4, 10, 0)), self.today)
			self.assertEqual((phase, need, due), ("closer", 1, True), status)
			self.assertIn("no next step", reason); self.assertIn("since last contact", reason)

	def test_future_task_clears_it(self):
		phase, _, due, _ = ds._classify(
			_row("Make Offer", next_future_due=datetime(2026, 9, 9, 9, 0)), self.today
		)
		self.assertEqual((phase, due), ("scheduled", False))

	def test_task_due_today_keeps_it_and_names_the_task(self):
		phase, _, due, reason = ds._classify(
			_row("Contract Sent", tasks_due_now=1, due_task_title="Send DD docs"), self.today
		)
		self.assertEqual((phase, due), ("closer", True))
		self.assertIn("Send DD docs", reason)

	def test_no_board_cadence_while_a_sequence_drives_it(self):
		# never-called, week-one, weekly, monthly: none of them make a card now
		for kw in (
			dict(first_call=None, last_call=None),
			dict(creation=datetime(2026, 9, 4, 9, 0), first_call=datetime(2026, 9, 4, 9, 0), last_call=datetime(2026, 9, 4, 9, 0)),
			dict(last_call=datetime(2026, 8, 25, 9, 0)),
			dict(last_call=datetime(2026, 6, 1, 9, 0)),
		):
			phase, need, due, _ = ds._classify(_row("Called No Answer", in_sequence=True, **kw), self.today)
			self.assertEqual((phase, need, due), ("sequence", 0, False), kw)

	def test_no_sequence_and_no_next_step_is_poked_daily(self):
		phase, need, due, reason = ds._classify(
			_row("Follow Up", last_contact=datetime(2026, 9, 1, 10, 0)), self.today
		)
		self.assertEqual((phase, need, due), ("nudge", 1, True))
		self.assertIn("no next step", reason)
		self.assertIn("6 days since last contact", reason)
		phase, _, _, reason = ds._classify(_row("New", last_call=None, first_call=None), self.today)
		self.assertEqual(phase, "nudge")
		self.assertIn("never contacted", reason)

	def test_last_contact_counts_texts(self):
		r = _row("Follow Up", last_call=datetime(2026, 8, 1, 9, 0), last_contact=datetime(2026, 9, 6, 9, 0))
		self.assertEqual(ds.last_contact_label(r, self.today), "1 day since last contact")
		r = _row("Follow Up", last_contact=datetime(2026, 9, 7, 9, 0))
		self.assertEqual(ds.last_contact_label(r, self.today), "last contact today")

	def test_future_task_silences_the_poke(self):
		phase, _, due, _ = ds._classify(
			_row("Follow Up", next_future_due=datetime(2026, 9, 12, 9, 0)), self.today
		)
		self.assertEqual((phase, due), ("scheduled", False))

	def test_task_due_today_is_the_only_other_card(self):
		phase, need, due, reason = ds._classify(
			_row("Called No Answer", tasks_due_now=1, due_task_title="Text Joe — day 3 of 10"),
			self.today,
		)
		self.assertEqual((phase, need, due), ("task", 1, True))
		self.assertIn("Text Joe", reason)
		self.assertIn("closer", ds.CADENCE_PHASES)

	def test_past_contract_sent_is_not_due(self):
		for status in ds.POST_CONTRACT_STATUSES:
			phase, need, due, _ = ds._classify(_row(status), self.today)
			self.assertEqual((phase, need, due), ("dispo", 0, False), status)

	def test_past_contract_with_a_due_task_gets_a_task_card(self):
		for status in ds.POST_CONTRACT_STATUSES:
			phase, need, due, reason = ds._classify(
				_row(status, tasks_due_now=1, due_task_title="Call title co"), self.today
			)
			self.assertEqual((phase, need, due), ("task", 1, True), status)
			self.assertIn(status, reason)
			self.assertIn("Call title co", reason)
		phase, _, due, _ = ds._classify(_row("Contract Sent"), self.today)
		self.assertEqual((phase, due), ("closer", True))


class FollowUpReopenTests(unittest.TestCase):
	now = datetime(2026, 10, 8, 14, 0)

	def _card(self, state="Done", resolved=datetime(2026, 10, 8, 11, 0), lead="L1", name="C1"):
		return {"name": name, "lead": lead, "state": state, "resolved_at": resolved}

	def _task(self, due, lead="L1", title="Follow up"):
		return {"reference_docname": lead, "title": title, "due_date": due}

	def test_follow_up_due_after_resolve_reopens(self):
		picks = ds.followup_reopens([self._card()], [self._task(datetime(2026, 10, 8, 13, 0))], self.now)
		self.assertEqual([p[0] for p in picks], ["C1"])

	def test_skipped_card_reopens_too(self):
		picks = ds.followup_reopens([self._card("Skipped")], [self._task(datetime(2026, 10, 8, 13, 0))], self.now)
		self.assertEqual(len(picks), 1)

	def test_not_yet_due_stays_closed(self):
		self.assertEqual(ds.followup_reopens([self._card()], [self._task(datetime(2026, 10, 8, 15, 0))], self.now), [])

	def test_task_already_due_when_resolved_stays_closed(self):
		self.assertEqual(ds.followup_reopens([self._card()], [self._task(datetime(2026, 10, 8, 8, 0))], self.now), [])

	def test_lead_with_open_card_is_left_alone(self):
		cards = [self._card(), self._card("To Call", None, name="C2")]
		self.assertEqual(ds.followup_reopens(cards, [self._task(datetime(2026, 10, 8, 13, 0))], self.now), [])

	def test_one_card_per_lead_latest_resolved(self):
		cards = [self._card(name="C1", resolved=datetime(2026, 10, 8, 9, 0)),
		         self._card(name="C2", resolved=datetime(2026, 10, 8, 10, 0))]
		picks = ds.followup_reopens(cards, [self._task(datetime(2026, 10, 8, 13, 0))], self.now)
		self.assertEqual([p[0] for p in picks], ["C2"])


class DueTaskAssigneeTests(unittest.TestCase):
	def test_assignee_of_task_due_today(self):
		eod = datetime(2026, 10, 8, 23, 59, 59)
		tasks = [
			{"reference_docname": "L1", "assigned_to": "dennis@x", "due_date": datetime(2026, 10, 4, 9, 0)},
			{"reference_docname": "L1", "assigned_to": "exe@x", "due_date": datetime(2026, 10, 9, 9, 0)},
			{"reference_docname": "L2", "assigned_to": "", "due_date": datetime(2026, 10, 8, 9, 0)},
		]
		self.assertEqual(ds.due_task_assignees(tasks, eod), {"L1": {"dennis@x"}})


class SequenceTaskTitleTests(unittest.TestCase):
	def test_day_number(self):
		self.assertEqual(ds.sequence_day("Text Joe — day 2 of 10"), 2)
		self.assertEqual(ds.sequence_day("Text Joe — day 1 of 10"), 1)
		self.assertEqual(ds.sequence_day("Call Joe"), 0)
		self.assertEqual(ds.sequence_day("Follow up"), 0)

	def test_call_task(self):
		self.assertTrue(ds.is_sequence_call_task("Call Joe Williams"))
		self.assertFalse(ds.is_sequence_call_task("Text Joe — day 1 of 10"))
		self.assertFalse(ds.is_sequence_call_task("Follow up"))

	def test_triple_dial_suffix(self):
		self.assertEqual(
			ds.with_triple_dial("Text Joe — day 3 of 10", True),
			"Text Joe — day 3 of 10 · triple dial",
		)
		self.assertEqual(ds.with_triple_dial("Text Joe — day 2 of 10", False), "Text Joe — day 2 of 10")

	def test_board_rows_are_text_then_triple_dial(self):
		rows = ds.board_task_rows(
			[
				{"title": "Text Joe — day 3 of 10"},
				{"title": "Text Joe — day 1 of 10"},
				{"title": "Call Joe"},
			]
		)
		self.assertEqual(
			[t["title"] for t in rows],
			["Text Joe — day 3 of 10", "Call Joe"],
		)

	def test_board_rows_text_only_day(self):
		rows = ds.board_task_rows([{"title": "Text Joe — day 2 of 10"}])
		self.assertEqual([t["title"] for t in rows], ["Text Joe — day 2 of 10"])

	def test_board_rows_plain_follow_up(self):
		rows = ds.board_task_rows([{"title": "Follow up"}])
		self.assertEqual([t["title"] for t in rows], ["Follow up"])


if __name__ == "__main__":
	unittest.main()

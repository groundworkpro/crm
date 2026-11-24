"""Unanswered seller text replies make a card on the owner's Today board."""

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from crm.tests.frappe_shim import install

shim = install()

from crm.api import daily_standup as ds  # noqa: E402

TODAY = date(2026, 11, 24)
REPLY_AT = datetime(2026, 11, 24, 10, 30)


def _row(status="Called No Answer", **kw):
	base = dict(
		name="LEAD-1", lead_name="Jeff Bradley", status=status,
		creation=datetime(2026, 9, 1, 9, 0), first_call=datetime(2026, 9, 1, 10, 0),
		last_call=datetime(2026, 10, 1, 10, 0), calls_today=0, next_future_due=None,
		tasks_due_now=0, due_task_title=None, last_contact=REPLY_AT, in_sequence=False,
		reply_snippet=None,
	)
	base.update(kw)
	return SimpleNamespace(**base)


class UnansweredReplyTests(unittest.TestCase):
	def test_fresh_reply_needs_a_person(self):
		self.assertEqual(ds.unanswered_reply(REPLY_AT, "Yes I am", datetime(2026, 11, 20), TODAY), "Yes I am")

	def test_answered_by_text_call_or_resolved_card(self):
		self.assertIsNone(ds.unanswered_reply(REPLY_AT, "Yes", datetime(2026, 11, 24, 11, 0), TODAY))
		self.assertIsNone(ds.unanswered_reply(REPLY_AT, "Yes", REPLY_AT, TODAY))

	def test_opt_out_and_old_replies_skip(self):
		for word in ("STOP", "Stop.", " stop all ", "Unsubscribe"):
			self.assertIsNone(ds.unanswered_reply(REPLY_AT, word, None, TODAY), word)
		self.assertIsNone(ds.unanswered_reply(datetime(2026, 10, 20, 9), "Yes", None, TODAY))
		self.assertEqual(ds.unanswered_reply(datetime(2026, 10, 26, 9), "Yes", None, TODAY), "Yes")
		self.assertIsNone(ds.unanswered_reply(None, None, None, TODAY))

	def test_acks_and_reactions_skip(self):
		for text in ("Ok", "Thanks!", "Thank you.", "Sounds good, thanks.", "ok, thanks",
		             "\u200a\u200b\U0001f44d\u200b to \u201c\u200aHi Kim, did you have",
		             'Liked \u201cHi Kim\u201d', "Loved \u201cthanks\u201d"):
			self.assertIsNone(ds.unanswered_reply(REPLY_AT, text, None, TODAY), text)
		for text in ("what's your offer", "Yes", "It's Riley", "Ok, send it over", "Not yet. Call me"):
			self.assertIsNotNone(ds.unanswered_reply(REPLY_AT, text, None, TODAY), text)

	def test_snippet(self):
		self.assertEqual(ds.reply_snippet(""), "(picture)")
		self.assertEqual(ds.reply_snippet("a\n  b"), "a b")
		long = ds.reply_snippet("x" * 200)
		self.assertEqual(len(long), 60)
		self.assertTrue(long.endswith("\u2026"))


class ClassifyReplyTests(unittest.TestCase):
	def test_reply_beats_a_booked_future_task(self):
		phase, need, due, reason = ds._classify(
			_row(reply_snippet="Yes I am", next_future_due=datetime(2026, 12, 16, 9)), TODAY
		)
		self.assertEqual((phase, need, due), ("task", 1, True))
		self.assertTrue(reason.startswith("Replied by text: \u201cYes I am\u201d"), reason)

	def test_reply_beats_sequence_and_closer(self):
		for status, kw in (("Future Follow Up", {"in_sequence": True}), ("Make Offer", {})):
			phase, _, due, reason = ds._classify(_row(status, reply_snippet="ok", **kw), TODAY)
			self.assertEqual((phase, due), ("task", True), status)
			self.assertIn("Replied by text", reason)

	def test_dispo_lead_keeps_its_rules(self):
		phase, _, due, _ = ds._classify(_row("Buyer Assigned", reply_snippet="ok"), TODAY)
		self.assertEqual((phase, due), ("dispo", False))

	def test_no_reply_unchanged(self):
		phase, _, due, _ = ds._classify(_row(next_future_due=datetime(2026, 12, 16, 9)), TODAY)
		self.assertEqual((phase, due), ("scheduled", False))


class ReplyCardUpdateTests(unittest.TestCase):
	R = "Replied by text: \u201cYes\u201d \u00b7 last contact today"

	def test_open_card_is_relabelled_once(self):
		cards = [{"name": "C1", "lead": "L1", "state": "To Call", "reason": "task: Text Jeff"}]
		self.assertEqual(ds.reply_card_updates(cards, {"L1": self.R}), [("C1", False, self.R)])
		cards[0]["reason"] = self.R
		self.assertEqual(ds.reply_card_updates(cards, {"L1": self.R}), [])

	def test_resolved_card_reopens_latest(self):
		cards = [
			{"name": "C1", "lead": "L1", "state": "Done", "resolved_at": datetime(2026, 11, 24, 9)},
			{"name": "C2", "lead": "L1", "state": "Skipped", "resolved_at": datetime(2026, 11, 24, 10)},
		]
		self.assertEqual(ds.reply_card_updates(cards, {"L1": self.R}), [("C2", True, self.R)])

	def test_lead_without_a_card_is_left_to_the_normal_insert(self):
		self.assertEqual(ds.reply_card_updates([], {"L1": self.R}), [])


class SortTests(unittest.TestCase):
	def test_replies_first(self):
		rows = [_row(name="A", lead_name="A", status="Make Offer"), _row(name="B", lead_name="B", reply_snippet="Yes")]
		orig = ds._fetch_chase_rows
		ds._fetch_chase_rows = lambda today: rows
		try:
			due = ds.build_setter_queue(TODAY)["due"]
		finally:
			ds._fetch_chase_rows = orig
		self.assertEqual([r.name for r in due], ["B", "A"])


if __name__ == "__main__":
	unittest.main()

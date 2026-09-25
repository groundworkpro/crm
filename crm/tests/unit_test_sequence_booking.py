"""A rep's booked follow-up after today holds the lead's sequences until then."""

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from crm.tests.frappe_shim import install

shim = install()

from crm.api import sequence_booking as sb  # noqa: E402
from crm.tests.frappe_shim import _AttrDict  # noqa: E402

TODAY = datetime(2026, 9, 25, 10, 0)

def _task(**kw):
	base = dict(
		name="T1",
		reference_doctype="CRM Lead",
		reference_docname="LEAD-1",
		status="Todo",
		owner="german.haikazounian@groundworkpro.com",
		due_date=datetime(2026, 10, 11, 9, 0),
	)
	base.update(kw)
	d = _AttrDict(base)
	d["has_value_changed"] = kw.get("has_value_changed", lambda f: True)
	return d

class PureTests(unittest.TestCase):
	def test_engine_owners_are_not_manual(self):
		for owner in ("Administrator", "Guest", "", None):
			self.assertFalse(sb.is_manual(owner), owner)
		self.assertTrue(sb.is_manual("dennis.szafran@groundworkpro.com"))

	def test_after_today_is_calendar_day(self):
		self.assertFalse(sb.is_after_today(datetime(2026, 9, 25, 23, 59), TODAY))
		self.assertTrue(sb.is_after_today(datetime(2026, 9, 26, 0, 1), TODAY))
		self.assertTrue(sb.is_after_today(date(2026, 9, 26), TODAY))
		self.assertFalse(sb.is_after_today(None, TODAY))

	def test_booking_needs_open_manual_future_lead_task(self):
		self.assertTrue(sb.is_booking(_task(), TODAY))
		self.assertFalse(sb.is_booking(_task(owner="Administrator"), TODAY))
		self.assertFalse(sb.is_booking(_task(status="Done"), TODAY))
		self.assertFalse(sb.is_booking(_task(status="Canceled"), TODAY))
		self.assertFalse(sb.is_booking(_task(due_date=datetime(2026, 9, 25, 17, 0)), TODAY))
		self.assertFalse(sb.is_booking(_task(due_date=datetime(2026, 9, 1, 9, 0)), TODAY))
		self.assertFalse(sb.is_booking(_task(reference_doctype="CRM Deal"), TODAY))

class HookTests(unittest.TestCase):
	def setUp(self):
		self.calls = []
		self._orig = (sb.now_datetime, sb.hold_for_booking)
		sb.now_datetime = lambda: TODAY
		sb.hold_for_booking = lambda lead, due, reason: self.calls.append((lead, reason, due))
		shim.utils.format_datetime = lambda d, fmt=None: "11 Oct"

	def tearDown(self):
		sb.now_datetime, sb.hold_for_booking = self._orig

	def test_booking_pauses(self):
		sb.on_task_change(_task(), "after_insert")
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(self.calls[0][0], "LEAD-1")
		self.assertIn("11 Oct", self.calls[0][1])

	def test_today_task_does_not_pause(self):
		sb.on_task_change(_task(due_date=datetime(2026, 9, 25, 16, 0)), "after_insert")
		self.assertEqual(self.calls, [])

	def test_sequence_task_does_not_pause(self):
		sb.on_task_change(_task(owner="Administrator"), "after_insert")
		self.assertEqual(self.calls, [])

	def test_unrelated_edit_does_not_repause(self):
		sb.on_task_change(_task(has_value_changed=lambda f: False), "on_update")
		self.assertEqual(self.calls, [])

	def test_moving_due_date_later_pauses(self):
		sb.on_task_change(_task(has_value_changed=lambda f: f == "due_date"), "on_update")
		self.assertEqual(len(self.calls), 1)

class HoldTests(unittest.TestCase):
	def test_hold_target_is_morning_after(self):
		self.assertEqual(sb.hold_target(datetime(2026, 10, 11, 9, 0)), datetime(2026, 10, 12, 8, 0))
		self.assertEqual(sb.hold_target(date(2026, 10, 11)), datetime(2026, 10, 12, 8, 0))

	def test_needs_hold_only_moves_forward(self):
		t = datetime(2026, 10, 12, 8, 0)
		self.assertTrue(sb.needs_hold(datetime(2026, 10, 1, 8, 0), t))
		self.assertTrue(sb.needs_hold(None, t))
		self.assertFalse(sb.needs_hold(t, t))
		self.assertFalse(sb.needs_hold(datetime(2026, 11, 1, 8, 0), t))

	def test_hold_moves_early_enrollments_and_cancels_engine_tasks(self):
		writes = []
		queries = []

		def get_all(doctype, filters=None, **k):
			queries.append((doctype, dict(filters or {})))
			if doctype == "CRM Sequence Enrollment":
				return [
					_AttrDict(name="ENR-EARLY", next_run=datetime(2026, 10, 1, 8, 0)),
					_AttrDict(name="ENR-LATE", next_run=datetime(2026, 11, 1, 8, 0)),
				]
			return ["S1", "S2"]

		shim.get_all = get_all
		orig_gd = shim.utils.get_datetime
		shim.utils.get_datetime = lambda d: d
		orig_now = sb.now_datetime
		sb.now_datetime = lambda: TODAY
		shim.db.set_value = lambda dt, name, field, val=None, **k: writes.append((dt, name, field, val))
		try:
			held, canceled = sb.hold_for_booking("LEAD-1", datetime(2026, 10, 11, 9, 0), "follow-up booked")
		finally:
			sb.now_datetime = orig_now
			shim.utils.get_datetime = orig_gd
		self.assertEqual(held, ["ENR-EARLY"])
		self.assertEqual(canceled, ["S1", "S2"])
		enr_write = writes[0]
		self.assertEqual(enr_write[:2], ("CRM Sequence Enrollment", "ENR-EARLY"))
		self.assertEqual(enr_write[2]["next_run"], datetime(2026, 10, 12, 8, 0))
		self.assertIn("held until", enr_write[2]["last_log"])
		self.assertNotIn("status", enr_write[2])  # held, never paused
		self.assertEqual(queries[1][1]["owner"], "Administrator")
		self.assertEqual(writes[1:], [("CRM Task", "S1", "status", "Canceled"), ("CRM Task", "S2", "status", "Canceled")])

	def test_step_guard(self):
		orig = (sb.future_booking, sb.hold_for_booking)
		calls = []
		sb.hold_for_booking = lambda lead, due, reason: calls.append((lead, due))
		shim.db.commit = lambda: None
		shim.utils.format_datetime = lambda d, fmt=None: "11 Oct"
		try:
			sb.future_booking = lambda lead, now=None: None
			self.assertTrue(sb.check_before_step(SimpleNamespace(lead="LEAD-1")))
			sb.future_booking = lambda lead, now=None: datetime(2026, 10, 11, 9, 0)
			self.assertFalse(sb.check_before_step(SimpleNamespace(lead="LEAD-1")))
			self.assertEqual(calls, [("LEAD-1", datetime(2026, 10, 11, 9, 0))])
		finally:
			sb.future_booking, sb.hold_for_booking = orig

if __name__ == "__main__":
	unittest.main()

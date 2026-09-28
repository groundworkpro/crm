"""A rep's booked follow-up ends New Lead 10-Day and hands off to Long-Term."""

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from crm.tests.frappe_shim import install

shim = install()

from crm.api import sequence_booking as sb  # noqa: E402
from crm.tests.frappe_shim import _AttrDict  # noqa: E402

TODAY = datetime(2026, 9, 25, 10, 0)
CHAIN = {"New Lead 10-Day": "Long-Term Follow-Up"}
GOVERNED = {"Long-Term Follow-Up"}

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

class _Patched(unittest.TestCase):
	"""Swap module attributes for the test and put them back afterwards —
	the shim is shared by every test file."""

	def patch(self, obj, name, value):
		orig = getattr(obj, name)
		setattr(obj, name, value)
		self.addCleanup(setattr, obj, name, orig)

	def setUp(self):
		self.patch(sb, "self_governed", lambda seq: seq in GOVERNED)
		self.patch(sb, "next_sequence", lambda seq: CHAIN.get(seq))
		self.patch(sb, "now_datetime", lambda: TODAY)
		self.patch(shim.utils, "format_datetime", lambda d, fmt=None: "11 Oct")

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

	def test_rep_task_rule_detected(self):
		self.assertTrue(sb.has_rep_task_rule([{"condition": ""}, {"condition": " not has_open_rep_task "}]))
		self.assertFalse(sb.has_rep_task_rule([{"condition": None}, {"condition": "not recently_contacted"}]))
		self.assertFalse(sb.has_rep_task_rule(None))

	def test_parse_statuses(self):
		self.assertEqual(sb.parse_statuses(" New \n\nCalled No Answer\n"), ["New", "Called No Answer"])
		self.assertEqual(sb.parse_statuses(None), [])

class HookTests(_Patched):
	def setUp(self):
		super().setUp()
		self.calls = []
		self.patch(sb, "apply_booking", lambda lead, reason: self.calls.append((lead, reason)))

	def test_booking_applies(self):
		sb.on_task_change(_task(), "after_insert")
		self.assertEqual(self.calls, [("LEAD-1", "follow-up booked by german.haikazounian@groundworkpro.com for 11 Oct")])

	def test_today_task_does_nothing(self):
		sb.on_task_change(_task(due_date=datetime(2026, 9, 25, 16, 0)), "after_insert")
		self.assertEqual(self.calls, [])

	def test_sequence_task_does_nothing(self):
		sb.on_task_change(_task(owner="Administrator"), "after_insert")
		self.assertEqual(self.calls, [])

	def test_unrelated_edit_does_not_reapply(self):
		sb.on_task_change(_task(has_value_changed=lambda f: False), "on_update")
		self.assertEqual(self.calls, [])

	def test_moving_due_date_later_applies(self):
		sb.on_task_change(_task(has_value_changed=lambda f: f == "due_date"), "on_update")
		self.assertEqual(len(self.calls), 1)

class HandOffTests(_Patched):
	def setUp(self):
		super().setUp()
		self.writes = []
		self.enrolled = []
		self.patch(shim, "get_all", self._get_all)
		self.patch(shim.db, "set_value", lambda dt, name, field, val=None, **k: self.writes.append((dt, name, field, val)))
		self.patch(sb, "enroll_next", lambda lead, nxt, frm: self.enrolled.append((lead, nxt, frm)) or True)

	def _get_all(self, doctype, filters=None, **k):
		if doctype == "CRM Sequence Enrollment":
			return [
				_AttrDict(name="ENR-10", sequence="New Lead 10-Day"),
				_AttrDict(name="ENR-LT", sequence="Long-Term Follow-Up"),
				_AttrDict(name="ENR-UC", sequence="Under Contract Seller Check-In"),
			]
		self.task_filters = dict(filters or {})
		return ["S1", "S2"]

	def test_ten_day_goes_to_long_term_others_untouched(self):
		handed, canceled = sb.apply_booking("LEAD-1", "follow-up booked for 11 Oct")
		self.assertEqual(handed, [("ENR-10", "Long-Term Follow-Up")])
		self.assertEqual(self.enrolled, [("LEAD-1", "Long-Term Follow-Up", "New Lead 10-Day")])
		stop = self.writes[0]
		self.assertEqual(stop[:2], ("CRM Sequence Enrollment", "ENR-10"))
		self.assertEqual(stop[2]["status"], "Stopped")
		self.assertIn("handed to Long-Term Follow-Up", stop[2]["last_log"])
		# Long-Term (self-governed) and Under Contract (no chain) are not written
		self.assertEqual([w[1] for w in self.writes if w[0] == "CRM Sequence Enrollment"], ["ENR-10"])
		self.assertEqual(canceled, ["S1", "S2"])
		self.assertEqual(self.task_filters["owner"], "Administrator")
		self.assertEqual(
			[w for w in self.writes if w[0] == "CRM Task"],
			[("CRM Task", "S1", "status", "Canceled"), ("CRM Task", "S2", "status", "Canceled")],
		)

class EnrollNextTests(_Patched):
	def setUp(self):
		super().setUp()
		self.inserted = []
		self.lead = _AttrDict(status="Called No Answer", converted=0)
		self.exists = False

		def get_value(dt, name, field=None, *a, **k):
			if dt == "CRM Lead":
				return self.lead
			return {"enabled": 1, "lead_statuses": "New\nCalled No Answer\nFuture Follow Up"}[field]

		self.patch(shim.db, "get_value", get_value)
		self.patch(shim.db, "exists", lambda dt, f=None: self.exists)
		doc = SimpleNamespace(insert=lambda **k: self.inserted.append(1))
		self.patch(shim, "get_doc", lambda d: self.inserted.append(d) or doc)

	def test_enrolls(self):
		self.assertTrue(sb.enroll_next("LEAD-1", "Long-Term Follow-Up", "New Lead 10-Day"))
		self.assertEqual(self.inserted[0]["sequence"], "Long-Term Follow-Up")
		self.assertEqual(self.inserted[0]["status"], "Active")

	def test_never_twice(self):
		self.exists = True
		self.assertFalse(sb.enroll_next("LEAD-1", "Long-Term Follow-Up", "New Lead 10-Day"))

	def test_status_gate_and_converted(self):
		self.lead = _AttrDict(status="Underwriting", converted=0)
		self.assertFalse(sb.enroll_next("LEAD-1", "Long-Term Follow-Up", "New Lead 10-Day"))
		self.lead = _AttrDict(status="New", converted=1)
		self.assertFalse(sb.enroll_next("LEAD-1", "Long-Term Follow-Up", "New Lead 10-Day"))
		self.assertEqual(self.inserted, [])

class StepGuardTests(_Patched):
	def setUp(self):
		super().setUp()
		self.calls = []
		self.patch(sb, "apply_booking", lambda lead, reason: self.calls.append(lead))
		self.patch(shim.db, "commit", lambda: None)

	def test_guard(self):
		self.patch(sb, "future_booking", lambda lead, now=None: None)
		self.assertTrue(sb.check_before_step(SimpleNamespace(lead="L", sequence="New Lead 10-Day")))
		self.patch(sb, "future_booking", lambda lead, now=None: datetime(2026, 10, 11, 9, 0))
		self.assertTrue(sb.check_before_step(SimpleNamespace(lead="L", sequence="Long-Term Follow-Up")))
		self.assertTrue(sb.check_before_step(SimpleNamespace(lead="L", sequence="Under Contract Seller Check-In")))
		self.assertEqual(self.calls, [])
		self.assertFalse(sb.check_before_step(SimpleNamespace(lead="L", sequence="New Lead 10-Day")))
		self.assertEqual(self.calls, ["L"])

if __name__ == "__main__":
	unittest.main()

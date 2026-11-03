"""Lead page Sequences card: progress, pause reason, actions, and set_state."""

import unittest
from types import SimpleNamespace

from crm.tests.frappe_shim import install

shim = install()

from crm.api import sequence_booking as sb  # noqa: E402
from crm.api import sequence_lead as sl  # noqa: E402


def _step(t, subject, message=""):
	return {"step_type": t, "subject": subject, "message": message}


class PureTests(unittest.TestCase):
	def test_branching_rows_are_one_step(self):
		steps = [_step("Text", "a"), _step("Call", "b"), _step("Call", "b"), _step("Text", "c")]
		self.assertEqual(sl.step_groups(steps), [1, 2, 2, 3])
		self.assertEqual(sl.step_groups([]), [])

	def test_progress(self):
		groups = [1, 2, 2, 3]
		self.assertEqual(sl.progress(0, groups), (0, 3))
		self.assertEqual(sl.progress(None, groups), (0, 3))
		self.assertEqual(sl.progress(3, groups), (2, 3))
		self.assertEqual(sl.progress(9, groups), (3, 3))
		self.assertEqual(sl.progress(2, []), (0, 0))

	def test_reason(self):
		self.assertEqual(
			sl.reason("2026-09-28 15:45:01.605335 auto-paused: Text reply received"), "Text reply received"
		)
		self.assertEqual(sl.reason("2026-11-03 10:00:00 resumed: by dennis"), "by dennis")
		self.assertEqual(sl.reason(""), "")
		self.assertEqual(sl.reason(None), "")

	def test_actions(self):
		self.assertEqual(sl.actions_for("Paused", "Long-Term Follow-Up", False), ["move_next", "resume", "stop"])
		self.assertEqual(sl.actions_for("Paused", "Long-Term Follow-Up", True), ["resume", "stop"])
		self.assertEqual(sl.actions_for("Active", None, False), ["pause", "stop"])
		self.assertEqual(sl.actions_for("Stopped", "Long-Term Follow-Up", False), [])
		self.assertEqual(sl.actions_for("Completed", None, False), [])

	def test_should_cancel_tasks(self):
		self.assertTrue(sl.should_cancel_tasks("stop", [], None))
		self.assertFalse(sl.should_cancel_tasks("stop", ["Other"], None))
		self.assertTrue(sl.should_cancel_tasks("move_next", ["LT"], "LT"))
		self.assertFalse(sl.should_cancel_tasks("move_next", ["LT", "Other"], "LT"))


class SetStateTests(unittest.TestCase):
	def patch(self, obj, name, value):
		orig = getattr(obj, name)
		setattr(obj, name, value)
		self.addCleanup(setattr, obj, name, orig)

	def setUp(self):
		self.enr = SimpleNamespace(name="ENR-1", lead="LEAD-1", sequence="New Lead 10-Day", status="Paused")
		self.patch(shim, "get_doc", lambda *a, **k: self.enr)
		self.patch(sb, "next_sequence", lambda seq: {"New Lead 10-Day": "Long-Term Follow-Up"}.get(seq))
		self.writes, self.enrolled, self.canceled = [], [], []
		self.patch(shim.db, "set_value", lambda dt, name, vals, **k: self.writes.append((name, vals)))
		self.patch(shim.db, "exists", lambda *a, **k: False)
		self.patch(shim, "get_all", lambda *a, **k: [])
		self.patch(sb, "enroll_next", lambda lead, nxt, frm, why="": self.enrolled.append((lead, nxt, why)) or True)
		self.patch(sb, "cancel_engine_tasks", lambda lead: self.canceled.append(lead) or [])
		self.patch(sl, "for_lead", lambda lead: ["refreshed", lead])

	def test_move_next_stops_and_enrolls(self):
		out = sl.set_state("ENR-1", "move_next")
		self.assertEqual(out, ["refreshed", "LEAD-1"])
		self.assertEqual(self.enrolled[0][:2], ("LEAD-1", "Long-Term Follow-Up"))
		self.assertEqual(self.writes[0][1]["status"], "Stopped")
		self.assertIn("moved to Long-Term Follow-Up", self.writes[0][1]["last_log"])
		self.assertEqual(self.canceled, ["LEAD-1"])

	def test_move_next_refused_when_gates_fail(self):
		self.patch(sb, "enroll_next", lambda *a, **k: False)
		self.patch(shim.db, "get_value", lambda *a, **k: "Follow Up")
		with self.assertRaises(shim.ValidationError):
			sl.set_state("ENR-1", "move_next")
		self.assertEqual(self.writes, [])

	def test_resume(self):
		sl.set_state("ENR-1", "resume")
		self.assertEqual(self.writes[0][1]["status"], "Active")
		self.assertEqual(self.canceled, [])

	def test_wrong_state_refused(self):
		self.enr.status = "Stopped"
		with self.assertRaises(shim.ValidationError):
			sl.set_state("ENR-1", "resume")
		with self.assertRaises(shim.ValidationError):
			sl.set_state("ENR-1", "bogus")


if __name__ == "__main__":
	unittest.main()

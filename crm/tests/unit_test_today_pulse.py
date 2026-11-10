"""The shared Acq pulse must not silently report only one teammate's work."""

import unittest
from datetime import datetime
from unittest.mock import patch

from crm.tests.frappe_shim import install

shim = install()

from crm.api import today_pulse as pulse  # noqa: E402


class PulseRosterTests(unittest.TestCase):
	def setUp(self):
		self.config = patch.dict(shim.conf, {}, clear=True)
		self.config.start()
		self.addCleanup(self.config.stop)
		self.now = datetime(2026, 9, 8, 16, 0)
		self.since = datetime(2026, 9, 8, 15, 30)
		self.ger = "german.haikazounian@groundworkpro.com"
		# Synthetic logins: coverage must not depend on a hardcoded current roster.
		self.exe = "exe@example.test"
		self.dennis = "dennis@example.test"

	def test_pulse_post_is_off_unless_enabled(self):
		self.assertFalse(pulse.pulse_enabled())
		with patch.object(pulse, "send_to_channel") as send, patch.object(
			pulse, "is_business_day", return_value=True
		), patch.object(pulse, "now_datetime", return_value=self.now):
			self.assertIsNone(pulse.send_today_pulse())
			send.assert_not_called()
		shim.conf["today_pulse_enabled"] = 1
		self.assertTrue(pulse.pulse_enabled())

	def delta(self, rows, stamp="resolved_at"):
		with patch.object(shim, "get_all", return_value=rows):
			return pulse._delta_cards("2026-09-08", self.since, self.now, stamp, pulse._pulse_users())

	def test_default_counts_every_teammate(self):
		for actor in (self.ger, self.exe, self.dennis, "new-rep@example.test", None):
			with self.subTest(actor=actor):
				result = self.delta([shim._dict(state="Done", resolved_by=actor)])
				self.assertEqual(result["total"], 1)

	def test_ger_off_still_reports_exe_and_dennis(self):
		rows = [
			shim._dict(state="Done", resolved_by=self.exe),
			shim._dict(state="Skipped", resolved_by=self.dennis),
		]
		self.assertEqual(self.delta(rows), {"done": 1, "skipped": 1, "total": 2})

	def test_legacy_done_stamps_count_other_reps(self):
		self.assertEqual(self.delta([shim._dict(state="Done", done_by=self.exe)], "done_at")["total"], 1)

	def test_unset_or_empty_config_is_team_wide(self):
		for value in (None, [], "", "  ,  "):
			with self.subTest(value=value):
				shim.conf["today_pulse_users"] = value
				self.assertEqual(pulse._pulse_users(), ())

	def test_explicit_roster_override_is_preserved(self):
		for value in ([self.exe], f" {self.exe}, "):
			with self.subTest(value=value):
				shim.conf["today_pulse_users"] = value
				self.assertEqual(pulse._pulse_users(), (self.exe,))
				rows = [shim._dict(state="Done", resolved_by=u) for u in (self.exe, self.dennis)]
				self.assertEqual(self.delta(rows)["total"], 1)

	def test_calls_count_other_reps_and_number_fallback(self):
		rows = [
			shim._dict(caller=self.exe, receiver=None, type="Outgoing", duration=120, **{"from": "+15551111111"}),
			shim._dict(caller=None, receiver=self.dennis, type="Incoming", duration=180, to="+15552222222"),
			shim._dict(caller=None, receiver=None, type="Outgoing", duration=60, **{"from": "+15553333333"}),
		]
		with patch.object(shim, "get_all", return_value=rows), patch.object(
			pulse.telephony, "line_owners", return_value={"5553333333": "new-rep@example.test"}
		) as lines:
			result = pulse._calls("2026-09-08", self.since, self.now, pulse._pulse_users())
		self.assertEqual(result["calls"], 3)
		self.assertEqual(result["talk_seconds"], 360)
		lines.assert_called_once_with(users=())

	def test_message_reports_progress_when_ger_is_off(self):
		cards = [
			shim._dict(state="Done", resolved_by=self.exe, resolved_at=self.since),
			shim._dict(state="Skipped", resolved_by=self.dennis, resolved_at=self.since),
		]
		def rows(doctype, **kwargs):
			return cards if doctype == pulse.DOCTYPE else []

		with patch.object(shim, "get_all", side_effect=rows), patch.object(
			pulse, "_available", return_value=True
		), patch.object(pulse, "_resolved_stamp_field", return_value="resolved_at"), patch.object(
			pulse, "_number_users", return_value={}
		):
			data = pulse.build_pulse(now=self.now, since=self.since)
			text = pulse.render_markdown(data)
		self.assertEqual(data["board"]["resolved"], data["delta"]["total"])
		self.assertIn("**+1** done · +1 skipped", text)
		self.assertNotIn("Nothing resolved", text)


if __name__ == "__main__":
	unittest.main()

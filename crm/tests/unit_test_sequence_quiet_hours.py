"""Drainer quiet hours + business days: scheduled steps never fire at night,
and never on a weekend or a holiday."""

import unittest
from datetime import datetime

from crm.tests.frappe_shim import install

shim = install()

from crm.api import sequence_drain as sd  # noqa: E402


def step(step_type="Text", wait_value=1, wait_unit="Days"):
	return {"step_type": step_type, "wait_value": wait_value, "wait_unit": wait_unit}


class QuietHoursTests(unittest.TestCase):
	def test_daytime_runs_now(self):
		self.assertIsNone(sd.quiet_hold_until(datetime(2026, 9, 9, 14, 30), step()))
		self.assertIsNone(sd.quiet_hold_until(datetime(2026, 9, 9, 8, 0), step()))
		self.assertIsNone(sd.quiet_hold_until(datetime(2026, 9, 9, 19, 59), step()))

	def test_late_night_waits_for_tomorrow_morning(self):
		self.assertEqual(
			sd.quiet_hold_until(datetime(2026, 9, 9, 23, 5), step()),
			datetime(2026, 9, 10, 8, 0),
		)
		self.assertEqual(
			sd.quiet_hold_until(datetime(2026, 9, 9, 20, 0), step("Call")),
			datetime(2026, 9, 10, 8, 0),
		)

	def test_early_morning_waits_for_today(self):
		self.assertEqual(
			sd.quiet_hold_until(datetime(2026, 9, 9, 6, 15), step()),
			datetime(2026, 9, 9, 8, 0),
		)

	def test_early_morning_rep_tasks_run_now(self):
		# Call/Task steps only make a rep task; the midnight/5am board pass must
		# be able to fire them so the cards are there before 8am.
		early = datetime(2026, 9, 9, 5, 0)
		self.assertIsNone(sd.quiet_hold_until(early, step("Call", 1, "Weeks")))
		self.assertIsNone(sd.quiet_hold_until(early, step("Task")))
		self.assertIsNone(sd.quiet_hold_until(datetime(2026, 9, 9, 0, 5), step("Call")))
		# but a text to the seller still waits, and an evening rep task too
		self.assertEqual(sd.quiet_hold_until(early, step("Text")), datetime(2026, 9, 9, 8, 0))
		self.assertEqual(
			sd.quiet_hold_until(datetime(2026, 9, 9, 22, 0), step("Task")),
			datetime(2026, 9, 10, 8, 0),
		)

	def test_instant_burst_is_never_held(self):
		night = datetime(2026, 9, 9, 23, 5)
		self.assertIsNone(sd.quiet_hold_until(night, step(wait_value=0)))
		self.assertIsNone(sd.quiet_hold_until(night, step(wait_value=5, wait_unit="Seconds")))
		self.assertIsNone(sd.quiet_hold_until(night, step(wait_value=3, wait_unit="Minutes")))
		self.assertEqual(
			sd.quiet_hold_until(night, step(wait_value=1, wait_unit="Hours")),
			datetime(2026, 9, 10, 8, 0),
		)

	def test_non_seller_steps_are_never_held(self):
		night = datetime(2026, 9, 9, 23, 5)
		self.assertIsNone(sd.quiet_hold_until(night, step("Pushover")))
		self.assertIsNone(sd.quiet_hold_until(night, step("Email")))
		self.assertIsNone(sd.quiet_hold_until(night, None))


class CalendarDueTests(unittest.TestCase):
	def test_one_day_lands_next_morning_not_plus_24h(self):
		self.assertEqual(
			sd.calendar_due(datetime(2026, 9, 9, 11, 39), step()),
			datetime(2026, 9, 10, 8, 0),
		)
		self.assertEqual(
			sd.calendar_due(datetime(2026, 9, 9, 23, 5), step()),
			datetime(2026, 9, 10, 8, 0),
		)

	def test_zero_and_non_day_waits_are_left_alone(self):
		now = datetime(2026, 9, 9, 11, 39)
		self.assertIsNone(sd.calendar_due(now, step(wait_value=0)))
		self.assertIsNone(sd.calendar_due(now, step(wait_value=30, wait_unit="Minutes")))
		self.assertIsNone(sd.calendar_due(now, None))

	def test_weeks_and_multi_day(self):
		now = datetime(2026, 9, 9, 14, 0)
		self.assertEqual(
			sd.calendar_due(now, step(wait_value=2)),
			datetime(2026, 9, 11, 8, 0),
		)
		self.assertEqual(
			sd.calendar_due(now, step(wait_value=1, wait_unit="Weeks")),
			datetime(2026, 9, 16, 8, 0),
		)


class BusinessDayTests(unittest.TestCase):
	# Sep 2026: 11th Fri, 12th Sat, 13th Sun, 14th Mon. Sep 7 is Labor Day.
	def setUp(self):
		shim.conf.clear()

	def test_weekend_rolls_to_monday_morning(self):
		self.assertEqual(
			sd.business_hold_until(datetime(2026, 9, 12, 8, 0), step()),
			datetime(2026, 9, 14, 8, 0),
		)
		self.assertEqual(
			sd.business_hold_until(datetime(2026, 9, 13, 8, 0), step("Task")),
			datetime(2026, 9, 14, 8, 0),
		)

	def test_holiday_rolls_too(self):
		# Labor Day, Mon Sep 7 2026 -> Tue
		self.assertEqual(
			sd.business_hold_until(datetime(2026, 9, 7, 8, 0), step()),
			datetime(2026, 9, 8, 8, 0),
		)

	def test_columbus_day_does_not_delay_followup(self):
		monday = datetime(2026, 10, 12, 8, 0)
		self.assertEqual(
			sd.hold_until(datetime(2026, 10, 10, 8, 0), step()), monday
		)
		self.assertIsNone(sd.business_hold_until(monday, step("Task")))
		self.assertIsNone(sd.hold_until(monday, step()))

	def test_business_day_is_left_alone(self):
		self.assertIsNone(sd.business_hold_until(datetime(2026, 9, 11, 8, 0), step()))
		self.assertIsNone(sd.business_hold_until(datetime(2026, 9, 14, 15, 30), step()))

	def test_idempotent(self):
		"""drain_due re-aligns every active enrollment once a minute — a second
		pass over an already-rolled step must not push it another day out."""
		rolled = sd.business_hold_until(datetime(2026, 9, 12, 8, 0), step())
		self.assertIsNone(sd.business_hold_until(rolled, step()))

	def test_intro_burst_and_other_types_exempt(self):
		sat = datetime(2026, 9, 12, 10, 0)
		self.assertIsNone(sd.business_hold_until(sat, step(wait_value=0)))
		self.assertIsNone(sd.business_hold_until(sat, step(wait_value=5, wait_unit="Minutes")))
		self.assertIsNone(sd.business_hold_until(sat, step("Email")))
		self.assertIsNone(sd.business_hold_until(sat, None))
		self.assertIsNone(sd.business_hold_until(None, step()))

	def test_site_config_holiday_counts(self):
		shim.conf["crm_holidays"] = ["2026-09-11"]
		self.assertEqual(
			sd.business_hold_until(datetime(2026, 9, 11, 8, 0), step()),
			datetime(2026, 9, 14, 8, 0),
		)


class HoldUntilTests(unittest.TestCase):
	def setUp(self):
		shim.conf.clear()

	def test_friday_night_waits_for_monday_not_saturday(self):
		"""The bug Exe reported: day 4 landed Saturday, day 5 Sunday, so Monday
		opened with three overdue to-dos on one lead."""
		self.assertEqual(
			sd.hold_until(datetime(2026, 9, 11, 22, 0), step()),
			datetime(2026, 9, 14, 8, 0),
		)

	def test_weekend_daytime_waits_for_monday(self):
		self.assertEqual(
			sd.hold_until(datetime(2026, 9, 12, 11, 0), step()),
			datetime(2026, 9, 14, 8, 0),
		)

	def test_weekday_daytime_runs_now(self):
		self.assertIsNone(sd.hold_until(datetime(2026, 9, 14, 11, 0), step()))

	def test_weekday_night_still_waits_for_the_morning(self):
		self.assertEqual(
			sd.hold_until(datetime(2026, 9, 14, 23, 0), step()),
			datetime(2026, 9, 15, 8, 0),
		)

	def test_intro_burst_fires_even_on_a_saturday_night(self):
		self.assertIsNone(sd.hold_until(datetime(2026, 9, 12, 23, 0), step(wait_value=0)))


class SellerLocalTextTests(unittest.TestCase):
	# Times are site-local (Central). Mon Sep 14 2026 is a business day (CDT).
	def setUp(self):
		shim.conf.clear()

	def test_state_lookup(self):
		self.assertEqual(sd.lead_timezones("FL"), ("America/New_York",))
		self.assertEqual(sd.lead_timezones(" new york "), ("America/New_York",))
		self.assertEqual(sd.lead_timezones("ca"), ("America/Los_Angeles",))
		self.assertEqual(sd.lead_timezones(""), sd.UNKNOWN_STATE_TZS)
		self.assertEqual(sd.lead_timezones(None), sd.UNKNOWN_STATE_TZS)
		self.assertEqual(sd.lead_timezones("Ontario"), sd.UNKNOWN_STATE_TZS)

	def test_eastern_seller_at_8_central_is_fine(self):
		# 8:00 CT = 9:00 ET
		self.assertIsNone(sd.seller_hold_until(datetime(2026, 9, 14, 8, 0), step(), ("America/New_York",)))

	def test_pacific_seller_waits_until_9_their_time(self):
		# 8:00 CT = 6:00 PT -> 9:00 PT = 11:00 CT
		self.assertEqual(
			sd.seller_hold_until(datetime(2026, 9, 14, 8, 0), step(), ("America/Los_Angeles",)),
			datetime(2026, 9, 14, 11, 0),
		)

	def test_central_seller_waits_until_9(self):
		self.assertEqual(
			sd.seller_hold_until(datetime(2026, 9, 14, 8, 0), step(), ("America/Chicago",)),
			datetime(2026, 9, 14, 9, 0),
		)

	def test_eastern_evening_cutoff(self):
		# 19:30 CT = 20:30 ET -> next morning 9:00 ET = 8:00 CT
		self.assertEqual(
			sd.seller_hold_until(datetime(2026, 9, 14, 19, 30), step(), ("America/New_York",)),
			datetime(2026, 9, 15, 8, 0),
		)

	def test_unknown_state_needs_both_coasts(self):
		self.assertEqual(
			sd.seller_hold_until(datetime(2026, 9, 14, 8, 0), step(), sd.UNKNOWN_STATE_TZS),
			datetime(2026, 9, 14, 11, 0),
		)
		# 18:30 CT = 19:30 ET ok, 16:30 PT ok
		self.assertIsNone(sd.seller_hold_until(datetime(2026, 9, 14, 18, 30), step(), sd.UNKNOWN_STATE_TZS))

	def test_friday_evening_pacific_goes_to_monday_late_morning(self):
		self.assertEqual(
			sd.seller_hold_until(datetime(2026, 9, 11, 21, 0), step(), ("America/Los_Angeles",)),
			datetime(2026, 9, 14, 11, 0),
		)

	def test_rep_tasks_and_intro_burst_ignore_seller_zone(self):
		pt = ("America/Los_Angeles",)
		self.assertIsNone(sd.seller_hold_until(datetime(2026, 9, 14, 8, 0), step("Task"), pt))
		self.assertIsNone(sd.seller_hold_until(datetime(2026, 9, 14, 5, 0), step("Call"), pt))
		self.assertIsNone(sd.seller_hold_until(datetime(2026, 9, 14, 23, 0), step(wait_value=0), pt))

	def test_idempotent(self):
		pt = ("America/Los_Angeles",)
		held = sd.seller_hold_until(datetime(2026, 9, 14, 8, 0), step(), pt)
		self.assertIsNone(sd.seller_hold_until(held, step(), pt))

	def test_no_zone_falls_back_to_site_rules(self):
		self.assertEqual(
			sd.seller_hold_until(datetime(2026, 9, 14, 23, 0), step(), None),
			datetime(2026, 9, 15, 8, 0),
		)


if __name__ == "__main__":
	unittest.main()

"""Business-day helpers: weekends, US federal holidays, site-config extras."""

import unittest
from datetime import date

from crm.tests.frappe_shim import install

shim = install()

from crm.api import daily_standup as ds  # noqa: E402


class HolidayTests(unittest.TestCase):
	def setUp(self):
		shim.conf.clear()

	def test_2026_federal_calendar(self):
		h = ds.us_federal_holidays(2026)
		self.assertEqual(
			sorted(h),
			[
				date(2026, 1, 1),
				date(2026, 1, 19),  # MLK
				date(2026, 2, 16),  # Presidents'
				date(2026, 5, 25),  # Memorial
				date(2026, 6, 19),
				date(2026, 7, 3),  # Jul 4 is a Saturday → observed Friday
				date(2026, 9, 7),  # Labor Day
				date(2026, 10, 12),  # Columbus
				date(2026, 11, 11),
				date(2026, 11, 26),  # Thanksgiving
				date(2026, 12, 25),
			],
		)

	def test_columbus_day_is_a_working_day(self):
		for year, day in [(2025, 13), (2026, 12), (2027, 11)]:
			with self.subTest(year=year):
				d = date(year, 10, day)
				self.assertIn(d, ds.us_federal_holidays(year))
				self.assertFalse(ds.is_holiday(d))
				self.assertTrue(ds.is_business_day(d))

	def test_columbus_day_counts_in_cadence_and_recap(self):
		self.assertEqual(ds.business_days_between(date(2026, 10, 9), date(2026, 10, 12)), 1)
		self.assertEqual(ds.previous_business_day(date(2026, 10, 13)), date(2026, 10, 12))

	def test_explicit_company_day_off_can_override_columbus_day(self):
		shim.conf["crm_holidays"] = ["2026-10-12"]
		self.assertFalse(ds.is_business_day(date(2026, 10, 12)))

	def test_sunday_holiday_observed_monday(self):
		# Christmas 2022 was a Sunday → observed Mon Dec 26
		self.assertIn(date(2022, 12, 26), ds.us_federal_holidays(2022))
		self.assertNotIn(date(2022, 12, 25), ds.us_federal_holidays(2022))

	def test_labor_day_is_not_a_business_day(self):
		self.assertFalse(ds.is_business_day(date(2026, 9, 7)))
		self.assertTrue(ds.is_business_day(date(2026, 9, 8)))
		self.assertFalse(ds.is_business_day(date(2026, 9, 6)))

	def test_previous_business_day_skips_holiday_and_weekend(self):
		# Tue Sep 8 → skips Labor Day Monday and the weekend → Fri Sep 4
		self.assertEqual(ds.previous_business_day(date(2026, 9, 8)), date(2026, 9, 4))

	def test_business_days_between_excludes_holiday(self):
		# Fri Sep 4 → Tue Sep 8: only Tuesday counts
		self.assertEqual(ds.business_days_between(date(2026, 9, 4), date(2026, 9, 8)), 1)

	def test_extra_holidays_from_config(self):
		shim.conf["crm_holidays"] = ["2026-11-27"]
		self.assertFalse(ds.is_business_day(date(2026, 11, 27)))
		shim.conf["crm_holidays"] = '["2026-11-27"]'
		self.assertFalse(ds.is_business_day(date(2026, 11, 27)))
		shim.conf["crm_holidays"] = "garbage"
		self.assertTrue(ds.is_business_day(date(2026, 11, 27)))


if __name__ == "__main__":
	unittest.main()

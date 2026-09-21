"""Recent-contact context used by CRM Sequence conditions."""

import unittest
from datetime import datetime
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import sequence_contact as contact  # noqa: E402


class ContactRecencyTests(unittest.TestCase):
	def setUp(self):
		shim.conf.clear()

	def test_same_day_contact_is_recent(self):
		self.assertTrue(
			contact.contacted_within_one_business_day(
				datetime(2026, 9, 14, 9, 0), datetime(2026, 9, 14, 16, 0)
			)
		)

	def test_prior_business_day_contact_is_recent(self):
		self.assertTrue(
			contact.contacted_within_one_business_day(
				datetime(2026, 9, 14, 9, 0), datetime(2026, 9, 15, 9, 0)
			)
		)

	def test_friday_contact_is_recent_on_monday(self):
		self.assertTrue(
			contact.contacted_within_one_business_day(
				datetime(2026, 9, 11, 16, 0), datetime(2026, 9, 14, 9, 0)
			)
		)

	def test_more_than_one_business_day_is_not_recent(self):
		self.assertFalse(
			contact.contacted_within_one_business_day(
				datetime(2026, 9, 10, 16, 0), datetime(2026, 9, 14, 9, 0)
			)
		)

	def test_configured_holiday_uses_shared_business_day_policy(self):
		# Friday -> Tuesday crosses Labor Day plus a configured Monday closure:
		# only Tuesday is a business day after contact.
		shim.conf["crm_holidays"] = ["2026-09-14"]
		self.assertTrue(
			contact.contacted_within_one_business_day(
				datetime(2026, 9, 11, 16, 0), datetime(2026, 9, 15, 9, 0)
			)
		)

	def test_absent_or_bad_contact_is_not_recent(self):
		self.assertFalse(contact.contacted_within_one_business_day(None, datetime(2026, 9, 14, 9, 0)))
		self.assertFalse(contact.contacted_within_one_business_day("not a date", datetime(2026, 9, 14, 9, 0)))


class ContactSourceTests(unittest.TestCase):
	def setUp(self):
		shim.conf.clear()
		shim.db.doctypes.clear()
		shim.db.sql.reset_mock()
		shim.db.sql.side_effect = None

	def test_latest_timestamp_selects_newest_real_source(self):
		latest = contact.latest_contact_timestamp(
			datetime(2026, 9, 10, 10, 0),  # call
			"2026-09-14 08:45:00",  # text
			datetime(2026, 9, 13, 12, 0),  # email
		)
		self.assertEqual(latest, datetime(2026, 9, 14, 8, 45))

	def test_query_uses_all_sources_and_excludes_text_placeholders(self):
		shim.db.doctypes.add("Quo Message")
		shim.db.sql.side_effect = [
			[{"last_contact": datetime(2026, 9, 10, 10, 0)}],
			[{"last_contact": datetime(2026, 9, 13, 12, 0)}],
			[{"last_contact": datetime(2026, 9, 14, 8, 45)}],
		]

		self.assertEqual(contact.latest_contact_for_lead("CRM-LEAD-00001"), datetime(2026, 9, 14, 8, 45))
		self.assertEqual(shim.db.sql.call_count, 3)
		self.assertTrue(all(call.args[1] == {"lead": "CRM-LEAD-00001"} for call in shim.db.sql.call_args_list))
		self.assertIn("not in ('scheduled', 'canceled', 'failed')", shim.db.sql.call_args_list[-1].args[0])

	def test_public_context_api_uses_runner_contract_and_is_json_safe(self):
		with mock.patch.object(contact, "latest_contact_for_lead", return_value=datetime(2026, 9, 11, 16, 0)) as latest:
			self.assertEqual(
				contact.get_sequence_contact_context("CRM-LEAD-00001", now="2026-09-14 09:00:00"),
				{
					"contacted_within_one_business_day": True,
					"last_contact": "2026-09-11 16:00:00",
				},
			)
		latest.assert_called_once_with("CRM-LEAD-00001")
		self.assertTrue(contact.get_sequence_contact_context._whitelisted)

	def test_context_is_json_safe(self):
		shim.db.sql.side_effect = [
			[{"last_contact": datetime(2026, 9, 11, 16, 0)}],
			[{"last_contact": None}],
		]
		self.assertEqual(
			contact.context("CRM-LEAD-00001", now="2026-09-14 09:00:00"),
			{
				"contacted_within_one_business_day": True,
				"last_contact": "2026-09-11 16:00:00",
			},
		)


if __name__ == "__main__":
	unittest.main()

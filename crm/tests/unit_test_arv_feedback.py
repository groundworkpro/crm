"""ARV feedback message to the pricer's owner."""

import unittest

from crm.tests.frappe_shim import install

shim = install()

from crm.api import arv_feedback  # noqa: E402


class MessageTests(unittest.TestCase):
	def test_links_comps_and_shows_both_arvs(self):
		text = arv_feedback.build_message(
			"https://crm.x", "CRM-LEAD-1", "618 NW 32nd St", 185000.4, "Comp 2 is a flip\nnext door", 160000, "comp 1 $180k"
		)
		self.assertIn("[618 NW 32nd St](https://crm.x/crm/leads/CRM-LEAD-1/comps)", text)
		self.assertIn("AI's ARV: **$185,000** · my ARV: **$160,000**", text)
		self.assertIn("> Comp 2 is a flip\n> next door", text)
		self.assertIn("```\ncomp 1 $180k\n```", text)

	def test_no_lead_no_user_arv_no_details(self):
		text = arv_feedback.build_message("https://crm.x", None, "1 Main St", "200000", "too high")
		self.assertIn("off on 1 Main St.", text)
		self.assertIn("AI's ARV: **$200,000**", text)
		self.assertNotIn("my ARV", text)
		self.assertNotIn("```", text)

	def test_details_cannot_break_out_of_code_block(self):
		text = arv_feedback.build_message("s", None, "a", 1, "f", details="x ``` y")
		self.assertEqual(text.count("```"), 2)

	def test_bad_numbers_are_dropped(self):
		self.assertEqual(arv_feedback._money("abc"), "")
		self.assertEqual(arv_feedback._money(None), "")


if __name__ == "__main__":
	unittest.main()

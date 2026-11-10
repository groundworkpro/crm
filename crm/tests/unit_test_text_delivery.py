"""Dead-number rule: block after an undelivered text until the number proves alive."""

import unittest
from datetime import datetime as dt

from crm.tests.frappe_shim import install

shim = install()

from crm.api import text_delivery as td  # noqa: E402

D1, D2, D3 = dt(2026, 10, 30), dt(2026, 11, 2), dt(2026, 11, 5)


class BlockReasonTests(unittest.TestCase):
	def r(self, history, line="mobile"):
		return td.block_reason_from(history, line, "(773) 902-8249")

	def test_clean_number(self):
		self.assertEqual(self.r([("Outgoing", "delivered", D1)]), "")
		self.assertEqual(self.r([]), "")

	def test_last_text_undelivered_blocks(self):
		msg = self.r([("Outgoing", "undelivered", D3), ("Outgoing", "delivered", D1)])
		self.assertIn("not delivered", msg)
		self.assertIn("Nov 5", msg)

	def test_pending_text_after_failure_still_blocks(self):
		self.assertIn("not delivered", self.r([("Outgoing", "sent", D3), ("Outgoing", "undelivered", D2)]))

	def test_reply_or_later_delivery_unblocks(self):
		self.assertEqual(self.r([("Incoming", "", D3), ("Outgoing", "undelivered", D2)]), "")
		self.assertEqual(self.r([("Outgoing", "delivered", D3), ("Outgoing", "undelivered", D2)]), "")

	def test_landline_blocks_voip_does_not(self):
		self.assertIn("landline", self.r([], "fixed"))
		self.assertEqual(self.r([], "voip"), "")
		self.assertEqual(self.r([], ""), "")


if __name__ == "__main__":
	unittest.main()

"""crm.api.arv.save_arv: validation, column guard, timeline comment."""

import unittest

from crm.tests.frappe_shim import install

shim = install()

from crm.api import arv as mod  # noqa: E402


class SaveArv(unittest.TestCase):
	def setUp(self):
		shim.db.values = {("CRM Lead", "L1"): {"name": "L1"}}
		shim.db.columns = {"CRM Lead": {"arv"}}
		shim.db.set_value.reset_mock()
		shim.get_doc.reset_mock()

	def comment(self):
		return shim.get_doc.call_args[0][0]["content"]

	def test_saves_field_and_comment(self):
		out = mod.save_arv("L1", "212400.4", arv_low=200000, arv_high=225000, offer=128000)
		self.assertEqual(out, {"stored": True, "arv": 212400})
		shim.db.set_value.assert_called_once_with("CRM Lead", "L1", "arv", 212400)
		self.assertIn("ARV $212,400", self.comment())
		self.assertIn("range $200,000–$225,000", self.comment())
		self.assertIn("offer $128,000", self.comment())

	def test_without_field_still_logs(self):
		shim.db.columns = {}
		self.assertEqual(mod.save_arv("L1", 150000)["stored"], False)
		shim.db.set_value.assert_not_called()
		self.assertIn("ARV $150,000", self.comment())

	def test_rejects_bad_amounts_and_missing_lead(self):
		for bad in ("abc", 0, -5, 60_000_000):
			with self.assertRaises(Exception):
				mod.save_arv("L1", bad)
		with self.assertRaises(Exception):
			mod.save_arv("nope", 100000)
		shim.db.set_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()

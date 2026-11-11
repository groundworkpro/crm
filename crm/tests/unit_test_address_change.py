"""An edited lead address drops the old house's location and relocates the circle.

German (#bugs, 2026-11-11): fixing a wrong subject address on the lead left the
comps map and the comping tool on the old house. See crm/api/address_change.py.
"""

import unittest
from unittest import mock

from crm.tests.frappe_shim import install

install()

import frappe  # noqa: E402

from crm.api import address_change as ac  # noqa: E402
from crm.api import address_resolve as ar  # noqa: E402


class Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class FakeLead(dict):
	doctype = "CRM Lead"

	def __init__(self, before=None, new=False, **values):
		super().__init__(**values)
		self.name = values.get("name", "CRM-LEAD-2026-00001")
		self.flags = Flags()
		self._before = before
		self._new = new

	def get(self, key, default=None):
		return super().get(key, default)

	def set(self, key, value):
		self[key] = value

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return self._before


OLD = {
	"property_address": "123 Mian St", "property_city": "Pittsburgh",
	"property_state": "PA", "property_zip": "15216",
}
CACHED = {
	"property_lat": 40.1, "property_lng": -80.1, "zillow_facts": '{"zpid": "1"}',
	"zillow_zpid": "1", "batchdata_comps": "[]", "parcel_lat": 40.1,
	"parcel_lng": -80.1, "parcel_source": "redfin", "parcel_address_key": "x",
}


def lead(address, **extra):
	before = FakeLead(**OLD, **CACHED)
	return FakeLead(before=before, **{**OLD, **CACHED, "property_address": address, **extra})


class AddressChangeTest(unittest.TestCase):
	def setUp(self):
		frappe.db.columns["CRM Lead"] = set(ac.LOCATION_FIELDS)
		frappe.enqueue.reset_mock()
		frappe._cache.store.clear()

	def test_a_new_address_blanks_every_location_cache(self):
		doc = lead("123 Main St")
		ac.before_save(doc)
		self.assertTrue(doc.flags.address_changed)
		for field, blank in ac.LOCATION_FIELDS.items():
			self.assertEqual(doc.get(field), blank, field)

	def test_a_cosmetic_edit_keeps_the_caches(self):
		doc = lead("123 Mian Street")
		ac.before_save(doc)
		self.assertFalse(doc.flags.address_changed)
		self.assertEqual(doc.get("property_lat"), 40.1)
		self.assertEqual(doc.get("zillow_zpid"), "1")

	def test_a_zip_fix_counts(self):
		doc = lead("123 Mian St", property_zip="15226")
		ac.before_save(doc)
		self.assertTrue(doc.flags.address_changed)

	def test_insert_is_not_an_edit(self):
		doc = FakeLead(new=True, **OLD, **CACHED)
		ac.before_save(doc)
		self.assertFalse(doc.flags.address_changed)
		self.assertEqual(doc.get("property_lat"), 40.1)

	def test_missing_columns_are_skipped(self):
		frappe.db.columns["CRM Lead"] = {"property_lat", "property_lng"}
		doc = lead("500 Oak Ave")
		ac.before_save(doc)
		self.assertIsNone(doc.get("property_lat"))
		self.assertEqual(doc.get("zillow_zpid"), "1")

	def test_on_update_clears_redis_and_enqueues_relocate(self):
		doc = lead("500 Oak Ave")
		ac.before_save(doc)
		for key in ac._redis_keys(doc.name):
			frappe._cache.store[key] = {"old": True}
		ac.on_update(doc)
		for key in ac._redis_keys(doc.name):
			self.assertNotIn(key, frappe._cache.store)
		frappe.enqueue.assert_called_once()
		self.assertEqual(frappe.enqueue.call_args.args[0], "crm.api.address_change.relocate")
		self.assertEqual(frappe.enqueue.call_args.kwargs["lead"], doc.name)
		self.assertTrue(frappe.enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_unchanged_save_does_nothing(self):
		doc = lead("123 Mian St")
		ac.before_save(doc)
		ac.on_update(doc)
		frappe.enqueue.assert_not_called()

	def test_suggestion_accept_does_not_enqueue_a_second_relocate(self):
		doc = lead("500 Oak Ave")
		doc.flags.address_change_handled = True
		ac.before_save(doc)
		ac.on_update(doc)
		frappe.enqueue.assert_not_called()

	def test_relocate_moves_the_circle_then_warms(self):
		order = []
		with mock.patch.object(
			ar, "resolve_after_address_correction",
			side_effect=lambda lead: order.append("resolve") or {"ok": True, "exact": True},
		), mock.patch("crm.api.geo._enabled", return_value=True), mock.patch(
			"crm.api.geo.warm_lead", side_effect=lambda lead: order.append("warm") or {"ok": True},
		):
			out = ac.relocate("CRM-LEAD-2026-00001")
		self.assertEqual(order, ["resolve", "warm"])
		self.assertEqual(out["warmed"], {"ok": True})

	def test_relocate_without_an_exact_point_does_not_warm(self):
		with mock.patch.object(
			ar, "resolve_after_address_correction", return_value={"ok": False, "exact": False},
		), mock.patch("crm.api.geo.warm_lead") as warm:
			out = ac.relocate("CRM-LEAD-2026-00001")
		warm.assert_not_called()
		self.assertIsNone(out["warmed"])

	def test_hooks_are_wired(self):
		import pathlib

		hooks = (pathlib.Path(ac.__file__).parents[1] / "hooks.py").read_text()
		self.assertIn('"before_save": ["crm.api.address_change.before_save"]', hooks)
		self.assertIn('"crm.api.address_change.on_update"', hooks)
		src = (pathlib.Path(ar.__file__)).read_text()
		self.assertIn("doc.flags.address_change_handled = True", src)


if __name__ == "__main__":
	unittest.main()

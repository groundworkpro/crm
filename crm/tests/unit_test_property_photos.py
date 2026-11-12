"""A seller's second house (a CRM Property, `PROP-`) gets its own Drive photo folder.

Dennis, 2026-11-12: 446 Remington St (PROP-00025) hangs off Carlos Ortiz's lead,
whose Photos folder is 338 Orange St. Every photos endpoint now takes a property
name too and resolves, caches and publishes against the property, leaving the
lead's folder alone.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from crm.tests.frappe_shim import install

frappe = install()

from crm.api import photos  # noqa: E402


class _Doc(dict):
	def get(self, k, d=None):
		return dict.get(self, k, d)


PROP = "PROP-00025"
LEAD = "CRM-LEAD-2026-01956"


class PropertyPhotosTest(unittest.TestCase):
	def setUp(self):
		frappe.db.values = {("CRM Property", PROP): 1, ("CRM Lead", LEAD): 1}
		frappe.db.set_value.reset_mock()
		frappe.db.get_value.reset_mock(return_value=True, side_effect=True)
		frappe.db.get_value.return_value = None
		frappe.has_permission.reset_mock()
		frappe.get_roles.return_value = ["Sales User"]
		frappe.get_meta.return_value = SimpleNamespace(has_field=lambda f: True)
		frappe.published.clear()

	def test_dispatch(self):
		self.assertEqual(photos._doctype(PROP), "CRM Property")
		self.assertEqual(photos._doctype(LEAD), "CRM Lead")

	def test_property_check_uses_sales_roles_not_lead_permission(self):
		photos._check(PROP, "write")
		frappe.has_permission.assert_not_called()
		frappe.get_roles.return_value = ["Guest"]
		with self.assertRaises(frappe.PermissionError):
			photos._check(PROP)

	def test_missing_property(self):
		with self.assertRaises(frappe.DoesNotExistError):
			photos._check("PROP-99999")

	def test_folder_named_after_the_property_address(self):
		doc = _Doc(property_address="446 Remington St", property_city="Rochester",
		           property_state="NY", property_zip="14621")
		with mock.patch.object(frappe, "get_doc", return_value=doc) as gd:
			addr = photos._address(PROP)
		gd.assert_called_with("CRM Property", PROP)
		self.assertEqual(photos._normalize(addr), photos._normalize("446 Remington St, Rochester, NY 14621"))

	def test_cache_lands_on_the_property(self):
		photos._cache_on_lead(PROP, {"id": "F1", "webViewLink": "https://drive/F1"})
		args = frappe.db.set_value.call_args[0]
		self.assertEqual(args[:2], ("CRM Property", PROP))
		self.assertEqual(args[2], {"photo_folder_id": "F1", "photo_folder_url": "https://drive/F1"})

	def test_no_cache_fields_no_write(self):
		frappe.get_meta.return_value = SimpleNamespace(has_field=lambda f: False)
		photos._cache_on_lead(PROP, {"id": "F1"})
		frappe.db.set_value.assert_not_called()

	def test_cached_folder_read_from_the_property(self):
		frappe.db.get_value.return_value = "F1"
		with mock.patch.object(photos, "_drive_get", return_value={"id": "F1", "name": "x"}):
			f = photos._resolve_folder("tok", PROP)
		self.assertEqual(f["id"], "F1")
		self.assertEqual(frappe.db.get_value.call_args[0][:2], ("CRM Property", PROP))

	def test_realtime_names_the_property(self):
		photos._publish(PROP)
		payload = frappe.published[-1][0][1]
		self.assertEqual(payload, {"reference_doctype": "CRM Property", "reference_docname": PROP})

	def test_texted_pictures_stay_on_the_lead(self):
		with self.assertRaises(frappe.ValidationError):
			photos.save_media_to_photos(PROP, url="https://x/a.jpg")


if __name__ == "__main__":
	unittest.main()

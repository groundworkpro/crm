"""A calc saved on a property linked to a lead also lands on that lead's timeline."""
import json
import unittest
from unittest.mock import MagicMock, patch

from crm.tests.frappe_shim import install
frappe = install()
import html as _h

# cash_offer imports these at load; the shim does not carry them. Add them only
# for the import and put frappe.utils back so other test files see it unchanged.
_STUBS = {
	"escape_html": lambda s: _h.escape(str(s or ""), quote=True),
	"flt": lambda v, p=None: float(v or 0),
	"get_url": lambda p="": "https://crm.example" + p,
}
_saved = {k: getattr(frappe.utils, k) for k in _STUBS if hasattr(frappe.utils, k)}
for _k, _v in _STUBS.items():
	setattr(frappe.utils, _k, _v)
try:
	from crm.api import cash_offer, properties
finally:
	for _k in _STUBS:
		if _k in _saved:
			setattr(frappe.utils, _k, _saved[_k])
		else:
			delattr(frappe.utils, _k)

SCENE = [{"kind": "cash", "arv": 200000, "pct": 0.7, "rehab_psf": 20, "fee": 10000}]


class PropertyCalcOnLead(unittest.TestCase):
	def setUp(self):
		self.inserted = []
		self.recorded = []
		self.notes = []
		self.prop = {"property_address": "12 Oak St, Memphis, TN 38104", "lead": "CRM-LEAD-00001"}

		def get_doc(arg, *a):
			if isinstance(arg, dict):
				d = MagicMock()
				d.insert = lambda **k: self.inserted.append(arg)
				return d
			raise AssertionError(arg)

		patch.object(cash_offer, "_guard", lambda: None).start()
		patch.object(frappe, "has_permission", lambda *a, **k: True, create=True).start()
		patch.object(frappe, "get_doc", side_effect=get_doc).start()
		patch.object(frappe, "get_all", return_value=[]).start()
		patch.object(frappe.db, "exists", side_effect=lambda dt, n=None: dt != "DocType" or True).start()
		patch.object(frappe.db, "has_column", side_effect=lambda dt, c: c == "lead").start()
		patch.object(
			frappe.db, "get_value",
			side_effect=lambda dt, n, f=None, as_dict=False, **k: {x: self.prop.get(x) for x in f},
		).start()
		patch.object(properties, "record_offer", lambda n, p, h: self.recorded.append((n, p, h))).start()
		patch.object(
			cash_offer, "_upsert_wholesale_note",
			lambda lead, scenes, street="": self.notes.append((lead, street)),
		).start()

	def tearDown(self):
		patch.stopall()

	def _save(self):
		return cash_offer.save_cash_offer("PROP-00007", json.dumps(SCENE), "[]", 1000, "roof")

	def test_linked_property_posts_on_lead(self):
		out = self._save()
		self.assertEqual(out["lead"], "CRM-LEAD-00001")
		self.assertEqual(len(self.recorded), 1)
		self.assertEqual(self.recorded[0][1]["address"], "12 Oak St")
		self.assertEqual(len(self.inserted), 1)
		c = self.inserted[0]
		self.assertEqual((c["reference_doctype"], c["reference_name"]), ("CRM Lead", "CRM-LEAD-00001"))
		self.assertIn("For 12 Oak St", c["content"])
		self.assertIn("/crm/properties/PROP-00007", c["content"])
		self.assertEqual(self.notes, [("CRM-LEAD-00001", "12 Oak St")])

	def test_unlinked_property_stays_on_property(self):
		self.prop["lead"] = ""
		out = self._save()
		self.assertEqual(out["lead"], "")
		self.assertEqual(len(self.recorded), 1)
		self.assertNotIn("address", self.recorded[0][1])
		self.assertEqual(self.inserted, [])
		self.assertEqual(self.notes, [])

	def test_lead_calc_unchanged(self):
		cash_offer.save_cash_offer("CRM-LEAD-00002", json.dumps(SCENE), "[]", 1000, "")
		self.assertEqual(self.recorded, [])
		self.assertEqual(self.inserted[0]["reference_name"], "CRM-LEAD-00002")
		self.assertNotIn("For ", self.inserted[0]["content"])
		self.assertEqual(self.notes, [("CRM-LEAD-00002", "")])


if __name__ == "__main__":
	unittest.main()

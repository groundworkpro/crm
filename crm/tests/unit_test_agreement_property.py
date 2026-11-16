"""Create Agreement for a lead's second house (a linked CRM Property)."""

import json
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import agreement as ag  # noqa: E402
from crm.api import contract_parse as cp  # noqa: E402

FOLDER = ag.TEMPLATE_FOLDER
TEMPLATES = [
	{"id": 10, "name": "Purchase Agreement - One Seller", "folder_name": FOLDER},
	{"id": 11, "name": "Purchase Agreement - Two Sellers", "folder_name": FOLDER},
	{"id": 20, "name": "SubTo Purchase Agreement - One Seller", "folder_name": FOLDER},
	{"id": 40, "name": "Amendment - One Seller", "folder_name": FOLDER},
]

REAPI_RAW = json.dumps({
	"lotInfo": {"apn": "RAW-APN"},
	"ownerInfo": {"owner1FullName": "Mark Onofri"},
	"propertyInfo": {"address": {"county": "Onondaga County", "state": "NY"}},
})


def _resp(payload, status=200):
	r = mock.MagicMock()
	r.status_code = status
	r.ok = status < 400
	r.json.return_value = payload
	r.text = json.dumps(payload)
	return r


def _doc(fields, name):
	d = mock.MagicMock()
	d.name = name
	d.get.side_effect = lambda k, default=None: fields.get(k, default)
	return d


LEAD = {
	"lead_name": "Mark Onofri", "email": "m@example.com",
	"property_address": "1 Main St", "property_city": "Syracuse", "property_state": "NY",
	"property_zip": "13203", "property_county": "Lead County", "apn": "LEAD-APN",
	"acq_price": 90000, "closing_date": "2026-12-01",
}
PROP = {
	"property_address": "112 Highland Ave, Syracuse, NY 13203", "property_city": "",
	"property_state": "", "property_zip": "", "lead": "CRM-LEAD-1",
}


class FactsTests(unittest.TestCase):
	def test_reapi_and_batchdata_raw(self):
		self.assertEqual(ag._pull_facts(REAPI_RAW),
		                 {"apn": "RAW-APN", "county": "Onondaga County", "state": "NY"})
		bd = {"ids": {"apn": "B-1"}, "address": {"county": "Shelby", "state": "TN"}}
		self.assertEqual(ag._pull_facts(bd), {"apn": "B-1", "county": "Shelby", "state": "TN"})
		self.assertFalse(any(ag._pull_facts("not json").values()))

	def test_state_from_address(self):
		self.assertEqual(ag._state_from_address("940 Tifton St, Norfolk, VA 23513"), "VA")
		self.assertEqual(ag._state_from_address("9634 Combs LnLouisville, KY 40258-1234"), "KY")
		self.assertEqual(ag._state_from_address("940 Tifton St"), "")

	def test_agreement_property_marker(self):
		self.assertEqual(ag.agreement_property({"property": "PROP-00002"}), "PROP-00002")
		self.assertEqual(ag.agreement_property(
			{"template_title": "Purchase Agreement - One Seller — 112 Highland Ave (PROP-00022)"}), "PROP-00022")
		self.assertEqual(ag.agreement_property({"template_title": "Purchase Agreement - One Seller"}), "")


class CreateTests(unittest.TestCase):
	def setUp(self):
		shim.conf["docuseal_api_token"] = "t"

	def _create(self, template="standard", prop_fields=PROP, pulls=None, **kw):
		leaddoc = _doc(LEAD, "CRM-LEAD-1")
		propdoc = _doc(prop_fields, "PROP-00022")
		user = mock.MagicMock(full_name="Dennis Szafran", email="dennis@groundworkpro.com")
		posted, rows = {}, []

		def get_doc(*a, **k):
			if a and a[0] == "User":
				return user
			if a and a[0] == "CRM Lead":
				return leaddoc
			if a and a[0] == ag.PROPERTY_DOCTYPE:
				return propdoc
			rows.append(a[0])
			return mock.MagicMock(name="AGR-1")

		def get_all(doctype, **k):
			if doctype == ag.TAX_PULL_DOCTYPE:
				return pulls if pulls is not None else [{"apn": "PULL-APN", "raw_response": REAPI_RAW}]
			return []

		def fake_post(url, json=None, **k):
			posted.update(payload=json)
			return _resp([{"submission_id": 5, "role": "Buyer", "slug": "b"},
			              {"submission_id": 5, "role": "Seller", "slug": "s"}])

		with mock.patch.object(ag.frappe.db, "exists", return_value=True), \
			mock.patch.object(ag.frappe.db, "has_column", return_value=True), \
			mock.patch.object(ag.frappe, "has_permission", return_value=True, create=True), \
			mock.patch.object(ag.frappe, "get_doc", create=True, side_effect=get_doc), \
			mock.patch.object(ag.frappe, "get_all", create=True, side_effect=get_all), \
			mock.patch.object(ag.requests, "get", return_value=_resp({"data": TEMPLATES})), \
			mock.patch.object(ag.requests, "post", side_effect=fake_post):
			out = ag.create_docuseal_agreement("CRM-LEAD-1", template=template, **kw)
		return out, posted["payload"], rows

	def test_standard_for_second_property(self):
		out, p, rows = self._create(property="PROP-00022")
		buyer = p["submitters"][0]["values"]
		self.assertEqual(buyer["Property Address"], "112 Highland Ave, Syracuse, NY 13203")
		self.assertEqual(buyer["County"], "Onondaga County")
		self.assertEqual(buyer["APN"], "PULL-APN")
		self.assertEqual(buyer["Governing State"], "NY")
		self.assertEqual(buyer["Seller Name(s)"], "Mark Onofri")
		self.assertEqual(p["submitters"][1]["values"]["Property Address"], buyer["Property Address"])
		self.assertEqual(out["template"], "Purchase Agreement - One Seller — 112 Highland Ave (PROP-00022)")
		self.assertEqual(out["property"], "PROP-00022")

	def test_no_tax_pull_leaves_county_and_parcel_blank(self):
		_, p, _ = self._create(property="PROP-00022", pulls=[])
		buyer = p["submitters"][0]["values"]
		self.assertNotIn("County", buyer)
		self.assertNotIn("APN", buyer)
		self.assertNotIn("LEAD-APN", json.dumps(p))
		self.assertEqual(buyer["Governing State"], "NY")

	def test_subto_for_second_property(self):
		_, p, _ = self._create(template="subto", property="PROP-00022")
		buyer = p["submitters"][0]["values"]
		self.assertEqual(buyer["Parcel ID"], "PULL-APN")
		self.assertEqual(buyer["County"], "Onondaga County")

	def test_amendment_for_second_property_skips_lead_terms(self):
		_, p, _ = self._create(template="amendment", property="PROP-00022")
		buyer = p["submitters"][0]["values"]
		self.assertNotIn("Amended Purchase Price", buyer)
		self.assertNotIn("Amended Closing Date", buyer)

	def test_lead_house_unchanged(self):
		out, p, _ = self._create()
		buyer = p["submitters"][0]["values"]
		self.assertEqual(buyer["Property Address"], "1 Main St, Syracuse, NY 13203")
		self.assertEqual(buyer["County"], "Lead County")
		self.assertEqual(buyer["APN"], "LEAD-APN")
		self.assertEqual(out["template"], "Purchase Agreement - One Seller")

	def test_property_of_another_lead_is_refused(self):
		with self.assertRaises(Exception):
			self._create(property="PROP-00022", prop_fields=dict(PROP, lead="CRM-LEAD-9"))


class ParseSkipTests(unittest.TestCase):
	def test_second_property_contract_never_writes_the_lead(self):
		agr = _doc({"lead": "CRM-LEAD-1",
		            "template_title": "Purchase Agreement - One Seller — 112 Highland Ave (PROP-00022)"},
		           "AGR-1")
		with mock.patch.object(cp.frappe.db, "exists", return_value=True), \
			mock.patch.object(cp.frappe, "has_permission", return_value=True, create=True), \
			mock.patch.object(cp.frappe, "get_doc", create=True, return_value=agr) as gd, \
			mock.patch.object(cp, "_stamp") as stamp:
			out = cp.write_agreement_fields("AGR-1", {"acq_price": 50000})
		self.assertEqual(out["skipped"], "second property")
		self.assertEqual(stamp.call_args[0][1], "skipped")
		self.assertFalse(any(c.args and c.args[0] == "CRM Lead" for c in gd.call_args_list))


if __name__ == "__main__":
	unittest.main()

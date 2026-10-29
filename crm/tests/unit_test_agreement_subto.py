"""SubTo agreement type: template resolution and buyer prefill."""

import json
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import agreement as ag  # noqa: E402

FOLDER = ag.TEMPLATE_FOLDER
TEMPLATES = [
	{"id": 10, "name": "Purchase Agreement - One Seller", "folder_name": FOLDER},
	{"id": 11, "name": "Purchase Agreement - Two Sellers", "folder_name": FOLDER},
	{"id": 20, "name": "SubTo Purchase Agreement - One Seller", "folder_name": FOLDER},
	{"id": 21, "name": "SubTo Purchase Agreement - Two Sellers", "folder_name": FOLDER},
	{"id": 19, "name": "SubTo Purchase Agreement - One Seller", "folder_name": FOLDER, "archived_at": "x"},
	{"id": 99, "name": "SubTo Purchase Agreement - One Seller", "folder_name": "Default"},
]


def _resp(payload, status=200):
	r = mock.MagicMock()
	r.status_code = status
	r.json.return_value = payload
	r.text = json.dumps(payload)
	return r


class ResolveTests(unittest.TestCase):
	def setUp(self):
		shim.conf["docuseal_api_token"] = "t"

	def _resolve(self, **kw):
		with mock.patch.object(ag.requests, "get", return_value=_resp({"data": TEMPLATES})):
			return ag._resolve_template_ids(**kw)

	def test_subto_one_and_two_sellers(self):
		self.assertEqual(self._resolve(want_aif=False, want_two=False, want_subto=True),
		                 ([20], "SubTo Purchase Agreement - One Seller"))
		self.assertEqual(self._resolve(want_aif=False, want_two=True, want_subto=True),
		                 ([21], "SubTo Purchase Agreement - Two Sellers"))

	def test_standard_never_picks_subto(self):
		self.assertEqual(self._resolve(want_aif=False, want_two=False)[0], [10])
		self.assertEqual(self._resolve(want_aif=False, want_two=True)[0], [11])


class CreateTests(unittest.TestCase):
	def test_subto_prefills_lead_identity_not_money(self):
		shim.conf["docuseal_api_token"] = "t"
		lead = {"lead_name": "Felici Love", "email": "f@example.com", "property_county": "Cook",
		        "apn": "17-01-234-005", "acq_price": 150000}
		leaddoc = mock.MagicMock()
		leaddoc.get.side_effect = lambda k, d=None: lead.get(k, d)
		user = mock.MagicMock(full_name="Dennis Szafran", email="dennis@groundworkpro.com")
		posted = {}

		def fake_post(url, json=None, **kw):
			posted.update(url=url, payload=json)
			return _resp([{"submission_id": 5, "role": "Buyer", "slug": "b"},
			              {"submission_id": 5, "role": "Seller", "name": "Felici Love", "slug": "s"}])

		with mock.patch.object(ag.frappe.db, "exists", return_value=True), \
			mock.patch.object(ag.frappe, "has_permission", return_value=True, create=True), \
			mock.patch.object(ag.frappe, "get_doc", create=True,
			                  side_effect=lambda *a, **k: user if a[0] == "User" else (leaddoc if a[0] == "CRM Lead" else mock.MagicMock(name="AGR-1"))), \
			mock.patch.object(ag, "_full_property_address", return_value="1 Main St, Chicago, IL 60601"), \
			mock.patch.object(ag.requests, "get", return_value=_resp({"data": TEMPLATES})), \
			mock.patch.object(ag.requests, "post", side_effect=fake_post):
			out = ag.create_docuseal_agreement("CRM-LEAD-1", template="subto", two_sellers="1")

		self.assertEqual(out["template"], "SubTo Purchase Agreement - One Seller")
		p = posted["payload"]
		self.assertTrue(posted["url"].endswith("/submissions"))
		self.assertEqual(p["template_id"], 20)
		buyer = p["submitters"][0]["values"]
		self.assertEqual(buyer, {
			"Seller Name(s)": "Felici Love",
			"Property Address": "1 Main St, Chicago, IL 60601",
			"County": "Cook",
			"Parcel ID": "17-01-234-005",
			"Signer Name": "Dennis Szafran",
		})
		self.assertEqual([s["role"] for s in p["submitters"]], ["Buyer", "Seller"])
		self.assertEqual(p["submitters"][1]["values"]["Seller Name"], "Felici Love")


if __name__ == "__main__":
	unittest.main()

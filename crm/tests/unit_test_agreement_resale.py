"""Resale (no-assignment) Purchase Agreement: template resolution and prefill."""

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
	{"id": 30, "name": "Resale Purchase Agreement - One Seller", "folder_name": FOLDER},
	{"id": 31, "name": "Resale Purchase Agreement - Two Sellers", "folder_name": FOLDER},
	{"id": 29, "name": "Resale Purchase Agreement - One Seller", "folder_name": FOLDER, "archived_at": "x"},
	{"id": 99, "name": "Resale Purchase Agreement - One Seller", "folder_name": "Default"},
]


def _resp(payload, status=200):
	r = mock.MagicMock()
	r.status_code = status
	r.ok = status < 400
	r.json.return_value = payload
	r.text = json.dumps(payload)
	return r


class ResolveTests(unittest.TestCase):
	def setUp(self):
		shim.conf["docuseal_api_token"] = "t"

	def _resolve(self, **kw):
		with mock.patch.object(ag.requests, "get", return_value=_resp({"data": TEMPLATES})):
			return ag._resolve_template_ids(**kw)

	def test_resale_one_and_two_sellers(self):
		self.assertEqual(self._resolve(want_aif=False, want_two=False, want_resale=True),
		                 ([30], "Resale Purchase Agreement - One Seller"))
		self.assertEqual(self._resolve(want_aif=False, want_two=True, want_resale=True),
		                 ([31], "Resale Purchase Agreement - Two Sellers"))

	def test_standard_never_picks_resale(self):
		# Resale ids are higher; Standard must still pick the plain Purchase Agreement.
		self.assertEqual(self._resolve(want_aif=False, want_two=False)[0], [10])
		self.assertEqual(self._resolve(want_aif=False, want_two=True)[0], [11])


class CreateTests(unittest.TestCase):
	def test_resale_gets_the_standard_prefill(self):
		shim.conf["docuseal_api_token"] = "t"
		lead = {"lead_name": "Felici Love", "email": "f@example.com", "property_state": "Illinois"}
		leaddoc = mock.MagicMock()
		leaddoc.get.side_effect = lambda k, d=None: lead.get(k, d)
		user = mock.MagicMock(full_name="Dennis Szafran", email="dennis@groundworkpro.com")
		posted = {}

		def fake_post(url, json=None, **kw):
			posted.update(url=url, payload=json)
			return _resp([{"submission_id": 5, "role": "Buyer", "slug": "b"},
			              {"submission_id": 5, "role": "Seller 1", "slug": "s1"},
			              {"submission_id": 5, "role": "Seller 2", "slug": "s2"}])

		with mock.patch.object(ag.frappe.db, "exists", return_value=True), \
			mock.patch.object(ag.frappe, "has_permission", return_value=True, create=True), \
			mock.patch.object(ag.frappe, "get_doc", create=True,
			                  side_effect=lambda *a, **k: user if a[0] == "User" else (leaddoc if a[0] == "CRM Lead" else mock.MagicMock(name="AGR-1"))), \
			mock.patch.object(ag, "_full_property_address", return_value="1 Main St, Chicago, IL 60601"), \
			mock.patch.object(ag.requests, "get", return_value=_resp({"data": TEMPLATES})), \
			mock.patch.object(ag.requests, "post", side_effect=fake_post):
			out = ag.create_docuseal_agreement("CRM-LEAD-1", template="resale", two_sellers="2",
			                                   seller2_name="Sam Love")

		self.assertEqual(out["template"], "Resale Purchase Agreement - Two Sellers")
		p = posted["payload"]
		self.assertEqual(p["template_id"], 31)
		buyer = p["submitters"][0]["values"]
		self.assertEqual(buyer["Seller Name(s)"], "Felici Love and Sam Love")
		for k, v in {"Access Days": "3", "Due Diligence Days": "30", "Closing Days": "15",
		             "Earnest Money": "100", "Additional Terms": "None."}.items():
			self.assertEqual(buyer[k], v)
		self.assertNotIn("Sale Price", buyer)
		self.assertEqual([s["role"] for s in p["submitters"]], ["Buyer", "Seller 1", "Seller 2"])


if __name__ == "__main__":
	unittest.main()

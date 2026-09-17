"""Public signed-PDF URL: HMAC token, https site URL, guest gate."""

import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()
shim.conf["encryption_key"] = "test-encryption-key"

from crm.api import agreement as ag  # noqa: E402


class PdfTokenTests(unittest.TestCase):
	def setUp(self):
		shim.conf["encryption_key"] = "test-encryption-key"
		shim.conf.pop("agreement_pdf_secret", None)

	def test_token_is_stable_and_name_specific(self):
		a = ag._pdf_token("AGR-1")
		self.assertEqual(a, ag._pdf_token("AGR-1"))
		self.assertEqual(len(a), 32)
		self.assertNotEqual(a, ag._pdf_token("AGR-2"))

	def test_dedicated_secret_beats_encryption_key(self):
		first = ag._pdf_token("AGR-1")
		shim.conf["agreement_pdf_secret"] = "other-secret"
		self.assertNotEqual(first, ag._pdf_token("AGR-1"))

	def test_ok_rejects_wrong_and_short(self):
		name = "AGR-1"
		good = ag._pdf_token(name)
		self.assertTrue(ag._pdf_token_ok(name, good))
		self.assertFalse(ag._pdf_token_ok(name, "x" * 32))
		self.assertFalse(ag._pdf_token_ok(name, "short"))
		self.assertFalse(ag._pdf_token_ok(name, None))
		self.assertFalse(ag._pdf_token_ok(name, ""))


class PublicUrlTests(unittest.TestCase):
	def setUp(self):
		shim.conf["encryption_key"] = "test-encryption-key"

	def test_url_is_absolute_https_and_carries_token(self):
		url = ag._public_pdf_url("AGR-99")
		self.assertTrue(
			url.startswith(
				"https://crm.example.test/api/method/crm.api.agreement.public_signed_pdf?"
			)
		)
		self.assertIn("agreement=AGR-99", url)
		self.assertIn("token=" + ag._pdf_token("AGR-99"), url)

	def test_http_site_is_forced_to_https(self):
		orig = shim.utils.get_url
		shim.utils.get_url = lambda: "http://crm.groundworkpro.com"
		try:
			url = ag._public_pdf_url("AGR-1")
			self.assertTrue(url.startswith("https://crm.groundworkpro.com/"))
		finally:
			shim.utils.get_url = orig

	def test_localhost_http_is_left_alone(self):
		orig = shim.utils.get_url
		shim.utils.get_url = lambda: "http://localhost:8080"
		try:
			url = ag._public_pdf_url("AGR-1")
			self.assertTrue(url.startswith("http://localhost:8080/"))
		finally:
			shim.utils.get_url = orig


class ShapeTests(unittest.TestCase):
	def setUp(self):
		shim.conf["encryption_key"] = "test-encryption-key"
		shim.get_cached_value = lambda *a, **k: "Lance"

	def test_completed_row_gets_public_url(self):
		row = shim._dict(
			name="AGR-1",
			owner="lance@x.com",
			seller_links="[]",
			provider="docuseal",
			agreement_status="completed",
			signed_count=2,
			total_signers=2,
		)
		ag._shape_agreement(row)
		self.assertTrue(row["is_signed"])
		self.assertEqual(row["signed_pdf_url"], ag._public_pdf_url("AGR-1"))

	def test_unsigned_row_has_no_url(self):
		row = shim._dict(
			name="AGR-1",
			owner="lance@x.com",
			seller_links="[]",
			provider="docuseal",
			agreement_status="sent",
			signed_count=0,
			total_signers=2,
		)
		ag._shape_agreement(row)
		self.assertFalse(row["is_signed"])
		self.assertIsNone(row["signed_pdf_url"])


class GuestGateTests(unittest.TestCase):
	def setUp(self):
		shim.conf["encryption_key"] = "test-encryption-key"

	def test_bad_token_never_loads_the_row(self):
		original = shim.db.exists
		shim.db.exists = mock.MagicMock(side_effect=AssertionError("must not exist-check"))
		try:
			with self.assertRaises(shim.PermissionError):
				ag.public_signed_pdf("AGR-1", token="nope")
		finally:
			shim.db.exists = original

	def test_allow_guest_is_set(self):
		self.assertTrue(ag.public_signed_pdf._allow_guest)

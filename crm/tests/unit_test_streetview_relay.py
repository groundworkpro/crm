"""Street View Static is the comp gallery's LAST fallback, relayed by CRM.

The server key is deliberately absent from the browser URL. PropWarehouse owns
Google egress and its permanent pano cache; the authenticated CRM endpoint is a
narrow byte relay because a rep's browser cannot reach the Docker bridge.
"""
import inspect
import os
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

frappe = install()

from crm.api import streetview


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VUE = os.path.join(ROOT, "frontend", "src", "components", "CompDetailModal.vue")


class FakeResponse:
	def __init__(self, *, status=200, json_body=None, body=b"", content_type="image/jpeg", length=None):
		self.status_code = status
		self._json = json_body
		self._body = body
		self.headers = {"Content-Type": content_type}
		if length is not None:
			self.headers["Content-Length"] = str(length)

	def json(self):
		if isinstance(self._json, Exception):
			raise self._json
		return self._json

	def iter_content(self, chunk_size=65536):
		for i in range(0, len(self._body), chunk_size):
			yield self._body[i:i + chunk_size]


class Relay(unittest.TestCase):
	def setUp(self):
		frappe.local.response = {}

	def call(self, responses, lat=39.9, lng=-75.1):
		# A single FakeResponse is a RETURN VALUE, not a MagicMock side_effect.
		# Passing it as side_effect makes Mock try to iterate the object, raises
		# before production code sees the response, and lets every clean-404 test
		# pass vacuously. Lists model sequential metadata/image responses; an
		# Exception deliberately exercises the redacted failure path.
		mock_get = ({"side_effect": responses}
					if isinstance(responses, (list, tuple, Exception))
					else {"return_value": responses})
		with patch("crm.api.comps._guard"), \
			 patch("crm.api.vendor_facts._base_url", return_value="http://warehouse:8120"), \
			 patch("requests.get", **mock_get) as get:
			result = streetview.comp_streetview(lat, lng)
		return result, get

	def test_happy_path_streams_exact_jpeg_bytes_inline(self):
		jpeg = b"\xff\xd8real-jpeg\xff\xd9"
		meta = FakeResponse(json_body={
			"ok": True, "available": True,
			"image_path": "/streetview/image?pano=abc&heading=12",
		})
		image = FakeResponse(body=jpeg, length=len(jpeg))
		result, get = self.call([meta, image])

		self.assertIsNone(result)
		self.assertEqual(frappe.local.response["filecontent"], jpeg)
		self.assertEqual(frappe.local.response["content_type"], "image/jpeg")
		self.assertEqual(frappe.local.response["display_content_as"], "inline")
		self.assertEqual(frappe.local.response["headers"]["Cache-Control"],
						 "private, max-age=86400")
		self.assertEqual(get.call_count, 2)
		self.assertEqual(get.call_args_list[1].args[0],
						 "http://warehouse:8120/streetview/image?pano=abc&heading=12")
		self.assertTrue(get.call_args_list[1].kwargs["stream"])

	def test_no_imagery_is_a_clean_404_and_never_fetches_bytes(self):
		result, get = self.call(FakeResponse(json_body={"ok": True, "available": False}))
		self.assertIsNone(result)
		self.assertEqual(frappe.local.response, {"http_status_code": 404})
		get.assert_called_once()

	def test_caller_cannot_supply_a_path_and_upstream_ssrf_path_is_rejected(self):
		# Public signature is coordinates only: there is no URL/path knob to abuse.
		self.assertEqual(list(inspect.signature(streetview.comp_streetview).parameters), ["lat", "lng"])
		for evil in (
			"http://169.254.169.254/latest/meta-data",
			"//evil.example/streetview/image?x=1",
			"/streetview/image-not-really?x=1",
			"/streetview/image?x=1#fragment",
		):
			with self.subTest(evil=evil):
				frappe.local.response = {}
				meta = FakeResponse(json_body={"available": True, "image_path": evil})
				_, get = self.call(meta)
				self.assertEqual(frappe.local.response, {"http_status_code": 404})
				get.assert_called_once()

	def test_non_jpeg_is_rejected(self):
		meta = FakeResponse(json_body={"available": True, "image_path": "/streetview/image?x=1"})
		image = FakeResponse(body=b"<html>upstream error</html>", content_type="text/html")
		self.call([meta, image])
		self.assertEqual(frappe.local.response, {"http_status_code": 404})

	def test_oversized_header_and_stream_are_rejected(self):
		meta = FakeResponse(json_body={"available": True, "image_path": "/streetview/image?x=1"})
		too_big = streetview.MAX_JPEG_BYTES + 1
		self.call([meta, FakeResponse(body=b"x", length=too_big)])
		self.assertEqual(frappe.local.response, {"http_status_code": 404})

		frappe.local.response = {}
		meta = FakeResponse(json_body={"available": True, "image_path": "/streetview/image?x=1"})
		self.call([meta, FakeResponse(body=b"x" * too_big)])
		self.assertEqual(frappe.local.response, {"http_status_code": 404})

	def test_exception_does_not_echo_internal_base_or_key(self):
		secret = "AIza-SHOULD-NEVER-LEAVE"
		message = f"failed http://warehouse:8120/streetview?key={secret}"
		self.call(RuntimeError(message))
		self.assertEqual(frappe.local.response, {"http_status_code": 404})
		self.assertNotIn(secret, repr(frappe.local.response))
		self.assertNotIn("warehouse:8120", repr(frappe.local.response))

	def test_invalid_coordinates_are_rejected_before_network(self):
		for lat, lng in (("nan", 1), (91, 1), (1, 181), (None, None)):
			with self.subTest(lat=lat, lng=lng):
				frappe.local.response = {}
				_, get = self.call([], lat, lng)
				self.assertEqual(frappe.local.response, {"http_status_code": 404})
				get.assert_not_called()


class FrontendContract(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		with open(VUE, encoding="utf-8") as fh:
			cls.source = fh.read()

	def test_static_is_requested_only_after_real_photo_rungs_are_empty(self):
		s = self.source
		real = s.index("const realPhotos = computed")
		static = s.index("const streetViewPhotoUrl = computed")
		combined = s.index("const photos = computed", static)
		self.assertLess(real, static)
		self.assertLess(static, combined)
		self.assertIn("loading.value || realPhotos.value.length", s[static:combined])
		self.assertIn("if (realPhotos.value.length) return realPhotos.value", s[combined:combined + 250])
		self.assertIn("crm.api.streetview.comp_streetview", s[static:combined])

	def test_real_listing_photo_prevents_street_view(self):
		# This is intentionally a source-level assertion: the project has no JS
		# unit runner, and the Python suite is the required bench-free gate. It is
		# mutation-sensitive -- deleting/inverting this return makes the test fail.
		self.assertIn(
			"if (realPhotos.value.length) return realPhotos.value",
			self.source,
		)
		self.assertIn(
			"if (props.subjectMode || !response.value || loading.value || realPhotos.value.length) return ''",
			self.source,
		)

	def test_gallery_labels_the_fallback_street_view(self):
		self.assertIn('v-if="isStaticStreetView && heroLoaded"', self.source)
		self.assertIn("{{ __('Street View') }}", self.source)

	def test_embed_utility_is_not_conflated_with_static(self):
		block_start = self.source.index("const streetViewPhotoUrl = computed")
		block = self.source[block_start:block_start + 900]
		self.assertNotIn("maps/embed", block)
		self.assertNotIn("MAPS_EMBED_KEY", block)
		self.assertIn("/api/method/crm.api.streetview.comp_streetview", block)


if __name__ == "__main__":
	unittest.main()

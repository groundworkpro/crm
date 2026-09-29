"""Source key for texted/emailed pictures saved to a lead's Photos folder.

Must match `sourceKey()` in frontend/src/composables/leadPhotoSaves.js.
"""

import unittest

from crm.tests.frappe_shim import install

install()

from crm.api.photos import media_source_key  # noqa: E402


class MediaSourceKeyTests(unittest.TestCase):
	def test_quo_url_ignores_signature(self):
		a = "https://share.quo.com/v1/resource/message-media/9wp0od9wrxx0ylhwknivzvnp0.jpg?sig=AAA"
		b = "https://share.quo.com/v1/resource/message-media/9wp0od9wrxx0ylhwknivzvnp0.jpg?sig=BBB"
		self.assertEqual(media_source_key(url=a), "quo:9wp0od9wrxx0ylhwknivzvnp0.jpg")
		self.assertEqual(media_source_key(url=a), media_source_key(url=b))

	def test_file(self):
		self.assertEqual(media_source_key(file="abc123"), "file:abc123")

	def test_fits_drive_app_properties(self):
		long = "https://x.example/" + "a" * 300
		self.assertLessEqual(len("crm_source") + len(media_source_key(url=long)), 124)


if __name__ == "__main__":
	unittest.main()

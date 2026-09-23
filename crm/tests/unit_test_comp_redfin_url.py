"""The comp gallery: which provider's photos win, and how the link is got.

TWO separate contracts live in `_shape_detail`, and they used to be tangled:

* **The photo ladder is Redfin -> Zillow -> Realtor.** Redfin leads because it
  returns a full gallery where both vendors return one frame, and because its
  /photos is our own store rather than a billed call. Zillow is already fetched
  with the facts, so it is the second rung and Realtor is only asked when that
  gallery is still thin.

* **The Redfin listing LINK must never hold the gallery hostage.** Measured on
  prod 2026-09-10: Zillow facts + photos 0.39s, then `/url` 6.3s run serially
  after them -- a rep waited ~7s for photos that were ready, to get a link. The
  lookup runs on a thread started before the vendor calls and is joined with a
  short budget; a late answer is filled in by a background job that patches the
  cached entry.

The two now interact: Redfin's `/photos` carries the matched row's observed
listing path, so on the happy path the link arrives WITH the gallery and the
`/url` thread is never joined at all. The thread is only waited on when Redfin
did not match the house -- which is exactly when there is no observed path to
carry.

The FACTS blob stays Zillow-first and is fetched unconditionally: Redfin's
/facts has `listing_remarks` but no HOA, parking, heating, cooling or price
history, and `CompDetailModal.vue` reads all of those.
"""
import threading
import time
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install
install()

from crm.api import comps, redfin


def row():
	return {"name": "zillow::1", "address": "5 Main St, Minneapolis, MN 55401", "lat": 45.0, "lng": -93.0}


def gallery(photos=(), url=None):
	return {"photos": list(photos), "url": url}


class RedfinUrlBudget(unittest.TestCase):
	"""The /url thread itself, independent of the ladder."""

	def test_no_service_falls_back_to_inline_lookup(self):
		with patch.object(redfin, "_base_url", return_value=None), \
			 patch.object(redfin, "redfin_listing_url", return_value="https://redfin/x") as inline:
			job = comps._start_redfin_url("5 Main St", 45, -93)
			self.assertIsNone(job)
			url, pending = comps._finish_redfin_url(job, "5 Main St", 45, -93)
		inline.assert_called_once()
		self.assertEqual((url, pending), ("https://redfin/x", False))

	def test_fast_answer_rides_the_request(self):
		with patch.object(redfin, "_base_url", return_value="http://svc"), \
			 patch.object(redfin, "_fetch_listing_url", return_value="https://redfin/fast"):
			job = comps._start_redfin_url("5 Main St", 45, -93)
			self.assertIsNotNone(job)
			url, pending = comps._finish_redfin_url(job, "5 Main St", 45, -93, budget=1.0)
		self.assertEqual((url, pending), ("https://redfin/fast", False))

	def test_slow_answer_is_pending_not_waited_for(self):
		release = threading.Event()

		def slow(*a):
			release.wait(5)
			return "https://redfin/slow"

		with patch.object(redfin, "_base_url", return_value="http://svc"), \
			 patch.object(redfin, "_fetch_listing_url", side_effect=slow):
			job = comps._start_redfin_url("5 Main St", 45, -93)
			t = time.time()
			url, pending = comps._finish_redfin_url(job, "5 Main St", 45, -93, budget=0.1)
			self.assertLess(time.time() - t, 1.0)
		release.set()
		self.assertEqual((url, pending), (None, True))

	def test_thread_error_is_swallowed(self):
		with patch.object(redfin, "_base_url", return_value="http://svc"), \
			 patch.object(redfin, "_fetch_listing_url", side_effect=RuntimeError("boom")):
			job = comps._start_redfin_url("5 Main St", 45, -93)
			url, pending = comps._finish_redfin_url(job, "5 Main St", 45, -93, budget=1.0)
		self.assertEqual((url, pending), (None, False))


class PhotoLadder(unittest.TestCase):
	"""Redfin -> Zillow -> Realtor, and each rung only while the gallery is thin.

	Every provider is stubbed in all four tests: an unstubbed rung would make a
	real HTTP attempt, which is both flaky and a silent pass for the wrong
	reason.
	"""

	def _shape(self, redfin_photos=(), redfin_url=None, realtor=(), zillow_photos=(),
	           details=None):
		from crm.api import apivex

		with patch.object(redfin, "_base_url", return_value="http://svc"), \
			 patch.object(redfin, "_fetch_listing_url", return_value=None), \
			 patch.object(redfin, "redfin_gallery",
			              return_value=gallery(redfin_photos, redfin_url)), \
			 patch.object(apivex, "realtor_photo_urls", return_value=list(realtor)), \
			 patch.object(comps, "_zillow_detail",
			              return_value=(details if details is not None else {"address": "5 Main St"},
			                            list(zillow_photos))):
			return comps._shape_detail(row())

	def test_redfin_wins_when_it_has_a_gallery(self):
		"""The measured default: Redfin returns ~22 images, vendors return one."""
		out = self._shape(redfin_photos=["r1", "r2", "r3"], realtor=["x1", "x2"],
		                  zillow_photos=["z1", "z2"])
		self.assertEqual(out["photos"], ["r1", "r2", "r3"])
		self.assertEqual(out["photo_source"], "redfin")

	def test_zillow_is_second_when_redfin_is_thin(self):
		out = self._shape(redfin_photos=["r1"], realtor=["x1", "x2"], zillow_photos=["z1", "z2"])
		self.assertEqual(out["photos"], ["z1", "z2"])
		self.assertEqual(out["photo_source"], "zillow")

	def test_realtor_is_the_last_rung(self):
		out = self._shape(redfin_photos=[], realtor=["x1", "x2"], zillow_photos=["z1"])
		self.assertEqual(out["photos"], ["x1", "x2"])
		self.assertEqual(out["photo_source"], "realtor")

	def test_no_provider_has_photos(self):
		out = self._shape()
		self.assertEqual(out["photos"], [])
		self.assertEqual(out["photo_source"], "")
		self.assertFalse(out["photos_available"])

	def test_a_thin_rung_never_replaces_a_thicker_one(self):
		"""Each rung must IMPROVE on the gallery, not merely be non-empty."""
		out = self._shape(redfin_photos=["r1"], realtor=["x1"], zillow_photos=["z1"])
		self.assertEqual(out["photos"], ["r1"])
		self.assertEqual(out["photo_source"], "redfin")

	def test_facts_stay_zillow_even_when_redfin_supplies_the_photos(self):
		"""CompDetailModal reads HOA/parking/heating off this blob; Redfin's
		/facts carries none of them, so the Zillow call is unconditional."""
		out = self._shape(redfin_photos=["r1", "r2"], details={"hoa_fee": 250, "address": "5 Main St"})
		self.assertEqual(out["photo_source"], "redfin")
		self.assertEqual(out["details"], {"hoa_fee": 250, "address": "5 Main St"})
		self.assertTrue(out["available"])


class RedfinUrlFromTheGallery(unittest.TestCase):
	"""Where the listing link comes from, now that two paths can supply it."""

	def test_gallery_url_short_circuits_the_thread(self):
		"""Redfin matched, so /photos already carried the observed path."""
		from crm.api import apivex

		with patch.object(redfin, "_base_url", return_value="http://svc"), \
			 patch.object(redfin, "redfin_gallery",
			              return_value=gallery(["r1", "r2"], "https://redfin/from-gallery")), \
			 patch.object(apivex, "realtor_photo_urls", return_value=[]), \
			 patch.object(comps, "_finish_redfin_url") as finish, \
			 patch.object(comps, "_zillow_detail", return_value=({"address": "5 Main St"}, [])):
			out = comps._shape_detail(row())
		finish.assert_not_called()
		self.assertEqual(out["redfin_url"], "https://redfin/from-gallery")
		self.assertFalse(out["redfin_url_pending"])

	def test_unmatched_house_still_waits_on_the_thread(self):
		"""No gallery url means Redfin did not match, so the /url thread is the
		only route to a link -- and a slow one must report pending, not block."""
		release = threading.Event()

		def slow(*a):
			release.wait(5)
			return "https://redfin/slow"

		from crm.api import apivex

		with patch.object(redfin, "_base_url", return_value="http://svc"), \
			 patch.object(redfin, "_fetch_listing_url", side_effect=slow), \
			 patch.object(redfin, "redfin_gallery", return_value=gallery([], None)), \
			 patch.object(apivex, "realtor_photo_urls", return_value=[]), \
			 patch.object(comps, "REDFIN_URL_BUDGET", 0.1), \
			 patch.object(comps, "_zillow_detail",
			              return_value=({"address": "5 Main St"}, ["p1", "p2"])):
			result = comps._shape_detail(row())
		release.set()
		self.assertEqual(result["photos"], ["p1", "p2"])
		self.assertEqual(result["photo_source"], "zillow")
		self.assertIsNone(result["redfin_url"])
		self.assertTrue(result["redfin_url_pending"])
		self.assertEqual(result["redfin_url_point"][1:], [45.0, -93.0])


if __name__ == "__main__":
	unittest.main()

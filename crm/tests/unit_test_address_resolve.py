"""The pure half of address_resolve: geometry, and the address verdict.

`classify_address` is the one piece a human sees the consequences of — it
decides whether a rep gets interrupted — so the cases below are the real prod
strings, not invented ones. The three "different" address cases are exactly the
leads Redfin could not match on 2026-09-19; the "cosmetic" ones are what Google
did to the other 13, which must stay silent.
"""

import inspect
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import address_resolve as ar  # noqa: E402


class Geometry(unittest.TestCase):
	def test_bearing_cardinals(self):
		# Due north, east, south, west from one point.
		self.assertAlmostEqual(ar.bearing(40.0, -80.0, 40.01, -80.0), 0.0, places=1)
		self.assertAlmostEqual(ar.bearing(40.0, -80.0, 40.0, -79.99), 90.0, places=1)
		self.assertAlmostEqual(ar.bearing(40.0, -80.0, 39.99, -80.0), 180.0, places=1)
		self.assertAlmostEqual(ar.bearing(40.0, -80.0, 40.0, -80.01), 270.0, places=1)

	def test_bearing_is_always_in_range(self):
		for dlat, dlng in ((0.01, 0.01), (-0.01, 0.01), (-0.01, -0.01), (0.01, -0.01)):
			h = ar.bearing(33.46, -81.95, 33.46 + dlat, -81.95 + dlng)
			self.assertGreaterEqual(h, 0.0)
			self.assertLess(h, 360.0)

	def test_the_augusta_case(self):
		"""The measured example: the pano is WEST of 824 Forsythe St.

		Standing at the panorama Google picks for the rooftop and turning to the
		house gives ~270 degrees — and 270 is the heading that visibly showed the
		house in the live check, where the hardcoded 0 showed the road.
		"""
		cam = (33.459755, -81.955800)
		house = (33.459755, -81.955570)
		self.assertAlmostEqual(ar.bearing(*cam, *house), 90.0, places=0)
		# ...and from the other side, the reciprocal.
		self.assertAlmostEqual(ar.bearing(*house, *cam), 270.0, places=0)

	def test_metres_matches_the_measured_census_error(self):
		# Lead 00042: census point vs Zillow rooftop, measured at 53m.
		d = ar.metres(33.46023011, -81.95558682, 33.459755, -81.95557)
		self.assertGreater(d, 45)
		self.assertLess(d, 60)


class Ladder(unittest.TestCase):
	"""Who wins, and who never gets asked.

	The ordering is the cost control AND the reason a rep is or is not
	interrupted, so it is pinned here rather than left to the call site.
	"""

	ROOFTOP = {
		"lat": 1.0, "lng": 2.0, "location_type": "ROOFTOP",
		"exact": True, "partial_match": False,
	}

	def test_redfin_wins_outright(self):
		self.assertEqual(
			ar.pick_point(redfin=(33.1, -81.1), zillow=(33.2, -81.2), google=self.ROOFTOP),
			(33.1, -81.1, "redfin"),
		)

	def test_zillow_covers_a_redfin_miss(self):
		self.assertEqual(
			ar.pick_point(redfin=None, zillow=(33.2, -81.2), google=self.ROOFTOP),
			(33.2, -81.2, "zillow"),
		)

	def test_google_only_when_both_miss(self):
		self.assertEqual(
			ar.pick_point(redfin=None, zillow=None, google=self.ROOFTOP),
			(1.0, 2.0, "google"),
		)

	def test_nothing_resolves_to_nothing(self):
		self.assertEqual(ar.pick_point(), (None, None, ""))

	def test_an_interpolated_geocode_is_diagnostic_not_a_parcel(self):
		"""RANGE_INTERPOLATED is the Census bug by another name -- reject it."""
		self.assertEqual(
			ar.pick_point(google={
				"lat": 1.0, "lng": 2.0, "location_type": "RANGE_INTERPOLATED",
				"exact": False, "partial_match": False,
			}),
			(None, None, ""),
		)

	def test_approximate_is_diagnostic_not_a_parcel(self):
		self.assertEqual(
			ar.pick_point(google={
				"lat": 1.0, "lng": 2.0, "location_type": "APPROXIMATE",
				"exact": False, "partial_match": False,
			}),
			(None, None, ""),
		)

	def test_partial_rooftop_is_not_exact(self):
		self.assertEqual(
			ar.pick_point(google={
				"lat": 1.0, "lng": 2.0, "location_type": "ROOFTOP",
				"exact": True, "partial_match": True,
			}),
			(None, None, ""),
		)

	def test_only_explicit_nonpartial_rooftop_wins(self):
		self.assertEqual(
			ar.pick_point(google={
				"lat": 1.0, "lng": 2.0, "location_type": "ROOFTOP",
				"exact": True, "partial_match": False,
			}),
			(1.0, 2.0, "google"),
		)

	def test_a_google_answer_with_no_coordinate_is_no_answer(self):
		self.assertEqual(ar.pick_point(google={"lat": None, "lng": None}), (None, None, ""))


class AddressVerdict(unittest.TestCase):
	def test_identical_is_same(self):
		self.assertEqual(
			ar.classify_address("824 Forsythe St, Augusta, GA 30901",
			                    "824 Forsythe St, Augusta, GA 30901"),
			"same",
		)

	def test_country_suffix_is_not_a_correction(self):
		"""Google appends ', USA' to everything. Prompting on that is noise."""
		self.assertEqual(
			ar.classify_address("602 Baker St, Morristown, TN 37813",
			                    "602 Baker St, Morristown, TN 37813, USA"),
			"same",
		)

	def test_abbreviation_and_punctuation_are_cosmetic(self):
		# The real prod pair: "Heard Av." -> "Heard Ave".
		self.assertIn(
			ar.classify_address("1506 Heard Av., Augusta, GA 30904",
			                    "1506 Heard Ave, Augusta, GA 30904, USA"),
			("same", "cosmetic"),
		)

	def test_vendor_junk_tail_is_cosmetic_not_a_correction(self):
		"""The vendor duplicates part of the street into a fake unit.

		Google strips it. Every word Google kept we already had, in order, so it
		is a cleanup — worth recording, not worth a prompt.
		"""
		for ours, theirs in (
			("124 Stephen Ave Ste Phen, Oviedo, FL 32765",
			 "124 Stephen Ave, Oviedo, FL 32765, USA"),
			("5401 N Flint Ave Fl Int, Tucson, AZ 85704, Arizona",
			 "5401 N Flint Ave, Tucson, AZ 85704, USA"),
		):
			self.assertEqual(ar.classify_address(ours, theirs), "cosmetic", ours)

	def test_missing_city_is_cosmetic(self):
		"""A hand-entered lead is street-only; Google fills the rest in."""
		self.assertEqual(
			ar.classify_address("824 Forsythe St",
			                    "824 Forsythe St, Augusta, GA 30901, USA"),
			"cosmetic",
		)

	def test_a_different_house_number_always_prompts(self):
		self.assertEqual(
			ar.classify_address("824 Forsythe St, Augusta, GA 30901",
			                    "819 Forsythe St, Augusta, GA 30901, USA"),
			"different",
		)

	def test_a_different_street_prompts(self):
		self.assertEqual(
			ar.classify_address("824 Forsythe St, Augusta, GA 30901",
			                    "824 Fenwick St, Augusta, GA 30901, USA"),
			"different",
		)

	def test_a_different_zip_prompts(self):
		self.assertEqual(
			ar.classify_address("824 Forsythe St, Augusta, GA 30901",
			                    "824 Forsythe St, Augusta, GA 30904, USA"),
			"different",
		)

	def test_directionals_are_collapsed_not_dropped(self):
		"""'N' and 'North' are the same road; 'N' and 'S' are not."""
		self.assertIn(
			ar.classify_address("1115 North Holly Ave, Oklahoma City, OK 73127",
			                    "1115 N Holly Ave, Oklahoma City, OK 73127, USA"),
			("same", "cosmetic"),
		)
		self.assertEqual(
			ar.classify_address("1115 N Holly Ave, Oklahoma City, OK 73127",
			                    "1115 S Holly Ave, Oklahoma City, OK 73127, USA"),
			"different",
		)

	def test_no_google_answer_never_prompts(self):
		"""An empty rewrite is not a disagreement — it is silence."""
		self.assertEqual(ar.classify_address("824 Forsythe St, Augusta, GA", ""), "same")
		self.assertEqual(ar.classify_address("824 Forsythe St, Augusta, GA", None), "same")

	def test_a_missing_house_number_is_substantive(self):
		"""Losing the house number means Google geocoded the street, not the house."""
		self.assertEqual(
			ar.classify_address("824 Forsythe St, Augusta, GA 30901",
			                    "Forsythe St, Augusta, GA 30901, USA"),
			"different",
		)


class ServiceContracts(unittest.TestCase):
	def test_exact_endpoint_keeps_cache_and_billing_metadata(self):
		body = {
			"ok": True, "matched": True, "exact": True,
			"lat": 39.1, "lng": -75.2, "normalized": "1 Main St, Town, PA 19000",
			"source": "google", "location_type": "ROOFTOP", "partial_match": False,
			"cache": "hit", "billed": 0, "reason": None,
		}
		with patch.object(ar, "_json_request", return_value=(body, None)) as req:
			out = ar._google_exact("1 Main Street")
		self.assertEqual(out["status"], "exact")
		self.assertEqual(out["cache"], "hit")
		self.assertEqual(out["billed"], 0)
		self.assertEqual(req.call_args.args[:3], ("POST", ar._propwarehouse_base(), "/address/exact"))

	def test_hard_exact_failure_is_retryable_not_no_match(self):
		with patch.object(ar, "_json_request", return_value=(None, "unavailable")):
			self.assertEqual(ar._google_exact("1 Main St"), {
				"status": "transient", "reason": "unavailable",
			})

	def test_honest_no_match_is_authoritative(self):
		body = {"ok": True, "matched": False, "exact": False, "reason": "no_match"}
		with patch.object(ar, "_json_request", return_value=(body, None)):
			out = ar._google_exact("not a house")
		self.assertEqual(out["status"], "no_match")

	def test_old_redfin_server_cannot_guess_point_exactness(self):
		body = {"ok": True, "matched": True, "lat": 1.0, "lng": 2.0,
		        "point_source": "redfin"}
		with patch.object(ar, "_json_request", return_value=(body, None)):
			out = ar._redfin_facts("1 Main St", 1, 2, normalize=True)
		self.assertIsNone(out["point"])

	def test_redfin_point_requires_both_exact_flag_and_source(self):
		body = {"ok": True, "matched": True, "lat": 1.0, "lng": 2.0,
		        "point_exact": True, "point_source": "redfin"}
		with patch.object(ar, "_json_request", return_value=(body, None)):
			out = ar._redfin_facts("1 Main St", 1, 2, normalize=False)
		self.assertEqual(out["point"], (1.0, 2.0))

	def test_cached_zillow_opts_out_of_redfin_paid_normalization(self):
		body = {"ok": True, "matched": False}
		with patch.object(ar, "_json_request", return_value=(body, None)) as req:
			ar._redfin_facts("1 Main St", 1, 2, normalize=False)
		params = req.call_args.kwargs["params"]
		self.assertEqual(params["normalize"], 0)

	def test_propwarehouse_url_is_strict_not_redfin_fallback(self):
		with patch.dict(os.environ, {}, clear=True), patch.object(ar.frappe, "conf", {}):
			self.assertEqual(ar._propwarehouse_base(), "")

	def test_no_google_endpoint_or_key_remains_in_crm_module(self):
		source = Path(ar.__file__).read_text()
		self.assertNotIn("maps.googleapis.com", source)
		self.assertNotIn("google_maps_key", source)
		self.assertNotIn("CRM Geocode", source)


class SuggestionKeys(unittest.TestCase):
	class Doc:
		doctype = "CRM Lead"
		name = "LEAD-1"

		def __init__(self, **values):
			self.values = values

		def get(self, key, default=None):
			return self.values.get(key, default)

	def test_dismissal_suppresses_only_the_same_pair(self):
		doc = self.Doc(
			parcel_address_key=ar._address_key("10 Main St"),
			address_suggestion_key=ar._suggestion_key("10 Main St", "12 Main St"),
			address_suggestion_state="dismissed",
		)
		self.assertEqual(ar._suggestion_fields(doc, "10 Main St", "12 Main St"), {})
		new = ar._suggestion_fields(doc, "10 Main St", "14 Main St")
		self.assertEqual(new["address_suggestion_state"], "pending")
		self.assertEqual(new["address_suggested"], "14 Main St")

	def test_address_change_clears_an_unrelated_old_decision(self):
		doc = self.Doc(
			parcel_address_key=ar._address_key("10 Main St"),
			address_suggestion_key="old", address_suggestion_state="dismissed",
		)
		self.assertEqual(
			ar._suggestion_fields(doc, "20 Oak St", ""),
			{"address_suggested": "", "address_suggestion_key": "",
			 "address_suggestion_state": ""},
		)


class SafetyShape(unittest.TestCase):
	def test_manual_api_has_no_circle_moving_argument(self):
		self.assertEqual(list(inspect.signature(ar.resolve_now).parameters), ["subject"])

	def test_warm_lead_gates_on_exact_resolution_before_posting(self):
		root = Path(__file__).resolve().parents[2]
		source = (root / "crm" / "api" / "geo.py").read_text()
		resolve_at = source.index("resolved = resolve_at_ingest(lead)")
		gate = source.index('if not resolved.get("ok") or not resolved.get("exact")')
		post = source.index('requests.post(')
		self.assertLess(resolve_at, gate)
		self.assertLess(gate, post)

	def test_setup_schema_contains_only_fields_the_orchestrator_uses(self):
		root = Path(__file__).resolve().parents[2]
		setup = (root.parent / "frappe-crm-deploy" / "scripts" / "setup_geocode.py").read_text()
		for field in ar.REQUIRED_FIELDS:
			self.assertIn(f'"fieldname": "{field}"', setup)
		self.assertNotIn('DOCTYPE_NAME = "CRM Geocode"', setup)
		self.assertNotIn('"fieldname": "sv_pano_id"', setup)
		self.assertNotIn('"fieldname": "sv_heading"', setup)


if __name__ == "__main__":
	unittest.main()

"""Rent estimate from nearby rentals (Dennis, 2026-11-17)."""
import unittest

from crm.tests.frappe_shim import install

install()

from crm.api import rent_estimate as re_


def row(rid, rent, beds=3, sqft=1500, dist_m=500, source="redfin", last_seen="2026-11-16", **kw):
	r = {"rental_id": rid, "rent": str(rent), "beds": str(beds), "baths": "2", "sqft": str(sqft),
		 "dist_m": dist_m, "source": source, "status": "active", "last_seen": last_seen,
		 "street": f"{rid} Main St", "city": "Springfield", "state": "CO", "lat": 1, "lng": 2}
	r.update(kw)
	return r


def fetcher(by_radius):
	calls = []

	def fetch(lat, lng, radius, cls):
		calls.append((radius, cls))
		return by_radius.get(radius, [])

	fetch.calls = calls
	return fetch


class RentEstimate(unittest.TestCase):
	def test_median_and_quartiles_of_similar_houses(self):
		rows = [row(i, r) for i, r in enumerate([1500, 1600, 1700, 1800, 1900])]
		out = re_.estimate_from_listings(0, 0, beds=3, sqft=1500, fetch=fetcher({1.0: rows}))
		self.assertEqual(out["count"], 5)
		self.assertEqual(out["median"], 1700)
		self.assertEqual((out["low"], out["high"]), (1600, 1800))
		self.assertEqual(out["radius_mi"], 1.0)
		self.assertTrue(out["filtered"])
		self.assertEqual(out["by_sqft"], 1700)

	def test_same_house_from_two_sources_counts_once(self):
		rows = [row(1, 2000, source="redfin", last_seen="2026-11-10"),
				row(1, 2100, source="zillow", last_seen="2026-11-16")]
		out = re_.estimate_from_listings(0, 0, fetch=fetcher({3.0: rows}))
		self.assertEqual(out["count"], 1)
		self.assertEqual(out["median"], 2100)
		self.assertEqual(out["listings"][0]["sources"], ["redfin", "zillow"])

	def test_widens_the_circle_until_enough(self):
		near = [row(i, 1500) for i in range(2)]
		far = near + [row(10 + i, 1700) for i in range(4)]
		f = fetcher({1.0: near, 2.0: far})
		out = re_.estimate_from_listings(0, 0, beds=3, fetch=f)
		self.assertEqual(out["radius_mi"], 2.0)
		self.assertEqual(out["count"], 6)
		self.assertEqual([c[0] for c in f.calls], [1.0, 2.0])

	def test_same_listing_with_and_without_unit_counts_once(self):
		rows = [row(1, 2300, street="10990 Dusk Sky Point", unit="#3248", source="zillow"),
				row(2, 2300, street="10990 Dusk Sky Pt", source="redfin")]
		out = re_.estimate_from_listings(0, 0, fetch=fetcher({3.0: rows}))
		self.assertEqual(out["count"], 1)
		self.assertEqual(out["listings"][0]["sources"], ["redfin", "zillow"])

	def test_unlike_houses_are_left_out_then_used_loosely(self):
		like = [row(i, 1500) for i in range(2)]
		unlike = [row(10 + i, 4000, beds=6, sqft=4000) for i in range(6)]
		out = re_.estimate_from_listings(0, 0, beds=3, sqft=1500, fetch=fetcher({3.0: like + unlike}))
		self.assertTrue(out["loose"])
		self.assertFalse(out["filtered"])
		self.assertEqual(out["count"], 8)

	def test_bad_rents_are_dropped(self):
		rows = [row(1, 50), row(2, 250000), row(3, 1800)]
		out = re_.estimate_from_listings(0, 0, fetch=fetcher({3.0: rows}))
		self.assertEqual(out["count"], 1)
		self.assertEqual(out["median"], 1800)

	def test_townhouse_searches_both_classes(self):
		f = fetcher({})
		re_.estimate_from_listings(0, 0, property_type="Townhouse", fetch=f)
		self.assertEqual({c[1] for c in f.calls}, {"townhouse", "sfr"})

	def test_warehouse_down_is_not_cached_as_none_found(self):
		out = re_.estimate_from_listings(0, 0, fetch=lambda *a: None)
		self.assertTrue(out["unavailable"])

	def test_nothing_nearby(self):
		out = re_.estimate_from_listings(0, 0, fetch=fetcher({}))
		self.assertEqual(out["count"], 0)
		self.assertNotIn("unavailable", out)


if __name__ == "__main__":
	unittest.main()

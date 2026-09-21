"""listing_state: auction flag, live homeStatus, $0 ForSale rows."""

import unittest

from crm.tests.frappe_shim import install

install()

from crm.api.zillow_comps import (  # noqa: E402
	_apply_listing,
	_is_auction,
	_migrate_area_v8,
	_migrate_pin_v4,
	_shape_search,
	_state_from_facts,
	listing_state,
)


def _search_prop(**kw):
	base = {
		"address": "1623 Nevada Ave E, Saint Paul, MN 55106",
		"latitude": 44.9859,
		"longitude": -93.0319,
		"price": 190400,
		"zpid": 2107713,
		"listingStatus": "RECENTLY_SOLD",
		"dateSold": 1780459200000,
		"listingSubType": {},
		"imgSrc": "https://example.com/p.jpg",
	}
	base.update(kw)
	return base


class ListingStateTests(unittest.TestCase):
	def test_auction_flag_beats_recently_sold(self):
		prop = _search_prop(listingSubType={"is_forAuction": True})
		self.assertTrue(_is_auction(prop))
		self.assertEqual(listing_state(prop, "sold"), "auction")

	def test_plain_recently_sold(self):
		self.assertEqual(listing_state(_search_prop(), "sold"), "sold")

	def test_for_sale_fsba(self):
		prop = _search_prop(
			listingStatus="FOR_SALE",
			price=249000,
			listingSubType={"is_FSBA": True},
			dateSold=None,
		)
		self.assertEqual(listing_state(prop, "sale"), "for_sale")

	def test_pending(self):
		prop = _search_prop(listingStatus="PENDING", listingSubType={"is_FSBA": True})
		self.assertEqual(listing_state(prop, "sale"), "pending")

	def test_shape_keeps_zero_price_auction(self):
		# Auctions are exempt from the unpriced drop (Lance, 2026-09-22): a $0-ask
		# auction is a deal-flow signal even though it is not pricing evidence.
		row = _shape_search(
			_search_prop(
				price=0,
				listingStatus="FOR_SALE",
				listingSubType={"is_forAuction": True},
				dateSold=None,
			),
			"sale",
		)
		self.assertIsNotNone(row)
		self.assertEqual(row["listing_state"], "auction")
		self.assertEqual(row["status"], "Active")
		self.assertEqual(row["price"], 0)

	def test_shape_drops_for_sale_without_price(self):
		self.assertIsNone(
			_shape_search(_search_prop(price=None, listingStatus="FOR_SALE", dateSold=None), "sale")
		)

	def test_shape_drops_rental_without_price(self):
		self.assertIsNone(
			_shape_search(_search_prop(price=0, listingStatus="FOR_RENT", dateSold=None), "rent")
		)

	def test_migrate_area_v8_strips_unpriced_rows(self):
		priced = {"address": "a", "price": 100}
		unpriced = {"address": "b", "price": None}
		zero = {"address": "c", "price": 0}
		auction = {"address": "d", "price": 0, "listing_state": "auction"}
		out = _migrate_area_v8({"rows": [priced, unpriced, zero, auction], "complete": True})
		self.assertEqual(out["rows"], [priced, auction])
		self.assertTrue(out["complete"])
		# A bare-list blob (older shape) is tolerated too.
		self.assertEqual(_migrate_area_v8([priced, unpriced]), [priced])

	def test_shape_drops_sold_without_price(self):
		self.assertIsNone(_shape_search(_search_prop(price=0), "sold"))

	def test_facts_live_auction(self):
		self.assertEqual(
			_state_from_facts({"home_status": "FOR_SALE", "is_for_auction": True}),
			"auction",
		)

	def test_facts_for_sale_not_inferred(self):
		self.assertEqual(_state_from_facts({"home_status": "FOR_SALE"}), "for_sale")
		self.assertIsNone(_state_from_facts({"home_status": "RECENTLY_SOLD"}))

	def test_pin_v4_refetches_live_listing_missing_auction_flag(self):
		self.assertIsNone(_migrate_pin_v4({"home_status": "FOR_SALE", "zpid": 1}))

	def test_pin_v4_keeps_sold_without_refetch(self):
		out = _migrate_pin_v4({"home_status": "RECENTLY_SOLD", "zpid": 1})
		self.assertEqual(out["is_for_auction"], False)

	def test_pin_v4_keeps_blob_that_already_has_the_flag(self):
		blob = {"home_status": "FOR_SALE", "is_for_auction": True}
		self.assertIs(blob, _migrate_pin_v4(blob))

	def test_apply_listing_auction_does_not_keep_sale_price(self):
		row = {"price": 190400, "status": "Inactive", "listing_state": "sold", "removed_date": "2026-06-03"}
		_apply_listing(row, 0, None, "auction")
		self.assertEqual(row["listing_state"], "auction")
		self.assertEqual(row["status"], "Active")
		self.assertEqual(row["price"], 0)
		self.assertIsNone(row["removed_date"])


if __name__ == "__main__":
	unittest.main()

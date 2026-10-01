"""Flip warnings from Redfin's per-house timeline (Lance, 2026-09-29)."""
import datetime as dt
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import redfin_history as rh
from crm.api import sale_history


def ms(d):
	return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def ev(d, word, price, source="NORTHSTARMLS"):
	return {"date": ms(d), "event": word, "price": price, "source": source}


TODAY = dt.date(2026, 9, 29)


class Translate(unittest.TestCase):
	def test_words_become_zillow_words(self):
		ph = rh.price_history([ev("2026-01-02", "Listed", 1), ev("2026-02-02", "Price Changed", 1),
							   ev("2026-03-02", "Contingent", 1), ev("2026-04-02", "Sold (MLS)", 1),
							   ev("2025-01-02", "Relisted", 1), ev("2024-01-02", "Listed for Rent", 1)])
		self.assertEqual([e["event"] for e in ph],
						 ["Sold", "Pending sale", "Price change", "Listed for sale", "Listed for sale", "Listed for Rent"])
		self.assertEqual(ph[0]["date"], "2026-04-02")

	def test_mls_and_public_record_copies_of_one_sale_collapse(self):
		ph = rh.price_history([
			ev("2026-05-20", "Sold (Public Records)", 400000, "Public Records"),
			ev("2026-04-30", "Sold (MLS)", 400000),
			ev("2025-11-01", "Sold (Public Records)", 200000, "Public Records"),
		])
		sold = [e for e in ph if e["event"] == "Sold"]
		self.assertEqual([e["date"] for e in sold], ["2026-04-30", "2025-11-01"])

	def test_the_duplicate_no_longer_hides_the_flip(self):
		ph = rh.price_history([
			ev("2026-05-20", "Sold (Public Records)", 400000, "Public Records"),
			ev("2026-04-30", "Sold (MLS)", 400000),
			ev("2026-03-01", "Listed", 410000),
			ev("2025-11-01", "Sold (Public Records)", 200000, "Public Records"),
		])
		h = sale_history.parse(ph, TODAY, "RECENTLY_SOLD")
		self.assertEqual(h["flip"]["kind"], "resale")
		self.assertEqual(h["flip"]["bought_price"], 200000)

	def test_a_relist_at_a_big_markup_reads_as_flip_in_progress(self):
		ph = rh.price_history([ev("2026-09-01", "Listed", 339500),
							   ev("2026-01-15", "Sold (Public Records)", 152000, "Public Records")])
		h = sale_history.parse(ph, TODAY, rh.home_status({"listing_state": "for_sale"}))
		self.assertEqual(h["flip"]["kind"], "relist")


class Attach(unittest.TestCase):
	def test_known_houses_get_redfin_history_and_unknown_go_back(self):
		from crm.api import redfin
		rows = [{"name": "redfin::11", "address": "1 Oak St", "listing_state": "sold"},
				{"name": "CRM-1", "address": "2 Elm Street", "listing_state": "for_sale"},
				{"name": "zillow::9", "address": "3 Pine St", "listing_state": "sold"}]
		index = {redfin.street_key("2 Elm St"): {"property_id": 22}}
		events = {"11": [ev("2026-01-02", "Sold (MLS)", 250000)], "22": []}
		with patch.object(rh, "events_many", return_value=events) as em:
			info, lacking, unread = rh.attach(rows, TODAY, index)
		self.assertEqual(em.call_args[0][0], ["11", "22"])
		self.assertEqual([r["name"] for r in lacking], ["zillow::9"])
		self.assertEqual(rows[0]["sale_history"]["last_sale"]["price"], 250000)
		self.assertTrue(rows[1]["sale_history_missing"])
		self.assertEqual((info["checked"], info["with_history"], info["missing"]), (2, 1, 1))

	def test_known_but_not_read_is_unchecked_not_clean(self):
		rows = [{"name": "redfin::11", "address": "1 Oak St"}]
		with patch.object(rh, "events_many", return_value={}):
			info, _, unread = rh.attach(rows, TODAY, {})
		self.assertTrue(rows[0]["sale_history_unchecked"])
		self.assertEqual(unread, rows)
		self.assertEqual(info["unchecked"], 1)


class ZillowCacheFillsTimelineOnly(unittest.TestCase):
	def test_old_zillow_status_does_not_override_redfin(self):
		from crm.api import zillow_comps as zc
		row = {"address": "1 Oak St", "listing_state": "sold", "square_footage": 1500}
		facts = {"home_status": "FOR_RENT", "sqft": 900,
				 "price_history": [{"date": "2026-01-02", "event": "Sold", "price": 250000}]}
		with patch.object(zc.zillow_api, "_api_key", return_value="k"), \
			 patch.object(zc, "_pin_facts_many", return_value={"1 Oak St": facts}):
			zc.attach_sale_history([row], TODAY, cache_only=True, history_only=True)
		self.assertEqual((row["listing_state"], row["square_footage"]), ("sold", 1500))
		self.assertEqual(row["sale_history"]["last_sale"]["price"], 250000)


if __name__ == "__main__":
	unittest.main()

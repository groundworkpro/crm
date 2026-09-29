"""Comps by address (no CRM record) and BatchData bought once per house.

The money rules: a house already paid for — by the CRM before the move, or by
any system through PropWarehouse — is never bought again, and the lead is no
longer where a new purchase is kept.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from crm.tests.frappe_shim import install

frappe = install()

from crm.api import address_comps, batchdata_comps, comps, vendor_facts  # noqa: E402


class Doc(dict):
	"""Enough of a Document: .get, attribute access, .set."""

	def __getattr__(self, k):
		return self.get(k)

	def __setattr__(self, k, v):
		self[k] = v

	def set(self, k, v):
		self[k] = v


RAW = {
	"_id": "p1",
	"address": {"street": "9 Oak St", "city": "Wichita", "state": "KS", "zip": "67203",
				"latitude": 37.7, "longitude": -97.3},
	"building": {"bedroomCount": 3},
	"sale": {"lastSale": {"price": 150000, "saleDate": "2026-01-02"}},
}


def lead(**kw):
	d = Doc(doctype="CRM Lead", name="CRM-LEAD-1", property_address="1 Elm St, Wichita, KS 67203",
			property_city="Wichita", property_state="KS", property_zip="67203")
	d.update(kw)
	return d


class Warehouse:
	"""A fake PropWarehouse: one row per address, bought on first spend."""

	def __init__(self, rows=(RAW,), error=None):
		self.rows = list(rows)
		self.error = error
		self.stored = False
		self.calls = []

	def __call__(self, street, city="", state="", zip_code="", take=5, spend=True, force=False, caller=""):
		self.calls.append(spend)
		if self.stored and not force:
			return {"ok": True, "matched": bool(self.rows), "source": "store",
					"fetched_at": "2026-09-01T00:00:00+00:00", "payload": {"properties": self.rows}}
		if not spend:
			return {"ok": True, "matched": False, "source": "store", "error": "not_cached", "payload": None}
		if self.error:
			return {"ok": False, "matched": False, "source": "live", "error": self.error, "payload": None}
		self.stored = True
		return {"ok": True, "matched": bool(self.rows), "source": "live", "payload": {"properties": self.rows}}


class BatchDataThroughWarehouse(unittest.TestCase):
	def setUp(self):
		frappe.local.batchdata_warehouse = None
		frappe.db.columns["CRM Lead"] = {"batchdata_comps", "batchdata_comps_fetched_at"}
		frappe.db.set_value.reset_mock()
		self.wh = Warehouse()
		for p in (
			patch.object(vendor_facts, "batchdata_comps", self.wh),
			patch.object(vendor_facts, "_base_url", lambda: "http://pw:8120"),
			patch.object(batchdata_comps, "_post", MagicMock(side_effect=AssertionError("direct BatchData call"))),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_buys_through_the_warehouse_and_never_writes_the_lead(self):
		out = batchdata_comps.fetch_for_lead(lead())
		self.assertEqual([c["name"] for c in out], ["batchdata::p1"])
		self.assertEqual(out[0]["listing_state"], "sold")
		frappe.db.set_value.assert_not_called()

	def test_a_house_bought_elsewhere_boards_free(self):
		self.wh.stored = True
		self.assertEqual(len(batchdata_comps.cached_comps(lead())), 1)
		self.assertEqual(self.wh.calls, [False])
		self.assertIsNotNone(batchdata_comps.cached_at(lead()))

	def test_one_map_open_is_one_store_read_and_one_buy(self):
		doc = lead()
		self.assertIsNone(batchdata_comps.cached_at(doc))
		batchdata_comps.fetch_for_lead(doc)
		batchdata_comps.cached_comps(doc)
		self.assertEqual(self.wh.calls, [False, True])

	def test_comps_the_lead_paid_for_before_the_move_are_not_bought_again(self):
		import time

		old = {"t": time.time() - 86400, "comps": [{"name": "batchdata::old", "lat": 1, "lng": 1, "price": 1}]}
		doc = lead(batchdata_comps=json.dumps(old))
		self.assertEqual([c["name"] for c in batchdata_comps.fetch_for_lead(doc)], ["batchdata::old"])
		self.assertEqual(self.wh.calls, [])

	def test_warehouse_down_falls_back_to_the_direct_call(self):
		frappe.conf["batchdata_comps_api_key"] = "k"
		self.addCleanup(frappe.conf.pop, "batchdata_comps_api_key", None)
		with patch.object(vendor_facts, "batchdata_comps", lambda *a, **k: None), \
			 patch.object(batchdata_comps, "_post", return_value={"results": {"properties": [RAW]}}) as post:
			out = batchdata_comps.fetch_for_lead(lead())
		post.assert_called_once()
		self.assertEqual(len(out), 1)

	def test_empty_wallet_alerts_once_not_on_every_open(self):
		self.wh.error = "insufficient_balance"
		with patch.object(batchdata_comps, "_report_wallet_empty") as alert:
			self.assertEqual(batchdata_comps.fetch_for_lead(lead()), [])
			alert.assert_called_once()
		with patch.object(vendor_facts, "batchdata_comps",
						  lambda *a, **k: {"ok": False, "matched": False, "source": "store",
										   "error": "insufficient_balance"}), \
			 patch.object(batchdata_comps, "_report_wallet_empty") as alert:
			frappe.local.batchdata_warehouse = None
			batchdata_comps.fetch_for_lead(lead())
			alert.assert_not_called()


class AddressSubject(unittest.TestCase):
	def test_whole_address_and_split_fields_are_the_same_house(self):
		self.assertEqual(
			address_comps.subject_name("1 Elm St, Wichita, KS 67203", "Wichita", "KS", "67203"),
			address_comps.subject_name("1  ELM st", "wichita", "ks", "67203"),
		)
		self.assertTrue(address_comps.subject_name("1 Elm St", "", "", "67203").startswith("ADDR-"))

	def test_runs_the_lead_board_on_an_unsaved_subject_with_the_callers_picks(self):
		seen = {}

		def board(name, **kw):
			seen["name"] = name
			seen["kw"] = kw
			seen["doc"] = comps._load_subject(name)
			return {"lead": name}

		with patch.object(frappe, "get_doc", side_effect=lambda d: Doc(d)), \
			 patch.object(comps, "get_lead_comps", side_effect=board):
			out = address_comps.get_address_comps(
				"1 Elm St", city="Wichita", state="KS", zip="67203", lat="37.7", lng="-97.3",
				beds=3, sqft=1400, comp_state={"selected": ["redfin::1"], "hidden": []},
			)
		doc = seen["doc"]
		self.assertEqual(out["lead"], seen["name"])
		self.assertEqual((doc.property_lat, doc.property_lng), (37.7, -97.3))
		self.assertEqual((doc.bedrooms, doc.square_footage), ("3", "1400"))
		self.assertEqual(json.loads(seen["kw"]["state"])["selected"], ["redfin::1"])
		# Gone after the request: nothing about the house outlives the call.
		with self.assertRaises(frappe.DoesNotExistError):
			comps._load_subject(seen["name"])

	def test_no_picks_means_a_clean_board_not_someone_elses(self):
		with patch.object(frappe, "get_doc", side_effect=lambda d: Doc(d)), \
			 patch.object(comps, "get_lead_comps", return_value={}) as board:
			address_comps.get_address_comps("1 Elm St", zip="67203")
		self.assertEqual(json.loads(board.call_args.kwargs["state"]), {"hidden": [], "selected": []})

	def test_needs_a_zip_or_city_and_state(self):
		with self.assertRaises(frappe.ValidationError):
			address_comps.get_address_comps("1 Elm St")


if __name__ == "__main__":
	unittest.main()

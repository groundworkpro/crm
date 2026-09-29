"""Every Zillow call on the comps page goes through PropWarehouse first.

RapidAPI is only called directly when the warehouse cannot be asked at all
(down, or an old build without the route). A warehouse FAILURE (its quota
floor, Zillow down) is not a reason to spend the key directly.
"""
import unittest
from unittest.mock import patch

from crm.tests.frappe_shim import install

install()

from crm.api import vendor_facts, zillow, zillow_comps


def prop(zpid, status="FOR_SALE"):
	return {"zpid": zpid, "address": f"{zpid} Main St", "latitude": 45.1,
			"longitude": -93.0, "price": 300000, "listingStatus": status}


class AreaSearch(unittest.TestCase):
	def test_asks_the_warehouse_for_the_same_circle_with_pending(self):
		env = {"ok": True, "matched": True, "payload": {"results": [prop(1)], "capped": False}}
		with patch.object(vendor_facts, "zillow_search", return_value=env) as ws, \
			 patch.object(zillow_comps, "_search_window") as direct:
			rows, complete = zillow_comps._search("-93.0123 45.1234,1", "ForSale")
		direct.assert_not_called()
		args, kw = ws.call_args
		self.assertEqual(args[:4], (45.1234, -93.0123, 0.5, "ForSale"))
		self.assertTrue(kw["include_pending"])
		self.assertEqual([str(r["zpid"]) for r in rows], ["1"])
		self.assertTrue(complete)

	def test_solds_do_not_ask_for_pending(self):
		env = {"ok": True, "matched": False, "payload": {"results": [], "capped": False}}
		with patch.object(vendor_facts, "zillow_search", return_value=env) as ws:
			zillow_comps._search("-93 45,4", "RecentlySold", "24m")
		self.assertFalse(ws.call_args.kwargs["include_pending"])
		self.assertEqual(ws.call_args.args[2], 2.0)

	def test_a_capped_answer_is_not_complete(self):
		env = {"ok": True, "matched": True, "payload": {"results": [prop(1)], "capped": True}}
		with patch.object(vendor_facts, "zillow_search", return_value=env):
			_, complete = zillow_comps._search("-93 45,1", "ForSale")
		self.assertFalse(complete)

	def test_warehouse_down_falls_back_to_rapidapi(self):
		with patch.object(vendor_facts, "zillow_search", return_value=None), \
			 patch.object(zillow_comps, "_search_window", return_value=([], True)) as direct:
			zillow_comps._search("-93 45,1", "ForSale")
		direct.assert_called_once()

	def test_warehouse_failure_does_not_spend_the_key_directly(self):
		env = {"ok": False, "matched": False, "payload": None, "error": "quota"}
		with patch.object(vendor_facts, "zillow_search", return_value=env), \
			 patch.object(zillow_comps, "_search_window") as direct:
			rows, complete = zillow_comps._search("-93 45,1", "ForSale")
		direct.assert_not_called()
		self.assertIsNone(rows)
		self.assertFalse(complete)


class SaleHistoryLookups(unittest.TestCase):
	def test_hits_misses_and_failures_are_kept_apart(self):
		envs = [
			{"ok": True, "matched": True, "payload": {"zpid": 1}},
			{"ok": True, "matched": False, "payload": None},
			{"ok": False, "matched": False, "payload": None, "error": "quota"},
		]
		with patch.object(vendor_facts, "zillow_property_many", return_value=envs), \
			 patch.object(zillow_comps.zillow_api, "fetch_many") as direct:
			out = zillow_comps._property_bodies(["a", "b", "c"])
		direct.assert_not_called()
		self.assertEqual(out[0], {"zpid": 1})
		self.assertEqual(out[1], {})
		self.assertIsNone(out[2])

	def test_only_unreachable_addresses_go_direct(self):
		envs = [{"ok": True, "matched": True, "payload": {"zpid": 1}}, None]
		with patch.object(vendor_facts, "zillow_property_many", return_value=envs), \
			 patch.object(zillow_comps.zillow_api, "fetch_many", return_value=[{"zpid": 2}]) as direct:
			out = zillow_comps._property_bodies(["a", "b"])
		self.assertEqual(direct.call_args.args[0], [("/property", {"address": "b"})])
		self.assertEqual(out, [{"zpid": 1}, {"zpid": 2}])


class SubjectLookup(unittest.TestCase):
	def test_warehouse_answer_is_used(self):
		with patch.object(vendor_facts, "zillow_property",
						  return_value={"ok": True, "matched": True, "payload": {"zpid": 9}}), \
			 patch.object(zillow, "_request") as direct:
			self.assertEqual(zillow._fetch("1 Main"), {"zpid": 9})
		direct.assert_not_called()

	def test_warehouse_miss_is_a_cacheable_negative(self):
		with patch.object(vendor_facts, "zillow_property",
						  return_value={"ok": True, "matched": False, "payload": None}):
			self.assertEqual(zillow._fetch("1 Main"), {})

	def test_warehouse_down_goes_direct(self):
		with patch.object(vendor_facts, "zillow_property", return_value=None), \
			 patch.object(zillow, "_request", return_value={"zpid": 3}) as direct:
			self.assertEqual(zillow._fetch("1 Main"), {"zpid": 3})
		direct.assert_called_once()


class Client(unittest.TestCase):
	def test_search_asks_for_the_whole_market(self):
		with patch.object(vendor_facts, "_get", return_value={}) as g:
			vendor_facts.zillow_search(45.0, -93.0, 0.5, "ForSale", include_pending=True)
		path, params = g.call_args.args
		self.assertEqual(path, "/zillow/search")
		self.assertEqual(params["full"], "true")
		self.assertEqual(params["include_pending"], "true")

	def test_many_resolves_the_url_once_on_the_calling_thread(self):
		with patch.object(vendor_facts, "_base_url", return_value="http://w") as b, \
			 patch.object(vendor_facts, "_get", return_value={"ok": True}) as g:
			out = vendor_facts.zillow_property_many(["a", "b", "c"])
		self.assertEqual(b.call_count, 1)
		self.assertEqual(len(out), 3)
		self.assertTrue(all(c.kwargs["base"] == "http://w" for c in g.call_args_list))


if __name__ == "__main__":
	unittest.main()

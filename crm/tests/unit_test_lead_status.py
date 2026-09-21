"""Lead status lifecycle: the watched registry and view-JSON cleaning."""

import json
import unittest

from crm.tests.frappe_shim import install

shim = install()

# The constant-only imports below drag in controllers and third-party libs
# (pypika, phonenumbers, crm.utils) that need a bench. Stub them: the tests
# only read the status tuples, and the registry must see the REAL constants.
import sys  # noqa: E402
import types  # noqa: E402

_pypika = types.ModuleType("pypika")
_pypika_fn = types.ModuleType("pypika.functions")
_pypika_fn.Function = lambda *a, **k: None
_pypika.functions = _pypika_fn
sys.modules.setdefault("pypika", _pypika)
sys.modules.setdefault("pypika.functions", _pypika_fn)

_crm_utils = types.ModuleType("crm.utils")
_crm_utils.sales_user_only = lambda fn: fn
sys.modules["crm.utils"] = _crm_utils

_dashboard = types.ModuleType("crm.api.dashboard")
_dashboard.get_leads_by_source = lambda *a, **k: None
sys.modules["crm.api.dashboard"] = _dashboard

from crm.api import lead_status as ls  # noqa: E402

# Statuses retired or renamed away — must never reappear in the code constants
# the guard derives from (2026-09-22: "Signed Contract" retired; "Needs
# Listing"/"Marketing to Buyer" were renamed long ago and silently unmatched).
RETIRED = ("Signed Contract", "Needs Listing", "Marketing to Buyer")

CURRENT_POST_CONTRACT = {
	"Photos & Lockbox In Progress",
	"Submit to Dispo",
	"Dispo Accepted",
	"Buyer Assigned",
}


class WatchRegistryTests(unittest.TestCase):
	def test_registry_derives_from_constants(self):
		watched = ls.watched_statuses()
		# The whole point of the guard: every name app logic matches by name
		# shows up here, with at least one usage site named.
		for name in (
			"New",
			"Called No Answer",
			"Follow Up",
			"Future Follow Up",
			"Underwriting",
			"Make Offer",
			"Contract Sent",
		):
			self.assertIn(name, watched)
			self.assertTrue(watched[name], f"{name} has no usage site")
		self.assertTrue(CURRENT_POST_CONTRACT <= set(watched))

	def test_retired_statuses_are_not_watched(self):
		watched = ls.watched_statuses()
		for name in RETIRED:
			self.assertNotIn(name, watched)

	def test_acq_and_dispo_do_not_overlap(self):
		from crm.api import leads_dashboard as ld

		self.assertFalse(set(ld.ACQ_STATUSES) & set(ld.DISPO_STATUSES))

	def test_post_contract_matches_dispo_gate(self):
		# The two sets are documented as kept in sync; pin it so a one-sided
		# edit fails here instead of on a rep's board.
		from crm.api import daily_standup as ds
		from crm.api import investorlift_ingest as il

		self.assertEqual(set(ds.POST_CONTRACT_STATUSES), set(il.DISPO_LEAD_STATUSES))


class CleanViewJsonTests(unittest.TestCase):
	def test_removes_named_and_soft_deleted_entries(self):
		raw = json.dumps(
			[
				{"name": "New"},
				{"name": "Signed Contract", "delete": True},
				{"name": "Follow Up"},
			]
		)
		new_raw, changed = ls._clean_view_json(raw, "Signed Contract")
		self.assertTrue(changed)
		self.assertEqual([c["name"] for c in json.loads(new_raw)], ["New", "Follow Up"])

	def test_no_match_is_unchanged(self):
		raw = json.dumps([{"name": "New"}])
		new_raw, changed = ls._clean_view_json(raw, "Gone")
		self.assertFalse(changed)
		self.assertEqual(new_raw, raw)

	def test_empty_and_garbage_are_safe(self):
		self.assertEqual(ls._clean_view_json(None, "New"), (None, False))
		self.assertEqual(ls._clean_view_json("", "New"), ("", False))
		self.assertEqual(ls._clean_view_json("not json", "New"), ("not json", False))


if __name__ == "__main__":
	unittest.main()

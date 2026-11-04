"""AVM training: bulk reruns go on their own queue with a trial setup, marks need a reason, team ARVs come from saved offers."""

import html
import json
import types
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import ai_pricer, ai_suggestions, avm_training as T  # noqa: E402
from crm.tests.unit_test_ai_pricer import _LogBase  # noqa: E402


def offer_comment(lead, arv, at="2026-10-30 10:00"):
	attr = html.escape(json.dumps({"lead": lead, "scenarios": [{"kind": "cash", "arv": arv}]}), quote=True)
	return types.SimpleNamespace(reference_name=lead, content=f'<div class="cash-offer" data-cash-offer="{attr}"></div>',
	                             creation=at, owner="german@groundworkpro.com")


class AvmTrainingTest(_LogBase):
	def setUp(self):
		super().setUp()
		self.store = {}
		shim.db.get_default.side_effect = lambda k, *a: self.store.get(k)
		shim.db.set_default = mock.MagicMock(side_effect=lambda k, v, *a: self.store.__setitem__(k, v))
		shim.db.doctypes.add(ai_suggestions.DOCTYPE)

	def tearDown(self):
		shim.db.get_default.side_effect = None
		shim.db.doctypes.discard(ai_suggestions.DOCTYPE)
		shim.get_all.side_effect = None
		super().tearDown()

	def test_team_arv_is_the_latest_saved_offer(self):
		shim.get_all.return_value = [offer_comment("L1", 210000, "2026-10-31"), offer_comment("L1", 190000), offer_comment("L2", 0)]
		self.assertEqual(T.team_arvs(["L1", "L2"]), {"L1": {"arv": 210000.0, "at": "2026-10-31", "by": "german@groundworkpro.com"}})
		shim.get_all.return_value = []

	def test_setup_checks_tools_and_ranges(self):
		self.assertEqual(T.clean_setup('{"4": {"tool": "jev", "note": " roads "}, "5": {"size_band": 0.2}, "x": 1}'),
		                 {"4": {"tool": "jev", "note": "roads"}, "5": {"size_band": 0.2}})
		for bad in ({"4": {"tool": "rm -rf"}}, {"7": {"max_mi": 9}}):
			with self.assertRaises(Exception):
				T.clean_setup(bad)

	def test_start_queues_on_bulk_with_trial_setup_and_logs_apart_from_the_lead_chat(self):
		with self.assertRaises(Exception):
			T.start("t", [])
		with self.assertRaises(Exception):
			T.start("t", [f"L{i}" for i in range(T.MAX_LEADS + 1)])
		name = T.start("Step 4 on Jev", '["CRM-LEAD-1", "CRM-LEAD-2"]', setup={"4": {"tool": "jev"}}, steps=[4, "99"])["name"]
		jobs = [json.loads(j) for j in self.r.lists[T.BULK]]
		self.assertNotIn(ai_pricer.QUEUE, self.r.lists)
		self.assertEqual([(j["kind"], j["lead"], j["setup"]) for j in jobs],
		                 [("decide", "CRM-LEAD-1", {"4": {"tool": "jev"}}), ("decide", "CRM-LEAD-2", {"4": {"tool": "jev"}})])
		run = json.loads(self.store[T.RUN + name])
		self.assertEqual((run["steps"], json.loads(self.store[T.INDEX])), ([4], [name]))
		self.assertTrue(all(r["chat"] == T.CHAT_PREFIX + name for r in self.rows))

	def test_results_log_once_and_compare_to_team(self):
		name = T.start("t", ["CRM-LEAD-1"], setup={"4": {"tool": "jev"}, "6": {"tool": "jev", "note": ""}, "5": {"size_band": 0.3}})["name"]
		job = json.loads(self.r.lists[T.BULK][0])["id"]
		self.r.kv[ai_pricer.JOB + job] = json.dumps({"status": "done", "decide": {"arv": 220000, "steps": [{"n": 4}], "comps_used": []}})

		def get_all(doctype, **kw):
			return [offer_comment("CRM-LEAD-1", 200000)] if doctype == "Comment" else []
		shim.get_all.side_effect = get_all
		out = T.run(name)
		row = out["rows"][0]
		self.assertEqual((row["status"], row["arv"], row["team_arv"], row["gap"]), ("done", 220000, 200000.0, 0.1))
		self.assertEqual(out["differs"], [4])
		self.assertEqual([r["role"] for r in self.rows], ["user", "ai"])
		self.assertEqual((out["summary"]["done"], out["summary"]["within_10"]), (1, 1))

	def test_wrong_mark_needs_reason_and_is_filed_for_review(self):
		name = T.start("t", ["CRM-LEAD-1"], setup={"4": {"tool": "jev"}})["name"]
		with self.assertRaises(Exception):
			T.mark(name, "CRM-LEAD-1", 4, "false", " ")
		with self.assertRaises(Exception):
			T.mark(name, "CRM-LEAD-9", 4, "true")
		T.mark(name, "CRM-LEAD-1", 4, "false", "The highway does split it")
		s = [r for r in self.rows if r.get("doctype") == ai_suggestions.DOCTYPE][-1]
		self.assertEqual((s["source"], s["step"], s["old_value"], s["new_value"], s["reason_type"], s["chat"], s["scope"]),
		                 ("Comps page", 4, "Jev", "Wrong", T.REASON_TYPE, T.CHAT_PREFIX + name, "Teach the agent"))

	def test_apply_makes_the_trial_setup_the_teams_with_a_logged_reason(self):
		name = T.start("t", ["CRM-LEAD-1"], setup={"4": {"tool": "jev", "note": "only highways"}})["name"]
		with self.assertRaises(Exception):
			T.apply_setup(name, "")
		with mock.patch.object(shim, "get_roles", return_value=["Sales User"]), \
				mock.patch.object(shim.session, "user", "german@groundworkpro.com"), self.assertRaises(Exception):
			T.apply_setup(name, "Rep tries the team setup")
		out = T.apply_setup(name, "Right on 9 of 10")
		self.assertEqual(out["setup"]["4"], {"tool": "jev", "note": "only highways"})
		self.assertEqual(len(out["lines"]), 2)  # tool and team rule; untouched steps log nothing
		self.assertIn("Changed Step 4 tool: Rule (code) -> Jev. Why: Right on 9 of 10 (AVM training run t)", out["lines"])


if __name__ == "__main__":
	unittest.main()

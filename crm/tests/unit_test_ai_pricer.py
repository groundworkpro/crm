"""Ask AI pricer: who sees it, what a job carries, and the Redis hand-off to agentbox."""

import json
import types
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import ai_pricer  # noqa: E402


class FakeRedis:
	"""Stands in for redis.Redis's unbound methods called on frappe.cache()."""

	def __init__(self):
		self.kv, self.lists = {}, {}

	def set(self, _r, k, v, ex=None):
		self.kv[k] = v

	def get(self, _r, k):
		return self.kv.get(k)

	def rpush(self, _r, k, v):
		self.lists.setdefault(k, []).append(v)

	def delete(self, _r, k):
		return 1 if self.kv.pop(k, None) is not None else 0


class _Base(unittest.TestCase):
	def setUp(self):
		self.r = FakeRedis()
		self.p = mock.patch.object(ai_pricer, "_redis", return_value=(None, self.r))
		self.p.start()
		shim.conf.clear()
		shim.session.user = "lance.johnson@groundworkpro.com"
		doc = types.SimpleNamespace(name="CRM-LEAD-1", get=lambda k, d=None: {"lead_name": "Donna"}.get(k, d))
		shim.get_doc.return_value = doc
		self.a = mock.patch.object(ai_pricer, "_address", return_value="618 NW 32nd St, Oklahoma City, OK 73118")
		self.a.start()

	def tearDown(self):
		self.p.stop()
		self.a.stop()


class AiPricerTest(_Base):
	def test_default_is_everyone_and_config_narrows_it(self):
		self.assertTrue(ai_pricer._allowed("german@groundworkpro.com"))
		shim.conf["ai_pricer_users"] = "lance.johnson@groundworkpro.com, exe.ortiz@groundworkpro.com"
		self.assertTrue(ai_pricer._allowed("exe.ortiz@groundworkpro.com"))
		self.assertFalse(ai_pricer._allowed("german@groundworkpro.com"))

	def test_status_hidden_for_others(self):
		shim.conf["ai_pricer_users"] = ["lance.johnson@groundworkpro.com"]
		shim.session.user = "german@groundworkpro.com"
		self.assertEqual(ai_pricer.status(), {"enabled": False})

	def test_offline_worker_refuses_up_front(self):
		with self.assertRaises(shim.ValidationError):
			ai_pricer.ask("CRM-LEAD-1")
		self.assertEqual(self.r.lists, {})

	def test_price_job_round_trip(self):
		self.r.kv[ai_pricer.SEEN] = "1"
		jid = ai_pricer.ask("CRM-LEAD-1")["job"]
		job = json.loads(self.r.lists[ai_pricer.QUEUE][0])
		self.assertEqual((job["kind"], job["address"], job["lead"]), ("price", "618 NW 32nd St, Oklahoma City, OK 73118", "CRM-LEAD-1"))
		self.assertEqual(ai_pricer.get_job(jid)["status"], "queued")
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "answer": {"arv": 445000}})
		self.assertEqual(ai_pricer.get_job(jid)["answer"]["arv"], 445000)

	def test_ask_needs_question_and_trims_history(self):
		with self.assertRaises(shim.ValidationError):
			ai_pricer.build_job("ask", "L", "addr", "  ")
		hist = [{"role": "ai", "text": "x"}] * 20 + ["junk"]
		job = ai_pricer.build_job("ask", "L", "addr", "full rehab?", hist)
		self.assertEqual(len(job["history"]), 11)

	def test_facts_cleaned_saved_and_sent(self):
		self.assertEqual(ai_pricer.clean_facts({"type": "single_family", "sqft": "1,240", "baths": "1.5", "beds": "",
		                                        "year_built": "abc", "color": "red"}),
		                 {"type": "single_family", "sqft": 1240, "baths": 1.5})
		self.assertEqual(ai_pricer.clean_facts({"type": "castle"}), {})
		self.r.kv[ai_pricer.SEEN] = "1"
		store = {}
		shim.db.get_default.side_effect = lambda k, *a: store.get(k)
		shim.db.set_default = lambda k, v: store.__setitem__(k, v)
		try:
			ai_pricer.ask("CRM-LEAD-1", facts=json.dumps({"type": "single_family"}))
			ai_pricer.ask("CRM-LEAD-1", kind="decide", facts={"sqft": 1100})
			ai_pricer.ask("CRM-LEAD-1")
		finally:
			shim.db.get_default.side_effect = None
		jobs = [json.loads(x) for x in self.r.lists[ai_pricer.QUEUE]]
		self.assertEqual(jobs[0]["subject_facts"], {"type": "single_family"})
		self.assertEqual(jobs[1]["subject_facts"], {"type": "single_family", "sqft": 1100})
		self.assertEqual(jobs[2]["subject_facts"], {"type": "single_family", "sqft": 1100})

	def test_comment_lists_why_low(self):
		html = ai_pricer.comment_html({"arv": 1, "confidence": "low", "would_change_it": "x",
		                               "low_why": [{"text": "Records say condo <b>"}]})
		self.assertIn("Why low confidence", html)
		self.assertIn("condo &lt;b&gt;", html)
		self.assertNotIn("Would change it", html)

	def test_get_job_rejects_bad_ids(self):
		with self.assertRaises(shim.ValidationError):
			ai_pricer.get_job("../etc")
		self.assertEqual(ai_pricer.get_job("a" * 32)["status"], "error")


class _LogBase(_Base):
	"""The CRM AI Chat Message doctype exists; inserted docs are collected in self.rows."""

	def setUp(self):
		super().setUp()
		self.rows = []
		doc = types.SimpleNamespace(name="CRM-LEAD-1", get=lambda k, d=None: {"lead_name": "Donna"}.get(k, d))

		def get_doc(arg, *a):
			if isinstance(arg, dict):
				return types.SimpleNamespace(insert=lambda **k: self.rows.append(arg))
			return doc

		shim.get_doc.side_effect = get_doc
		shim.db.doctypes.add(ai_pricer.CHAT_DOCTYPE)
		self.r.kv[ai_pricer.SEEN] = "1"

	def tearDown(self):
		shim.get_doc.side_effect = None
		shim.db.doctypes.discard(ai_pricer.CHAT_DOCTYPE)
		super().tearDown()


class AiChatLogTest(_LogBase):
	"""Both sides of the chat land in CRM AI Chat Message once the doctype exists."""

	def test_question_and_reply_logged_once(self):
		chat = "c" * 32
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "Use 5308 S Peoria, it's pending at 179,900", chat=chat)["job"]
		self.assertEqual(len(self.rows), 1)
		self.assertEqual((self.rows[0]["role"], self.rows[0]["chat"], self.rows[0]["user"]), ("user", chat, "lance.johnson@groundworkpro.com"))
		self.assertIn("5308 S Peoria", self.rows[0]["text"])
		ai_pricer.get_job(jid)  # still queued: nothing new
		self.assertEqual(len(self.rows), 1)
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "text": "Pending isn't sold."})
		ai_pricer.get_job(jid)
		ai_pricer.get_job(jid)  # polled again: no duplicate
		self.assertEqual([r["role"] for r in self.rows], ["user", "ai"])
		self.assertEqual((self.rows[1]["text"], self.rows[1]["chat"], self.rows[1]["job"]), ("Pending isn't sold.", chat, jid))

	def test_price_card_logged_as_summary_and_json(self):
		jid = ai_pricer.ask("CRM-LEAD-1", chat="bad id!")["job"]
		self.assertEqual(self.rows[0]["text"], "Price this house")
		self.assertEqual(len(self.rows[0]["chat"]), 32)  # junk chat id replaced
		answer = {"arv": 165000, "arv_low": 150000, "arv_high": 185000, "confidence": "med", "offers": {"offer": 43550, "repairs": 46950}, "comps_used": [{"address": "797 Seward", "price": 170000}]}
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "answer": answer})
		ai_pricer.get_job(jid)
		self.assertIn("ARV $165,000", self.rows[1]["text"])
		self.assertIn("797 Seward $170,000", self.rows[1]["text"])
		self.assertEqual(json.loads(self.rows[1]["answer"])["offers"]["offer"], 43550)

	def test_errors_logged_and_no_doctype_writes_nothing(self):
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "why?")["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "error", "error": "model timed out"})
		ai_pricer.get_job(jid)
		self.assertEqual((self.rows[1]["error"], self.rows[1]["text"]), (1, "model timed out"))
		shim.db.doctypes.discard(ai_pricer.CHAT_DOCTYPE)
		ai_pricer.ask("CRM-LEAD-1", "ask", "again?")
		self.assertEqual(len(self.rows), 2)

	def test_chat_history_returns_latest_chat_and_pending_job(self):
		R = shim._dict
		card = {"arv": 165000, "offers": {"offer": 43550}}
		rows = [
			R(role="user", kind="price", job="j1", text="Price this house", answer=None, error=0),
			R(role="ai", kind="price", job="j1", text="ARV $165,000", answer=json.dumps(card), error=0),
			R(role="user", kind="ask", job="j2", text="full rehab?", answer=None, error=0),
			R(role="ai", kind="ask", job="j2", text="model timed out", answer=None, error=1),
			R(role="user", kind="ask", job="j3", text="and now?", answer=None, error=0),
		]
		self.r.kv[ai_pricer.JOB + "j3"] = json.dumps({"status": "running"})
		shim.get_all.side_effect = [[R(chat="c" * 32)], rows]
		try:
			out = ai_pricer.chat_history("CRM-LEAD-1")
		finally:
			shim.get_all.side_effect = None
		self.assertEqual(out["chat"], "c" * 32)
		self.assertEqual(out["pending"], "j3")
		self.assertEqual(out["messages"][1]["result"]["offers"]["offer"], 43550)
		self.assertTrue(out["messages"][3]["error"])
		self.assertEqual([m["role"] for m in out["messages"]], ["user", "ai", "user", "ai", "user"])

	def test_chat_history_empty_without_rows_or_doctype(self):
		self.assertEqual(ai_pricer.chat_history("CRM-LEAD-1")["messages"], [])
		shim.db.doctypes.discard(ai_pricer.CHAT_DOCTYPE)
		self.assertIsNone(ai_pricer.chat_history("CRM-LEAD-1")["chat"])


class AiSaveCommentTest(_LogBase):
	""""Save as comment" posts the stored AI reply on the lead's timeline."""

	def _comments(self):
		return [r for r in self.rows if r.get("doctype") == "Comment"]

	def test_price_card_from_transcript(self):
		R = shim._dict
		card = {"arv": 165000, "arv_low": 150000, "arv_high": 185000, "confidence": "med", "repair_tier": "kitchen_baths",
				"subject_note": "3/2 <brick>", "comps_used": [{"address": "797 Seward", "price": 170000, "why": "same block"}],
				"offers": {"classic": 68550, "two_x": 43550, "recommend": "two_x", "repairs": 46950, "fee": 10000}}
		rows = [R(role="user", text="Price this house", answer=None, error=0), R(role="ai", text="ARV", answer=json.dumps(card), error=0)]
		shim.get_all.return_value = rows
		try:
			ai_pricer.save_comment("CRM-LEAD-1", "a" * 32)
		finally:
			shim.get_all.return_value = []
		(c,) = self._comments()
		self.assertEqual((c["reference_doctype"], c["reference_name"], c["comment_type"]), ("CRM Lead", "CRM-LEAD-1", "Comment"))
		for bit in ("ARV $165,000", "$150,000", "medium confidence", "797 Seward $170,000 — same block", "use 2× repairs", "Repairs $46,950", "&lt;brick&gt;"):
			self.assertIn(bit, c["content"])
		self.assertNotIn("Asked:", c["content"])
		self.assertTrue(c["content"].startswith('<div data-ai-comping="1"><p><b>AI comping tool analysis</b></p>'))

	def test_text_reply_falls_back_to_redis(self):
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "full rehab?")["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "text": "Then offer\n$30k less."})
		ai_pricer.save_comment("CRM-LEAD-1", jid)
		(c,) = self._comments()
		self.assertIn("Then offer<br>$30k less.", c["content"])

	def test_unfinished_or_bad_job_refused(self):
		with self.assertRaises(shim.ValidationError):
			ai_pricer.save_comment("CRM-LEAD-1", "../x")
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "why?")["job"]
		with self.assertRaises(shim.ValidationError):
			ai_pricer.save_comment("CRM-LEAD-1", jid)  # still queued
		self.assertEqual(self._comments(), [])

	def test_question_shown_when_asked(self):
		html = ai_pricer.comment_html(None, "Pending isn't sold.", "Use <5308 S Peoria>?")
		self.assertIn("Asked: Use &lt;5308 S Peoria&gt;?", html)


class AiCorrectionTest(_LogBase):
	"""A follow-up the worker flags as a correction is filed as a suggested pricer fix."""

	def setUp(self):
		super().setUp()
		from crm.api import ai_suggestions

		self.sug = ai_suggestions
		shim.db.doctypes.add(ai_suggestions.DOCTYPE)

	def tearDown(self):
		shim.db.doctypes.discard(self.sug.DOCTYPE)
		super().tearDown()

	def _suggestions(self):
		return [r for r in self.rows if r.get("doctype") == self.sug.DOCTYPE]

	def test_flagged_reply_files_suggestion(self):
		chat = "d" * 32
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "408 Forest Hills isn't a flip, kitchen is original", chat=chat)["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps(
			{"status": "done", "text": "Then it's an as-is sale.", "correction": "Check photos before calling a comp updated."}
		)
		ai_pricer.get_job(jid)
		ai_pricer.get_job(jid)
		(s,) = self._suggestions()
		self.assertEqual((s["source"], s["status"], s["lead"], s["chat"], s["job"]), ("Chat", "New", "CRM-LEAD-1", chat, jid))
		self.assertIn("isn't a flip", s["correction"])
		self.assertEqual(s["lesson"], "Check photos before calling a comp updated.")
		self.assertEqual(s["context"], "Then it's an as-is sale.")
		self.assertIn("618 NW 32nd St", s["address"])
		ai_row = [r for r in self.rows if r.get("doctype") == ai_pricer.CHAT_DOCTYPE and r["role"] == "ai"]
		self.assertNotIn("question", ai_row[0])

	def test_plain_question_or_error_files_nothing(self):
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "how old is comp 2?")["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "text": "1967."})
		ai_pricer.get_job(jid)
		jid = ai_pricer.ask("CRM-LEAD-1", "ask", "repairs are 70k")["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "error", "error": "x", "correction": "y"})
		ai_pricer.get_job(jid)
		self.assertEqual(self._suggestions(), [])



class CompsPageChangeTest(AiCorrectionTest):
	"""The 13-step run and a rep's override of what it filled, with the reason."""

	def test_decide_job_carries_page_and_logs_summary(self):
		jid = ai_pricer.ask("CRM-LEAD-1", "decide", page="Repairs $30/sf")["job"]
		job = json.loads(self.r.lists[ai_pricer.QUEUE][0])
		self.assertEqual((job["kind"], job["page"]), ("decide", "Repairs $30/sf"))
		self.assertEqual(self.rows[0]["text"], "Run the 13 steps")
		d = {"arv": 210000, "arv_low": 200000, "arv_high": 230000, "confidence": "med", "repairs": {"per_sf": 30},
		     "comps_used": [{"address": "2503 E 24th St", "role": "anchor", "adjusted": 211395}], "steps": []}
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "decide": d})
		ai_pricer.get_job(jid)
		self.assertIn("ARV $210,000", self.rows[1]["text"])
		self.assertIn("2503 E 24th St (anchor) $211,395", self.rows[1]["text"])
		row = types.SimpleNamespace(role="ai", text="x", job=jid, kind="decide", error=0, answer=self.rows[1]["answer"])
		self.assertEqual(ai_pricer._history_row(row)["decide"]["arv"], 210000)

	def test_price_card_keeps_its_13_steps_for_the_rail(self):
		jid = ai_pricer.ask("CRM-LEAD-1", "price")["job"]
		d = {"arv": 210000, "steps": [{"n": 1, "title": "Subject", "text": "3 bd"}]}
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "answer": {"arv": 210000}, "decide": d})
		ai_pricer.get_job(jid)
		row = types.SimpleNamespace(role="ai", text="x", job=jid, kind="price", error=0, answer=self.rows[1]["answer"])
		h = ai_pricer._history_row(row)
		self.assertEqual(h["result"], {"arv": 210000})
		self.assertEqual(h["steps_run"]["steps"][0]["text"], "3 bd")
		self.assertNotIn("decide", h)

	def test_change_needs_reason_and_lands_in_chat_and_suggestions(self):
		with self.assertRaises(Exception):
			ai_pricer.log_change("CRM-LEAD-1", "  ", "Repairs", "$30/sf", "$45/sf", step=12)
		self.assertEqual(self.rows, [])
		r = ai_pricer.log_change("CRM-LEAD-1", "Roof is shot", "Repairs", "$30/sf", "$45/sf", step="12",
		                         reason_type="Photos show it", scope="Teach the agent", chat="e" * 32)
		chat = [x for x in self.rows if x.get("doctype") == ai_pricer.CHAT_DOCTYPE]
		(s,) = self._suggestions()
		self.assertEqual(r["text"], "Changed Repairs: $30/sf -> $45/sf. Why: Photos show it: Roof is shot")
		self.assertEqual((chat[0]["kind"], chat[0]["text"], chat[0]["chat"]), ("change", r["text"], "e" * 32))
		self.assertEqual((s["source"], s["step"], s["old_value"], s["new_value"], s["scope"], s["correction"]),
		                 ("Comps page", 12, "$30/sf", "$45/sf", "Teach the agent", "Roof is shot"))
		self.assertTrue(s["lesson"].startswith("Step 12: Changed Repairs"))

	def test_step_change_saves_setup_logs_and_rides_on_jobs(self):
		store = {}
		shim.db.get_default.side_effect = lambda k, *a: store.get(k)
		shim.db.set_default = mock.MagicMock(side_effect=lambda k, v, *a: store.__setitem__(k, v))
		try:
			with self.assertRaises(Exception):
				ai_pricer.save_step(12, " ", tool="code")
			with self.assertRaises(Exception):
				ai_pricer.save_step(14, "x", tool="code")
			self.assertEqual(ai_pricer.save_step(4, "already code", tool="code")["lines"], [])
			with self.assertRaises(Exception):
				ai_pricer.save_step(12, "x", tool="rm -rf")
			r = ai_pricer.save_step("12", "No photos: be safe", tool="model:anthropic/claude-sonnet-5-5",
			                        note="Pre-1950: full rehab", lead="CRM-LEAD-1", chat="e" * 32)
			self.assertEqual(r["setup"]["12"], {"tool": "model:anthropic/claude-sonnet-5-5", "note": "Pre-1950: full rehab"})
			self.assertEqual(r["lines"][0], "Changed Step 12 tool: Jev -> anthropic/claude-sonnet-5-5. Why: No photos: be safe")
			self.assertEqual(len(self._suggestions()), 2)
			self.assertEqual(ai_pricer.save_step(12, "again", note="Pre-1950: full rehab")["lines"], [])
			self.assertEqual(ai_pricer.save_step(9, "same", tool="jev", note="", lead="CRM-LEAD-1")["lines"], [])
			ai_pricer.save_step(5, "tighter", size_band=0.2)
			r = ai_pricer.save_step(4, "Roads here do not split buyers", tool="jev", note="Side streets never count",
			                        lead="CRM-LEAD-1")
			self.assertEqual(r["lines"][0], "Changed Step 4 tool: Rule (code) -> Jev. Why: Roads here do not split buyers")
			ai_pricer.ask("CRM-LEAD-1", "decide")
			job = json.loads(self.r.lists[ai_pricer.QUEUE][-1])
			self.assertEqual((job["setup"]["12"]["tool"], job["setup"]["5"]["size_band"], job["setup"]["4"]["tool"]),
			                 ("model:anthropic/claude-sonnet-5-5", 0.2, "jev"))
			ai_pricer.ask("CRM-LEAD-1")  # Ask AI's price card runs the 13 steps too, with the same team setup
			job = json.loads(self.r.lists[ai_pricer.QUEUE][-1])
			self.assertEqual((job["kind"], job["setup"]["12"]["tool"], job["setup"]["5"]["size_band"]),
			                 ("price", "model:anthropic/claude-sonnet-5-5", 0.2))
		finally:
			shim.db.get_default.side_effect = None


	def test_rep_step_change_is_a_suggestion_for_their_run_of_this_house_only(self):
		store = {}
		shim.db.get_default.side_effect = lambda k, *a: store.get(k)
		shim.db.set_default = mock.MagicMock(side_effect=lambda k, v, *a: store.__setitem__(k, v))
		shim.session.user = "german@groundworkpro.com"
		try:
			with mock.patch.object(shim, "get_roles", return_value=["Sales User"]):
				with self.assertRaises(Exception):  # off a lead it would have nowhere to apply
					ai_pricer.save_step(12, "be safe", tool="code")
				r = ai_pricer.save_step(12, "Roof is shot", note="Full rehab", lead="CRM-LEAD-1", chat="f" * 32)
				self.assertFalse(r["team_wide"])
				self.assertEqual(r["setup"]["12"]["note"], "Full rehab")
				self.assertNotIn(ai_pricer.SETUP_KEY, store)  # the team setup is untouched
				sug = self._suggestions()[-1]
				self.assertEqual((sug["step"], sug["scope"]), (12, "This house only"))
				self.assertEqual(ai_pricer.step_setup("CRM-LEAD-1", detail=1)["mine"], ["12"])
				self.assertEqual(ai_pricer.step_setup(), {})
				ai_pricer.ask("CRM-LEAD-1", "decide")  # their rerun follows it
				self.assertEqual(json.loads(self.r.lists[ai_pricer.QUEUE][-1])["setup"]["12"]["note"], "Full rehab")
				ai_pricer.ask("CRM-LEAD-2", "decide")  # another house does not
				self.assertNotIn("12", json.loads(self.r.lists[ai_pricer.QUEUE][-1])["setup"])
			shim.session.user = "exe.ortiz@groundworkpro.com"  # nor does anyone else's run of the same house
			ai_pricer.ask("CRM-LEAD-1", "decide")
			self.assertNotIn("12", json.loads(self.r.lists[ai_pricer.QUEUE][-1])["setup"])
			shim.session.user = "german@groundworkpro.com"
			self.assertEqual(ai_pricer.clear_run_setup("CRM-LEAD-1")["mine"], [])
			ai_pricer.ask("CRM-LEAD-1", "decide")
			self.assertNotIn("12", json.loads(self.r.lists[ai_pricer.QUEUE][-1])["setup"])
		finally:
			shim.db.get_default.side_effect = None


class AiCompsListTest(_LogBase):
	"""The comps page's "Ask AI's comps" list: from ?ai=<job>, else the latest run on the lead."""

	CARD = {"arv": 210000, "arv_low": 200000, "arv_high": 230000, "confidence": "med",
	        "comps_used": [{"address": "2503 E 24th St", "price": 205000, "why": "anchor, 0.20 mi"}]}
	STEPS = {"arv": 210000, "confidence": "med", "comps_used": [
		{"address": "2503 E 24th St", "price": 205000, "adjusted": 211395, "role": "anchor",
		 "sqft": 1200, "dist_mi": 0.2, "adjustments": ["+$6k size"], "cond_p": {"a": 1}},
		{"address": "", "price": 1}],
		"rows": [{"address": "2503 E 24th St", "lat": 35.1, "lng": -97.5, "step": "used", "beds": 3,
		          "baths": 1.5, "year_built": 1950, "kind": "sold", "sqft": 999, "why": "anchor"}]}

	def test_job_from_redis_merges_the_steps_comps(self):
		jid = "a" * 32
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "answer": self.CARD, "decide": self.STEPS})
		out = ai_pricer.ai_comps("CRM-LEAD-1", jid)
		self.assertEqual((out["job"], out["kind"], out["arv"]), (jid, "price", 210000))
		self.assertEqual(len(out["comps"]), 1)
		c = out["comps"][0]
		self.assertEqual((c["role"], c["adjusted"], c["lat"], c["lng"], c["why"]), ("anchor", 211395, 35.1, -97.5, "anchor, 0.20 mi"))
		self.assertNotIn("cond_p", c)
		# Facts the comps list lacks come from the trace row; the list's own values win.
		self.assertEqual((c["beds"], c["baths"], c["year_built"], c["kind"], c["sqft"]), (3, 1.5, 1950, "sold", 1200))
		self.assertEqual(c["adjustments"], ["+$6k size"])

	def test_latest_logged_run_by_anyone_when_no_job(self):
		R = shim._dict
		rows = [R(job="j2", kind="decide", user="dennis@groundworkpro.com", answer="not json", creation="2026-11-05 10:00"),
		        R(job="j1", kind="price", user="dennis@groundworkpro.com", answer=json.dumps(self.CARD), creation="2026-11-04 09:00")]
		shim.get_all.side_effect = [rows]
		shim.db.get_value.return_value = "Dennis"
		try:
			out = ai_pricer.ai_comps("CRM-LEAD-1")
		finally:
			shim.get_all.side_effect = None
			shim.db.get_value.return_value = None
		self.assertEqual((out["job"], out["user"], out["kind"]), ("j1", "Dennis", "price"))
		self.assertEqual(out["comps"], [{"address": "2503 E 24th St", "price": 205000, "why": "anchor, 0.20 mi"}])

	def test_expired_job_falls_back_and_nothing_is_none(self):
		shim.get_all.return_value = []
		self.assertIsNone(ai_pricer.ai_comps("CRM-LEAD-1", "b" * 32))
		shim.db.doctypes.discard(ai_pricer.CHAT_DOCTYPE)
		self.assertIsNone(ai_pricer.ai_comps("CRM-LEAD-1"))
		with self.assertRaises(shim.ValidationError):
			ai_pricer.ai_comps("CRM-LEAD-1", "../x")


if __name__ == "__main__":
	unittest.main()


class AiPropertySubjectTest(_LogBase):
	"""Dennis, 2026-11-05: Ask AI on a lead's additional properties (CRM Property, PROP-)."""

	def setUp(self):
		super().setUp()
		from crm.api import comps, properties

		self.flags = []
		lead = types.SimpleNamespace(name="CRM-LEAD-1", get=lambda k, d=None: {"lead_name": "Mark"}.get(k, d))
		prop = types.SimpleNamespace(name="PROP-00022", notes="Vacant duplex", get=lambda k, d=None: {"notes": "Vacant duplex"}.get(k, d))

		def get_doc(arg, *a):
			if isinstance(arg, dict):
				flags = types.SimpleNamespace(ignore_links=False)
				self.flags.append((arg, flags))
				return types.SimpleNamespace(insert=lambda **k: self.rows.append(arg), flags=flags)
			return prop if (a and str(a[0]).startswith("PROP-")) else lead

		shim.get_doc.side_effect = get_doc
		shim.db.values[("CRM Property", "PROP-00022")] = 1
		shim.db.values[("CRM Lead", "CRM-LEAD-1")] = 1
		self.g = mock.patch.object(comps, "_guard")
		self.guard = self.g.start()
		self.l = mock.patch.object(properties, "linked_lead_and_street", return_value=("CRM-LEAD-1", "112 Highland Ave"))
		self.l.start()
		shim.has_permission.reset_mock()

	def tearDown(self):
		self.g.stop()
		self.l.stop()
		shim.db.values.pop(("CRM Property", "PROP-00022"), None)
		shim.db.values.pop(("CRM Lead", "CRM-LEAD-1"), None)
		super().tearDown()

	def test_property_is_priced_and_logged_under_its_own_name(self):
		ai_pricer.ask("PROP-00022")
		job = json.loads(self.r.lists[ai_pricer.QUEUE][0])
		self.assertEqual(job["lead"], "PROP-00022")
		self.guard.assert_called()  # the properties pages' sales-role check, not CRM Lead's
		shim.has_permission.assert_not_called()
		(row, flags), = [(r, f) for r, f in self.flags if r.get("doctype") == ai_pricer.CHAT_DOCTYPE]
		self.assertEqual(row["lead"], "PROP-00022")
		self.assertTrue(flags.ignore_links)

	def test_missing_property_refused(self):
		with self.assertRaises(Exception):
			ai_pricer.ask("PROP-99999")

	def test_ask_gets_the_property_note_and_the_sellers_lead_notes(self):
		shim.get_all.return_value = [shim._dict(title="Call", content="<p>Seller wants 90k for both</p>", creation="2026-11-04 10:00")]
		import re

		try:
			with mock.patch.object(shim.utils, "strip_html", lambda h: re.sub("<[^>]+>", "", h), create=True):
				ai_pricer.ask("PROP-00022", "ask", "what about the duplex?")
		finally:
			shim.get_all.return_value = []
		job = json.loads(self.r.lists[ai_pricer.QUEUE][0])
		self.assertIn("additional property", job["context"])
		self.assertIn("Vacant duplex", job["context"])
		self.assertIn("Seller wants 90k for both", job["context"])

	def test_lead_info_lists_the_sellers_houses(self):
		from crm.api import properties

		with mock.patch.object(properties, "_available", return_value=True), \
				mock.patch.object(properties, "_lead_supported", return_value=True):
			shim.get_all.return_value = ["PROP-00022"]
			try:
				info = ai_pricer.lead_info("PROP-00022")
			finally:
				shim.get_all.return_value = []
		self.assertEqual((info["kind"], info["lead"]), ("property", "CRM-LEAD-1"))
		self.assertEqual([(h["name"], h["kind"]) for h in info["houses"]], [("CRM-LEAD-1", "lead"), ("PROP-00022", "property")])

	def test_comment_goes_on_the_sellers_lead_naming_the_house(self):
		jid = ai_pricer.ask("PROP-00022", "ask", "rent?")["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "text": "About $1,400."})
		out = ai_pricer.save_comment("PROP-00022", jid)
		(c,) = [r for r in self.rows if r.get("doctype") == "Comment"]
		self.assertEqual((c["reference_doctype"], c["reference_name"]), ("CRM Lead", "CRM-LEAD-1"))
		self.assertIn("For 112 Highland Ave (PROP-00022)", c["content"])
		self.assertEqual(out["on"], "CRM-LEAD-1")

	def test_unlinked_property_keeps_its_comment(self):
		from crm.api import properties

		jid = ai_pricer.ask("PROP-00022", "ask", "rent?")["job"]
		self.r.kv[ai_pricer.JOB + jid] = json.dumps({"status": "done", "text": "About $1,400."})
		with mock.patch.object(properties, "linked_lead_and_street", return_value=("", "112 Highland Ave")):
			out = ai_pricer.save_comment("PROP-00022", jid)
		(c,) = [r for r in self.rows if r.get("doctype") == "Comment"]
		self.assertEqual((c["reference_doctype"], c["reference_name"]), ("CRM Property", "PROP-00022"))
		self.assertIsNone(out["on"])

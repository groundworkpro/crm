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


if __name__ == "__main__":
	unittest.main()

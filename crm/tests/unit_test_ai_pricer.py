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


class AiChatLogTest(_Base):
	"""Both sides of the chat land in CRM AI Chat Message once the doctype exists."""

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


if __name__ == "__main__":
	unittest.main()

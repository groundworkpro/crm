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


class AiPricerTest(unittest.TestCase):
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

	def test_default_rollout_is_lance_and_exe(self):
		self.assertTrue(ai_pricer._allowed("exe.ortiz@groundworkpro.com"))
		self.assertFalse(ai_pricer._allowed("german@groundworkpro.com"))
		shim.conf["ai_pricer_users"] = "*"
		self.assertTrue(ai_pricer._allowed("german@groundworkpro.com"))

	def test_status_hidden_for_others(self):
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


if __name__ == "__main__":
	unittest.main()

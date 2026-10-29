"""Suggested pricer fixes: Disagree feedback is filed, reviewers pull and mark them."""

import types
import unittest
from unittest import mock

from crm.tests.frappe_shim import install

shim = install()

from crm.api import ai_suggestions, arv_feedback  # noqa: E402


class _Base(unittest.TestCase):
	def setUp(self):
		self.rows = []

		def get_doc(arg, *a):
			if isinstance(arg, dict):
				doc = types.SimpleNamespace(name=f"S{len(self.rows) + 1}")
				doc.insert = lambda **k: (self.rows.append(arg), doc)[1]
				return doc
			return types.SimpleNamespace(check_permission=lambda *a: None)

		shim.get_doc.side_effect = get_doc
		shim.db.doctypes.add(ai_suggestions.DOCTYPE)
		shim.conf.clear()

	def tearDown(self):
		shim.get_doc.side_effect = None
		shim.db.doctypes.discard(ai_suggestions.DOCTYPE)
		shim.session.user = "lance.johnson@groundworkpro.com"


class RecordTest(_Base):
	def test_row_shape_and_bad_numbers(self):
		row = ai_suggestions.build_row("Disagree", "d@x", "  comp 2 is a flip ", ai_arv="185000", rep_arv="abc")
		self.assertEqual((row["status"], row["correction"], row["ai_arv"], row["rep_arv"]), ("New", "comp 2 is a flip", 185000.0, None))
		with self.assertRaises(ValueError):
			ai_suggestions.build_row("Other", "d@x", "x")

	def test_nothing_without_doctype_or_text(self):
		self.assertIsNone(ai_suggestions.record("Chat", "d@x", "   "))
		shim.db.doctypes.discard(ai_suggestions.DOCTYPE)
		self.assertIsNone(ai_suggestions.record("Chat", "d@x", "fix"))
		self.assertEqual(self.rows, [])

	def test_write_failure_is_swallowed(self):
		shim.get_doc.side_effect = RuntimeError("db down")
		self.assertIsNone(ai_suggestions.record("Chat", "d@x", "fix"))


class DisagreeTest(_Base):
	def test_rep_disagree_files_suggestion_and_still_sends_dm(self):
		shim.session.user = "dennis@groundworkpro.com"
		with mock.patch("crm.api.talk.dm_channel_between", return_value="dm-1", create=True), \
			mock.patch.object(arv_feedback, "_site_url", return_value="https://crm.x"):
			r = arv_feedback.send(ai_arv=185000, feedback="Comp 2 is a flip", address="618 NW 32nd St", user_arv=160000, details="comp 1")
		kinds = [row["doctype"] for row in self.rows]
		self.assertIn(ai_suggestions.DOCTYPE, kinds)
		self.assertEqual(len(kinds), 2)  # suggestion + Talk message
		s = next(row for row in self.rows if row["doctype"] == ai_suggestions.DOCTYPE)
		self.assertEqual((s["source"], s["user"], s["rep_arv"], s["context"]), ("Disagree", "dennis@groundworkpro.com", 160000.0, "comp 1"))
		self.assertTrue(r["suggestion"])

	def test_owner_disagree_files_suggestion_without_dm(self):
		shim.session.user = arv_feedback.DEFAULT_RECIPIENT
		r = arv_feedback.send(ai_arv=185000, feedback="too high", address="1 Main St")
		self.assertEqual([row["doctype"] for row in self.rows], [ai_suggestions.DOCTYPE])
		self.assertIsNone(r["channel"])


class ReviewTest(_Base):
	def test_only_owner_admin_or_sysmanager_reviews(self):
		shim.session.user = "dennis@groundworkpro.com"
		with mock.patch.object(shim, "get_roles", return_value=["Sales User"]):
			with self.assertRaises(shim.PermissionError):
				ai_suggestions.export()
			shim.session.user = arv_feedback.DEFAULT_RECIPIENT
			self.assertEqual(ai_suggestions.export(), [])

	def test_set_status_validates(self):
		with self.assertRaises(shim.ValidationError):
			ai_suggestions.set_status("S1", "Maybe")
		shim.db.values[(ai_suggestions.DOCTYPE, "S1")] = 1
		self.assertEqual(ai_suggestions.set_status('["S1","S9"]', "Added to skill", "in SKILL.md")["updated"], ["S1"])


if __name__ == "__main__":
	unittest.main()

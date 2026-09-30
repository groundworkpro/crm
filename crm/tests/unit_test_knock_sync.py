"""crm.api.knock.sync: Knock's one-way text mirror.

What must hold: a retry never duplicates a text or a lead; an older snapshot
never overwrites a newer one; an unclear phone match refuses rather than
guessing; and only `lead_owner` changes on the lead (no status, notes, or
`_assign`). Runs against a small in-memory stand-in for the CRM tables.
"""

import contextlib
import dataclasses
import json
import unittest
from itertools import count

from crm.tests.frappe_shim import install

shim = install()

from crm.api import knock  # noqa: E402

SELLER = "+15125550199"


def thread(revision=1, texts=None, **extra):
	p = {
		"knock_id": "tel" + SELLER, "revision": revision, "phone": SELLER,
		"owner_email": "exe.ortiz@groundworkpro.com", "crm_id": "",
		"first_name": "Pat", "last_name": "Seller", "address": "10 Main St",
		"city": "Austin", "state": "TX", "zip": "78701",
		"messages": texts if texts is not None else [
			{"id": "g1", "body": "Is this Lance?", "speaker": "Pat Seller", "received": True, "at": "9:00", "unix": 1790000000},
			{"id": "tel#3", "body": "This is Exe", "speaker": "Exe", "received": False, "at": "9:01", "unix": 1790000060},
		],
	}
	p.update(extra)
	return p


class Doc:
	def __init__(self, db, fields):
		self.__dict__.update(fields)
		self._db = db
		self.saves = 0

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def check_permission(self, perm):
		pass

	def insert(self, ignore_permissions=False, set_name=None):
		self.name = set_name or f"CRM-LEAD-2026-{next(self._db.ids):05d}"
		self._db.put(self)
		return self

	def save(self, ignore_permissions=False):
		self.saves += 1
		self._db.put(self)


class FakeCRM:
	"""Documents by (doctype, name), wired into the shim's get_doc / exists."""

	def __init__(self):
		self.docs = {}
		self.ids = count(100)
		self.locks = []
		shim.db.values = {}
		shim.get_doc.side_effect = self.get_doc
		shim.db.get_value.side_effect = lambda doctype, name, field: 1 if doctype == "User" else None
		shim.db.sql.side_effect = None
		shim.db.sql.return_value = []
		shim.db.commit.reset_mock()
		shim.db.rollback.reset_mock()
		shim.has_permission.return_value = True
		shim.session.user = "knock-api@groundworkpro.com"
		shim.utils.get_system_timezone = lambda: "America/Chicago"

		@contextlib.contextmanager
		def lock(key, timeout=None, blocking_timeout=None):
			self.locks.append(key)
			yield

		shim._cache.lock = lock

	def put(self, doc):
		self.docs[(doc.doctype, doc.name)] = doc
		shim.db.values[(doc.doctype, doc.name)] = doc

	def get_doc(self, arg, name=None):
		if isinstance(arg, dict):
			return Doc(self, arg)
		return self.docs[(arg, name)]

	def lead(self, name, phone, address="10 Main St", owner="lance.johnson@groundworkpro.com"):
		d = Doc(self, {"doctype": "CRM Lead", "name": name, "phone": phone, "mobile_no": "",
			"property_address": address, "lead_owner": owner, "status": "Qualified", "notes": "keep me"})
		self.put(d)
		return d

	def of(self, doctype):
		return [d for (t, _), d in self.docs.items() if t == doctype]


def row(name, address):
	return shim._dict(name=name, property_address=address)


class Payload(unittest.TestCase):
	def test_rejects_what_it_cannot_trust(self):
		bad = [
			thread(phone="512-555-0199"),
			thread(revision=0),
			thread(revision=True),
			thread(owner_email="someone@gmail.com"),
			thread(knock_id=""),
			thread(texts=[{"id": "a", "body": "x", "speaker": "s", "received": "yes", "at": "1"}]),
			thread(texts=[{"id": "a", "body": "x", "speaker": "s", "received": True, "at": "1"}] * 2),
		]
		for p in bad:
			with self.assertRaises(ValueError, msg=p):
				knock.parse_payload(p)
		self.assertEqual(len(knock.parse_payload(json.dumps(thread())).texts), 2)

	def test_names_are_stable_and_distinct(self):
		a = knock.stable_name("text", "t1", "m1")
		self.assertEqual(a, knock.stable_name("text", "t1", "m1"))
		self.assertNotEqual(a, knock.stable_name("text", "t1", "m2"))
		self.assertNotEqual(a, knock.stable_name("text", "t2", "m1"))
		self.assertNotEqual(knock.stable_name("text", "t1m", "1"), knock.stable_name("text", "t1", "m1"))

	def test_phone_digits(self):
		for raw in ("(512) 555-0199", "+1 512.555.0199", "15125550199", "5125550199"):
			self.assertEqual(knock.phone_digits(raw), "5125550199")
		self.assertEqual(knock.phone_digits("555-0199"), "")

	def test_content_is_escaped_and_legacy_dates_are_not_invented(self):
		t = knock.Text("m", "<script>x</script>\nline2", "Pat & Co", True, 0, "9:05")
		html = knock.message_content(t)
		self.assertNotIn("<script>", html)
		self.assertIn("&lt;script&gt;", html)
		self.assertIn("Pat &amp; Co", html)
		self.assertIn("date unavailable", html)
		self.assertNotIn("date unavailable", knock.message_content(dataclasses.replace(t, unix=5)))


class Sync(unittest.TestCase):
	def setUp(self):
		self.user = shim.session.user
		self.crm = FakeCRM()

	def tearDown(self):
		# The shim is shared by every unit test: leave it as we found it.
		shim.session.user = self.user
		shim.get_doc.side_effect = None
		shim.db.get_value.side_effect = None
		shim.db.sql.return_value = []
		shim.db.values = {}
		del shim._cache.lock

	def test_new_seller_creates_one_lead_and_each_text_once(self):
		first = knock.sync(thread(1))
		self.assertEqual(first["added"], 2)
		self.assertFalse(first["stale"])
		self.assertEqual(len(self.crm.of("CRM Lead")), 1)
		lead = self.crm.of("CRM Lead")[0]
		self.assertEqual(lead.lead_owner, "exe.ortiz@groundworkpro.com")
		self.assertEqual(lead.source, "Knock")
		comms = self.crm.of("Communication")
		self.assertEqual({c.sent_or_received for c in comms}, {"Received", "Sent"})
		self.assertTrue(all(c.reference_name == lead.name and c.communication_medium == "SMS" for c in comms))
		# The anchor now links this thread to the lead: no second lead on retry.
		again = knock.sync(thread(2))
		self.assertEqual(again["added"], 0)
		self.assertEqual(len(self.crm.of("CRM Lead")), 1)
		self.assertEqual(len(self.crm.of("Communication")), 2)
		self.assertEqual(self.crm.locks, [self.crm.locks[0]] * 2)
		self.assertTrue(shim.db.commit.called)

	def test_older_or_equal_revisions_change_nothing(self):
		knock.sync(thread(5))
		later = thread(5, texts=thread()["messages"] + [
			{"id": "g2", "body": "late", "speaker": "Pat Seller", "received": True, "at": "9:09", "unix": 0}])
		self.assertTrue(knock.sync(later)["stale"])
		self.assertTrue(knock.sync(thread(3, texts=later["messages"]))["stale"])
		self.assertEqual(len(self.crm.of("Communication")), 2)
		self.assertEqual(knock.sync(thread(6, texts=later["messages"]))["added"], 1)

	def test_existing_lead_matched_by_formatted_phone_changes_only_owner(self):
		lead = self.crm.lead("CRM-LEAD-2026-00042", "(512) 555-0199")
		shim.db.sql.return_value = [row(lead.name, "10 Main St, Austin TX")]
		out = knock.sync(thread(1))
		self.assertEqual(out["id"], lead.name)
		self.assertEqual(len(self.crm.of("CRM Lead")), 1)
		self.assertEqual(lead.lead_owner, "exe.ortiz@groundworkpro.com")
		self.assertEqual((lead.status, lead.notes), ("Qualified", "keep me"))
		self.assertIsNone(lead.get("_assign"))
		self.assertEqual(lead.saves, 1)
		# Owner already right: no save at all.
		knock.sync(thread(2))
		self.assertEqual(lead.saves, 1)

	def test_unclear_phone_match_refuses_without_creating(self):
		self.crm.lead("A", "5125550199", "1 Oak St")
		self.crm.lead("B", "5125550199", "2 Elm St")
		shim.db.sql.return_value = [row("A", "1 Oak St"), row("B", "2 Elm St")]
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1))
		self.assertEqual(len(self.crm.of("CRM Lead")), 2)
		self.assertEqual(self.crm.of("Communication"), [])
		self.assertTrue(shim.db.rollback.called)

	def test_same_phone_different_property_refuses(self):
		self.crm.lead("A", "5125550199", "99 Other Rd")
		shim.db.sql.return_value = [row("A", "99 Other Rd")]
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1))
		self.assertEqual(len(self.crm.of("CRM Lead")), 1)

	def test_explicit_link_must_be_the_same_seller(self):
		self.crm.lead("CRM-LEAD-2026-00077", "+15125550100")
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1, crm_id="CRM-LEAD-2026-00077"))
		self.crm.lead("CRM-LEAD-2026-00078", "512 555 0199")
		self.assertEqual(knock.sync(thread(1, crm_id="CRM-LEAD-2026-00078"))["id"], "CRM-LEAD-2026-00078")

	def test_deleted_crm_lead_is_not_silently_recreated(self):
		knock.sync(thread(1))
		lead = self.crm.of("CRM Lead")[0]
		del self.crm.docs[("CRM Lead", lead.name)]
		shim.db.values.pop(("CRM Lead", lead.name))
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(2))
		self.assertEqual(self.crm.of("CRM Lead"), [])

	def test_a_text_already_filed_on_another_lead_stops_the_sync(self):
		knock.sync(thread(1))
		for c in self.crm.of("Communication"):
			c.reference_name = "CRM-LEAD-OTHER"
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(2))

	def test_guests_and_disabled_owners_are_refused(self):
		shim.session.user = "Guest"
		with self.assertRaises(shim.PermissionError):
			knock.sync(thread(1))
		shim.session.user = "knock-api@groundworkpro.com"
		shim.db.get_value.side_effect = lambda *a: 0
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1))
		self.assertEqual(self.crm.of("CRM Lead"), [])

	def test_dates_use_the_site_timezone(self):
		knock.sync(thread(1))
		dates = sorted(str(c.get("communication_date")) for c in self.crm.of("Communication"))
		# 1790000000 = 2026-09-21 14:13:20 UTC = 09:13:20 Chicago (CDT).
		self.assertEqual(dates[0], "2026-09-21 09:13:20")


if __name__ == "__main__":
	unittest.main()

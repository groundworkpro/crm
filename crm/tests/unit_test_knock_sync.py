"""crm.api.knock.sync: Knock's one-way text mirror into Quo Message.

What must hold: every Knock text lands once as a Quo Message the lead's Text
Messages tab and the Today board already read, marked as Knock's and linked
back; a retry adds nothing; an older snapshot never overwrites a newer one; an
unclear phone match refuses rather than guessing; a CRM lead is only created
when Knock explicitly asks; and only `lead_owner` changes on the lead, and only
when Knock names an owner. Runs against a small in-memory stand-in for the CRM.
"""

import contextlib
import json
import unittest
from datetime import datetime
from itertools import count
from zoneinfo import ZoneInfo

from crm.tests.frappe_shim import install

shim = install()

from crm.api import knock  # noqa: E402

SELLER = "+15125550199"
LINE = "+16104458065"
URL = "https://knock.groundworkpro.com/?lead=CRM-LEAD-2026-00458"
EXE = "exe.ortiz@groundworkpro.com"


def text(id, body="Hi", received=False, unix=1790000000, **kw):
	return {"id": id, "body": body, "speaker": "x", "received": received, "at": "9:00", "unix": unix, **kw}


def thread(revision=1, texts=None, **extra):
	p = {
		"knock_id": "CRM-LEAD-2026-00458", "revision": revision, "phone": SELLER,
		"owner_email": EXE, "crm_id": "", "create": False, "knock_url": URL, "line": LINE,
		"first_name": "Pat", "last_name": "Seller", "address": "10 Main St",
		"city": "Austin", "state": "TX", "zip": "78701",
		"messages": texts if texts is not None else [
			text("a1", "Hey Pat, this is Lance.", automated=True),
			text("s1", "No I haven't.", received=True, unix=1790000060),
			text("t1", "I'm an investor", sender_email=EXE, unix=1790000120),
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
		if set_name:
			self.name = set_name
		elif self.doctype == knock.MESSAGE:
			self.name = self.id  # autoname field:id
		else:
			self.name = f"CRM-LEAD-2026-{next(self._db.ids):05d}"
		self._db.put(self)
		return self

	def save(self, ignore_permissions=False):
		self.saves += 1
		self._db.put(self)

	def db_set(self, field, value, update_modified=True):
		setattr(self, field, value)


class FakeCRM:
	"""Documents by (doctype, name), wired into the shim's get_doc / exists."""

	def __init__(self, users=(EXE, "lance.johnson@groundworkpro.com")):
		self.docs = {}
		self.ids = count(100)
		self.locks = []
		shim.db.values = {}
		shim.db.doctypes = {knock.MESSAGE}
		shim.get_doc.side_effect = self.get_doc
		shim.db.get_value.side_effect = lambda doctype, name, field: (1 if name in users else 0) if doctype == "User" else None
		shim.db.sql.side_effect = None
		shim.db.sql.return_value = []
		shim.db.commit.reset_mock()
		shim.db.rollback.reset_mock()
		shim.has_permission.return_value = True
		shim.get_meta.side_effect = lambda doctype: type("M", (), {"has_field": staticmethod(lambda f: True)})()
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

	def lead(self, name, phone=SELLER, address="10 Main St", owner="lance.johnson@groundworkpro.com"):
		d = Doc(self, {"doctype": "CRM Lead", "name": name, "phone": phone, "mobile_no": "",
			"property_address": address, "lead_owner": owner, "status": "New", "notes": "keep me"})
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
			thread(create=True, owner_email=""),
			thread(create="yes"),
			thread(knock_url="javascript:alert(1)"),
			thread(line="555"),
			thread(texts=[text("a", received="yes")]),
			thread(texts=[text("a"), text("a")]),
			thread(texts=[text("a", sender_email="x@gmail.com")]),
			thread(texts=[text("a", failed="no")]),
		]
		for p in bad:
			with self.assertRaises(ValueError, msg=p):
				knock.parse_payload(p)
		self.assertEqual(len(knock.parse_payload(json.dumps(thread())).texts), 3)
		# An AI thread names no owner, and that's fine.
		self.assertEqual(knock.parse_payload(thread(owner_email="")).owner, "")

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

	def test_rows_say_who_sent_it_and_how_it_went(self):
		t = knock.parse_payload(thread(texts=[
			text("a1", automated=True, failed=True),
			text("s1", received=True, unix=0),
			text("t1", sender_email=EXE),
		]))
		tz = ZoneInfo("America/Chicago")
		ai, seller, exe = (knock.message_row(t, "L", m, True, tz) for m in t.texts)
		self.assertEqual((ai["direction"], ai["status"], ai["activity_source"]), ("Outgoing", "undelivered", "Sequence"))
		self.assertNotIn("sent_by", ai)
		self.assertEqual((ai["from"], ai["to"]), (LINE, SELLER))
		self.assertEqual((seller["direction"], seller["status"], seller["from"], seller["to"]), ("Incoming", "received", SELLER, LINE))
		self.assertNotIn("activity_source", seller)
		# No date known: none invented (the CRM falls back to when it was filed).
		self.assertNotIn("message_date", seller)
		self.assertEqual((exe["activity_source"], exe["sent_by"], exe["status"]), ("Manual", EXE, "delivered"))
		self.assertTrue(all(r["knock_url"] == URL and r["id"].startswith("knock-text-") for r in (ai, seller, exe)))
		# 1790000000 = 2026-09-21 14:13:20 UTC = 09:13:20 Chicago (CDT).
		self.assertEqual(exe["message_date"], datetime(2026, 9, 21, 9, 13, 20))
		# A site without the Knock link field still files the text.
		self.assertNotIn("knock_url", knock.message_row(t, "L", t.texts[0], False, tz))


class Sync(unittest.TestCase):
	def setUp(self):
		self.user = shim.session.user
		self.crm = FakeCRM()

	def tearDown(self):
		# The shim is shared by every unit test: leave it as we found it.
		shim.session.user = self.user
		shim.get_doc.side_effect = None
		shim.get_meta.side_effect = None
		shim.db.get_value.side_effect = None
		shim.db.sql.return_value = []
		shim.db.values = {}
		shim.db.doctypes = set()
		del shim._cache.lock

	def messages(self):
		return self.crm.of(knock.MESSAGE)

	def test_files_each_text_once_on_the_existing_lead(self):
		lead = self.crm.lead("CRM-LEAD-2026-00458", "(512) 555-0199")
		out = knock.sync(thread(1, crm_id="CRM-LEAD-2026-00458"))
		self.assertEqual((out["id"], out["added"]), (lead.name, 3))
		self.assertEqual(len(self.crm.of("CRM Lead")), 1)
		self.assertTrue(all(m.reference_docname == lead.name and m.knock_url == URL for m in self.messages()))
		self.assertEqual(lead.lead_owner, EXE)
		self.assertEqual((lead.status, lead.notes, lead.saves), ("New", "keep me", 1))
		self.assertIsNone(lead.get("_assign"))
		again = knock.sync(thread(2, crm_id="CRM-LEAD-2026-00458"))
		self.assertEqual((again["added"], len(self.messages())), (0, 3))
		self.assertEqual(lead.saves, 1)
		self.assertTrue(shim.db.commit.called)

	def test_recovered_dates_fill_gaps_and_nothing_else(self):
		self.crm.lead("CRM-LEAD-2026-00458")
		knock.sync(thread(1, crm_id="CRM-LEAD-2026-00458", texts=[text("a1", unix=0), text("a2", unix=1790000000)]))
		undated = [m for m in self.messages() if not m.get("message_date")]
		self.assertEqual(len(undated), 1)
		out = knock.sync(thread(2, crm_id="CRM-LEAD-2026-00458", texts=[text("a1", unix=1790000500, body="changed"), text("a2", unix=1)]))
		self.assertEqual((out["added"], out["dated"]), (0, 1))
		by_id = {m.id: m for m in self.messages()}
		a1 = by_id[knock.stable_name("text", "CRM-LEAD-2026-00458", "a1")]
		a2 = by_id[knock.stable_name("text", "CRM-LEAD-2026-00458", "a2")]
		self.assertEqual(a1.message_date, datetime(2026, 9, 21, 9, 21, 40))
		self.assertEqual(a1.content, "Hi")  # the text itself is never rewritten
		self.assertEqual(a2.message_date, datetime(2026, 9, 21, 9, 13, 20))

	def test_an_ai_thread_leaves_the_owner_alone(self):
		lead = self.crm.lead("CRM-LEAD-2026-00458", owner="german.haikazounian@groundworkpro.com")
		knock.sync(thread(1, crm_id="CRM-LEAD-2026-00458", owner_email=""))
		self.assertEqual((lead.lead_owner, lead.saves), ("german.haikazounian@groundworkpro.com", 0))
		self.assertEqual(len(self.messages()), 3)

	def test_no_lead_is_made_unless_knock_asks(self):
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1))
		self.assertEqual((self.crm.of("CRM Lead"), self.messages()), ([], []))
		self.assertTrue(shim.db.rollback.called)
		out = knock.sync(thread(2, create=True))
		self.assertEqual(len(self.crm.of("CRM Lead")), 1)
		lead = self.crm.of("CRM Lead")[0]
		self.assertEqual((lead.source, lead.lead_owner, out["added"]), ("Knock", EXE, 3))

	def test_older_or_equal_revisions_change_nothing(self):
		self.crm.lead("CRM-LEAD-2026-00458")
		knock.sync(thread(5, crm_id="CRM-LEAD-2026-00458"))
		more = thread()["messages"] + [text("s2", received=True)]
		self.assertTrue(knock.sync(thread(5, texts=more))["stale"])
		self.assertTrue(knock.sync(thread(3, texts=more))["stale"])
		self.assertEqual(len(self.messages()), 3)
		self.assertEqual(knock.sync(thread(6, texts=more))["added"], 1)

	def test_unclear_or_different_matches_refuse(self):
		self.crm.lead("A", SELLER, "1 Oak St")
		self.crm.lead("B", SELLER, "2 Elm St")
		shim.db.sql.return_value = [row("A", "1 Oak St"), row("B", "2 Elm St")]
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1, create=True))
		shim.db.sql.return_value = [row("A", "1 Oak St")]
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1, create=True))
		self.assertEqual((len(self.crm.of("CRM Lead")), self.messages()), (2, []))

	def test_explicit_link_must_be_the_same_seller(self):
		self.crm.lead("CRM-LEAD-2026-00077", "+15125550100")
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1, crm_id="CRM-LEAD-2026-00077"))

	def test_deleted_crm_lead_is_not_silently_recreated(self):
		self.crm.lead("CRM-LEAD-2026-00458")
		knock.sync(thread(1, crm_id="CRM-LEAD-2026-00458"))
		del self.crm.docs[("CRM Lead", "CRM-LEAD-2026-00458")]
		shim.db.values.pop(("CRM Lead", "CRM-LEAD-2026-00458"))
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(2, create=True))
		self.assertEqual(self.crm.of("CRM Lead"), [])

	def test_a_text_already_filed_on_another_lead_stops_the_sync(self):
		self.crm.lead("CRM-LEAD-2026-00458")
		knock.sync(thread(1, crm_id="CRM-LEAD-2026-00458"))
		for m in self.messages():
			m.reference_docname = "CRM-LEAD-OTHER"
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(2))

	def test_senders_who_arent_active_users_are_left_blank(self):
		self.crm = FakeCRM(users=())
		self.crm.lead("CRM-LEAD-2026-00458")
		knock.sync(thread(1, crm_id="CRM-LEAD-2026-00458", owner_email=""))
		t1 = [m for m in self.messages() if m.content == "I'm an investor"][0]
		self.assertIsNone(t1.get("sent_by"))
		self.assertEqual(t1.activity_source, "Manual")

	def test_guests_and_disabled_owners_are_refused(self):
		shim.session.user = "Guest"
		with self.assertRaises(shim.PermissionError):
			knock.sync(thread(1))
		shim.session.user = "knock-api@groundworkpro.com"
		with self.assertRaises(shim.ValidationError):
			knock.sync(thread(1, owner_email="dennis.szafran@groundworkpro.com"))
		self.assertEqual(self.crm.of("CRM Lead"), [])


if __name__ == "__main__":
	unittest.main()

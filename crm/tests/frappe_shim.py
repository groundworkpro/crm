"""A tiny stand-in for `frappe` so pure logic in crm/api and crm/integrations
can be unit-tested with plain `unittest`, without a bench.

Install it BEFORE importing any crm module:

    from crm.tests.frappe_shim import install
    shim = install()
    from crm.api import talk

What it provides is deliberately small and explicit. Anything a test needs
beyond this should be set on the returned shim (`shim.conf[...] = ...`,
`shim.db.has_column = lambda ...`), not added blindly here — the point is that
Frappe glue stays thin enough that the pure parts can be reached around it.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from unittest import mock


class _Local(types.SimpleNamespace):
	pass


class _Session(types.SimpleNamespace):
	pass


class _DB:
	def __init__(self):
		self.columns: dict[str, set[str]] = {}
		self.doctypes: set[str] = set()
		self.values: dict = {}
		self.get_value = mock.MagicMock(return_value=None)
		self.set_value = mock.MagicMock()
		self.get_default = mock.MagicMock(return_value=None)
		self.sql = mock.MagicMock(return_value=[])
		self.get_list = mock.MagicMock(return_value=[])
		self.count = mock.MagicMock(return_value=0)
		self.commit = mock.MagicMock()
		self.rollback = mock.MagicMock()

	def has_column(self, doctype, column):
		return column in self.columns.get(doctype, set())

	def exists(self, doctype, name=None):
		if doctype == "DocType":
			return name in self.doctypes
		return self.values.get((doctype, name)) is not None


class _Cache:
	def __init__(self):
		self.store = {}
		self.hashes = {}

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value

	def delete_value(self, key):
		self.store.pop(key, None)

	def hget(self, name, key):
		return self.hashes.get(name, {}).get(key)

	def hset(self, name, key, value):
		self.hashes.setdefault(name, {})[key] = value

	def hdel(self, name, key):
		self.hashes.get(name, {}).pop(key, None)

	def hgetall(self, name):
		return dict(self.hashes.get(name, {}))


class ValidationError(Exception):
	pass


class PermissionError(Exception):  # noqa: A001 - mirrors frappe's name
	pass


class DoesNotExistError(Exception):
	pass


def install(user="lance.johnson@groundworkpro.com"):
	"""Register a fake `frappe` (and `requests`, if absent) in sys.modules.

	Idempotent: a second call returns the shim already installed, so every test
	module and every crm module share ONE fake (a module imported under shim A
	and a test asserting on shim B would silently disagree).
	"""
	existing = sys.modules.get("frappe")
	if existing is not None and getattr(existing, "_is_crm_test_shim", False):
		existing.session.user = user
		return existing
	shim = types.ModuleType("frappe")
	shim._is_crm_test_shim = True
	shim.conf = {}
	shim.local = _Local(response={}, request=None, cache={})
	shim.request = None
	shim.session = _Session(user=user)
	shim.db = _DB()
	shim._cache = _Cache()
	shim.cache = lambda: shim._cache
	shim.published = []
	shim.errors = []
	shim.inserted = []
	shim.defaults_set = []
	shim.ValidationError = ValidationError
	shim.PermissionError = PermissionError
	shim.DoesNotExistError = DoesNotExistError

	def whitelist(allow_guest=False, methods=None):
		def deco(fn):
			fn._whitelisted = True
			fn._allow_guest = allow_guest
			return fn

		return deco

	shim.whitelist = whitelist
	shim._ = lambda s: s
	shim.throw = mock.MagicMock(side_effect=lambda msg, exc=ValidationError, title=None: (_ for _ in ()).throw(exc(msg)))
	shim.publish_realtime = lambda *a, **k: shim.published.append((a, k))
	shim.log_error = lambda *a, **k: shim.errors.append((a, k))
	shim.get_traceback = lambda: "traceback"
	shim.as_json = lambda v, **k: __import__("json").dumps(v, default=str)
	shim.parse_json = lambda v: __import__("json").loads(v) if isinstance(v, str) else v
	shim._dict = lambda *a, **k: types.SimpleNamespace(**dict(*a, **k)) if False else _AttrDict(*a, **k)
	shim.get_all = mock.MagicMock(return_value=[])
	shim.get_list = mock.MagicMock(return_value=[])
	shim.get_doc = mock.MagicMock()
	shim.get_cached_doc = mock.MagicMock()
	shim.new_doc = mock.MagicMock()
	shim.get_meta = mock.MagicMock()
	shim.get_roles = mock.MagicMock(return_value=["System Manager", "Sales Manager"])
	shim.only_for = lambda *roles: None
	shim.has_permission = mock.MagicMock(return_value=True)
	shim.enqueue = mock.MagicMock()
	shim.flags = types.SimpleNamespace(in_test=True)

	defaults = types.ModuleType("frappe.defaults")
	defaults.set_user_default = lambda key, value, user=None: shim.defaults_set.append((key, value, user))
	defaults.get_user_default = lambda key, user=None: shim.db.get_default(key, user)
	shim.defaults = defaults

	utils = types.ModuleType("frappe.utils")
	utils.now = lambda: datetime(2026, 9, 7, 12, 0, 0).strftime("%Y-%m-%d %H:%M:%S.%f")
	utils.now_datetime = lambda: datetime(2026, 9, 7, 12, 0, 0)
	utils.get_datetime = lambda v: v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
	utils.format_datetime = lambda v, fmt=None: utils.get_datetime(v).strftime("%-d %b")
	utils.add_days = lambda d, n: d

	def _add_to_date(d, years=0, months=0, weeks=0, days=0, hours=0, minutes=0, seconds=0, **_):
		from datetime import timedelta

		return utils.get_datetime(d) + timedelta(
			days=days + 7 * weeks + 30 * months + 365 * years, hours=hours, minutes=minutes, seconds=seconds
		)

	utils.add_to_date = _add_to_date
	utils.getdate = lambda v=None: (v if isinstance(v, datetime) else datetime.fromisoformat(str(v))).date() if v else datetime(2026, 9, 7).date()
	# Real frappe: days from `d2` to `d1`, so a past `d2` yields a POSITIVE age.
	# `_recency_days` relies on that sign, and a reversed shim would make every
	# comp look like it sold in the future.
	utils.date_diff = lambda d1, d2: (utils.getdate(d1) - utils.getdate(d2)).days
	utils.today = lambda: "2026-09-07"
	utils.nowdate = utils.today
	utils.get_fullname = lambda u: u
	utils.get_url = lambda: "https://crm.example.test"
	utils.escape_html = lambda s: s
	utils.cint = lambda v: int(v or 0)
	utils.cstr = lambda v: "" if v is None else str(v)
	utils.random_string = lambda n: "x" * n
	shim.utils = utils

	sys.modules["frappe"] = shim
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.defaults"] = defaults
	exceptions = types.ModuleType("frappe.exceptions")
	exceptions.ValidationError = ValidationError
	exceptions.PermissionError = PermissionError
	sys.modules["frappe.exceptions"] = exceptions
	background_jobs = types.ModuleType("frappe.utils.background_jobs")
	background_jobs.is_job_enqueued = lambda job_id: False
	sys.modules["frappe.utils.background_jobs"] = background_jobs
	safe_exec = types.ModuleType("frappe.utils.safe_exec")
	safe_exec.call_with_form_dict = lambda fn, form: fn()
	sys.modules["frappe.utils.safe_exec"] = safe_exec
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:  # minimal base for controllers
		def __init__(self, *a, **k):
			pass

	document.Document = Document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	if "requests" not in sys.modules:
		try:
			import requests  # noqa: F401
		except ImportError:
			req = types.ModuleType("requests")
			req.post = mock.MagicMock()
			req.get = mock.MagicMock()
			req.put = mock.MagicMock()
			req.delete = mock.MagicMock()
			req.request = mock.MagicMock()
			req.RequestException = Exception
			sys.modules["requests"] = req
	return shim


class _AttrDict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			return None

	def __setattr__(self, key, value):
		self[key] = value

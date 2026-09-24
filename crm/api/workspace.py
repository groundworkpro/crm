"""Which workspace a user sees: `classic` (production, unchanged) or `next`
(Telnyx phone dock + Talk left nav + Talk pages).

GATED TO AN ALLOWLIST. `next` is only offered to, and only accepted from, the
emails in site_config `crm_next_users`. With the key absent the list is Lance
alone, so deploying this changes nothing for anyone else: `get()` answers
`allowed: false` and the frontend never renders the switch, and `set_version`
refuses `next` server-side so the menu item being hidden is not the only guard.

The preference itself is a per-user Frappe default (`crm_workspace_version`),
the same no-doctype trick the task-due chips and Team Activity goals use:
nothing to migrate, readable from the user store at boot, one row per user.

Defaults are cached per process — `frappe.defaults._clear_cache("__default")`
after the write is the same fix Lead Assignment needed, or a toggle can read
stale in the very next request (see CLAUDE.md, "get_default can serve a STALE
value to a set_default in the same process").
"""

from __future__ import annotations

import json

import frappe
from frappe import _

DEFAULT_KEY = "crm_workspace_version"
VERSIONS = ("classic", "next")
DEFAULT_ALLOWLIST = ("lance.johnson@groundworkpro.com",)


def allowlist() -> set[str]:
	"""Lower-cased logins allowed to use `next`. Pure: takes conf from frappe.conf.

	A malformed value degrades to the default list rather than to "everyone" —
	a typo in site_config must never widen the rollout.
	"""
	raw = frappe.conf.get("crm_next_users")
	users = raw
	if isinstance(raw, str):
		try:
			users = json.loads(raw)
		except ValueError:
			users = None
	if not isinstance(users, (list, tuple)) or not users:
		users = DEFAULT_ALLOWLIST
	return {str(u).strip().lower() for u in users if str(u).strip()}


def is_allowed(user: str | None = None) -> bool:
	user = (user or frappe.session.user or "").strip().lower()
	return bool(user) and user in allowlist()


def normalize_version(value, allowed: bool) -> str:
	"""What a stored/requested value means for THIS user.

	Anything but an exact `next` is classic, and `next` is classic for a user who
	is not on the allowlist — so narrowing the list later demotes people without
	touching their stored default.
	"""
	return "next" if (value == "next" and allowed) else "classic"


def current_version(user: str | None = None) -> str:
	user = user or frappe.session.user
	stored = frappe.defaults.get_user_default(DEFAULT_KEY, user)
	return normalize_version(stored, is_allowed(user))


@frappe.whitelist()
def get():
	"""{ version, allowed } for the session user."""
	allowed = is_allowed()
	return {"version": current_version(), "allowed": allowed}


@frappe.whitelist()
def set_version(version: str):
	"""Store the session user's choice. Refuses `next` for anyone not allowed."""
	version = (version or "").strip().lower()
	if version not in VERSIONS:
		frappe.throw(_("Unknown workspace version: {0}").format(version))
	if version == "next" and not is_allowed():
		frappe.throw(_("The new workspace is not enabled for your account."), frappe.PermissionError)
	frappe.defaults.set_user_default(DEFAULT_KEY, version, frappe.session.user)
	try:
		frappe.defaults._clear_cache("__default")
	except Exception:
		pass
	return {"version": version}

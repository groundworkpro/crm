"""Per-lead underwriting workbook — a copy of the Google Sheets underwriting
template, pre-filled and dropped into the shared "Underwriting" Drive folder.

A user clicks **Create Underwriting Sheet** on a lead →
`create_underwriting_workbook` copies the template spreadsheet into the shared
Drive folder, renames it to the lead's `property_address`, fills the ARV input
block (Date / Team Member / Subject Zillow link), records a **CRM Underwriting
Workbook** row, and hands the sheet URL back so the team can open underwriting
later (sidebar card + Activity timeline).

Unlike the BatchData / Documenso integrations (whose external call lives in a
sandboxed ops *server script*), the Google call happens **here, in app code** —
a Frappe server script can't sign the OAuth2 JWT needed to mint a Google token.
The backend reads a Google service-account JSON from site config
(`google_sa_json`, delivered at deploy time the same way `live_demo.py` reads
`demo_password`) and impersonates a fixed Workspace identity
(`google_workspace_subject`, default Lance) via domain-wide delegation — so CRM
user emails need not match Google emails and the whole team sees the file via
the Shared Drive.

The `after_insert` hook mirrors the sheet URL back onto the lead and broadcasts a
`crm_underwriting` realtime event (site-wide, `after_commit`) so every open
Lead's Activity feed + Underwriting card refresh live — same pattern as
`crm_tax_pull` / `crm_esign`.
"""

import json
import re
import time

import jwt
import requests

import frappe
from frappe import _

WORKBOOK_DOCTYPE = "CRM Underwriting Workbook"

# The underwriting template + the shared "Underwriting" Drive folder it's copied
# into. Stable, but overridable via site config if they ever move.
TEMPLATE_ID = "11UKY7aommAKkElRvpA9SPDaLQTvGWHErF7HeNsXieko"
FOLDER_ID = "11xzBIXcsTpx8E_Fi34DnCdnXcy4Kgba9"

# The "Cash Offer" tab's only input: the subject's Zillow link at C11 (the comps
# block keys off it — C12 is `=zAddress(C11)&…`). This template carries no
# date/team-member fields; the CRM records the creator + timestamp on the
# workbook doctype instead.
CELL_ZILLOW = "'Cash Offer'!C11"

#: The template's comp block. Column A takes a Zillow link per row and the sheet
#: does the rest itself: B=zAddress, C=zCloseDate, E=zLivingArea, F=zPrice,
#: G=$/sqft, then row 21 averages G14:G20 and row 22 turns that into the ARV that
#: drives the offer. So "send these comps to underwriting" is literally "write
#: these links into A14:A20" — no template change needed.
COMP_FIRST_ROW = 14
COMP_LAST_ROW = 20
#: Lance's cap. The template has room for 7; asking a rep for more than four is
#: asking them to pad, and the average is only as good as its worst member.
MAX_SHEET_COMPS = 4

DEFAULT_SUBJECT = "lance.johnson@groundworkpro.com"

# The property API the sheet's comp custom functions use (key in site config,
# same value hardcoded in the bound Apps Script). We hit it once at sheet
# creation to resolve the subject's real Zillow *listing* URL from its address —
# the comp functions only accept a homedetails URL, not a /homes/.._rb/ search.
RAPIDAPI_HOST = "us-property-market1.p.rapidapi.com"
# Sent through PropWarehouse so it shares the Zillow rate limiter and quota.
from crm.api.zillow import BASE as ZILLOW_BASE  # noqa: E402

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = (
	"https://www.googleapis.com/auth/drive",
	"https://www.googleapis.com/auth/spreadsheets",
)


# ---------------------------------------------------------------------------
# Google auth + the Drive/Sheets calls
# ---------------------------------------------------------------------------
def _google_access_token() -> str:
	"""Mint a Google OAuth2 access token from the service-account JSON in site
	config. Uses PyJWT (RS256) + a token-endpoint POST — no Google client libs.

	Two modes:
	  - **SA acts as itself** (preferred): a dedicated SA that's a member of the
	    Underwriting Shared Drive. Set `google_workspace_subject` to "" (empty).
	  - **Domain-wide delegation**: impersonate a Workspace user — used only when
	    `google_workspace_subject` is set (or unset → default Lance, legacy)."""
	raw = frappe.conf.get("google_sa_json")
	if not raw:
		frappe.throw(_("Google service account is not configured (google_sa_json)."))
	sa = json.loads(raw) if isinstance(raw, str) else raw
	# .get(key, default): absent → legacy DWD as Lance; present-but-"" → no `sub`.
	subject = frappe.conf.get("google_workspace_subject", DEFAULT_SUBJECT)

	now = int(time.time())
	claims = {
		"iss": sa["client_email"],
		"scope": " ".join(SCOPES),
		"aud": TOKEN_URI,
		"iat": now,
		"exp": now + 3600,
	}
	if subject:
		claims["sub"] = subject
	assertion = jwt.encode(
		claims,
		sa["private_key"],
		algorithm="RS256",
		headers={"kid": sa.get("private_key_id")},
	)
	resp = requests.post(
		TOKEN_URI,
		data={
			"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
			"assertion": assertion,
		},
		timeout=30,
	)
	resp.raise_for_status()
	return resp.json()["access_token"]


def _create_sheet(token: str, name: str, zillow_url: str, comp_urls=None):
	"""Copy the template into the shared folder (renamed), fill the subject Zillow
	link (+ any chosen comps), return (spreadsheet_id, web_view_link)."""
	headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

	copy = requests.post(
		f"https://www.googleapis.com/drive/v3/files/{TEMPLATE_ID}/copy",
		params={"supportsAllDrives": "true", "fields": "id,name,webViewLink"},
		headers=headers,
		json={"name": name, "parents": [frappe.conf.get("underwriting_folder_id") or FOLDER_ID]},
		timeout=60,
	)
	copy.raise_for_status()
	info = copy.json()
	sid = info["id"]

	data = [{"range": CELL_ZILLOW, "values": [[zillow_url]]}]
	if comp_urls:
		# One link per row down column A; the sheet's own formulas fill the rest.
		rows = [[u] for u in comp_urls[: (COMP_LAST_ROW - COMP_FIRST_ROW + 1)]]
		end = COMP_FIRST_ROW + len(rows) - 1
		data.append(
			{
				"range": f"'Cash Offer'!A{COMP_FIRST_ROW}:A{end}",
				"values": rows,
			}
		)
	fill = requests.post(
		f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values:batchUpdate",
		headers=headers,
		json={"valueInputOption": "USER_ENTERED", "data": data},
		timeout=60,
	)
	fill.raise_for_status()

	url = info.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}/edit"
	return sid, url


def _zillow_search_url(address: str) -> str:
	"""Zillow's canonical hyphenated search URL — hyphenates the address rather
	than percent-encoding it ("84 Duck Pond Dr, West Saint Paul, MN 55118" →
	`84-Duck-Pond-Dr-West-Saint-Paul-MN-55118`); the percent-encoded form
	(spaces → %20, commas → %2C) resolves poorly and is ugly in the sheet."""
	slug = re.sub(r"[^A-Za-z0-9]+", "-", address).strip("-")
	return f"https://www.zillow.com/homes/{slug}_rb/"


def _zillow_detail_url(address: str):
	"""Resolve the property's real Zillow *listing* (homedetails) URL from its
	address via the property API — or None on any miss. The API returns a
	relative `url` like `/homedetails/.../<zpid>_zpid/`."""
	key = frappe.conf.get("rapidapi_key")
	if not key:
		return None
	try:
		resp = requests.get(
			f"{ZILLOW_BASE}/property",
			params={"address": address},
			headers={"x-rapidapi-key": key, "x-rapidapi-host": RAPIDAPI_HOST},
			timeout=20,
		)
		resp.raise_for_status()
		path = (resp.json() or {}).get("url")
		if isinstance(path, str) and path.startswith("/"):
			return f"https://www.zillow.com{path}"
	except Exception:
		frappe.log_error(title="Zillow detail URL lookup failed", message=frappe.get_traceback())
	return None


def _zillow_url(address: str) -> str:
	"""Best Zillow link for the sheet's subject (C11): the property's real listing
	URL when we can resolve it (so the subject row `=zAddress(C11)` … auto-fills),
	else the canonical hyphenated search URL as a graceful fallback."""
	return _zillow_detail_url(address) or _zillow_search_url(address)


# ---------------------------------------------------------------------------
# Whitelisted API
# ---------------------------------------------------------------------------
def _existing_workbook(lead: str):
	"""The lead's most recent workbook, or None (one workbook per lead)."""
	if not frappe.db.exists("DocType", WORKBOOK_DOCTYPE):
		return None
	rows = frappe.get_all(
		WORKBOOK_DOCTYPE,
		filters={"lead": lead},
		fields=["name", "sheet_url", "sheet_id", "address"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def create_underwriting_workbook(lead: str):
	"""Create (or return the existing) underwriting sheet for a lead."""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists("DocType", WORKBOOK_DOCTYPE):
		frappe.throw(_("Underwriting workbook storage isn't set up yet."))

	# One workbook per lead — re-clicking just opens the existing one.
	existing = _existing_workbook(lead)
	if existing:
		return {**existing, "existing": True}

	address = (frappe.db.get_value("CRM Lead", lead, "property_address") or "").strip()
	if not address:
		frappe.throw(_("Set a property address before creating an underwriting sheet."))

	try:
		token = _google_access_token()
		sid, url = _create_sheet(token, address, _zillow_url(address))
	except Exception:
		frappe.log_error(title="Underwriting workbook creation failed", message=frappe.get_traceback())
		frappe.throw(_("Couldn't create the underwriting sheet (Google API error)."))

	doc = frappe.get_doc(
		{
			"doctype": WORKBOOK_DOCTYPE,
			"lead": lead,
			"sheet_id": sid,
			"sheet_url": url,
			"address": address,
			"created_by_user": frappe.session.user,
			"workbook_created_at": frappe.utils.now_datetime(),
		}
	).insert(ignore_permissions=True)

	return {
		"name": doc.name,
		"sheet_url": url,
		"sheet_id": sid,
		"address": address,
		"existing": False,
	}


@frappe.whitelist()
def create_underwriting_from_comps(lead: str, comps=None):
	"""Create a NEW underwriting sheet for a lead, pre-loaded with chosen comps.

	Deliberately different from `create_underwriting_workbook`, which is one sheet
	per lead and re-opens the existing one. Underwriting the same house against a
	different set of comps is a different piece of work — a second opinion, or a
	re-run after the market moved — so this ALWAYS creates a new sheet and never
	touches an existing one. Overwriting the comps a colleague had already put in
	a sheet would destroy their work silently, which is the one outcome worth
	spending a few cents of Drive storage to avoid. (Lance's call.)

	`comps` is a list (or JSON string) of CRM Comp docnames.
	"""
	from crm.api.comps import _guard

	_guard()
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.db.exists("DocType", WORKBOOK_DOCTYPE):
		frappe.throw(_("Underwriting workbook storage isn't set up yet."))

	if isinstance(comps, str):
		try:
			comps = json.loads(comps)
		except Exception:
			comps = []
	comps = [str(c) for c in (comps or [])][:MAX_SHEET_COMPS]

	address = (frappe.db.get_value("CRM Lead", lead, "property_address") or "").strip()
	if not address:
		frappe.throw(_("Set a property address before creating an underwriting sheet."))

	# Resolve each comp to the homedetails URL the sheet's z* functions require.
	# A /homes/.._rb/ search URL is silently useless to them, so a comp we cannot
	# resolve is REPORTED rather than written as a link that will never populate.
	comp_urls, unresolved = [], []
	for name in comps:
		comp_addr = frappe.db.get_value("CRM Comp", name, "address")
		if not comp_addr:
			continue
		url = _zillow_detail_url(comp_addr)
		if url:
			comp_urls.append(url)
		else:
			unresolved.append(comp_addr)

	# Name subsequent sheets so a lead with several is legible in Drive.
	prior = frappe.db.count(WORKBOOK_DOCTYPE, {"lead": lead})
	name = address if not prior else f"{address} ({prior + 1})"

	try:
		token = _google_access_token()
		sid, url = _create_sheet(token, name, _zillow_url(address), comp_urls)
	except Exception:
		frappe.log_error(title="Underwriting sheet (from comps) failed", message=frappe.get_traceback())
		frappe.throw(_("Couldn't create the underwriting sheet (Google API error)."))

	doc = frappe.get_doc(
		{
			"doctype": WORKBOOK_DOCTYPE,
			"lead": lead,
			"sheet_id": sid,
			"sheet_url": url,
			"address": name,
			"created_by_user": frappe.session.user,
			"workbook_created_at": frappe.utils.now_datetime(),
		}
	).insert(ignore_permissions=True)

	return {
		"name": doc.name,
		"sheet_url": url,
		"sheet_id": sid,
		"address": name,
		"comps_written": len(comp_urls),
		"unresolved": unresolved,
		"sheet_number": prior + 1,
	}


def on_workbook_insert(doc, method=None):
	"""after_insert — mirror the URL onto the lead + live-refresh open clients."""
	if not doc.get("lead") or not frappe.db.exists("CRM Lead", doc.lead):
		return
	if doc.get("sheet_url") and frappe.get_meta("CRM Lead").has_field("underwriting_url"):
		frappe.db.set_value("CRM Lead", doc.lead, {"underwriting_url": doc.sheet_url}, update_modified=False)
	frappe.publish_realtime(
		"crm_underwriting",
		{"reference_doctype": "CRM Lead", "reference_docname": doc.lead},
		after_commit=True,
	)


@frappe.whitelist()
def get_underwriting_workbooks(lead: str):
	"""Underwriting workbooks for a lead, most recent first (card + timeline)."""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists("DocType", WORKBOOK_DOCTYPE):
		return []

	rows = frappe.get_all(
		WORKBOOK_DOCTYPE,
		filters={"lead": lead},
		fields=[
			"name",
			"sheet_id",
			"sheet_url",
			"address",
			"created_by_user",
			"workbook_created_at",
			"creation",
		],
		order_by="creation desc",
	)
	for r in rows:
		r["created_by_name"] = (
			frappe.get_cached_value("User", r.created_by_user, "full_name")
			if r.get("created_by_user")
			else None
		)
	return rows

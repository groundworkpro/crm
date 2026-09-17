"""E-sign agreements tracked per lead.

**DocuSeal** (current) — a user clicks **Create Agreement** on a lead →
`create_docuseal_agreement()` resolves the right DocuSeal template (Standard vs
Novation/+AIF vs Amendment vs Cancellation × one/two sellers, or a buyer-only
Unilateral Termination), prefills the applicable fields, creates a
submission with `send_email:false`, stores a **CRM Esign Agreement** row
(`provider="docuseal"`) and hands back self-serve signing links. A DocuSeal
webhook (`docuseal_webhook`) keeps the row's status current as recipients
view / sign / complete.

**Documenso** (legacy) — existing rows (`provider="documenso"`) were created by
the ops `create-agreement-draft` server script + tracked by the `documenso-webhook`
script; their signed-PDF download is proxied through `DOCUMENSO_API`. Those rows
keep working untouched — every provider-specific branch here keys off `provider`.

The `crm_esign` realtime event (published on the insert/update hooks below) drives
the live sidebar-card + Activity-timeline refresh for both providers.
"""

import hashlib
import hmac
import json
import re
from urllib.parse import urlencode

import frappe
import requests
from frappe import _

AGREEMENT_DOCTYPE = "CRM Esign Agreement"

# DocuSeal cloud API. Token in site_config `docuseal_api_token` (the Infisical
# DOCUSEAL_API_KEY mirrored in via `bench set-config`, like documenso_api_token).
DOCUSEAL_API = "https://api.docuseal.com"

# Our canonical e-sign templates live in this DocuSeal folder; templates anywhere
# else (team one-offs in Default) are invisible to the resolver.
TEMPLATE_FOLDER = "Purchase Agreements"

# Documenso public API (legacy rows). Signed PDF lives behind the token, so we
# proxy the bytes through the backend.
DOCUMENSO_API = "https://sign.groundworkpro.com/api/v2"

# Occupancy default text (buyer can still edit on the signing page).
_OCCUPANCY_DEFAULT = (
	"Seller shall vacate the Property and remove all personal belongings "
	"on or before the Closing Date."
)

# Governing law on the agreement reads as a 2-letter state code, so normalize
# whatever the lead stores ("Texas" / "TX" / "texas") to the abbreviation.
_STATE_ABBR = {
	"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
	"california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
	"district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
	"idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
	"kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
	"massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
	"missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
	"new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
	"north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
	"oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
	"south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
	"vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
	"wisconsin": "WI", "wyoming": "WY",
}


def _state_abbr(state) -> str:
	"""Return a 2-letter state code: pass-through if already 2 chars, else map."""
	s = (state or "").strip()
	if not s:
		return ""
	if len(s) == 2:
		return s.upper()
	return _STATE_ABBR.get(s.lower(), s)


def _full_property_address(leaddoc) -> str:
	"""The lead's property address as one full "street, city, ST zip" line.

	Webhook-created leads usually already carry the full address in
	`property_address`; manually-entered leads often have the street only, with
	city/state/zip in their own fields. Append each component only if it isn't
	already in the street string, so both shapes come out complete without
	duplication. (Unit/apt "line 2" has no field of its own — when present it
	lives inside `property_address` and passes through untouched.)
	"""
	street = (leaddoc.get("property_address") or "").strip().rstrip(",")
	city = (leaddoc.get("property_city") or "").strip()
	state = (leaddoc.get("property_state") or "").strip()
	zipcode = (leaddoc.get("property_zip") or "").strip()

	low = street.lower()
	parts = [street] if street else []
	if city and city.lower() not in low:
		parts.append(city)

	abbr = _state_abbr(state)
	# The abbreviation is matched case-sensitively ("IN" shouldn't match "in").
	has_state = bool(state) and (
		bool(re.search(rf"\b{re.escape(abbr)}\b", street))
		or (len(state) > 2 and bool(re.search(rf"\b{re.escape(state.lower())}\b", low)))
	)
	tail = abbr if (abbr and not has_state) else ""
	if zipcode and zipcode not in street:
		tail = f"{tail} {zipcode}".strip()
	if tail:
		parts.append(tail)
	return ", ".join(parts)


# --------------------------------------------------------------------------- #
# realtime + hooks (shared by both providers)
# --------------------------------------------------------------------------- #
def _publish(lead):
	if lead:
		frappe.publish_realtime(
			"crm_esign",
			{"reference_doctype": "CRM Lead", "reference_docname": lead},
			after_commit=True,
		)


def on_agreement_insert(doc, method=None):
	"""after_insert — stamp the created time + live-refresh the open lead."""
	if not doc.get("last_event_at"):
		frappe.db.set_value(doc.doctype, doc.name, {"last_event_at": doc.creation}, update_modified=False)
	_publish(doc.get("lead"))


def on_agreement_update(doc, method=None):
	"""on_update — webhook status changes land here; stamp + live-refresh."""
	frappe.db.set_value(doc.doctype, doc.name, {"last_event_at": frappe.utils.now_datetime()}, update_modified=False)
	_publish(doc.get("lead"))


# --------------------------------------------------------------------------- #
# DocuSeal — create
# --------------------------------------------------------------------------- #
def _docuseal_token() -> str:
	token = (frappe.conf.get("docuseal_api_token") or "").strip()
	if not token:
		frappe.throw(_("DocuSeal API token is not configured on this site."))
	return token


def _resolve_template_ids(
	want_aif: bool,
	want_two: bool,
	want_amendment: bool = False,
	want_cancellation: bool = False,
	want_termination: bool = False,
):
	"""Resolve the template id(s) for this deal; newest id wins per match.

	Templates in the "Purchase Agreements" folder (one document each):
	  Purchase Agreement - One Seller / - Two Sellers   (Buyer + Seller(s))
	  AIF - One Seller / - Two Sellers                  (Seller(s) only)
	  Amendment - One Seller / - Two Sellers            (Buyer + Seller(s))
	  Cancellation - One Seller / - Two Sellers         (Buyer + Seller(s))
	  Unilateral Termination - No EMD                    (Buyer only)

	A Standard deal is the Purchase Agreement template alone; a Novation deal
	adds the matching AIF document into the SAME envelope at create time (via
	`POST /submissions/pdf` `template_ids`) — no pre-merged templates to maintain.
	An Amendment (price / closing-date change to an executed agreement) and a
	Cancellation (cancel the contract + release the earnest money) are each their
	own single-document envelope. A Unilateral Termination is a buyer-only notice:
	no seller signature or seller signing link is created.

	ONLY the "Purchase Agreements" folder is considered — the team makes one-off
	templates in the DocuSeal UI for specific deals (e.g. "Amendment 17199
	Hamburg Detroit", default First Party/Second Party roles) which land in the
	Default folder and must never win the name match.
	Returns (template_ids, display_title).
	"""
	token = _docuseal_token()
	resp = requests.get(
		f"{DOCUSEAL_API}/templates",
		params={"limit": 100, "folder": TEMPLATE_FOLDER},
		headers={"X-Auth-Token": token},
		timeout=30,
	)
	items = [
		t for t in ((resp.json() or {}).get("data") or [])
		if (t.get("folder_name") or "") == TEMPLATE_FOLDER
	]

	def _best(pred):
		chosen = None
		for t in items:
			if t.get("archived_at"):
				continue
			if pred(t.get("name") or ""):
				if chosen is None or (t.get("id") or 0) > (chosen.get("id") or 0):
					chosen = t
		return chosen

	if want_termination:
		termination = _best(lambda n: n.startswith("Unilateral Termination - No EMD"))
		if not termination:
			frappe.throw(_("No matching DocuSeal Unilateral Termination template was found."))
		return [termination["id"]], termination["name"]

	if want_amendment:
		amd = _best(lambda n: n.startswith("Amendment") and (("Two" in n) == want_two))
		if not amd:
			frappe.throw(_("No matching DocuSeal Amendment template was found."))
		return [amd["id"]], amd["name"]

	if want_cancellation:
		cxl = _best(lambda n: n.startswith("Cancellation") and (("Two" in n) == want_two))
		if not cxl:
			frappe.throw(_("No matching DocuSeal Cancellation template was found."))
		return [cxl["id"]], cxl["name"]

	wpa = _best(lambda n: n.startswith("Purchase Agreement") and "AIF" not in n and (("Two" in n) == want_two))
	if not wpa:
		frappe.throw(_("No matching DocuSeal Purchase Agreement template was found."))
	template_ids = [wpa["id"]]
	title = wpa["name"]
	if want_aif:
		aif = _best(lambda n: n.startswith("AIF") and (("Two" in n) == want_two))
		if not aif:
			frappe.throw(_("No matching DocuSeal AIF template was found."))
		template_ids.append(aif["id"])
		title = wpa["name"] + " + AIF"
	return template_ids, title


def _binding_agreement_date(lead: str) -> str:
	"""The date the lead's Purchase Agreement went fully signed, as YYYY-MM-DD.

	That is the PSA's effective date ("day 0" in the contract parser's terms) and
	it is what the Amendment's "Binding Agreement Date of ____" refers to. Newest
	completed non-archived Purchase Agreement row wins; DocuSeal's `completed_at`
	for it is the authoritative stamp, with the row's `last_event_at` as the
	fallback (that column moves on any later webhook event, e.g. a post-signing
	view, so it is second choice). Empty string when the lead has no signed PSA —
	the rep then fills the blank as before. Never raises: a prefill must not be
	why an amendment cannot be drafted.
	"""
	try:
		filters = {"lead": lead, "template_title": ["like", "Purchase Agreement%"]}
		filters.update(_live_filter())
		rows = frappe.get_all(
			AGREEMENT_DOCTYPE, filters=filters,
			fields=["document_id", "agreement_status", "signed_count", "total_signers", "last_event_at"],
			order_by="creation desc",
		)
		signed = next((r for r in rows if _is_completed(r)), None)
		if not signed:
			return ""
		stamp = None
		if signed.get("document_id"):
			try:
				resp = requests.get(
					f"{DOCUSEAL_API}/submissions/{signed['document_id']}",
					headers={"X-Auth-Token": _docuseal_token()}, timeout=10,
				)
				if resp.ok:
					completed = (resp.json() or {}).get("completed_at")
					if completed:
						stamp = frappe.utils.convert_utc_to_system_timezone(
							frappe.utils.get_datetime(completed)
						)
			except Exception:
				stamp = None
		stamp = stamp or signed.get("last_event_at")
		return frappe.utils.getdate(stamp).isoformat() if stamp else ""
	except Exception:
		frappe.log_error(title="amendment: binding agreement date lookup failed")
		return ""


def _whole_dollars(value) -> str:
	"""250000.0 -> '250,000' for a '$____' blank; '' when unset."""
	try:
		n = float(value or 0)
	except (TypeError, ValueError):
		return ""
	return f"{int(round(n)):,}" if n > 0 else ""


def _placeholder_email(given: str, lead: str, idx: int) -> str:
	"""Signers need an email to complete; nobody is emailed (send_email:false)."""
	given = (given or "").strip()
	if "@" in given:
		return given
	return f"signer{idx}.{lead.lower()}@noreply.groundworkpro.com"


@frappe.whitelist()
def create_docuseal_agreement(
	lead: str,
	template: str = "standard",
	two_sellers=None,
	seller1_email: str = None,
	seller2_name: str = None,
	seller2_email: str = None,
	buyer: str = None,
):
	"""Create a DocuSeal submission for a lead; return self-serve signing links.

	The logged-in sales user is the Buyer/company representative (signs first,
	fields stay editable). Seller 1 is the lead and Seller 2 is collected for
	two-seller deals; unilateral termination notices have no seller signer.
	`buyer` (optional) links the agreement to a CRM Buyer — set when the draft
	is created from a buyer page, so it shows on that buyer's Agreements card.
	"""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Lead", "write", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if buyer and not frappe.db.exists("CRM Buyer", buyer):
		frappe.throw(_("Buyer not found"), frappe.DoesNotExistError)

	leaddoc = frappe.get_doc("CRM Lead", lead)
	template_key = str(template).strip().lower()
	want_amendment = template_key == "amendment"
	want_cancellation = template_key in ("cancellation", "cancel")
	want_termination = template_key in ("termination", "unilateral_termination", "termination_no_emd")
	want_aif = template_key in ("novation", "aif")
	# `two_sellers` arrives as a seller *count* ("1" / "2") from the modal, so
	# "1" must mean one seller — don't run it through the generic `_truthy`
	# (which treats "1" as true and wrongly demands a second seller).
	want_two = (not want_termination) and str(two_sellers).strip().lower() in (
		"2", "two", "true", "yes", "on"
	)

	# Buyer = the session user (its own no-login link comes back regardless).
	user = frappe.get_doc("User", frappe.session.user)
	buyer_name = user.full_name or user.first_name or frappe.session.user
	buyer_email = (user.email or frappe.session.user or "").strip()
	if "@" not in buyer_email:
		buyer_email = "acq@groundworkpro.com"

	# Seller 1 = the lead.
	seller1_name = (leaddoc.get("lead_name") or "").strip() or (
		((leaddoc.get("first_name") or "") + " " + (leaddoc.get("last_name") or "")).strip()
	)
	seller1_email = (seller1_email or leaddoc.get("email") or "").strip()
	seller2_name = (seller2_name or "").strip()
	seller2_email = (seller2_email or "").strip()
	if want_two and not seller2_name:
		frappe.throw(_("Enter the second seller's name."))

	template_ids, template_title = _resolve_template_ids(
		want_aif, want_two, want_amendment, want_cancellation, want_termination
	)

	addr = _full_property_address(leaddoc)
	sellers_joined = seller1_name + ((" and " + seller2_name) if (want_two and seller2_name) else "")

	def _clean(d):
		return {k: v for k, v in d.items() if v}

	# Buyer-role prefill: identity pulled off the lead + standard defaults for the
	# boilerplate terms (all still editable by the buyer on the signing page).
	# A termination notice pre-fills today's notice date, owner, phone, property,
	# and current user's name; contract date and title remain buyer-entered.
	# The Amendment (Sep-07 rebuild) is prefilled as a TEMPLATE off the lead:
	# the Binding Agreement Date is the date the PSA went fully signed, and the
	# amended price / closing date start at the lead's CURRENT terms so the rep
	# edits one number rather than typing the deal from memory. The checkboxes
	# stay unticked — which term is actually being amended is the rep's call, and
	# the "Other" line is free text. `Signer Name` is the PSA signature page's
	# field name, so the rep's printed name prefills the same way.
	# The Cancellation shares the branch: the contract date, escrow agent and
	# earnest-money disbursement are per-deal facts the buyer fills on the signing
	# page, and the amendment-only keys are ignored by DocuSeal on that template.
	if want_termination:
		buyer_values = _clean({
			"Notice Date": frappe.utils.nowdate(),
			"Property Owner": seller1_name,
			"Phone": (leaddoc.get("mobile_no") or leaddoc.get("phone") or "").strip(),
			"Property Address": addr,
			"Signer Name": buyer_name,
		})
	elif want_amendment or want_cancellation:
		buyer_values = _clean({
			"Seller Name(s)": sellers_joined,
			"Property Address": addr,
			"Signer Name": buyer_name,
		})
		if want_amendment:
			closing = leaddoc.get("closing_date")
			buyer_values.update(_clean({
				"Binding Agreement Date": _binding_agreement_date(lead),
				"Amended Purchase Price": _whole_dollars(leaddoc.get("acq_price")),
				"Amended Closing Date": frappe.utils.getdate(closing).isoformat() if closing else "",
			}))
	else:
		# The live Purchase Agreement template carries: Agreement Date, Seller
		# Name(s), Property Address, Sale Price, Due Diligence Days, Earnest Money,
		# Closing Days, Offer Expires Days, Additional Terms, Signer Name, Title
		# (+ the signature/date fields). Keys below that the template doesn't have
		# (County / APN / Governing State / Occupancy / Personal Property /
		# Disclosed Defects) are ignored by DocuSeal and kept only so an older
		# template revision would still prefill.
		#
		# Sale Price, Closing Days and Offer Expires Days are deliberately NOT
		# prefilled: they are per-deal terms with no safe default, and each is a
		# REQUIRED field, so the rep must enter them before they can sign.
		buyer_values = _clean({
			# from the lead
			"Seller Name(s)": sellers_joined,
			"Property Address": addr,
			"County": (leaddoc.get("property_county") or "").strip(),
			"APN": (leaddoc.get("apn") or "").strip(),
			"Governing State": _state_abbr(leaddoc.get("property_state")),
			"Signer Name": buyer_name,
			# standard defaults
			"Agreement Date": frappe.utils.nowdate(),
			"Earnest Money": "100",
			"Due Diligence Days": "30",
			"Occupancy": _OCCUPANCY_DEFAULT,
			"Personal Property": "None.",
			"Additional Terms": "None.",
			"Disclosed Defects": "None known.",
		})

	# Seller-role prefill. Field names differ across templates; pass the superset
	# and DocuSeal ignores keys that don't exist on that submitter.
	def _seller_values(name, two_idx=None):
		if two_idx == 1:
			d = {"Seller 1 Legal Name": name, "Seller 1 Name": name}
		elif two_idx == 2:
			d = {"Seller 2 Legal Name": name, "Seller 2 Name": name}
		else:
			d = {"Seller Name": name}
		d["Property Address"] = addr  # AIF puts the address on the seller role
		return _clean(d)

	submitters = [{
		"role": "Buyer",
		"name": buyer_name,
		"email": buyer_email,
		"send_email": False,
		"values": buyer_values,
	}]
	if not want_termination:
		if want_two:
			submitters.append({
				"role": "Seller 1", "name": seller1_name,
				"email": _placeholder_email(seller1_email, lead, 1),
				"send_email": False, "values": _seller_values(seller1_name, 1),
			})
			submitters.append({
				"role": "Seller 2", "name": seller2_name,
				"email": _placeholder_email(seller2_email, lead, 2),
				"send_email": False, "values": _seller_values(seller2_name, 2),
			})
		else:
			submitters.append({
				"role": "Seller", "name": seller1_name,
				"email": _placeholder_email(seller1_email, lead, 1),
				"send_email": False, "values": _seller_values(seller1_name),
			})

	# One template (Standard) → POST /submissions. Two+ (Novation = Purchase
	# Agreement + AIF) → POST /submissions/pdf with `template_ids`, which assembles
	# both documents into ONE envelope at create time — no pre-merged templates.
	token = _docuseal_token()
	if len(template_ids) == 1:
		endpoint, payload = "/submissions", {
			"template_id": template_ids[0],
			"send_email": False, "order": "preserved", "submitters": submitters,
		}
	else:
		endpoint, payload = "/submissions/pdf", {
			"template_ids": template_ids, "documents": [], "name": template_title,
			"send_email": False, "order": "preserved", "submitters": submitters,
		}
	try:
		resp = requests.post(
			f"{DOCUSEAL_API}{endpoint}",
			json=payload,
			headers={"X-Auth-Token": token, "Content-Type": "application/json"},
			timeout=90,
		)
	except requests.RequestException:
		frappe.throw(_("Could not reach DocuSeal to create the agreement."))
	if resp.status_code not in (200, 201):
		frappe.throw(_("DocuSeal rejected the agreement ({0}): {1}").format(resp.status_code, resp.text[:300]))

	# /submissions returns a list of submitters; /submissions/pdf returns a dict.
	data = resp.json()
	if isinstance(data, list):
		subs = data
		submission_id = subs[0].get("submission_id") if subs else None
	else:
		subs = (data or {}).get("submitters") or []
		submission_id = (data or {}).get("id")
	if not submission_id or not subs:
		frappe.throw(_("DocuSeal returned no submission."))
	buyer_link = ""
	seller_links = []
	for s in subs:
		link = s.get("embed_src") or s.get("slug") or ""
		if s.get("role") == "Buyer":
			buyer_link = link
		else:
			seller_links.append({"name": s.get("name"), "link": link})

	row = {
		"doctype": AGREEMENT_DOCTYPE,
		"lead": lead,
		"provider": "docuseal",
		"document_id": submission_id,
		"template_title": template_title,
		"agreement_status": "pending",
		"signed_count": 0,
		"total_signers": len(subs),
		"buyer_link": buyer_link,
		"seller_links": json.dumps(seller_links),
		"last_event": "created",
	}
	if buyer and frappe.db.has_column(AGREEMENT_DOCTYPE, "buyer"):
		row["buyer"] = buyer
	agr = frappe.get_doc(row)
	agr.insert(ignore_permissions=True)
	frappe.db.commit()

	# Capture Seller 2 as a Contact on the lead (mirrors the Documenso flow).
	if want_two and seller2_name:
		_attach_second_seller_contact(lead, seller2_name, seller2_email)

	return {
		"ok": True,
		"document_id": submission_id,
		"agreement": agr.name,
		"template": template_title,
		"buyer_link": buyer_link,
		"seller_links": seller_links,
	}


def _attach_second_seller_contact(lead: str, name: str, email: str):
	for dl in frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "CRM Lead", "link_name": lead, "parenttype": "Contact"},
		fields=["parent"],
	):
		existing = frappe.db.get_value("Contact", dl.parent, "full_name")
		if existing and existing.strip().lower() == name.strip().lower():
			return
	parts = name.split(" ", 1)
	cdoc = {
		"doctype": "Contact",
		"first_name": parts[0],
		"last_name": parts[1] if len(parts) > 1 else "",
		"links": [{"link_doctype": "CRM Lead", "link_name": lead}],
	}
	if email:
		cdoc["email_ids"] = [{"email_id": email, "is_primary": 1}]
	frappe.get_doc(cdoc).insert(ignore_permissions=True)


# --------------------------------------------------------------------------- #
# DocuSeal — webhook
# --------------------------------------------------------------------------- #
@frappe.whitelist(allow_guest=True)
def docuseal_webhook(secret: str = None):
	"""Keep a CRM Esign Agreement current as DocuSeal recipients view/sign/complete.

	Registered in DocuSeal Settings → Webhooks pointing at
	`/api/method/crm.api.agreement.docuseal_webhook?secret=<docuseal_webhook_secret>`
	with events form.viewed/started/completed/declined + submission.completed/
	expired/archived. We refetch the submission for authoritative status/counts,
	then save() — the on_update hook publishes `crm_esign` for the live refresh.
	"""
	expected = (frappe.conf.get("docuseal_webhook_secret") or "").strip()
	provided = secret or (frappe.request.args.get("secret") if frappe.request else None)
	if not expected or provided != expected:
		frappe.throw(_("Unauthorized"), frappe.PermissionError)

	body = {}
	if frappe.request:
		body = frappe.request.get_json(force=True, silent=True) or {}
	event = body.get("event_type")
	data = body.get("data") or {}
	sub_id = data.get("submission_id") or (data.get("submission") or {}).get("id") or data.get("id")
	if not sub_id:
		return {"ok": True, "skipped": "no submission id"}

	rows = frappe.get_all(
		AGREEMENT_DOCTYPE,
		filters={"document_id": sub_id, "provider": "docuseal"},
		fields=["name"],
	)
	if not rows:
		# Envelopes built by hand in the DocuSeal UI have no row yet. DocuSeal
		# webhooks are account-wide, so this is our only notice they exist —
		# try to work out which lead it belongs to rather than dropping it.
		adopted = None
		try:
			from crm.api.agreement_adopt import adopt_submission

			adopted = adopt_submission(sub_id)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "docuseal adoption failed")
		if not adopted or adopted.get("status") != "adopted":
			return {"ok": True, "skipped": "no agreement", "adoption": adopted}
		rows = frappe.get_all(
			AGREEMENT_DOCTYPE,
			filters={"document_id": sub_id, "provider": "docuseal"},
			fields=["name"],
		)

	status, signed, total = None, 0, 0
	token = (frappe.conf.get("docuseal_api_token") or "").strip()
	if token:
		try:
			r = requests.get(
				f"{DOCUSEAL_API}/submissions/{sub_id}",
				headers={"X-Auth-Token": token},
				timeout=20,
			)
			if r.ok:
				full = r.json() or {}
				status = full.get("status")
				submitters = full.get("submitters") or []
				total = len(submitters)
				signed = sum(1 for s in submitters if s.get("completed_at"))
		except requests.RequestException:
			pass

	for m in rows:
		agr = frappe.get_doc(AGREEMENT_DOCTYPE, m.name)
		if status:
			agr.agreement_status = status
		if total:
			agr.total_signers = total
			agr.signed_count = signed
		if event:
			agr.last_event = event
		agr.raw_last_payload = json.dumps(body)[:100000]
		agr.save(ignore_permissions=True)

		# Text/email the lead owner about this event (per their prefs). Never let
		# a notification failure break the webhook — DocuSeal still gets its 200.
		if event:
			try:
				from crm.api.agreement_notify import notify_event

				notify_event(agr, event, data)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "agreement notification failed")

		# Now fully signed? Queue the contract parser (Gemini reads the signed
		# PDF and writes acq price / DD expiration / closing date back onto the
		# lead). Fire-and-forget by design: the hourly catch-up sweep recovers
		# anything missed, so a parser problem must never cost DocuSeal its 200.
		try:
			from crm.api.contract_parse import trigger_parse

			trigger_parse(agr)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "contract parser trigger failed")

	return {"ok": True, "updated": len(rows)}


@frappe.whitelist()
def archive_agreement(agreement: str):
	"""Archive an agreement: archive the provider's submission + drop the CRM row.

	For DocuSeal, `DELETE /submissions/{id}` archives the submission (recoverable
	on DocuSeal, signing links stop working). The CRM Esign Agreement row is then
	removed so it disappears from the lead's card + timeline; a `crm_esign`
	realtime event refreshes the open lead.
	"""
	if not frappe.db.exists(AGREEMENT_DOCTYPE, agreement):
		frappe.throw(_("Agreement not found"), frappe.DoesNotExistError)
	wanted = ["lead", "document_id"]
	if frappe.db.has_column(AGREEMENT_DOCTYPE, "provider"):
		wanted.append("provider")
	agr = frappe.db.get_value(AGREEMENT_DOCTYPE, agreement, wanted, as_dict=True)
	if not agr.lead or not frappe.has_permission("CRM Lead", "write", agr.lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	provider = (agr.get("provider") or "documenso").lower()
	# Best-effort archive on the provider side — never block the local removal.
	if provider == "docuseal" and agr.document_id:
		token = (frappe.conf.get("docuseal_api_token") or "").strip()
		if token:
			try:
				requests.delete(
					f"{DOCUSEAL_API}/submissions/{agr.document_id}",
					headers={"X-Auth-Token": token},
					timeout=20,
				)
			except requests.RequestException:
				pass

	lead = agr.lead
	# Soft-archive rather than delete. Deleting lost the record entirely (a
	# signed contract could vanish with one click) and, now that unknown
	# submissions are auto-adopted, a later webhook event would recreate the row
	# we just removed. Keeping a flagged row makes the archive both recoverable
	# and a tombstone. Older sites without the column fall back to the delete.
	if frappe.db.has_column(AGREEMENT_DOCTYPE, "is_archived"):
		frappe.db.set_value(AGREEMENT_DOCTYPE, agreement, "is_archived", 1)
	else:
		frappe.delete_doc(AGREEMENT_DOCTYPE, agreement, ignore_permissions=True, force=True)
	frappe.db.commit()
	_publish(lead)
	return {"ok": True}


# --------------------------------------------------------------------------- #
# read + download (both providers)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def get_agreements(lead: str):
	"""E-sign agreements for a lead, most recent first (sidebar card + timeline)."""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Lead", "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists("DocType", AGREEMENT_DOCTYPE):
		return []

	filters = {"lead": lead}
	filters.update(_live_filter())
	rows = frappe.get_all(
		AGREEMENT_DOCTYPE, filters=filters, fields=_agreement_fields(), order_by="creation desc"
	)
	for r in rows:
		_shape_agreement(r)
	return rows


def _agreement_fields(extra=None):
	"""Row fields, skipping any column this site has not been migrated to yet."""
	fields = [
		"name", "document_id", "template_title", "agreement_status",
		"signed_count", "total_signers", "buyer_link", "seller_links",
		"last_event", "last_event_at", "creation", "owner",
	]
	fields += list(extra or [])
	for optional in ("provider", "source", "match_basis"):
		if frappe.db.has_column(AGREEMENT_DOCTYPE, optional):
			fields.append(optional)
	return fields


def _live_filter():
	"""Hide archived rows (kept as tombstones) from the cards + timeline."""
	if frappe.db.has_column(AGREEMENT_DOCTYPE, "is_archived"):
		return {"is_archived": 0}
	return {}


def _shape_agreement(r):
	r["created_by_name"] = frappe.get_cached_value("User", r.owner, "full_name") if r.owner else None
	try:
		r["seller_links"] = json.loads(r.get("seller_links") or "[]")
	except (ValueError, TypeError):
		r["seller_links"] = []
	r["provider"] = r.get("provider") or "documenso"
	r["is_signed"] = _is_completed(r)
	name = r.get("name")
	r["signed_pdf_url"] = _public_pdf_url(name) if r["is_signed"] and name else None
	return r


@frappe.whitelist()
def get_buyer_agreements(buyer: str):
	"""E-sign agreements linked to THIS buyer (via the agreement's `buyer` Link
	field), most recent first — the buyer page's Agreements card. Deliberately
	NOT every agreement on the buyer's engaged properties: those are usually
	seller-side (e.g. the purchase agreement with the property's sellers). Each
	row still carries `lead` + a display `property_label`."""
	if not frappe.db.exists("DocType", "CRM Buyer") or not frappe.db.exists("CRM Buyer", buyer):
		frappe.throw(_("Buyer not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Buyer", "read", buyer):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists("DocType", AGREEMENT_DOCTYPE) or not frappe.db.has_column(
		AGREEMENT_DOCTYPE, "buyer"
	):
		return []

	filters = {"buyer": buyer}
	filters.update(_live_filter())
	rows = frappe.get_all(
		AGREEMENT_DOCTYPE,
		filters=filters,
		fields=_agreement_fields(extra=["lead"]),
		order_by="creation desc",
	)
	labels = {}
	for r in rows:
		_shape_agreement(r)
		if r.lead not in labels:
			info = (
				frappe.db.get_value(
					"CRM Lead", r.lead, ["property_address", "lead_name"], as_dict=True
				)
				or {}
			)
			labels[r.lead] = info.get("property_address") or info.get("lead_name") or r.lead
		r["property_label"] = labels[r.lead]
	return rows


def _is_completed(agr) -> bool:
	"""Fully signed: status COMPLETED/completed, or every signer signed."""
	if (agr.get("agreement_status") or "").upper() == "COMPLETED":
		return True
	signed = agr.get("signed_count") or 0
	total = agr.get("total_signers") or 0
	return bool(total and signed >= total)


def _signed_filename(agr) -> str:
	base = (agr.get("template_title") or "agreement").strip()
	safe = re.sub(r"[^\w.-]+", "_", base).strip("_") or "agreement"
	return f"{safe}_signed.pdf"


def _pdf_secret() -> bytes:
	"""HMAC key for public PDF links. Prefers a dedicated config value so a
	rotation does not wait on changing the site encryption key."""
	s = (
		frappe.conf.get("agreement_pdf_secret")
		or frappe.conf.get("encryption_key")
		or ""
	).strip()
	if not s:
		s = getattr(getattr(frappe, "local", None), "site", None) or "crm"
	return s.encode()


def _pdf_token(name: str) -> str:
	return hmac.new(_pdf_secret(), (name or "").encode(), hashlib.sha256).hexdigest()[:32]


def _pdf_token_ok(name: str, given) -> bool:
	expected = _pdf_token(name)
	got = (given or "").strip()
	if len(got) != len(expected):
		return False
	return hmac.compare_digest(expected, got)


def _site_url() -> str:
	"""Public site URL. Workers often see http:// from get_url(); force https
	except on localhost (same rule as live_one)."""
	site = (frappe.utils.get_url() or "").rstrip("/")
	if site.startswith("http://") and "localhost" not in site and "127.0.0.1" not in site:
		site = "https://" + site[len("http://"):]
	return site


def _public_pdf_url(name: str) -> str:
	q = urlencode({"agreement": name, "token": _pdf_token(name)})
	return f"{_site_url()}/api/method/crm.api.agreement.public_signed_pdf?{q}"


def _load_signed_agreement(agreement: str):
	if not frappe.db.exists(AGREEMENT_DOCTYPE, agreement):
		frappe.throw(_("Agreement not found"), frappe.DoesNotExistError)
	wanted = ["lead", "document_id", "agreement_status", "signed_count", "total_signers", "template_title"]
	if frappe.db.has_column(AGREEMENT_DOCTYPE, "provider"):
		wanted.append("provider")
	agr = frappe.db.get_value(AGREEMENT_DOCTYPE, agreement, wanted, as_dict=True)
	if not agr:
		frappe.throw(_("Agreement not found"), frappe.DoesNotExistError)
	if not _is_completed(agr):
		frappe.throw(_("This agreement is not fully signed yet."))
	if not agr.document_id:
		frappe.throw(_("This agreement has no document on file."))
	return agr


def _stream_signed_pdf(agr):
	provider = (agr.get("provider") or "documenso").lower()
	content = _docuseal_signed_pdf(agr) if provider == "docuseal" else _documenso_signed_pdf(agr)
	frappe.local.response.filename = _signed_filename(agr)
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"
	# render in the browser tab instead of forcing a download (the frontend opens
	# this URL with target=_blank); right-click → Save still downloads it
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.content_type = "application/pdf"


@frappe.whitelist()
def download_signed_agreement(agreement: str):
	"""Stream the fully-signed PDF for a logged-in CRM user (lead read perm)."""
	agr = _load_signed_agreement(agreement)
	if not agr.lead or not frappe.has_permission("CRM Lead", "read", agr.lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	_stream_signed_pdf(agr)


@frappe.whitelist(allow_guest=True)
def public_signed_pdf(agreement: str, token: str = None):
	"""Unauthenticated stream of a fully-signed PDF.

	The Open signed PDF button's href is this URL, so the address bar is a
	link anyone can open (title company, listing agent, buyer) without a CRM
	session. Token is HMAC of the agreement name; the name alone is not enough.
	"""
	if not agreement or not _pdf_token_ok(agreement, token):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	_stream_signed_pdf(_load_signed_agreement(agreement))


def _docuseal_signed_pdf(agr) -> bytes:
	"""Fetch the combined signed PDF for a DocuSeal submission."""
	token = _docuseal_token()
	try:
		meta = requests.get(
			f"{DOCUSEAL_API}/submissions/{agr.document_id}",
			headers={"X-Auth-Token": token},
			timeout=30,
		)
		url = (meta.json() or {}).get("combined_document_url") if meta.ok else None
		if not url:
			# fall back to the per-document list (merged)
			docs = requests.get(
				f"{DOCUSEAL_API}/submissions/{agr.document_id}/documents",
				headers={"X-Auth-Token": token},
				timeout=30,
			).json()
			arr = (docs.get("documents") if isinstance(docs, dict) else docs) or []
			url = arr[0].get("url") if arr else None
		if not url:
			frappe.throw(_("DocuSeal has no signed document URL yet."))
		pdf = requests.get(url, timeout=60)
	except requests.RequestException:
		frappe.throw(_("Could not reach DocuSeal to fetch the signed document."))
	if pdf.status_code != 200 or not pdf.content:
		frappe.throw(_("Could not fetch the signed document from DocuSeal ({0}).").format(pdf.status_code))
	return pdf.content


def _documenso_signed_pdf(agr) -> bytes:
	"""Legacy: proxy the signed PDF from Documenso (token in site_config)."""
	token = (frappe.conf.get("documenso_api_token") or "").strip()
	if not token:
		frappe.throw(_("Documenso API token is not configured on this site."))
	try:
		resp = requests.get(
			f"{DOCUMENSO_API}/document/{agr.document_id}/download",
			params={"version": "signed"},
			headers={"Authorization": token},
			timeout=30,
		)
	except requests.RequestException:
		frappe.throw(_("Could not reach Documenso to fetch the signed document."))
	if resp.status_code != 200 or not resp.content:
		frappe.throw(_("Could not fetch the signed document from Documenso ({0}).").format(resp.status_code))
	return resp.content

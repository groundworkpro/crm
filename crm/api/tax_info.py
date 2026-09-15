"""Property tax / owner / lien info pulled from BatchData (per-lead, $0.22 a pull).

A user clicks **Fetch Tax Info** on a lead or the comps page → the `pull-tax-info`
server script (ops repo, `../frappe-crm-deploy`) hits BatchData's
`/property/lookup/all-attributes` with the deed-history Infisical key and stores the
raw property record on a **CRM Property Tax Pull** doc. The sandbox can't parse
richly or `publish_realtime`, so the app-code `after_insert` hook:

  - flattens headline columns (owner, APN, tax status, annual tax, assessed),
  - writes those back onto the CRM Lead (Property Details sidebar),
  - broadcasts `crm_tax_pull` so the Tax Info card + comps panel refresh.

Deed / mortgage / foreclosure / lien tables are parsed on *read* from
`raw_response` (`_dd_from_raw`) so older Property-Search pulls still work and we
don't need extra doctype columns.
"""

import json

import frappe
from frappe import _

TAX_PULL_DOCTYPE = "CRM Property Tax Pull"

# Headline fields mirrored onto the CRM Lead (only written when BatchData returns
# a value — never blanks out existing lead data). Guarded by has_field so the app
# still runs before the ops repo adds these custom fields.
LEAD_WRITEBACK_FIELDS = (
	"apn",
	"property_owner",
	"tax_status",
	"annual_tax",
	"assessed_value",
	"last_tax_pull_at",
	"last_tax_pull_by",
)


def _num(value):
	"""BatchData sends assessor money as ints; treat 0 as 'no data', not $0."""
	try:
		n = float(value)
	except (TypeError, ValueError):
		return None
	return n or None


def _iso_date(value):
	"""BatchData timestamps → YYYY-MM-DD, or None."""
	if not value or not isinstance(value, str):
		return None
	return value[:10]


def _listing_tax(p: dict):
	"""Newest listing.taxes row that has an amount (assessor block is often empty)."""
	rows = (p.get("listing") or {}).get("taxes") or []
	with_amt = [t for t in rows if isinstance(t, dict) and t.get("amount")]
	if not with_amt:
		return None
	with_amt.sort(key=lambda t: t.get("year") or 0, reverse=True)
	return with_amt[0]


def _parse_property(p: dict) -> dict:
	"""Flatten a BatchData property record into our pull-doc columns."""
	owner = p.get("owner") or {}
	ids = p.get("ids") or {}
	tax = p.get("tax") or {}
	assessment = p.get("assessment") or {}
	valuation = p.get("valuation") or {}
	quick = p.get("quickLists") or {}
	listing_tax = _listing_tax(p)

	delinquent_year = tax.get("taxDelinquentYear")
	annual = _num(tax.get("taxAmount")) or (_num(listing_tax.get("amount")) if listing_tax else None)
	tax_year = int(tax.get("taxYear") or (listing_tax or {}).get("year") or 0)
	# Currency/Int columns on the pull doc are NOT NULL DEFAULT 0 — store 0 (not
	# None) for "no data". The lead writeback still treats 0 as falsy and skips it.
	parsed = {
		"matched": 1 if (owner.get("fullName") or ids.get("apn")) else 0,
		"owner_name": owner.get("fullName"),
		"owner_occupied": 1 if owner.get("ownerOccupied") else 0,
		"owner_status_type": owner.get("ownerStatusType"),
		"apn": ids.get("apn"),
		"tax_id": ids.get("taxId"),
		"annual_tax": annual or 0,
		"tax_year": tax_year,
		"tax_delinquent_year": int(delinquent_year or 0),
		"tax_default": 1 if quick.get("taxDefault") else 0,
		"assessed_value": _num(assessment.get("totalAssessedValue")) or 0,
		"estimated_value": _num(valuation.get("estimatedValue")) or 0,
		"tax_status": _derive_tax_status(tax, assessment, quick),
	}
	return parsed


def _dd_from_raw(p: dict) -> dict:
	"""Compact records tables for the Tax Info card / comps panel."""
	if not isinstance(p, dict) or not p:
		return {}
	owner = p.get("owner") or {}
	mailing = owner.get("mailingAddress") or {}
	quick = p.get("quickLists") or {}
	open_lien = p.get("openLien") or {}
	fc = p.get("foreclosure") or {}
	valuation = p.get("valuation") or {}

	deeds = []
	for d in p.get("deedHistory") or []:
		if not isinstance(d, dict):
			continue
		deeds.append(
			{
				"date": _iso_date(d.get("recordingDate") or d.get("saleDate")),
				"type": d.get("documentType"),
				"buyers": d.get("buyers") or [],
				"sellers": d.get("sellers") or [],
				"price": _num(d.get("salePrice")),
				"foreclosure": bool(d.get("foreclosure")),
				"doc": d.get("documentNumber"),
			}
		)
	# Newest first — BatchData often returns chronological.
	deeds.sort(key=lambda r: r.get("date") or "", reverse=True)

	mortgages = []
	for m in p.get("mortgageHistory") or []:
		if not isinstance(m, dict):
			continue
		mortgages.append(
			{
				"date": _iso_date(m.get("recordingDate") or m.get("saleDate")),
				"lender": m.get("lenderName"),
				"amount": _num(m.get("loanAmount")),
				"type": m.get("loanType"),
				"rate": m.get("interestRate"),
				"borrowers": m.get("borrowers") or [],
			}
		)
	mortgages.sort(key=lambda r: r.get("date") or "", reverse=True)

	taxes = []
	for t in (p.get("listing") or {}).get("taxes") or []:
		if isinstance(t, dict) and t.get("amount"):
			taxes.append({"year": t.get("year"), "amount": _num(t.get("amount"))})
	taxes.sort(key=lambda r: r.get("year") or 0, reverse=True)

	foreclosure = None
	if fc.get("status") or fc.get("documentType") or fc.get("borrowerName"):
		foreclosure = {
			"status": fc.get("status"),
			"type": fc.get("documentType"),
			"date": _iso_date(fc.get("recordingDate") or fc.get("filingDate")),
			"auction": _iso_date(fc.get("auctionDate")),
			"case": fc.get("caseNumber") or fc.get("trusteeSaleNumber"),
			"borrower": fc.get("borrowerName"),
			"trustee": fc.get("trusteeName") or fc.get("currentLenderName"),
		}

	return {
		"mailing": " ".join(
			filter(
				None,
				[
					mailing.get("street"),
					mailing.get("city"),
					mailing.get("state"),
					mailing.get("zip"),
				],
			)
		)
		or None,
		"open_lien_count": int(open_lien.get("totalOpenLienCount") or 0),
		"tax_default": bool(quick.get("taxDefault")),
		"free_and_clear": bool(quick.get("freeAndClear")),
		"vacant": bool((p.get("general") or {}).get("vacant")),
		"equity_percent": valuation.get("equityPercent"),
		"foreclosure": foreclosure,
		"deeds": deeds,
		"mortgages": mortgages,
		"taxes": taxes[:12],
	}


def _derive_tax_status(tax: dict, assessment: dict, quick: dict) -> str:
	"""Human-readable tax standing from the flags BatchData returns.

	Order matters: a delinquency/default signal trumps a 'current' read. When the
	Tax Assessor product is off (no tax/assessment data at all) we fall back to
	the foreclosure quick-list flags, else report status unknown.
	"""
	delinquent_year = tax.get("taxDelinquentYear")
	if delinquent_year:
		return _("Tax delinquent since {0}").format(delinquent_year)
	if quick.get("taxDefault"):
		return _("In tax default")
	if quick.get("preforeclosure") or quick.get("noticeOfDefault"):
		return _("Pre-foreclosure")
	# Has assessor data and no delinquency signal → current.
	if tax.get("taxAmount") or tax.get("taxYear") or assessment.get("totalAssessedValue"):
		year = tax.get("taxYear")
		return _("Taxes current (as of {0})").format(year) if year else _("Taxes current")
	if quick.get("freeAndClear") and not quick.get("taxDefault"):
		return _("Taxes current")
	return _("Unknown")


def on_tax_pull_insert(doc, method=None):
	"""after_insert hook on CRM Property Tax Pull — parse + writeback + realtime."""
	try:
		record = json.loads(doc.get("raw_response") or "{}")
	except (ValueError, TypeError):
		record = {}

	parsed = _parse_property(record if isinstance(record, dict) else {})

	# The server script can't reliably stamp a time in the sandbox; fall back to
	# the doc's own creation timestamp so pulled_at is always populated.
	pulled_at = doc.get("pulled_at") or doc.creation
	parsed["pulled_at"] = pulled_at

	# 1) Persist the flattened columns on the pull doc itself.
	frappe.db.set_value(doc.doctype, doc.name, parsed, update_modified=False)

	# 2) Mirror the headline fields onto the lead (only non-empty values).
	if doc.get("lead") and frappe.db.exists("CRM Lead", doc.lead):
		lead_meta = frappe.get_meta("CRM Lead")
		writeback = {}
		if parsed.get("apn") and lead_meta.has_field("apn"):
			writeback["apn"] = parsed["apn"]
		if parsed.get("owner_name") and lead_meta.has_field("property_owner"):
			writeback["property_owner"] = parsed["owner_name"]
		if parsed.get("tax_status") and lead_meta.has_field("tax_status"):
			writeback["tax_status"] = parsed["tax_status"]
		if parsed.get("annual_tax") and lead_meta.has_field("annual_tax"):
			writeback["annual_tax"] = parsed["annual_tax"]
		if parsed.get("assessed_value") and lead_meta.has_field("assessed_value"):
			writeback["assessed_value"] = parsed["assessed_value"]
		if lead_meta.has_field("last_tax_pull_at"):
			writeback["last_tax_pull_at"] = pulled_at
		if lead_meta.has_field("last_tax_pull_by"):
			writeback["last_tax_pull_by"] = doc.get("pulled_by") or doc.owner
		if writeback:
			frappe.db.set_value("CRM Lead", doc.lead, writeback)

	# 3) Live-refresh every open client (Activity feed + Tax Info card). Site-wide,
	# after commit so a listener's reload can't read pre-commit rows.
	subject = doc.get("property") or doc.get("lead")
	doctype = "CRM Property" if doc.get("property") else "CRM Lead"
	if subject:
		frappe.publish_realtime(
			"crm_tax_pull",
			{"reference_doctype": doctype, "reference_docname": subject},
			after_commit=True,
		)


@frappe.whitelist()
def get_tax_pulls(lead: str):
	"""Tax-info pulls for a lead or scratch CRM Property, most recent first."""
	is_prop = str(lead or "").startswith("PROP-")
	subject_dt = "CRM Property" if is_prop else "CRM Lead"
	if not frappe.db.exists(subject_dt, lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission(subject_dt, "read", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.db.exists("DocType", TAX_PULL_DOCTYPE):
		return []

	meta = frappe.get_meta(TAX_PULL_DOCTYPE)
	filters = {"property": lead} if is_prop and meta.has_field("property") else {"lead": lead}
	pulls = frappe.get_all(
		TAX_PULL_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"pulled_by",
			"pulled_at",
			"creation",
			"cost",
			"source",
			"matched",
			"owner_name",
			"owner_occupied",
			"owner_status_type",
			"apn",
			"tax_id",
			"annual_tax",
			"tax_year",
			"tax_delinquent_year",
			"tax_default",
			"tax_status",
			"assessed_value",
			"estimated_value",
			"raw_response",
		],
		order_by="creation desc",
	)
	for pull in pulls:
		pull["pulled_by_name"] = frappe.get_cached_value("User", pull.pulled_by, "full_name") if pull.pulled_by else None
		try:
			raw = json.loads(pull.pop("raw_response", None) or "{}")
		except (ValueError, TypeError):
			raw = {}
			pull.pop("raw_response", None)
		pull["dd"] = _dd_from_raw(raw if isinstance(raw, dict) else {})
	return pulls

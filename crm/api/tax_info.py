"""Property tax / owner / lien info (per-lead pull, ~$0.10 on RealEstateAPI).

A user clicks **Fetch Tax Info** on a lead or the comps page → the `pull-tax-info`
server script (ops repo, `../frappe-crm-deploy`) hits RealEstateAPI's
`/v2/PropertyDetail` and stores the raw property record on a **CRM Property Tax
Pull** doc. Until 2026-09-28 it used BatchData (`lookup/all-attributes`, $0.22);
those rows stay readable: a RealEstateAPI record is first converted into the
BatchData shape (`_normalize`), and every parser below reads that one shape. The sandbox can't parse
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


def _is_reapi(p: dict) -> bool:
	"""RealEstateAPI PropertyDetail `data` vs a BatchData property record."""
	return isinstance(p, dict) and any(k in p for k in ("taxInfo", "ownerInfo", "lotInfo", "saleHistory"))

def _normalize(p: dict) -> dict:
	"""Map a RealEstateAPI record onto the BatchData fields we read; pass BatchData through."""
	if not _is_reapi(p):
		return p if isinstance(p, dict) else {}
	owner = p.get("ownerInfo") or {}
	tax = p.get("taxInfo") or {}
	lot = p.get("lotInfo") or {}
	mail = owner.get("mailAddress") or {}

	names = [n for n in (owner.get("owner1FullName"), owner.get("owner2FullName")) if n]
	if owner.get("corporateOwned") or owner.get("companyName"):
		status = "Company"
	else:
		status = owner.get("owner1Type")

	# Only an ACTIVE foreclosure filing is a current foreclosure; RealEstateAPI also
	# lists old, resolved ones (e.g. a 2008 lis pendens on a house sold since).
	active = [f for f in (p.get("foreclosureInfo") or []) if isinstance(f, dict) and f.get("active")]
	active.sort(key=lambda f: f.get("recordingDate") or "", reverse=True)
	fc = active[0] if active else {}

	deeds = []
	for d in p.get("saleHistory") or []:
		if isinstance(d, dict):
			deeds.append(
				{
					"recordingDate": d.get("recordingDate"),
					"saleDate": d.get("saleDate"),
					"documentType": d.get("documentType"),
					"buyers": [d["buyerNames"]] if d.get("buyerNames") else [],
					"sellers": [d["sellerNames"]] if d.get("sellerNames") else [],
					"salePrice": d.get("saleAmount"),
					"documentNumber": d.get("documentNumber"),
				}
			)

	mortgages = []
	for m in p.get("mortgageHistory") or []:
		if isinstance(m, dict):
			mortgages.append(
				{
					"recordingDate": m.get("recordingDate") or m.get("documentDate"),
					"lenderName": m.get("lenderName"),
					"loanAmount": m.get("amount"),
					"loanType": m.get("loanType"),
					"interestRate": m.get("interestRate") or None,  # 0 means "not recorded"
					"borrowers": [m["granteeName"]] if m.get("granteeName") else [],
				}
			)

	year = tax.get("year") or tax.get("assessmentYear")
	return {
		"owner": {
			"fullName": " & ".join(names) or None,
			"ownerOccupied": owner.get("ownerOccupied"),
			"ownerStatusType": status,
			"mailingAddress": {
				"street": mail.get("address"),
				"city": mail.get("city"),
				"state": mail.get("state"),
				"zip": mail.get("zip"),
			},
		},
		"ids": {"apn": lot.get("apn")},
		"tax": {
			"taxAmount": tax.get("taxAmount"),
			"taxYear": year,
			"taxDelinquentYear": tax.get("taxDelinquentYear"),
		},
		"assessment": {"totalAssessedValue": tax.get("assessedValue")},
		"valuation": {"estimatedValue": p.get("estimatedValue"), "equityPercent": p.get("equityPercent")},
		"quickLists": {
			"taxDefault": bool(p.get("taxLien")),
			"preforeclosure": bool(p.get("preForeclosure")),
			"freeAndClear": bool(p.get("freeClear")),
		},
		"openLien": {"totalOpenLienCount": len(p.get("currentMortgages") or [])},
		"foreclosure": {
			"status": "Active" if fc else None,
			"documentType": fc.get("documentType"),
			"recordingDate": fc.get("recordingDate"),
			"auctionDate": fc.get("auctionDate"),
			"caseNumber": fc.get("caseNumber") or fc.get("trusteeSaleNumber"),
			"borrowerName": fc.get("borrowerName"),
			"trusteeName": fc.get("trusteeFullName"),
			"currentLenderName": fc.get("lenderName"),
		}
		if fc
		else {},
		"deedHistory": deeds,
		"mortgageHistory": mortgages,
		"listing": {"taxes": [{"year": year, "amount": tax.get("taxAmount")}] if tax.get("taxAmount") else []},
		"general": {"vacant": p.get("vacant")},
	}

def _listing_tax(p: dict):
	"""Newest listing.taxes row that has an amount (assessor block is often empty)."""
	rows = (p.get("listing") or {}).get("taxes") or []
	with_amt = [t for t in rows if isinstance(t, dict) and t.get("amount")]
	if not with_amt:
		return None
	with_amt.sort(key=lambda t: t.get("year") or 0, reverse=True)
	return with_amt[0]


def _parse_property(p: dict) -> dict:
	"""Flatten a property record (either provider) into our pull-doc columns."""
	p = _normalize(p)
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
	raw = p
	p = _normalize(p)
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
		"report": _report_from_raw(raw),
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


# ---------------------------------------------------------------------------
# Full property report (the BatchLeads-style "Tax Information / Sale & Loan /
# Foreclosure & Lien" tabs). Built straight from the provider's raw record, not
# through `_normalize`, which only keeps what the compact card needs. Every
# value is optional; the frontend shows "N/A" for a missing one.
# ---------------------------------------------------------------------------


_money = _num  # a recorded $0 (no-consideration deed, unknown amount) shows as N/A


def _names(value):
	"""A list of names or one comma string → one display string."""
	if isinstance(value, (list, tuple)):
		return ", ".join(str(v) for v in value if v) or None
	return value or None


def _int(value):
	try:
		return int(float(value)) or None
	except (TypeError, ValueError):
		return None


def _address(*parts):
	return ", ".join(str(x) for x in parts if x) or None


def _compact(rows):
	"""Drop rows whose every value is empty."""
	return [r for r in rows if any(v not in (None, "", [], False) for v in r.values())]


def _report_from_raw(p: dict) -> dict:
	if not isinstance(p, dict) or not p:
		return {}
	report = _reapi_report(p) if _is_reapi(p) else _batchdata_report(p)
	for key in ("transactions", "loans"):
		report[key].sort(key=lambda r: r.get("recording_date") or r.get("sale_date") or "", reverse=True)
	report["current_loans"].sort(key=lambda r: r.get("recording_date") or "", reverse=True)
	report["foreclosures"].sort(key=lambda r: r.get("recording_date") or "", reverse=True)
	report["liens"].sort(key=lambda r: r.get("recording_date") or r.get("filing_date") or "", reverse=True)
	return report


def _reapi_report(p: dict) -> dict:
	owner = p.get("ownerInfo") or {}
	mail = owner.get("mailAddress") or {}
	tax = p.get("taxInfo") or {}
	lot = p.get("lotInfo") or {}
	zip_code = "-".join(z for z in (mail.get("zip"), mail.get("zip4")) if z)

	def sale(d):
		return {
			"sale_date": _iso_date(d.get("saleDate")),
			"recording_date": _iso_date(d.get("recordingDate")),
			"price": _money(d.get("saleAmount")),
			"doc_number": d.get("documentNumber"),
			"doc_type": d.get("documentType"),
			"buyers": _names(d.get("buyerNames")),
			"sellers": _names(d.get("sellerNames")),
			"transaction_type": d.get("transactionType"),
			"purchase_method": d.get("purchaseMethod"),
		}

	def loan(m):
		return {
			"recording_date": _iso_date(m.get("recordingDate") or m.get("documentDate")),
			"loan_type": m.get("loanType"),
			"amount": _money(m.get("amount")),
			"lender": m.get("lenderName"),
			"due_date": _iso_date(m.get("maturityDate")),
			"term_months": _int(m.get("term")) if (m.get("termType") or "Month") == "Month" else None,
			"rate": _num(m.get("interestRate")),
			"position": m.get("position"),
			"open": m.get("open"),
			"doc_number": m.get("documentNumber"),
		}

	transactions = [sale(d) for d in p.get("saleHistory") or [] if isinstance(d, dict)]
	transactions.sort(key=lambda r: r.get("recording_date") or "", reverse=True)
	last_sale = sale(p["lastSale"]) if isinstance(p.get("lastSale"), dict) and p["lastSale"] else None
	# RealEstateAPI has no separate "last transfer": the newest recorded deed is it,
	# shown only when it is not the last sale itself (a later quitclaim, etc.).
	last_transfer = None
	if transactions:
		newest = transactions[0]
		if not last_sale or (newest["recording_date"], newest["doc_type"]) != (
			last_sale["recording_date"],
			last_sale["doc_type"],
		):
			last_transfer = newest

	current = [loan(m) for m in p.get("currentMortgages") or [] if isinstance(m, dict)]
	# RealEstateAPI only estimates the payment/balance for the property as a whole;
	# with a single open loan that IS the loan's estimate.
	est_payment = _money(p.get("estimatedMortgagePayment"))
	est_balance = _money(p.get("openMortgageBalance") or p.get("estimatedMortgageBalance"))
	if len(current) == 1:
		current[0]["est_payment"] = est_payment
		current[0]["est_balance"] = est_balance

	foreclosures = []
	for f in p.get("foreclosureInfo") or []:
		if not isinstance(f, dict):
			continue
		foreclosures.append(
			{
				"active": bool(f.get("active")),
				"doc_number": f.get("documentNumber"),
				"doc_type": f.get("documentType"),
				"status": "Active" if f.get("active") else "Not active",
				"unpaid_amount": _money(f.get("defaultAmount")),
				"opening_bid": _money(f.get("openingBid")),
				"judgment_amount": _money(f.get("judgmentAmount")),
				"original_loan_amount": _money(f.get("originalLoanAmount")),
				"borrower": f.get("borrowerName"),
				"lender": f.get("lenderName"),
				"lender_phone": f.get("lenderPhone"),
				"recording_date": _iso_date(f.get("recordingDate")),
				"auction_date": _iso_date(f.get("auctionDate")),
				"auction_time": f.get("auctionTime"),
				"auction_location": f.get("auctionStreetAddress"),
				"default_date": None,
				"loan_number": None,
				"trustee": f.get("trusteeFullName"),
				"trustee_address": f.get("trusteeAddress"),
				"trustee_phone": f.get("trusteePhone"),
				"care_of": None,
				"case_number": f.get("caseNumber"),
				"trustee_sale_number": f.get("trusteeSaleNumber"),
			}
		)

	return {
		"source": "RealEstateAPI",
		"tax": {
			"parcel": lot.get("apn"),
			"owner": " & ".join(n for n in (owner.get("owner1FullName"), owner.get("owner2FullName")) if n)
			or owner.get("companyName"),
			"property_tax": _money(tax.get("taxAmount")),
			"tax_year": _int(tax.get("year")),
			"delinquent_year": _int(tax.get("taxDelinquentYear")),
			"total_assessed": _money(tax.get("assessedValue")),
			"assessed_land": _money(tax.get("assessedLandValue")),
			"assessed_improvement": _money(tax.get("assessedImprovementValue")),
			"assessment_year": _int(tax.get("assessmentYear")),
			"total_market": _money(tax.get("marketValue")),
			"market_land": _money(tax.get("marketLandValue")),
			"market_improvement": _money(tax.get("marketImprovementValue")),
			# RealEstateAPI dates the market value by the assessment roll.
			"market_value_year": _int(tax.get("assessmentYear")) if tax.get("marketValue") else None,
			"mailing": _address(mail.get("address"), mail.get("city"), " ".join(x for x in (mail.get("state"), zip_code) if x)),
		},
		"last_sale": last_sale,
		"last_transfer": last_transfer,
		"transactions": transactions,
		"current_loans": current,
		"loan_totals": {"open_balance": est_balance, "est_payment": est_payment},
		"loans": [loan(m) for m in p.get("mortgageHistory") or [] if isinstance(m, dict)],
		"foreclosures": foreclosures,
		# PropertyDetail carries only yes/no lien flags; the documents themselves are
		# RealEstateAPI's separate (paid) involuntary-liens report.
		"liens": [],
		"lien_flags": {
			"tax_lien": p.get("taxLien"),
			"lien": p.get("lien"),
			"judgment": p.get("judgment"),
		},
	}


def _batchdata_report(p: dict) -> dict:
	owner = p.get("owner") or {}
	mail = owner.get("mailingAddress") or {}
	tax = p.get("tax") or {}
	assessment = p.get("assessment") or {}
	sale_block = p.get("sale") or {}
	fc = p.get("foreclosure") or {}
	zip_code = "-".join(str(z) for z in (mail.get("zip"), mail.get("zipPlus4")) if z)

	def sale(d, buyers_key="buyers", sellers_key="sellers"):
		if not isinstance(d, dict):
			return None
		row = {
			"sale_date": _iso_date(d.get("saleDate")),
			"recording_date": _iso_date(d.get("recordingDate")),
			"price": _money(d.get("salePrice") or d.get("price")),
			"doc_number": d.get("documentNumber"),
			"doc_type": d.get("documentType"),
			"buyers": _names(d.get(buyers_key)),
			"sellers": _names(d.get(sellers_key)),
			"transaction_type": d.get("transactionType"),
			"new_construction": d.get("newConstruction"),
		}
		return row if any(v for k, v in row.items() if k != "price") else None

	open_lien = p.get("openLien") or {}
	current = []
	for m in open_lien.get("mortgages") or []:
		if isinstance(m, dict):
			current.append(
				{
					"recording_date": _iso_date(m.get("recordingDate")),
					"loan_type": m.get("loanType"),
					"amount": _money(m.get("loanAmount")),
					"lender": m.get("lenderName") or m.get("assignedLenderName"),
					"due_date": _iso_date(m.get("dueDate")),
					"term_months": _int(m.get("loanTermMonths")),
					"rate": _num(m.get("currentEstimatedInterestRate")),
					"est_payment": _money(m.get("estimatedPaymentAmount")),
					"est_balance": _money(m.get("currentEstimatedBalance")),
				}
			)
	loans = []
	for m in p.get("mortgageHistory") or []:
		if isinstance(m, dict):
			loans.append(
				{
					"recording_date": _iso_date(m.get("recordingDate") or m.get("documentDate")),
					"loan_type": m.get("loanType"),
					"amount": _money(m.get("loanAmount")),
					"lender": m.get("lenderName"),
					"due_date": _iso_date(m.get("dueDate")),
					"term_months": _int(m.get("loanTermMonths")),
					"rate": _num(m.get("interestRate")),
				}
			)

	foreclosures = []
	if fc:
		auction = _address(fc.get("auctionLocation"), fc.get("auctionCity"))
		foreclosures.append(
			{
				"active": None,
				"doc_number": fc.get("documentNumber"),
				"doc_type": fc.get("documentType"),
				"status": fc.get("status"),
				"unpaid_amount": _money(fc.get("unpaidBalance") or fc.get("pastDueAmount")),
				"opening_bid": _money(fc.get("auctionMinimumBidAmount")),
				"judgment_amount": _money(fc.get("judgmentAmount")),
				"original_loan_amount": _money(fc.get("originalLoanAmount")),
				"borrower": fc.get("borrowerName"),
				"lender": fc.get("currentLenderName"),
				"lender_phone": None,
				"recording_date": _iso_date(fc.get("recordingDate") or fc.get("filingDate")),
				"auction_date": _iso_date(fc.get("auctionDate")),
				"auction_time": fc.get("auctionTime"),
				"auction_location": auction,
				"default_date": _iso_date(fc.get("defaultDate")),
				"loan_number": fc.get("loanNumber"),
				"trustee": fc.get("trusteeName"),
				"trustee_address": fc.get("trusteeAddress"),
				"trustee_phone": fc.get("trusteePhone"),
				"care_of": fc.get("trusteeCareOfName"),
				"case_number": fc.get("caseNumber"),
				"trustee_sale_number": fc.get("trusteeSaleNumber"),
			}
		)

	liens = []
	for lien in (p.get("involuntaryLien") or {}).get("liens") or []:
		if isinstance(lien, dict):
			liens.append(
				{
					"doc_number": lien.get("documentNumber"),
					"doc_type": lien.get("documentType"),
					"lien_type": lien.get("lienType"),
					"lien_amount": _money(lien.get("lienAmount")),
					"judgment_amount": _money(lien.get("judgmentAmount")),
					"filing_date": _iso_date(lien.get("filingDate")),
					"recording_date": _iso_date(lien.get("recordingDate")),
					"tax_period_end": _iso_date(lien.get("taxPeriodEndDate")),
				}
			)

	quick = p.get("quickLists") or {}
	return {
		"source": "BatchData",
		"tax": {
			"parcel": (p.get("ids") or {}).get("apn"),
			"owner": owner.get("fullName"),
			"property_tax": _money(tax.get("taxAmount")),
			"tax_year": _int(tax.get("taxYear")),
			"delinquent_year": _int(tax.get("taxDelinquentYear")),
			"total_assessed": _money(assessment.get("totalAssessedValue")),
			"assessed_land": _money(assessment.get("assessedLandValue")),
			"assessed_improvement": _money(assessment.get("assessedImprovementValue")),
			"assessment_year": _int(assessment.get("assessmentYear")),
			"total_market": _money(assessment.get("totalMarketValue")),
			"market_land": _money(assessment.get("landMarketValue")),
			"market_improvement": _money(assessment.get("improvementMarketValue")),
			"market_value_year": _int(assessment.get("marketValueYear")),
			"mailing": _address(mail.get("street"), mail.get("city"), " ".join(x for x in (mail.get("state"), zip_code) if x)),
		},
		"last_sale": sale(sale_block.get("lastSale"), "saleBuyers", "saleSellers"),
		"last_transfer": sale(sale_block.get("lastTransfer"), "saleBuyers", "saleSellers"),
		"transactions": [r for r in (sale(d) for d in p.get("deedHistory") or []) if r],
		"current_loans": current,
		"loan_totals": {"open_balance": _money(open_lien.get("totalOpenLienBalance")), "est_payment": None},
		"loans": loans,
		"foreclosures": foreclosures,
		"liens": _compact(liens),
		"lien_flags": {
			"tax_lien": quick.get("taxDefault"),
			"lien": bool(liens) or None,
			"judgment": None,
		},
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

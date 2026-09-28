"""Tax-pull flattening: BatchData (legacy rows) and RealEstateAPI records."""

import unittest

from crm.tests.frappe_shim import install

install()

from crm.api.tax_info import _dd_from_raw, _is_reapi, _parse_property  # noqa: E402


POPLAR = {
	"ids": {"apn": "6157-012-010"},
	"owner": {
		"fullName": "PENNYMAC LOAN SERVICES LLC",
		"ownerOccupied": False,
		"ownerStatusType": "Company Owned",
		"mailingAddress": {
			"street": "3043 Townsgate Rd Ste 200",
			"city": "Westlake Village",
			"state": "CA",
			"zip": "91361",
		},
	},
	"tax": {},
	"assessment": {},
	"valuation": {"estimatedValue": 572374, "equityPercent": 100},
	"quickLists": {"taxDefault": False, "freeAndClear": True, "preforeclosure": False},
	"openLien": {"totalOpenLienCount": 0},
	"foreclosure": {
		"status": "Notice of Sale",
		"documentType": "Notice of Trustee Sale",
		"recordingDate": "2025-08-04T00:00:00.000Z",
		"caseNumber": "129377-CA",
		"borrowerName": "Jambon Nola T",
		"trusteeName": "Clear Recon Corp",
	},
	"deedHistory": [
		{
			"buyers": ["PENNYMAC LOAN SERVICES LLC"],
			"sellers": ["CLEAR RECON CORP"],
			"recordingDate": "2026-04-09T00:00:00.000Z",
			"documentType": "Trustee's Deed (Certificate of Title)",
			"salePrice": 574000,
			"foreclosure": True,
		},
		{
			"buyers": ["JAMBON NOLA T"],
			"sellers": ["ESCOBAR HORTENCIA"],
			"recordingDate": "2001-04-11T00:00:00.000Z",
			"documentType": "Grant Deed",
			"salePrice": 115000,
		},
	],
	"mortgageHistory": [
		{
			"recordingDate": "2012-10-26T00:00:00.000Z",
			"lenderName": "PENNYMAC LOAN SERVICES LLC",
			"loanAmount": 201465,
			"loanType": "FHA",
			"interestRate": 3.43,
		}
	],
	"listing": {
		"taxes": [
			{"year": 2026},
			{"amount": 4099.51, "year": 2025},
			{"amount": 4056.44, "year": 2024},
		]
	},
	"general": {"vacant": False},
}


class TaxInfoParseTests(unittest.TestCase):
	def test_listing_tax_fills_annual_when_assessor_block_empty(self):
		parsed = _parse_property(POPLAR)
		self.assertEqual(parsed["apn"], "6157-012-010")
		self.assertEqual(parsed["owner_name"], "PENNYMAC LOAN SERVICES LLC")
		self.assertEqual(parsed["annual_tax"], 4099.51)
		self.assertEqual(parsed["tax_year"], 2025)
		self.assertEqual(parsed["tax_status"], "Taxes current")
		self.assertEqual(parsed["estimated_value"], 572374)

	def test_dd_tables(self):
		dd = _dd_from_raw(POPLAR)
		self.assertEqual(dd["open_lien_count"], 0)
		self.assertTrue(dd["free_and_clear"])
		self.assertEqual(dd["foreclosure"]["case"], "129377-CA")
		self.assertEqual(dd["deeds"][0]["price"], 574000)
		self.assertTrue(dd["deeds"][0]["foreclosure"])
		self.assertEqual(dd["mortgages"][0]["lender"], "PENNYMAC LOAN SERVICES LLC")
		self.assertEqual(dd["taxes"][0]["year"], 2025)
		self.assertIn("Westlake Village", dd["mailing"])

	def test_empty_raw(self):
		self.assertEqual(_dd_from_raw({}), {})
		self.assertEqual(_parse_property({})["matched"], 0)


# Shape copied from a live /v2/PropertyDetail `data` (2026-09-28); values made up.
REAPI = {
	"ownerInfo": {
		"owner1FullName": "Pat Seller",
		"owner1Type": "Individual",
		"owner2FullName": "Sam Seller",
		"corporateOwned": False,
		"ownerOccupied": False,
		"mailAddress": {"address": "1 Mail Rd", "city": "Venice", "state": "FL", "zip": "34293"},
	},
	"lotInfo": {"apn": "0998-19-8204"},
	"taxInfo": {
		"assessedValue": 170400,
		"taxAmount": "2472.57",
		"taxDelinquentYear": None,
		"year": 2025,
		"assessmentYear": 2025,
	},
	"estimatedValue": 216000,
	"equityPercent": 40,
	"taxLien": False,
	"preForeclosure": False,
	"freeClear": False,
	"vacant": False,
	"currentMortgages": [{"amount": 144415}],
	"foreclosureInfo": [
		{"active": False, "documentType": "LisPendens", "recordingDate": "2008-09-19T00:00:00.000Z"}
	],
	"saleHistory": [
		{
			"recordingDate": "2021-04-06T00:00:00.000Z",
			"saleDate": "2021-03-31T00:00:00.000Z",
			"documentType": "Warranty Deed",
			"buyerNames": "Pat Seller, Sam Seller",
			"sellerNames": "Old Owner",
			"saleAmount": 169900,
			"documentNumber": "2021061729",
		},
		{"recordingDate": "2009-01-02T00:00:00.000Z", "documentType": "Quit Claim Deed", "saleAmount": 0},
	],
	"mortgageHistory": [
		{
			"recordingDate": "2021-03-31T00:00:00.000Z",
			"lenderName": "The Mortgage Firm Inc",
			"amount": 144415,
			"loanType": "Conventional",
			"interestRate": 0,
			"granteeName": "Pat Seller, Sam Seller",
		}
	],
}

BATCHDATA = {
	"owner": {"fullName": "Legacy Owner", "ownerOccupied": True, "ownerStatusType": "Individual"},
	"ids": {"apn": "LEG-1"},
	"tax": {"taxAmount": 1000, "taxYear": 2024},
	"assessment": {"totalAssessedValue": 50000},
	"quickLists": {},
	"openLien": {"totalOpenLienCount": 2},
}

class RealEstateApiTests(unittest.TestCase):
	def test_detects_provider(self):
		self.assertTrue(_is_reapi(REAPI))
		self.assertFalse(_is_reapi(BATCHDATA))
		self.assertFalse(_is_reapi({}))

	def test_headline_fields(self):
		out = _parse_property(REAPI)
		self.assertEqual(out["matched"], 1)
		self.assertEqual(out["owner_name"], "Pat Seller & Sam Seller")
		self.assertEqual(out["owner_status_type"], "Individual")
		self.assertEqual(out["owner_occupied"], 0)
		self.assertEqual(out["apn"], "0998-19-8204")
		self.assertEqual(out["annual_tax"], 2472.57)
		self.assertEqual(out["tax_year"], 2025)
		self.assertEqual(out["assessed_value"], 170400)
		self.assertEqual(out["estimated_value"], 216000)
		self.assertEqual(out["tax_default"], 0)
		self.assertEqual(out["tax_status"], "Taxes current (as of {0})".replace("{0}", "2025"))

	def test_tax_lien_and_delinquency(self):
		rec = dict(REAPI, taxLien=True)
		self.assertEqual(_parse_property(rec)["tax_status"], "In tax default")
		rec = dict(REAPI, taxInfo=dict(REAPI["taxInfo"], taxDelinquentYear=2023))
		out = _parse_property(rec)
		self.assertEqual(out["tax_delinquent_year"], 2023)
		self.assertIn("2023", out["tax_status"])

	def test_records_tables(self):
		dd = _dd_from_raw(REAPI)
		self.assertEqual(dd["mailing"], "1 Mail Rd Venice FL 34293")
		self.assertEqual(dd["open_lien_count"], 1)
		self.assertEqual(dd["equity_percent"], 40)
		self.assertEqual(len(dd["deeds"]), 2)
		self.assertEqual(dd["deeds"][0]["date"], "2021-04-06")  # newest first
		self.assertEqual(dd["deeds"][0]["price"], 169900)
		self.assertEqual(dd["deeds"][0]["buyers"], ["Pat Seller, Sam Seller"])
		self.assertEqual(dd["mortgages"][0]["lender"], "The Mortgage Firm Inc")
		self.assertIsNone(dd["mortgages"][0]["rate"])  # 0 = not recorded
		self.assertEqual(dd["taxes"], [{"year": 2025, "amount": 2472.57}])

	def test_old_inactive_foreclosure_is_not_shown(self):
		self.assertIsNone(_dd_from_raw(REAPI)["foreclosure"])

	def test_active_foreclosure_is_shown(self):
		rec = dict(
			REAPI,
			foreclosureInfo=[
				{
					"active": True,
					"documentType": "NoticeOfDefault",
					"recordingDate": "2026-08-01T00:00:00.000Z",
					"auctionDate": "2026-10-15T00:00:00.000Z",
					"caseNumber": "26-CA-1",
					"lenderName": "Some Bank",
				}
			],
		)
		fc = _dd_from_raw(rec)["foreclosure"]
		self.assertEqual(fc["type"], "NoticeOfDefault")
		self.assertEqual(fc["auction"], "2026-10-15")
		self.assertEqual(fc["trustee"], "Some Bank")

	def test_company_owner(self):
		rec = dict(REAPI, ownerInfo=dict(REAPI["ownerInfo"], corporateOwned=True))
		self.assertEqual(_parse_property(rec)["owner_status_type"], "Company")

	def test_no_match(self):
		self.assertEqual(_parse_property({})["matched"], 0)
		self.assertEqual(_dd_from_raw({}), {})

class LegacyBatchDataTests(unittest.TestCase):
	def test_old_pulls_still_parse(self):
		out = _parse_property(BATCHDATA)
		self.assertEqual(out["owner_name"], "Legacy Owner")
		self.assertEqual(out["apn"], "LEG-1")
		self.assertEqual(out["annual_tax"], 1000)
		self.assertEqual(_dd_from_raw(BATCHDATA)["open_lien_count"], 2)

if __name__ == "__main__":
	unittest.main()

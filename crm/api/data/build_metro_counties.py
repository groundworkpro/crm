"""Generate `metro_counties.json` -- which metro area each US county is in.

    python3 crm/api/data/build_metro_counties.py [--xlsx path/to/list1_2023.xlsx]

Source: the Census Bureau's 2023 OMB delineation file (list1_2023.xlsx), the
same vintage as the 393-metro list the CRM Metro Area doctype is seeded from
(../frappe-crm-deploy/scripts/data/us_metro_areas.txt). Only Metropolitan
Statistical Areas are kept, so every value here is a CRM Metro Area name.

Used by `crm.api.contractors` to put a lead in a metro: leads carry
`property_county` + `property_state` (filled on ~99.7% of them), and a
contractor covers metros, so county -> metro is the one join needed.

Keys use the same `name|N|ST` / `name|C|ST` scheme as dispo_buyers.json (N =
county, C = independent city) and are built with the same normalisation, so a
lead's county resolves identically for both lookups. Counties outside any
metro (rural, or in a micropolitan area) are simply absent.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

CBSA_XLSX = (
	"https://www2.census.gov/programs-surveys/metro-micro/geographies"
	"/reference-files/2023/delineation-files/list1_2023.xlsx"
)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_counties.json")

STATE_ABBR = {
	"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
	"California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
	"District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
	"Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
	"Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
	"Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
	"Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
	"New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
	"North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
	"Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
	"South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
	"Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
	"West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR",
}

# Mirror of crm.api.dispo_buyers._norm / _SUFFIX. Copied rather than imported
# because that module imports frappe and this script runs without a bench;
# unit_test_contractors checks the two stay in step.
_ABBR = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount"}
_SUFFIX = re.compile(
	r"\s+(county|parish|borough|census area|municipality|city and borough|city)$"
)

def _norm(s) -> str:
	if not s:
		return ""
	s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
	s = s.lower().replace("&", " and ")
	s = re.sub(r"[.'`]", "", s)
	s = re.sub(r"[^a-z0-9]+", " ", s).strip()
	return " ".join(_ABBR.get(p, p) for p in s.split())

def county_key(name: str, state: str) -> str:
	# Census writes independent cities with a LOWERCASE "city" ("Richmond city",
	# "Baltimore city"); "Carson City" / "James City County" are not.
	flag = "C" if re.search(r"\scity$", name) else "N"
	n = _SUFFIX.sub("", " " + _norm(name)).strip().replace(" ", "")
	return f"{n}|{flag}|{state}"

def _rows(blob: bytes) -> list[dict]:
	z = zipfile.ZipFile(io.BytesIO(blob))
	ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
	shared = []
	if "xl/sharedStrings.xml" in z.namelist():
		for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
			shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
	rows = []
	for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).find(f"{ns}sheetData"):
		cells = {}
		for c in row.findall(f"{ns}c"):
			v = c.find(f"{ns}v")
			if v is None:
				continue
			col = re.match(r"([A-Z]+)", c.get("r")).group(1)
			cells[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
		rows.append(cells)
	hdr_i = next(i for i, r in enumerate(rows) if "CBSA Code" in r.values())
	hdr = rows[hdr_i]
	return [{hdr[k]: v for k, v in r.items() if k in hdr} for r in rows[hdr_i + 1:]]

def build(blob: bytes) -> dict:
	counties: dict[str, str] = {}
	for d in _rows(blob):
		if d.get("Metropolitan/Micropolitan Statistical Area") != "Metropolitan Statistical Area":
			continue
		state = STATE_ABBR.get(d.get("State Name", ""))
		if not state or not d.get("County/County Equivalent"):
			continue
		key = county_key(d["County/County Equivalent"], state)
		prev = counties.get(key)
		if prev and prev != d["CBSA Title"]:
			raise RuntimeError(f"{key} maps to both {prev} and {d['CBSA Title']}")
		counties[key] = d["CBSA Title"]
	if len(set(counties.values())) < 380:
		raise RuntimeError("parsed too few metros -- the delineation file probably moved")
	return {
		"source": CBSA_XLSX,
		"vintage": "2023 OMB / Census delineation (list1_2023), Metropolitan Statistical Areas only",
		"counties": dict(sorted(counties.items())),
	}

def main(argv):
	ap = argparse.ArgumentParser()
	ap.add_argument("--xlsx", help="local copy of list1_2023.xlsx (else downloaded)")
	args = ap.parse_args(argv)
	if args.xlsx:
		blob = open(args.xlsx, "rb").read()
	else:
		req = urllib.request.Request(CBSA_XLSX, headers={"User-Agent": "Mozilla/5.0"})
		blob = urllib.request.urlopen(req, timeout=180).read()
	data = build(blob)
	with open(OUT, "w") as fh:
		json.dump(data, fh, indent=0, sort_keys=False)
		fh.write("\n")
	print(f"wrote {len(data['counties'])} counties in {len(set(data['counties'].values()))} metros -> {OUT}")

if __name__ == "__main__":
	main(sys.argv[1:])

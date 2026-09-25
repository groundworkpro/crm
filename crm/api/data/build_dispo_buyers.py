"""Generate `dispo_buyers.json` -- the CRM's copy of "who buys here?".

Run from an UP-TO-DATE checkout of ../leadmarket (which owns the scraping; the
repo was called istl-buyer when this script was written, hence the flag name):

    python3 crm/api/data/build_dispo_buyers.py --istl ~/Projects/Groundwork/leadmarket

leadmarket resolves the three differently, and the difference is why this script
exists rather than the CRM importing its modules:

  * New Western is keyed on (city, state) with a (county, state) fallback -- its
    dataset already ships that way, so it is copied across almost verbatim.
  * KeyGlee is keyed on **county FIPS**, and building FIPS needs a Zillow county
    CSV that leadmarket caches and the CRM has no reason to carry. So each
    KeyGlee territory's FIPS list is expanded HERE, once, into (county, state)
    keys. The CRM then does one kind of lookup for every buyer and ships no FIPS
    table, no Zillow dependency and no scraper.
  * ezREIdispo is FIPS too, and expanded the same way. It carries a RANK rather
    than a status, because their list is ordered and that ordering is the only
    thing on it we could not have worked out ourselves.

The county keys keep istl-buyer's `C`/`N` flag (independent city vs county)
because collapsing them is a real error: Virginia has both Richmond City and
Richmond County, Maryland has Baltimore City and Baltimore County, Missouri has
St. Louis city and St. Louis County. They are different places and a buyer can
work one and not the other.

This is a SNAPSHOT. All three open and close markets, so re-run it after
`python -m src.newwestern refresh` / `python -m src.keyglee refresh` /
`python -m src.ezrei rebuild` upstream. The generated file records where each
part came from and when.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

# --- Normalization ----------------------------------------------------------
# Deliberately a copy of the rules, not an import: this script runs against
# istl-buyer, but `dispo_buyers.py` in the CRM has to apply the IDENTICAL rules
# at runtime with no istl-buyer present. The two must agree exactly or a key
# written here is unfindable there, so they are tested against each other.

_ABBR = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount"}
_SUFFIX = re.compile(
    r"\s+(county|parish|borough|census area|municipality|city and borough|city)$"
)


def norm(s) -> str:
    """Lowercase, de-accent, de-punctuate, expand St/Ft/Mt."""
    if not s:
        return ""
    # NFKD + ascii fold BEFORE stripping non-alphanumerics. Without it "Doña Ana"
    # loses the enye entirely and normalizes to "doaana", which matches nothing
    # -- a real miss found on a live lead in Las Cruces, NM.
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return " ".join(_ABBR.get(p, p) for p in s.split())


def county_key(name, state) -> str | None:
    """'Virginia Beach City', 'VA' -> 'virginiabeach|C|VA'.

    The flag records whether the source called this an independent CITY or a
    county, so a city can only ever match a city.
    """
    if not name or not state:
        return None
    n = norm(name)
    flag = "C" if _SUFFIX.search(" " + n) and n.endswith(" city") else "N"
    n = _SUFFIX.sub("", " " + n).strip()
    return f"{n.replace(' ', '')}|{flag}|{str(state).upper()[:2]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--istl", required=True, help="path to the istl-buyer checkout")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "dispo_buyers.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.istl))
    from src import county_fips as cf  # noqa: E402

    nw_raw = json.load(open(os.path.join(args.istl, "src/data/newwestern_markets.json")))
    kg_raw = json.load(open(os.path.join(args.istl, "src/data/keyglee_markets.json")))
    ez_raw = json.load(open(os.path.join(args.istl, "src/data/ezrei_markets.json")))

    # --- New Western: city index + county index ---------------------------
    # This mirrors newwestern._load() step for step, including the overrides,
    # because the overrides are where a whole coverage TIER comes from. Skipping
    # them does not just lose a correction: Las Vegas and Tucson silently drop
    # from "Yes - NW market" to "No".
    nw_cities: dict[str, str] = {}
    nw_counties: dict[str, str] = {}
    claims: dict[str, list] = {}
    for market, m in nw_raw["markets"].items():
        for city, st in m.get("cities", []):
            nw_cities.setdefault(f"{norm(city).replace(' ', '')}|{st.upper()[:2]}", market)
        for county, st in m.get("counties", []) or []:
            k = county_key(county, st)
            if k:
                nw_counties.setdefault(k, market)
                claims.setdefault(k, []).append(market)

    ov = nw_raw.get("overrides") or {}
    # Drops first: a mis-attributed claim has to stop answering before a
    # corrected one can take its place.
    for row in ov.get("drop_cities") or []:
        nw_cities.pop(f"{norm(row[0]).replace(' ', '')}|{str(row[1]).upper()[:2]}", None)
    for row in ov.get("drop_counties") or []:
        k = county_key(row[0], row[1])
        if k:
            nw_counties.pop(k, None)
    # A re-pointed city is still on their published list -- only the market it
    # answers with changes -- so it stays a city-level claim.
    for row in ov.get("cities") or []:
        nw_cities[f"{norm(row[0]).replace(' ', '')}|{str(row[1]).upper()[:2]}"] = row[2]
    nw_asserted: dict[str, str] = {}
    for row in ov.get("counties") or []:
        county, st, market = row[0], row[1], row[2]
        k = county_key(county, st)
        if not k:
            continue
        nw_counties[k] = market  # overwrite, not setdefault
        # Re-pointing a county a market already earned through its own cities is
        # an attribution fix, not a new coverage claim, so it stays "nearby".
        # Only counties no market reached are asserted coverage.
        if market not in claims.get(k, []):
            nw_asserted[k] = market
    # Explicit promotion (mirrors newwestern._load): a county a market reached
    # through its own cities, but with evidence of a wider buy box (Atlanta's
    # stated 50mi radius). Always asserted, unlike the tie-break above.
    for row in ov.get("assert_counties") or []:
        k = county_key(row[0], row[1])
        if k:
            nw_counties[k] = row[2]
            nw_asserted[k] = row[2]

    # --- KeyGlee: FIPS -> (county, state) ---------------------------------
    # Reverse the crosswalk once. A FIPS can be reached by several spellings; we
    # only need one canonical (name, state) per code to rebuild a key.
    fips_to_key: dict[str, str] = {}
    for key, fips in cf.crosswalk().items():
        # key is 'normalizedname|C|ST' already in istl-buyer's own shape
        fips_to_key.setdefault(str(fips).zfill(5), key)

    kg_counties: dict[str, list] = {}
    unresolved = []
    for market, m in kg_raw["markets"].items():
        status = m.get("status")
        display = m.get("display") or market
        for entry in m.get("counties", []):
            fips = str(entry[0]).zfill(5)
            key = fips_to_key.get(fips)
            if not key:
                unresolved.append([fips, market])
                continue
            name, flag, st = key.split("|")
            kg_counties[f"{name}|{flag}|{st}"] = [display, status]

    # --- ezREIdispo: FIPS -> (county, state) ------------------------------
    # Same expansion as KeyGlee, but the value is [label, rank]: one positive
    # tier, so there is no status to carry, and the rank is what the badge says.
    ez_counties: dict[str, list] = {}
    ez_unresolved = []
    for c in ez_raw.get("counties") or []:
        key = fips_to_key.get(str(c["fips"]).zfill(5))
        if not key:
            ez_unresolved.append([c["fips"], c["label"]])
            continue
        ez_counties[key] = [c["label"], c["rank"]]

    out = {
        "generated_from": (
            "leadmarket (src/newwestern.py, src/keyglee.py, src/ezrei.py)"
        ),
        "new_western": {
            "source": nw_raw.get("source"),
            "retrieved": nw_raw.get("retrieved"),
            "note": nw_raw.get("note"),
            "cities": nw_cities,
            "counties": nw_counties,
            # Counties that answer at the ASSERTED tier -- coverage New Western
            # has evidence for (an office page, their market taxonomy) but does
            # not put on the locations page. Stronger than our county inference,
            # still not their published city list, so it keeps its own value.
            "asserted": sorted(nw_asserted),
        },
        "keyglee": {
            "source": kg_raw.get("source"),
            "note": kg_raw.get("note"),
            "counties": kg_counties,
            "unresolved_fips": unresolved,
        },
        "ezrei": {
            "source": ez_raw.get("source"),
            "version": ez_raw.get("version"),
            "note": ez_raw.get("note"),
            # key -> [label, rank]
            "counties": ez_counties,
            "unresolved_fips": ez_unresolved,
        },
    }
    with open(args.out, "w") as fh:
        json.dump(out, fh, separators=(",", ":"), sort_keys=True)

    print(
        f"new western: {len(nw_cities)} cities, {len(nw_counties)} counties "
        f"({len(nw_asserted)} asserted)"
    )
    print(f"keyglee:     {len(kg_counties)} counties ({len(unresolved)} FIPS unresolved)")
    print(f"ezreidispo:  {len(ez_counties)} counties "
          f"({len(ez_unresolved)} FIPS unresolved) [{ez_raw.get('version')}]")
    print(f"wrote {args.out} ({os.path.getsize(args.out):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

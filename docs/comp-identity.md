# Comp identity: the hide/pick orphaning hazard

**Status:** design only. No migration code exists and none should be written
until the option below is chosen.

**Why now:** the comps board is moving from Zillow-first to Redfin-first
(`redfin-scraper-api/bench/specs/crm-comps-rewrite-spec.md` §6.1, and the
benchmark behind it in `bench/README.md`). The rewrite cannot safely put a
Redfin row on the board until this is settled.

---

## 1. The hazard in one paragraph

A comp's `name` is its identity, and that identity currently encodes **which
vendor happened to supply the row**. `zillow_comps._shape_search` mints
`zillow::{zpid}` (`crm/api/zillow_comps.py:389-391`). Hides and picks are
persisted as JSON arrays of those names on the lead itself
(`comps.py:130-131`, written at `comps.py:927-935`). So the moment the same
house arrives from Redfin as `redfin::{property_id}` instead, **every hide and
every pick a rep made for that house stops matching** — the comp reappears on
the board, or silently drops out of the priced set. No error, no log line, no
migration failure. The lead just quietly looks different than the rep left it.

This is worse than a normal data migration because the damage is *invisible*
and *to human judgement*: the whole point of a hidden comp is that somebody
decided it was not comparable.

## 2. Exactly what is persisted

| what | where | shape |
|---|---|---|
| `comps_hidden` | `CRM Lead` / `CRM Property` column | JSON array of comp names |
| `comps_selected` | same | JSON array of comp names |
| `comps_types` | same (`comps.py:130-143`) | JSON **object keyed by comp name** |
| `sqft_override` | same | not name-keyed, unaffected |

All four are guarded by a `_supported()` probe (`_state_supported`
`comps.py:749`, `_types_supported` `comps.py:798`) because they are custom
fields created outside version control — see §6 for why that matters.

Written by `set_comp_state` (`comps.py:896`) via
`frappe.db.set_value(..., update_modified=False)`; read by `_comp_state`
(`comps.py:868`) through `_load_list` (`comps.py:857`), which **silently
returns `[]` on any parse failure**. That silence is convenient for
robustness and dangerous for migration: a half-migrated value reads as "no
hides" rather than as an error.

## 3. Every place the prefix is load-bearing

Verified by grep at the working tree, not taken from the spec:

| # | site | what it does |
|---|---|---|
| 1 | `comps.py:1143-1144` | `_shape_detail` extracts the zpid back out of the name |
| 2 | `comps.py:1387` | `get_comp_details` routes `zillow::` / `zillow-rent::` to the Zillow detail path |
| 3 | `comps.py:1395` | same for `batchdata::` |
| 4 | `comps.py:1887` | BatchData fallback gate keys on `zillow::` **and** `listing_state == "sold"` |
| 5 | `comps.py:1011` | detail cache key embeds the name |
| 6 | `redfin.py:545` | `is_istl_pool_row` — `name.startswith(("zillow", "batchdata"))` |
| 7 | `frontend/src/utils/comps.js:70,79` | `compState` / `isRentalComp` — `zillow-rent::` ⇒ `for_rent` |
| 8 | `comps.py:130-143` persisted lists | **the orphaning surface** |

Sites 1–7 are *routing* decisions and can be reworked freely: they are
recomputed on every request. Site 8 is the only one holding state that
outlives the request, and it is the only one that can be silently wrong.

Note site 6 is a prefix match, not `::`-delimited — `name.startswith("zillow")`
also matches `zillow-rent::`. Deliberate, and any new scheme must preserve it.

## 4. How many leads are affected

**These counts have to be taken ON THE BOX; they are not knowable from a dev
machine.** The CRM MariaDB runs in Docker on OVH (`frappe-crm-db-1`) and is
not reachable from a laptop — an attempt does not fail fast, it hangs until
something times out. Nothing in this repo caches the answer either. **Do not
plan the migration window without running these first**, read-only:

```sql
SELECT
  SUM(comps_hidden   IS NOT NULL AND comps_hidden   NOT IN ('', '[]')) AS leads_with_hides,
  SUM(comps_selected IS NOT NULL AND comps_selected NOT IN ('', '[]')) AS leads_with_picks,
  SUM(comps_types    IS NOT NULL AND comps_types    NOT IN ('', '{}')) AS leads_with_types,
  COUNT(*)                                                             AS leads_total
FROM `tabCRM Lead`;
```

and the same against `tabCRM Property`. Then the one that actually sizes the
risk — how many stored names carry a vendor prefix at all:

```sql
SELECT name, comps_hidden, comps_selected
FROM `tabCRM Lead`
WHERE comps_hidden LIKE '%zillow::%' OR comps_selected LIKE '%zillow::%'
LIMIT 50;
```

Run both on the box, e.g.:

```sh
# on OVH; `docker exec -i` so the heredoc reaches mysql's stdin
sudo docker exec -i frappe-crm-db-1 mysql -u<db_name> -p<db_password> <db_name>
```

with the credentials from
`/home/frappe/frappe-bench/sites/crm.groundworkpro.com/site_config.json`.

If the second query returns ~0 rows, this whole problem is theoretical and
Option A can be taken cheaply. If it returns thousands, Option B's migration
map stops being optional. **The choice below genuinely depends on that number
and I am not going to pretend otherwise.**

## 5. Options

### Option A — stable address-derived identity

Mint every comp name from the address, not the vendor:
`comps.address_key(address)` already exists (`comps.py:209`) and is exactly
this: normalised, slugified, md5-tailed, collision-resistant. It is already
the docname for real `CRM Comp` rows.

```
zillow::2081139012   ->   3362-n-22nd-st-milwaukee-wi-53206-a1b2c3d4
```

*For:* identity stops moving when the provider does, which is the actual
requirement — the cascade is explicitly designed to merge three providers, and
under Option B a house that starts arriving from Redfin instead of Zillow
changes identity *again*. One house, one name, forever. It also collapses the
`merge_key` dedupe problem (`zillow_comps.py:269`) into the identity itself.

*Against:* addresses are not perfectly stable either — Zillow writes
`3362 N 22nd STREET` where ISTL writes `3362 N 22nd St`, which is precisely
why `merge_key` exists as a *separate*, suffix-collapsing normaliser on top of
`address_key`. Using raw `address_key` would re-introduce the duplicate-pin
bug that `merge_key`'s docstring records. **If Option A is taken, the identity
must be built on `merge_key`'s normalisation, not `address_key`'s.** Also
loses the free zpid extraction at site 1, and `zillow-rent::` needs a separate
axis (a house listed for sale *and* for rent is two rows today and must stay
two) — suggest `{merge_key}:sale` / `{merge_key}:rent` rather than overloading
the prefix.

### Option B — keep vendor prefixes, ship a migration map

Keep `zillow::{zpid}`, add `redfin::{property_id}`, and maintain a translation
table so a stored `zillow::123` still resolves when the board now shows
`redfin::456`.

*For:* no change to sites 1–7; the rewrite can land provider-by-provider.

*Against:* the map has to be built from something, and the only thing that can
join a zpid to a Redfin property id **is the address** — so Option B needs
Option A's normaliser anyway, and then keeps it as a permanent second lookup
rather than as the identity. It also has to be maintained forever, re-run for
every new provider, and it fails open: an unmapped name silently reads as "not
hidden". That is the same invisible-failure mode we are trying to eliminate.

## 6. Recommendation

**Option A, built on `merge_key`'s normalisation, with a one-off backfill of
the two persisted lists.**

The deciding argument is not elegance, it is failure mode. Option B's failure
mode is a silent mismatch — identical to the bug being fixed, just rarer.
Option A's failure mode is two addresses normalising to one key, which is
*detectable before deploy* by running the normaliser over the existing comp
pool and counting collisions.

Two caveats I am not going to paper over:

- Option A is a bigger diff. Sites 1–7 all need touching, and site 1's zpid
  extraction has to move to a carried field (`row["zpid"]`) rather than being
  parsed back out of a string. That is better design regardless — parsing
  identity out of a name is what created this problem.
- If §4's second query shows a meaningful number of stored `zillow::` names,
  the backfill is mandatory and must run **before** the first Redfin row
  reaches the board, not alongside it.

## 7. Migration steps (Option A)

Do not start until §4 has real numbers.

1. **Measure.** Run the §4 queries. Record the counts in this file.
2. **Prove the normaliser.** Run the proposed key function over every distinct
   address in `tabCRM Comp` plus every address in the current Zillow area
   caches. Count collisions — two different addresses producing one key. If
   any exist, fix the normaliser before going further. This is a read-only
   script and gates everything after it.
3. **Add the resolver, dual-read.** `comps._comp_state` learns to match a
   stored name *either* exactly (today's behaviour) *or* via the new key.
   Deploy this alone. Nothing changes yet; hides and picks keep working
   exactly as now. This step is independently revertible.
4. **Backfill.** One idempotent script rewriting `comps_hidden`,
   `comps_selected` and the `comps_types` **keys** from `zillow::{zpid}` to
   the new identity, resolving zpid → address from the pin caches. Write the
   old value to a backup column or a JSON file first — `_load_list` swallows
   parse errors, so a bad write is invisible and unrecoverable without one.
   Idempotent because it will need re-running for leads touched between
   measure and cutover.
5. **Verify.** Re-run §4's second query; expect zero `zillow::` in the two
   lists. Spot-check ~20 leads that had hides, confirming the same houses are
   still hidden on the board.
6. **Mint the new identity** in `_shape_search` and the new Redfin adapter.
   Only now can a `redfin::` row safely reach the board.
7. **Drop the dual-read** from step 3 after a deploy or two, once no stored
   name carries a vendor prefix.

Steps 1–3 are safe to do now and are prerequisites for the comps rewrite.
Steps 4–7 belong with the rewrite itself.

## 8. What this does not cover

- `comps_types` is an **object keyed by comp name**, not an array. The
  backfill must rewrite keys, and a naive `json.loads` → rename → `dumps` will
  silently drop a duplicate if two old names map to one new key. Decide the
  tie-break (most recent wins? selected beats hidden?) before writing it.
- Practice mode passes its own state dict (`_parse_state_override`,
  `comps.py:874`) and never touches the lead. Unaffected, but its fixtures may
  embed `zillow::` names.
- `scripts/test_practice_chunks.py:45` stubs `get_lead_comps`; check whether
  it hardcodes comp names.

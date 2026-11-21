# Cycle 3 data export guide (Snowflake → CSV → this repo)

How to pull the D5–D7 (+ D10) extracts for the cycle 3 data pipeline when the session doing the processing
has no Snowflake access. You run each query in Snowsight (role `CAPONETEAM`), check the result, download it
as CSV and upload it.

**Where files go:** `data/raw/<export date>/`, e.g. `data/raw/2026-11-21/`. `data/` is gitignored; these files
are agency-level and must never be committed.

**SQL:** `sql/extract/*.sql`. The NetSuite queries (D5–D7) use column names confirmed by discovery. The Ceres
query (D10) still has `-- GUESS` columns until its discovery step runs.

**How to run:** paste the SQL into a **Snowsight worksheet** and run it. The download icon (↓) above the
results grid saves a CSV. Use the assistant only if a query errors ("fix this query: <error>").

**CSV hygiene:** don't open and re-save the files in Excel (it turns 11-digit tract GEOIDs into `1.21E+10`).

---

## Step 0. Discovery: done 2026-11-21

Files: `data/raw/2026-11-21/discovery_ns_columns.csv`, `discovery_ns_column_profile.md` (local only).
What it established:

- The Stitch NetSuite tables have **no VARIANT columns**. References such as `ENTITY` are plain NUMBER IDs
  that join to `<table>.ID`.
- **`CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY`** has about one row per item fulfillment, sometimes split by
  program:
  - agency `CUSTRECORD_FDS_AGENCY` → `CUSTOMER.ID`;
  - date `CUSTRECORD_FDS_TRANS_DATE`;
  - pounds `CUSTRECORD_FDS_TOTAL_WEIGHT`;
  - `CUSTRECORD_FDS_TRANS_ID` → `ITEMFULFILLMENT.ID`.
- **The summary record only starts on 2025-07-01**, so NetSuite Apr–Jun 2025 must come from somewhere else.
  D5b checks whether item fulfillments go back further; otherwise those months come from Ceres (D10).
- **Agency number** = `CUSTOMER.ENTITYID` (e.g. `HILL0113`). It matches `FTB.GOLD.AGENCIES.AGENCY_NO` for
  all 291 GOLD agencies.
- **`DISA-` / `FBFL-` / `FBKY-` are customer numbers, not item codes:** 12 DISA- (category DISASTER),
  9 FBFL- and 1 FBKY- (category FOODBANK).
- **`DELETEDRECORD` can't be used.** It has no NetSuite ID or record type, only a Stitch UUID. Proxy: keep
  only summary rows whose fulfillment still exists and isn't voided, and report the rest.
- **There's no address text on `CUSTOMER`.** The ZIP for agencies missing from GOLD is parsed from the
  ship-to address of their latest fulfillment.
- GOLD stores `ZIP_CODE` and `GEOID_AGENCES` as FLOAT. They're cast to text in the SQL.

---

## D5. Pounds per agency per month (NetSuite)

SQL: [`sql/extract/d5_ns_pounds_agency_month.sql`](../sql/extract/d5_ns_pounds_agency_month.sql). It has two
queries, and you download **both**. D5b is only a cross-check and never gets added to D5.

| Query | Save as | Columns |
|---|---|---|
| D5 (summary record) | `d5_ns_pounds_agency_month.csv` | agency_no, agency_name, month, pounds, pounds_no_fulfillment, n_records, n_records_no_fulfillment |
| D5b (fulfillment header weight) | `d5b_ns_fulfillment_weight_agency_month.csv` | agency_no, month, pounds_if_header, n_fulfillments |

**Sense-check** (queries at the bottom of the file):
- months run 2025-07 … 2026-10, and nothing later;
- `dup_keys` = 0 and `lbs_no_customer` = 0;
- **Aug 2025–Jun 2026 total ≈ 80.0M lbs.** If it's off by more than 5%, stop and tell Claude;
- `lbs_no_fulfillment` is small. Note the number;
- the side-by-side monthly totals (summary record vs. fulfillment header) should be close. Note the
  earliest month that has fulfillment data.

## D6. Agency → location

SQL: [`sql/extract/d6_agency_location.sql`](../sql/extract/d6_agency_location.sql).
**Save as:** `d6_agency_location.csv`. It has one row per customer that received food or is in GOLD.
Individuals get no name or ZIP.

**Sense-check:**
- `n_rows = n_agencies`;
- `n_both` ≈ 291;
- `n_bad_geoid` = 0;
- note `n_with_tract` and `n_no_location`;
- look at the top-10 `ship_zip` list. If one ZIP has dozens of agencies, it's probably an FTB warehouse
  (pickup orders), not where the agency is. Tell Claude which ZIP.

## D7. Exclusions: disaster, transfers to other food banks, hubs

SQL: [`sql/extract/d7_exclusion_candidates.sql`](../sql/extract/d7_exclusion_candidates.sql).
**Save as:** `d7_exclusions.csv`, **after reviewing** the `hub_candidate` and `internal_candidate` rows
(FTB decision #7).
- Hubs are found from sales orders whose external ID contains `-HUB-` (e.g. `…-POLK0060-OB-HUB-TEFAP`), or
  from names containing hub or redistribution.
- Delete any row that's ordinary local service, and add any known hub with `flag = hub`.
- Every row left in the file is excluded from the "local service" totals. It still counts in "all".

**Sense-check:** FY25-26 pounds and share by flag. Write them down.

---

## D10. Ceres history, pounds per agency per month

SQL: [`sql/extract/d10_ceres_pounds_agency_month.sql`](../sql/extract/d10_ceres_pounds_agency_month.sql).
This is now **needed**, not optional: it covers Apr–Jun 2025 (unless D5b shows NetSuite does), and it's the
source for seasonality and the hurricane overlay.

1. **Discovery.** Run queries 10.0–10.2 and save the 10.1 output as `discovery_ceres_columns.csv`. Upload it
   along with the 10.2 date-range result.
2. Claude fixes the `-- GUESS` names in 10.3.
3. You run 10.3 and save it as `d10_ceres_pounds_agency_month.csv`.

**Sense-check:**
- the first and last month;
- yearly totals (tens of millions of lbs per year);
- `dup_keys` = 0;
- the Jan–Sep 2025 monthly totals, to compare with NetSuite where the two overlap. The pipeline uses Ceres
  before the NetSuite start month and NetSuite after it, and checks the overlap months aren't counted twice.

---

## Files you download yourself (not from Snowflake)

| # | What | Where | Save as |
|---|---|---|---|
| D3 | Corrected MMG 2023 county data (our copy repeats the 2022 BLS unemployment in 2023) | Feeding America, Map the Meal Gap data request: <https://www.feedingamerica.org/research/map-the-meal-gap/by-county> (request form), or ask your FA contact for the corrected 2025-release county file | `data/external/MMG_2025_2019-2023_corrected.xlsx` |
| D4 | 2026 MMG technical brief | **Claude already downloaded it** from feedingamerica.org (2024-data release) | `data/external/mmg_brief/MMG_2026_Technical_Brief.pdf` |

To check D3: 2023 unemployment should differ from 2022 for the 10 FTB counties.

## Public data Claude downloaded (2026-11-21)

| # | What | Source | Local file |
|---|---|---|---|
| D8 | UF BEBR county population projections 2030–2050 (low/medium/high) with 2025 estimates; 2026 county estimates | <https://bebr.ufl.edu/population/population-data/> | `data/external/bebr/` |
| D9 | BLS LAUS monthly county unemployment rate, unemployed and labor force, Jan 2017–Sep 2026, 10 FTB counties (`LAUCN12xxx00000000{03,04,06}`) | BLS public API v1 | `data/external/bls/` |

## Status 2026-11-21
- **Received:** D5 (assistant's version, full range), D10, the overlap check, and D7 (`nonlocal_customers.csv`).
- **Cutover:** set to **2025-08-01** after the overlap check.
- **Still needed:**
  - D5 re-run with the 2025-08-01 floor;
  - D6 for **all** customers (the first D6 covered the Agency category only);
  - D10b Ceres customer ZIPs (query at the end of the D10 file).

## Addresses for tract geocoding (added 2026-11-21; first run done: D6b 480, D10c 919 rows)
Run and save to the raw folder:
- [`sql/extract/d6b_customer_addresses.sql`](../sql/extract/d6b_customer_addresses.sql) → `d6b_customer_addresses.csv`
- query D10c at the end of the D10 file → `d10c_ceres_customer_addresses.csv`

Claude then runs `src/data/cycle3_geocode.py`; see `docs/cycle3_data_dictionary.md`, "How to improve the matches".

## Upload checklist

```
data/raw/<date>/d5_ns_pounds_agency_month.csv
data/raw/<date>/d5b_ns_fulfillment_weight_agency_month.csv
data/raw/<date>/d6_agency_location.csv
data/raw/<date>/d7_exclusions.csv
data/raw/<date>/discovery_ceres_columns.csv        (then d10_ceres_pounds_agency_month.csv)
data/external/MMG_2025_2019-2023_corrected.xlsx    (D3)
```
In chat, also send the sense-check numbers: rows, month range, FY25-26 total, lbs_no_fulfillment, flagged
share, and any warehouse ZIP.

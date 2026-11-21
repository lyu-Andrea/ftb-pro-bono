# Cycle 3 plan: food insecurity forecast → food need → agency estimates → handoff to FTB

Status as of 2026-11-21. Branch: `cycle3-county-tract-model` on `lyu-Andrea/ftb-pro-bono`.

## Goal

1. **Forecast food insecurity (FI)** for FTB's 1,169 census tracts and 10 counties, 2025–2027.
2. **Turn it into food need in pounds**, using a stated assumption for pounds per food-insecure person.
3. **Estimate need for each partner agency**, using NetSuite distribution to see how need is currently shared out.
4. **Hand it over to FTB** as Gold tables plus Power BI, with a runbook so they can refresh it without us.

Constraints: public or FTB-owned data only (no Moody's), and keep the effort proportionate.

## Caveat to put in every FTB deliverable

> **Food distributed is an estimate of food needed, not a measure of it.** What an agency receives
> depends on supply, its capacity (storage, volunteers, opening hours), and what it orders. It can be
> well above or below the real need of the people it serves. The agency estimates show where to look,
> not proof of over- or under-supply. **FTB should set up a way to check them, i.e. agency
> feedback** (see step 6).

## Data plan: extract small aggregates to local files, then process in Python

Everything is small once aggregated, so we pull it down once and do all the cleaning in pandas,
in code that lives in the repo. Raw files go in `data/raw/` and `data/external/`, which git ignores.
The SQL extract queries are committed.

| # | Data | Source | Grain | Size | Status |
|---|---|---|---|---|---|
| D1 | MMG census tract 2020–2023 | `MMG_Consolidated.xlsx`, same as `FTB.SILVER.CENSUS_TRACT` | tract × year | 4.7K rows | have |
| D2 | MMG 2024 (national; filtered to FTB) | `MMG_2026_2024-data.xlsx` | tract, county | 85K rows | have |
| D3 | MMG county 2019–2024 (meal gap, cost per meal) | D1 + D2 County sheets | county × year | 60 rows | have |
| D4 | ALICE county 2010–2023 | `2025_ALICE_Florida_Data_Sheet.xlsx` (better than `GOLD.ALICE_REPORT`, which has one year) | county × year | 600 rows | have |
| D5 | Agencies with tract (`GEOID_AGENCES`) | `FTB.GOLD.AGENCIES` | agency | 293 rows | **need export** |
| D6 | Pounds distributed per agency per month, Apr 2025 → latest | `STITCH.SNOWFLAKE_NS_INT` (`CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY`, or `ITEMFULFILLMENT` / `SALESORDERITEM` + `ITEM`) | agency × month | ~10K rows | **need export** |
| D7 | Agency address / ZIP / county / category / status | `STITCH.SNOWFLAKE_NS_INT.CUSTOMER` | agency | ~2K rows | **need export** |
| D8 | Ordered vs. shipped quantity per agency per month | NetSuite `SALESORDERITEM` vs `ITEMFULFILLMENT` | agency × month | ~10K rows | **need export** (signal of supply shortfall) |
| D9 | *(optional)* Pre-NetSuite distribution history, incl. July 2025 | `FTB.BRONZE` Ceres item ledger, aggregated | agency × month | ~20K rows | optional |
| D10 | *(optional)* County unemployment, latest months | BLS LAUS (public) | county × month | tiny | optional, we download |
| D11 | Census tract centroids (for agency ↔ tract distance) | Census Gazetteer (public) | tract | tiny | we download |

## Steps

### 1. Data extraction (to local)
- [ ] Write `sql/extract/*.sql` for D5–D8 (and D9 if used). They're run in Snowsight, or on your machine with
      the existing key-pair loader pattern (`reporting/load_to_snowflake.py`), then exported to CSV.
- [ ] `src/data/make_dataset.py`: read raw files → write clean parquet to `data/processed/`.

### 2. Cleaning and integration (pandas)
- [ ] Standard keys: 5-digit county FIPS, 11-digit tract GEOID (as text), agency number (`CITR0003` style).
- [ ] Fix known Gold-table problems: blank and summary rows in `FTB_CENSUS_TRACK`, county names with stray
      spaces or commas, IDs stored as FLOAT, duplicate agency numbers, the "Grand Total" row in school pantries.
- [ ] NetSuite: drop records listed in `DELETEDRECORD` and dates in the future (the 2035 sales order).
      Convert everything to pounds; meals = lbs ÷ 1.2.
- [ ] Join Ceres (≤ Jul 2025) and NetSuite (≥ Apr 2025) without double-counting the overlap.
- [ ] Data checks in `src/data/validate.py`: row counts, unique keys, rates between 0 and 1, totals matching
      what FTB reports (e.g. FY25-26 ≈ 80M lbs).

### 3. FI model (v1 done; v2 tidy-up)
v1 (`scripts/cycle3_tract_model.py`): tract ridge model of each tract's position relative to the service-area
average, plus an area-wide level set by scenario (flat / recent trend). See `CHANGELOG.md` for results.
- [ ] **Uncertainty bands:** prediction intervals for each horizon, from the holdout errors.
- [ ] **Level scenario:** keep "flat" and "recent trend", and add a stress case (e.g. +2 pts, a
      recession or SNAP cut). Optionally link the level to a Florida unemployment outlook.
- [ ] **Consistency check:** tract roll-up vs. the MMG county rate for each year.
- [ ] Optional extra features: BLS county unemployment (fresher than ACS 5-year). Only keep them if the holdout improves.
- [ ] **Model choice stays simple** (ridge vs. persistence). There is too little data to justify anything heavier.

### 4. Food need in pounds (the assumption)
need_lbs(tract, year) = forecast FI persons × lbs per FI person.

Offer three assumptions side by side and let FTB choose:

| Option | Basis | Value (FTB 10 counties) | What it means |
|---|---|---|---|
| A. Full meal gap | MMG Annual Meal Gap ÷ FI persons × 1.2 lbs/meal | **≈ 228 lbs/person/yr** (2024; ~190 meals) → ~170M lbs total | Everything food-insecure people are short, from all sources (SNAP, other charities, FTB) |
| B. FTB's current share | FTB lbs distributed (12 mo) ÷ FI persons, per county | ≈ 75–80M lbs ÷ ~730K ≈ **~105 lbs/person/yr** (≈45% of A) | What FTB delivers today per food-insecure person; the base for "keep pace" planning |
| C. FTB-set target | A number FTB chooses (e.g. 50% of A) | — | A policy target |

- [ ] Publish a sensitivity table so the team can see how much the totals move with the assumption.

### 5. Agency-level estimate
For each agency: current lbs (last 12 months) vs. estimated need (2025–2027, by scenario and assumption).
- [ ] **Catchment v1 (simple):** an agency's need = its county's need × its share of the county's current
      distribution. This only tells FTB how much more each agency needs if need grows; it can't show
      under-served areas.
- [ ] **Catchment v2 (spatial):** geocode agencies (Census geocoder, or `GEOID_AGENCES`), then split each tract's
      need among agencies within X miles, weighted by distance and capacity. Tracts with high need and few
      agencies nearby come out as **under-served areas**.
- [ ] Flag each agency as *likely under / about right / likely over*, using a tolerance band (e.g. ±25%), plus the
      ordered-vs-shipped signal (D8). These are prompts to investigate, not verdicts.

### 6. Validation
| What | How | Pass criterion |
|---|---|---|
| FI model | 2024 holdout vs. persistence baselines (done). Re-test when MMG 2025 data is released (2027). | Beats or ties "keep the tract gap, shift by the area-wide change" at every horizon |
| County roll-up | Tract → county vs. the MMG county rate | Within ~1 pt |
| Data pipeline | Row counts and totals reconcile to the source and to FTB-reported totals | Exact or within 1% |
| Need / agency estimates | **No ground truth exists**, so: sensible-value checks, seasonality, review of the flagged agencies with FTB agency-relations staff | FTB staff agree the flagged list is plausible |

**Feedback system to recommend to FTB** (this is what eventually validates step 5):
- Agencies report unmet demand monthly or quarterly: people turned away, waitlists, early closures, shortages by category.
- Track NetSuite **ordered vs. shipped** and missed or cancelled appointments (`CUSTOMRECORD_FB_MHI_APPOINTMENT`).
- Track spoilage and returns as a sign of over-supply.
- Run a short annual agency survey on capacity: storage, volunteers, hours.
- Feed these back into the agency flags each cycle.

### 7. Handoff to FTB (productionize)
- [ ] **One command runs everything:** `make cycle3` → extract → clean → train → forecast → need → agency estimates → load.
- [ ] **Outputs to Snowflake `FTB.GOLD`:** `FI_FORECAST_TRACT`, `FI_FORECAST_COUNTY`, `FOOD_NEED_ESTIMATE`,
      `AGENCY_NEED_ESTIMATE`, all loaded with the existing `reporting/load_to_snowflake.py`. Power BI reads Gold
      through a future grant, so new tables show up without extra setup (see the CHANGELOG entry for 2026-08-03).
- [ ] **Power BI page:** tract map, county trend, agency table with flags, an assumption selector, and the caveat banner.
- [ ] **Documentation:** a model card (data, method, validation, limits), an assumptions register,
      a methodology note, and a refresh runbook (same style as `docs/Retrain-Tool-Setup-Guide.pdf`).
- [ ] **Refresh schedule:** retrain and re-forecast once a year when MMG publishes (spring); refresh
      distribution and agency flags monthly or quarterly. Every retrain appends to a history file (as `lag3_retrain_history.csv` does).
- [ ] **Tests:** unit tests for the cleaning functions, plus a smoke test of the whole pipeline on a small sample.
- [ ] **Walkthrough session** with the FTB team, and a named owner on their side.

## Order of work
1. Data exports D5–D8 → 2. cleaning and checks → 3. FI v2 (intervals, stress scenario) →
4. need in pounds plus sensitivity → 5. agency v1, then v2 → 6. validation review with FTB →
7. Gold tables, Power BI, docs, walkthrough.

## Decisions needed from the team
1. The pounds-per-person assumption: A, B, C, or show all three.
2. Agency catchment: v1 (county share) only, or also v2 (spatial).
3. Whether to include Ceres history (D9). It's only needed for seasonality or trends from before April 2025.
4. Final form of the deliverables: Gold tables + Power BI (recommended), or Excel.

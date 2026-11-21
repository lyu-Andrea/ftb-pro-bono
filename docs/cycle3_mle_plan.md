# Cycle 3 plan: food insecurity forecast → tract food need → handoff to FTB

Status as of 2026-11-21. Branch: `cycle3-county-tract-model` on `lyu-Andrea/ftb-pro-bono`.

## Goal and scope

FTB will use the model mainly for **annual planning**, and possibly for **quarterly or half-yearly** estimates as well (monthly is too fine for data that is published this late).

1. **Forecast food insecurity (FI)** for FTB's 1,169 census tracts, rolled up to its 10 counties.
2. **Turn it into food need in pounds per tract**, using a stated pounds-per-person assumption.
   NetSuite distribution, **totalled per tract** (not split by agency), is used to set that assumption and to
   compare current distribution with need, tract by tract.
3. **Split annual need into quarters (or halves)**, using the seasonal pattern of distribution.
4. **Hand over the model, not a dashboard.** The handoff is how to recalibrate the model each year, how to
   monitor it during the year, and a fixed output format. Connecting the outputs to the dashboard is a
   teammate's job, and our scope ends at the model outputs.

Constraints: public or FTB-owned data only (no Moody's), and keep the effort proportionate.

## Caveat to put in every FTB deliverable

> **Food distributed is an estimate of food needed, not a measure of it.** What reaches a tract depends
> on supply, agency capacity and ordering, and can be well above or below the real need there. Tract
> comparisons of distribution vs. need show where to look, not proof of over- or under-supply. **FTB
> should set up a way to check them, i.e. agency feedback** (see step 6).

## Data plan: extract small aggregates to local files, then process in Python

Raw files go in `data/raw/` and `data/external/`, which git ignores. The SQL extract queries are committed.

| # | Data | Source | Grain | Size | Status |
|---|---|---|---|---|---|
| D1 | MMG census tract 2020–2023 | `MMG_Consolidated.xlsx`, same as `FTB.SILVER.CENSUS_TRACT` | tract × year | 4.7K rows | have |
| D2 | MMG 2024 (national; filtered to FTB) | `MMG_2026_2024-data.xlsx` | tract, county | 85K rows | have |
| D3 | MMG county 2019–2024 (meal gap, cost per meal) | D1 + D2 County sheets | county × year | 60 rows | have |
| D4 | ALICE county 2010–2023 | `2025_ALICE_Florida_Data_Sheet.xlsx` (better than `GOLD.ALICE_REPORT`, which has one year) | county × year | 600 rows | have |
| D5 | Pounds distributed per agency per month, Apr 2025 → latest | `STITCH.SNOWFLAKE_NS_INT` (`CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY`, or `ITEMFULFILLMENT` / `SALESORDERITEM` + `ITEM`) | agency × month | ~10K rows | **need export** |
| D6 | Agency → tract (used only to total D5 per tract) | `FTB.GOLD.AGENCIES.GEOID_AGENCES`; NetSuite `CUSTOMER` address/ZIP for agencies missing there | agency | ~2K rows | **need export** |
| D7 | *(optional)* Pre-NetSuite distribution history, incl. July 2025 | `FTB.BRONZE` Ceres, aggregated per agency per quarter | agency × quarter | small | optional; more years give a steadier seasonal pattern |
| D11 | **Older MMG releases: national county, ~2009–2023** | Feeding America (free; request on their MMG site) | county × year | ~45K rows | **request**: needed for long-horizon backtests (step 5a) |
| D8 | *(optional)* Ordered vs. shipped quantity per month | NetSuite `SALESORDERITEM` vs `ITEMFULFILLMENT`, totalled per tract | tract × month | small | optional; a sign of supply shortfall |
| D9 | Monitoring indicators: county unemployment (BLS LAUS, monthly), SNAP caseload (FL DCF, monthly) | public | county × month | tiny | we download |
| D10 | Census tract centroids (for agencies without a tract) | Census Gazetteer / geocoder (public) | tract | tiny | we download |

## Steps

### 1. Data extraction (to local)
- [ ] Write `sql/extract/*.sql` for D5–D8. They're run in Snowsight, or locally with the existing
      key-pair pattern (`reporting/load_to_snowflake.py`), then exported to CSV.
- [ ] `src/data/make_dataset.py`: read raw files → write clean parquet to `data/processed/`.

### 2. Cleaning and integration (pandas)
- [ ] Standard keys: 5-digit county FIPS, 11-digit tract GEOID (as text).
- [ ] Fix known Gold-table problems: blank and summary rows, county names with stray spaces or commas,
      IDs stored as FLOAT, duplicate agency numbers.
- [ ] NetSuite: drop records listed in `DELETEDRECORD` and dates in the future. Convert everything to pounds (meals = lbs ÷ 1.2).
- [ ] Join Ceres and NetSuite without double-counting their overlap (Apr–Jul 2025).
- [ ] **Total distribution per tract per quarter.** Agencies are only a stepping stone here; nothing is
      reported per agency. One limitation: food is counted in the agency's own tract, not where its clients live.
- [ ] Data checks in `src/data/validate.py`: row counts, unique keys, rates between 0 and 1, totals that reconcile with
      FTB's figures (e.g. FY25-26 ≈ 80M lbs).

### 3. FI model (v1 done; v2 tidy-up)
v1 (`scripts/cycle3_tract_model.py`): tract ridge model of each tract's position relative to the service-area
average, plus an area-wide level set by scenario (flat / recent trend). See `CHANGELOG.md` for results.
- [ ] **Uncertainty bands:** prediction intervals for each horizon, from the holdout errors.
- [ ] **Level scenarios:** flat, recent trend, and a stress case (e.g. +2 pts). This is the main lever FTB adjusts during the year (step 6).
- [ ] **Consistency check:** tract roll-up vs. the MMG county rate.
- [ ] Optional: BLS county unemployment as a feature. Only keep it if the holdout improves.
- [ ] Model choice stays simple (ridge vs. persistence).

### 4. Food need in pounds, per tract and per period
Annual: need_lbs(tract, year) = forecast FI persons × lbs per FI person.

| Option | Basis | Value (FTB 10 counties) | What it means |
|---|---|---|---|
| A. Full meal gap | MMG Annual Meal Gap ÷ FI persons × 1.2 lbs/meal | **≈ 228 lbs/person/yr** (2024; ~190 meals) → ~170M lbs total | Everything food-insecure people are short, from all sources |
| B. FTB's current share | FTB lbs distributed (12 mo) ÷ FI persons, area-wide or per county | ≈ 75–80M ÷ ~730K ≈ **~105 lbs/person/yr** (≈45% of A) | What FTB delivers today per food-insecure person; base for "keep pace" planning |
| C. FTB-set target | A number FTB chooses | — | A policy target |

- [ ] Sensitivity table across A, B and C.
- [ ] **Per-tract comparison:** current tract distribution ÷ tract need under B. Flag tracts outside a tolerance band
      (e.g. ±25%) as *possibly under / possibly over-served*, as prompts to investigate.
- [ ] **Quarterly / half-yearly:** quarterly need = annual need × that quarter's seasonal share, using the
      quarterly pattern in distribution (NetSuite, plus Ceres if D7 is used). The forecast is still annual;
      quarters are an allocation of it. That needs to be stated clearly.

### 5. Validation: will a model built on old data still be accurate years later?

The data lags: MMG data for year *t* is published around *t+2*. FTB's concern is whether a model built
on (say) 2014–16 still holds up in 2018. We answer it in two stages: **backtesting** to test the
*methodology* on past data, then a **parallel run** to test the *live model* before FTB relies on it.

#### 5a. Backtesting (rolling origin, as-of): does the method work?
Pretend we're standing in origin year *s*. Train **only on what was known by then** (pairs whose target
year ≤ *s*), forecast *s+k*, and score against the actual *s+k*. Repeat for every origin and horizon.
The baselines to beat are **persistence** (use the latest published rate as-is, which is probably close to
FTB's current method) and **persistence with the correct area-wide change**.
`scripts/cycle3_tract_model.py` writes this to `results/cycle3/tract_backtest_as_of.csv`.

Results so far with tract data 2020–2024 (county roll-up MAE, percentage points):

| Horizon | Origin → target | Model, flat level | Persistence | Model, true level | Persistence, true level |
|---|---|---|---|---|---|
| 1 yr | 2021 → 2022 | 3.3 | 3.2 | 0.6 | 0.6 |
| 1 yr | 2022 → 2023 | 0.8 | 0.7 | **0.3** | 0.5 |
| 1 yr | 2023 → 2024 | 0.8 | 0.7 | 0.8 | 0.7 |
| 2 yr | 2022 → 2024 | 1.1 | 1.0 | **0.5** | 0.7 |
| 3 yr | — | not testable yet: it needs a 3-year change known before the origin | | | |

What this tells us:
- **The tract pattern part works.** When the area-wide level is right, the model is as good as or better than
  the baselines (county error 0.3–0.8 pts).
- **The area-wide level is the risk.** In 2021→2022 every method missed by about 3 pts, because of the
  post-pandemic jump. No tract data could have predicted it. With a flat level the model is only as good
  as persistence. This is why the level is a scenario, monitored during the year (6b).
- **Correction:** the earlier "3-year model beats the baseline" result (2024 holdout) trained on the 2020→2023
  change, which wasn't known in 2021. It was optimistic, and the as-of backtest replaces it.
- **Gap:** five tract years can't test 3-year horizons or periods before COVID. **Request older national
  county MMG releases (D11, ~2009–2023)** and run the same as-of backtest at county level, e.g.
  build on ≤2016 → test 2018 and 2019, build on ≤2019 → test 2022. That tests the method across normal
  years and a shock, which is exactly FTB's question.
- **Go/no-go for building the 2027 production model:** across all as-of backtests, the model must not be worse than
  persistence on average, and must be clearly better when the level is right. Otherwise FTB is told to use persistence.

#### 5b. Parallel run (shadow year): does the live model beat FTB's current method?
- **Year 1, the model runs alongside the current method and FTB doesn't act on it.** FTB plans as usual with its current
  method. Each cycle we run the model in batch and log its forecasts next to the current method's, using the same
  inputs and the same horizon, frozen with a date.
- **Write down the current method first** (e.g. "latest MMG rate × population", or "last year's pounds + x%") so it
  can be scored the same way.
- **Two scorecards, because the real answer arrives late:**
  1. **Quarterly (a proxy, not the truth):** each method's need, split by quarter, vs. actual distribution per
     tract and county. This says more about supply than need, so it can only flag big problems, not decide.
  2. **When the next MMG year is published (the real test):** score both methods' frozen forecasts for the year
     it covers. The first proper comparison comes at the first MMG release after the parallel run starts.
- **Decision rule at the end of year 1:** adopt the model if it beats or ties the current method on the MMG
  scorecard and isn't clearly worse on the proxy. Otherwise extend the parallel run, or blend the two.
- Each comparison is logged to `results/cycle3/parallel_run_log.csv` (forecast date, method, target, error).

#### 5c. Other checks
| What | How | Pass criterion |
|---|---|---|
| County roll-up | Tract → county vs. the MMG county rate | Within ~1 pt |
| Pipeline | Totals reconcile to the source and to FTB-reported figures | Within 1% |
| Seasonal pattern | Same quarter across years (once there are ≥2 years) | Stable shares |
| Tract distribution vs. need | No ground truth exists: sensible-value checks plus FTB staff review of the flagged tracts | FTB agrees the list is plausible |

### 6. Handoff package (what FTB receives)

**a. Recalibration runbook (yearly, when MMG publishes, usually in spring)**
- [ ] One command: `make cycle3` → load the new MMG year and ALICE → re-validate on the new year →
      refit → re-forecast → recompute need and seasonal shares → write outputs.
- [ ] A step-by-step guide (same style as `docs/Retrain-Tool-Setup-Guide.pdf`): where to put the new files,
      what to check in the validation report, and when *not* to promote the new model (e.g. if it does worse
      than persistence on the holdout).
- [ ] Every run appends to `results/cycle3/retrain_history.csv`: data years, settings, holdout errors.

**b. Monitoring during the year.** MMG is published once a year, so mid-year checks rely on signals that update sooner:
| Signal | Frequency | Compared against | Action if off |
|---|---|---|---|
| Distribution vs. planned need (tract and county, year to date, adjusted for season) | quarterly | annual need × seasonal shares to date | Over ±15%: investigate (supply problem vs. demand change) |
| County unemployment (BLS) | monthly | value implied by the chosen level scenario | Persistent move of more than 1 pt: switch the level scenario |
| SNAP caseload (FL DCF) | monthly | year-ago level | Large change or policy change: consider the stress scenario |
| Ordered vs. shipped (if D8 is used) | monthly | its own trend | A rising gap suggests supply shortfall, not falling need |
| **When the new MMG year is published** | yearly | last year's forecast | Record the actual error in the history file; this is the real accuracy check |
- [ ] `scripts/cycle3_monitor.py`: builds a one-page monitoring report from the latest extracts.

**c. Fixed output format for the dashboard teammate** (CSV or parquet; loading them is their job)
- [ ] `fi_forecast_tract`: tract, county, forecast year, scenario, rate, lower/upper bounds, FI persons
- [ ] `fi_forecast_county`: the same, totalled to county
- [ ] `food_need_tract`: tract, year, assumption, need lbs, current distribution lbs, ratio, flag
- [ ] `food_need_quarterly`: tract or county, quarter, scenario, assumption, need lbs
- [ ] `model_run_metadata`: run date, data years, validation errors, model version
- [ ] A data dictionary for every column.

**d. Documentation:** a model card (data, method, validation, limits), an assumptions register, a methodology note,
and the caveat above. Plus a walkthrough with the FTB owner and the dashboard teammate.

**e. Feedback system to recommend to FTB** (this is what eventually validates the tract comparisons):
- Agencies report unmet demand: people turned away, waitlists, early closures, shortages.
- Track ordered vs. shipped quantities and missed appointments (`CUSTOMRECORD_FB_MHI_APPOINTMENT`).
- Track spoilage and returns as a sign of over-supply.
- Run a short annual survey of agency capacity.

## Order of work
1. Exports D5–D6, plus the D11 request → 2. cleaning, tract totals, checks → 3. county as-of backtest on D11
(go/no-go) → 4. FI v2 (intervals, stress scenario) → 5. need in pounds, sensitivity, quarterly split →
6. runbook, monitoring, output format, docs → walkthrough → **parallel-run year** → adopt or not.

## Decisions needed from the team
1. The pounds-per-person assumption: A, B, C, or show all three (recommended).
2. Whether quarterly or half-yearly needs are wanted at v1, or annual only.
3. Whether to include Ceres history (D7) for a steadier seasonal pattern.
4. Monitoring thresholds (±15% distribution, 1 pt unemployment). These are placeholders for FTB to confirm.

# Cycle 3 plan: scenario model for food need → tract allocation → handoff to FTB

Status as of 2026-11-21. Branch: `cycle3-county-tract-model` on `lyu-Andrea/ftb-pro-bono`.
Design basis: the team's *Cycle 3 Forecasting Architecture Review* (Oct 30, 2026), updated with what we've
verified since (see "What changed since the October review").

## Goal and scope

FTB uses this for **annual planning**, and possibly **quarterly or half-yearly** checks.

1. **Scenario model of county food need.** Start from the latest official MMG level and apply published
   sensitivities and explicit, editable assumptions (unemployment, poverty, a policy/price shift, population).
2. **Allocate to census tracts** using each tract's stable share of its county's need.
3. **Convert to meals and pounds**, using FTB-owned parameters. NetSuite distribution is used **totalled per tract**
   (never per agency) to set and check coverage.
4. **Hand over the model**: a yearly recalibration runbook, monitoring during the year, and a fixed output
   format. The dashboard is a teammate's job, so our scope ends at the model outputs.

Constraints: public or FTB-owned data only (**no Moody's**), and keep the effort proportionate.

## Why scenarios rather than a fitted forecast (verified)
- **82% of year-to-year county change is one area-wide shift** (pandemic aid, its end, prices, SNAP), not local drivers.
  Our as-of tract backtest agrees: every method missed 2021→2022 by about 3 pts, and when the level is known,
  error drops to 0.3–0.8 pts at county level.
- **Too little comparable history to learn that level.** MMG data before the 2020 release isn't comparable
  (Feeding America says so), which leaves 6 county years (2019–2024) and 5 tract years (2020–2024).
- In that window food insecurity fell when unemployment spiked (2020) and jumped when unemployment was at a record low (2022).
  A local regression would get the sign of unemployment wrong.
- So the level is an **explicit assumption**, and the geography comes from **stable shares**.

## Caveat to put in every FTB deliverable
> **Food distributed is an estimate of food needed, not a measure of it.** It's limited by supply, agency
> capacity and ordering, and is counted where the agency is, not where its clients live. Comparisons of
> distribution vs. need show where to look, not proof. **FTB should set up a way to check them, i.e.
> agency feedback** (see 6e).

## Architecture

```
Layer 0 ASSUMPTIONS (one editable file, per scenario)
   base MMG year · MMG coefficients (from that year's brief) · Δunemployment · Δpoverty
   · common shift S · population path · meals short per person · coverage · lbs/meal · overlay settings
        │
Layer 1 COUNTY NEED ("how much")
   rate[c,T]   = MMG_rate[c,base] + β_unemp·Δunemp[c] + β_pov·Δpov[c] + S_T
   people[c,T] = rate[c,T] × population[c,T]          (population from BEBR projections)
        │
Layer 2 WHERE: TRACT ALLOCATION
   share[t]     = MMG FI persons[t] / Σ tracts in county   (latest year, or 2-year average; summed to county)
   people[t,T]  = people[c,T] × share[t]
   Challenger: our ridge tract model (scripts/cycle3_tract_model.py); used only if it beats shares in backtest
        │
Layer 3 CONVERT TO A PLAN
   meals_needed = people × meals short per person (MMG meal gap ÷ FI persons ≈ 190)
   FTB_meals    = meals_needed × coverage      (current coverage from NetSuite, per tract/county)
   FTB_lbs      = FTB_meals × 1.2
   quarterly    = annual × seasonal quarter share (from distribution history)
        │
Layer 4 OVERLAYS (optional)
   hurricane: baseline × in-footprint × (uplift − 1) × weeks/52 · later: in-year signals (211, SNAP caseload)
        │
OUTPUT: one table per scenario: people · meals needed · FTB meals · FTB lbs, by county and tract (annual + quarterly)
```

Every step is a multiplication a program manager can check in a spreadsheet. **New scenarios never need code changes.**

### Scenarios: fixed scenarios, not probability ranges
- **Baseline**, **economic stress**, plus a **labeled historical swing** (e.g. "a 2022-sized shift, S = +2.5 pts").
- **No P10/P50/P90.** There are only about 3–5 independent backtest errors, so a "90% interval" can't be checked.
- Menu for S, from history: 0 / +0.5 / +1.0 / +2.5 pts. FTB leadership chooses; the team advises.

### Population (Layer 0 → 1)
- County population paths from **UF BEBR** Florida county projections (public): low / medium / high series.
  Fast-growing counties (Pasco, Polk, Sumter) can move need as much as the rate does over 2–3 years.
- Tracts inherit county growth (tract-level projections are out of scope).

## What changed since the October review
| October document said | Now |
|---|---|
| Use Moody's for the unemployment path | **No Moody's** (project constraint). Use BLS LAUS history, FTB's own assumption, or public outlooks; BEBR for population |
| Coefficients 0.471 / 0.289 (2026 brief) | Read them **from the brief matching the base year**, recorded in Layer 0. The 2025 brief says 0.46 / 0.332, so they change between releases |
| 2024 tract tab not extracted | **Done**: 1,169 FTB tracts for 2024, matched to the 2023 IDs |
| No persistence baseline anywhere | **Done**: as-of backtest vs. persistence and persistence + area shift (`results/cycle3/tract_backtest_as_of.csv`) |
| 2023 county data has the BLS-unemployment error | **Confirmed** in our file (2023 = 2022 in all 10 counties). **Re-download the corrected file** before using 2023 |
| Two tract source files disagree | We use `MMG_Consolidated.xlsx` (same rows as `FTB.SILVER.CENSUS_TRACT`). Record it as the single source |
| (our earlier plan) Request MMG history back to 2009 for long backtests | **Withdrawn**: pre-2020 releases aren't comparable |
| (our earlier plan) Uncertainty bands | **Replaced** by fixed scenarios + a labeled historical swing |

## Data plan: extract small aggregates to local files, then process in Python
Raw files go in `data/raw/` and `data/external/`, which git ignores. The SQL extract queries are committed.

| # | Data | Source | Grain | Status |
|---|---|---|---|---|
| D1 | MMG census tract 2020–2023 | `MMG_Consolidated.xlsx` | tract × year | have |
| D2 | MMG 2024, tract + county | `MMG_2026_2024-data.xlsx` | tract, county | have |
| D3 | **Corrected MMG 2023 county estimates** | Feeding America (re-download) | county | **need** |
| D4 | MMG technical brief matching the base year (coefficients) | Feeding America | — | have 2025 brief; **get the 2026 one** |
| D5 | Pounds per agency per month, Apr 2025 → latest, totalled per tract | NetSuite (`STITCH.SNOWFLAKE_NS_INT`) | agency × month → tract × quarter | **need export** |
| D6 | Agency → tract | `FTB.GOLD.AGENCIES.GEOID_AGENCES`; NetSuite `CUSTOMER` ZIP for agencies missing there | agency | **need export** |
| D7 | Exclusions: disaster (`DISA-*`), transfers to other food banks (`FBFL-*`, `FBKY-*`), hub agencies | NetSuite | agency | flag in D5 |
| D8 | County population projections | UF BEBR (public) | county × year | we download |
| D9 | County unemployment, monthly | BLS LAUS (public) | county × month | we download |
| D10 | *(optional)* Ceres distribution history: seasonality, hurricane uplift (Ian 2022, Helene/Milton 2024) | `FTB.BRONZE` | agency × month | optional (Layer 4) |
| D11 | *(optional)* SNAP caseload, monthly | FL DCF (public) | county × month | in-year signal |

## Status (2026-11-21): Layers 0–4 built, first backtests run
Run with `python scripts/cycle3_scenarios.py` (outputs: `docs/cycle3_output_dictionary.md`) and
`python scripts/cycle3_backtest_scenario.py`. Data: private repo `lyu-Andrea/ftb-pro-bono-data`, cloned as `data/`.

**First results (baseline, FTB total):** 16.3% food insecure in 2026–27 (MMG 2024: 15.0%); ~867K people in 2027;
FTB plan ~97.7M lbs in 2027 at today's coverage (49.4% of need; last 4 quarters = 96.3M lbs local service).
Stress 2027: 17.8%, ~106.7M lbs. Historical swing: 18.3%, ~109.7M lbs.
Sensitivity (FTB-wide): +1 pt unemployment ≈ +25K people ≈ +2.8M lbs; +1 pt S ≈ +53K people ≈ +6.0M lbs.

**What changed vs. the October illustrative numbers:** BLS shows unemployment is **already up 1.2–2.5 pts since 2024**
in every FTB county (Hillsborough 3.4% → ~4.8% in 2026; Sumter 5.8% → ~8.3%). The baseline now starts from that
observed path, not +0.3. Coverage is computed against need in the *same period* as the pounds (not the 2024 meal gap),
so "keep current coverage" reproduces today's pounds and grows only with need.

**Backtest, Layer 1 (county, transitions touching 2023 excluded), mean FTB-wide level error:**
| Horizon | persistence | anchor + drivers, S = 0 | anchor + drivers + national S (actual) |
|---|---|---|---|
| 1 yr | 1.4 pts | 1.8 | **0.8** |
| 2 yr | 1.5 | 2.4 | **0.5** |
| 3 yr | 2.5 | 3.0 | **0.8** |
- **Drivers alone are worse than persistence** (unemployment fell 2020→22 while FI jumped). With the national shift
  (MMG year effects) added, error drops to 0.5–0.8 pts. FTB's counties track the national shift. **S is the
  single most important assumption.** (The "actual S" row uses hindsight; it tests the mechanism, not a forecast.)

**Backtest, Layer 2 (tract allocation, county totals known):**
| Horizon | Shares: misallocated / top-20% hit / Spearman | Ridge challenger |
|---|---|---|
| 1 yr | 6.6% / 81% / 0.93 | **5.4% / 83% / 0.95** |
| 2 yr | 9.5% / 72% / 0.87 | **7.0% / 79% / 0.93** |
| 3 yr | 11.8% / 67% / 0.81 | not testable yet |
- The ridge model is modestly better where it can be tested. The base year → plan year gap is ~3 years (MMG lag),
  which only shares can be tested at. **Decision for the team:** keep shares (simpler) or switch to ridge for Layer 2.

**Open data items:**
- The 3 hub agencies are excluded as non-local. One of them handles most of Citrus County's pounds, so Citrus coverage shows 7%. Decide (details in the private data repo).
- "Internal" (3.3M lbs) counts as local service. Confirm.
- 67% of high-need tracts have no agency located in them. Tract is too fine for a distribution comparison, so the
  next step is a service-area view (tract + neighbors, or ZIP).
- Corrected MMG 2023 county file still pending.

## Steps

### 1–2. Data extraction and cleaning
- [ ] SQL extracts for D5–D7 → CSV → `src/data/make_dataset.py` → `data/processed/` parquet.
- [ ] Standard keys (5-digit county FIPS, 11-digit tract GEOID as text). Fix the Gold-table problems. Drop records in NetSuite's `DELETEDRECORD` and future dates.
- [ ] Total distribution **per tract per quarter**, with D7 exclusions applied, so it reflects local client service.
- [ ] Checks: unique keys; totals reconcile with FTB's figures (FY25-26 ≈ 80M lbs); tract FI persons sum to county after rescaling.

### 3. Build Layers 0–3 (+ Layer 4 hurricane, optional)
- [ ] `config/cycle3_assumptions.yaml`: every forward-looking number, per scenario, with its source.
- [ ] `src/scenario/` with one small function per layer, and `scripts/cycle3_scenarios.py` that writes the output tables.
- [ ] Sensitivity table: +1 pt unemployment / poverty / S → people, meals, lbs.

### 4. Validation
**4a. As-of backtests: does the method work?** Build only on what was known at the origin year, then score the later year.
| Layer | Test | Baselines | Metric |
|---|---|---|---|
| 1 (county level) | Anchor + β·(actual Δdrivers), S = 0, on 2019–2024 county data | persistence; persistence + actual shift | MAE **and bias**, per horizon 1–3 yrs |
| 2 (tract allocation) | Shares from year *s* → tract people in *s+k* (county total known) | ridge challenger | tract MAE; **top-20% tract hit rate**; Spearman rank correlation; share of need that moved |
| 1–3 (scenario range) | Did the actual outcome fall between baseline and stress? | — | Descriptive only (~3–5 cases) |
Note: 2023 needs the corrected file (D3) before it's used in any county backtest.

**4b. Parallel run (shadow year).** In year 1 FTB keeps its current method and the model runs alongside, with forecasts
frozen and dated. **Write down FTB's current method first.** Two scorecards:
- **Quarterly:** each method's planned pounds vs. actual distribution, per tract and county. This is a proxy that reflects supply too.
- **When the next MMG year is published:** the real test.

Adopt the model if it ties or beats the current method on MMG and isn't clearly worse on the proxy.
Logged to `results/cycle3/parallel_run_log.csv`.

**4c. Other checks:** totals reconcile; stable quarter shares; FTB staff review of the flagged tracts (where distribution and need differ most).

### 5. Annual refresh and monitoring (part of the handoff)
**Yearly (when MMG publishes):**
1. Load the new year.
2. Re-anchor (default).
3. Update the coefficients from the new brief.
4. Recompute shares.
5. Score last year's forecast: bias, MAE, top-20% hit rate.
6. Decide: re-anchor only, update the S menu, or redevelop. If the model doesn't beat persistence for 2 refreshes in a row, simplify.

**Contingency:** USDA stopped the CPS Food Security Supplement in 2025, so **future MMG releases are uncertain**.
If MMG stops, keep the last MMG shares and coefficients, move the anchor forward with BLS and ACS changes, and validate against
distribution trends (adjusted for supply). The runbook states this explicitly.

**During the year (quarterly, light):**
| Signal | Source | Trigger → action |
|---|---|---|
| Distribution vs. plan (season-adjusted) | NetSuite | ±10–15% for 2 quarters → plan review |
| County unemployment | BLS LAUS | +1 pt vs. scenario → switch scenario |
| SNAP caseload | FL DCF | −5% vs. prior year → consider the stress S |
| Disaster declaration | FEMA | any → apply the hurricane overlay |
| 211 (future) | 211 | index > 1.2 → plan review |
A trigger prompts a **plan review**, not retraining. `scripts/cycle3_monitor.py` produces a one-page report.

### 6. Handoff package
- a. Recalibration runbook (step 5) + one command (`make cycle3`).
- b. Monitoring script + thresholds (step 5).
- c. **Output format for the dashboard teammate** (CSV/parquet + data dictionary):
  `need_county` and `need_tract` (scenario, year, people, meals needed, FTB meals, FTB lbs),
  `need_quarterly`, `tract_distribution_vs_need`, `assumptions_used`, `model_run_metadata`.
- d. Model card, assumptions register, methodology note, and the caveat. Mark the 2024–2031 recursive ZIP forecast docs as superseded.
- e. Feedback-system recommendation: agencies report unmet demand; track ordered vs. shipped quantities and missed
  appointments; track spoilage; run an annual capacity survey.
- f. Walkthrough with the FTB owner and the dashboard teammate.

## Order of work
1. Data: D3 (corrected 2023), D4 (2026 brief), D5–D7 exports, D8 BEBR.
2. Layers 0–3 + sensitivity.
3. Backtests 4a (Layer 1 on counties; Layer 2 shares vs. ridge, incl. top-20%).
4. Layer 4 hurricane (if FTB wants it; needs Ceres D10).
5. Runbook, monitoring, output format, docs → walkthrough.
6. Parallel-run year → adopt or not.

## Decisions needed from FTB
1. Which annual decision the plan supports (sourcing budget? agency allocation? board goal?).
2. Planning unit: meals, pounds, people, or all three.
3. Horizon and timing (FY+1 / FY+2; when the plan is set; fiscal year runs Jul–Jun).
4. Coverage target: keep today's ~45–50%, raise it, or set county targets (current county coverage varies ~5×).
5. Who sets the unemployment and S assumptions, and who signs off.
6. Which scenarios leadership plans with; whether to include the hurricane overlay, and its definition.
7. Exclusions for "local service" volume (disaster, transfers, hubs).
8. FTB's **current planning method**, which is the baseline for the parallel run.
9. Thresholds for the monitoring checks.

# FTB pro bono: notes for Claude sessions

Capital One Data Pro Bono project for **Feeding Tampa Bay (FTB)**. Cycle 3 = **scenario model**: county need from the latest MMG level + published
sensitivities + explicit assumptions (Layer 0–1), allocated to tracts by stable shares (Layer 2), converted to meals/lbs (Layer 3). **Read `docs/cycle3_mle_plan.md` first**: it holds the full plan,
the decisions made so far, and the open items.

## Scope and constraints
- Geography: FTB's 10 counties (Citrus, Hardee, Hernando, Highlands, Hillsborough, Manatee, Pasco, Pinellas, Polk, Sumter),
  1,169 census tracts. The output is per tract plus county roll-ups, **not ZIP**.
- **Public or FTB-owned data only. No Moody's.** Ignore the McKinsey readout.
- NetSuite distribution is used **totalled per tract** (and per quarter or half-year), never reported per agency.
  It's an *estimate* of need, not a measure of it; always add that caveat.
- Our job ends at the model outputs (fixed files + data dictionary). The dashboard / Power BI is a teammate's job.
- Validation = as-of rolling backtest (only data known at the origin year) + a parallel-run year against FTB's current method.
- Fixed scenarios (baseline / stress / labeled historical swing), **no P10/P90**. MMG data before the 2020 release isn't comparable.
- The 2023 county MMG data in our files has the BLS-unemployment error (2023 = 2022); use the corrected file.
- Keep it simple. The owner doesn't want effort beyond what the project needs.

## Data
- Snowflake (role `CAPONETEAM`): `FTB.SILVER` / `FTB.GOLD` (MMG, ALICE, agencies, school pantries),
  `FTB.BRONZE` (old Ceres ERP, until about Jul 2025), `STITCH.SNOWFLAKE_NS_INT` (NetSuite, Apr 2025 →).
  Cloud sessions have **no Snowflake credentials**; extracts are exported as CSV and uploaded.
- Data lives in the **private** repo `lyu-Andrea/ftb-pro-bono-data`, cloned as `data/` inside this repo (see its README).
  Nothing derived from FTB distribution (pounds, coverage) is committed here; it goes to `forecasts/` (gitignored).
- **`data/` and `forecasts/` are gitignored. This repo is public: never commit raw data, agency-level NetSuite data,
  keys, or account identifiers.**
- Expected file names in `data/external/`: `MMG_Consolidated.xlsx`, `MMG_2026_2024-data.xlsx`,
  `2025_ALICE_Florida_Data_Sheet.xlsx`.

## Code
- Scenario model: `config/cycle3_assumptions.yaml` (Layer 0) → `src/scenario/` → `scripts/cycle3_scenarios.py`
  (outputs to gitignored `forecasts/cycle3/scenario/`, documented in `docs/cycle3_output_dictionary.md`);
  backtests in `scripts/cycle3_backtest_scenario.py`.
- `scripts/cycle3_tract_model.py`: ridge tract model (the **challenger** to Layer 2 shares) and the as-of backtest.
  It reuses `src/models/train_model.py` (`fit_weighted_ridge`, `weighted_metrics`) and `src/features/build_features.clip_rate`.
- Earlier cycles (ZIP level): notebooks 1.2–1.6, `scripts/retrain_lag3_model.py`, `scripts/score_new_year.py`.
- Keys: county FIPS is 5 digits; tract GEOID is 11 digits, stored as text.
- Log notable changes in `CHANGELOG.md`.

## Git
- Push to the fork `lyu-Andrea/ftb-pro-bono` (upstream: `ltk1877/ftb-pro-bono`).
- Cycle 3 branch: `cycle3-county-tract-model`. Do parallel work on a branch created from it.

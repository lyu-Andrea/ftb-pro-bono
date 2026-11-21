# Changelog

## 2026-11-21 — Cycle 3 scenario model (Layers 0–4) + backtests

- `config/cycle3_assumptions.yaml`: every forward-looking number (base year 2024; MMG 2026 brief coefficients 0.471 / 0.289; BLS-observed unemployment; BEBR population; coverage; 3 scenarios; hurricane overlay off).
- `src/scenario/` (loaders + layers) and `scripts/cycle3_scenarios.py`: county need → tract shares → meals/lbs → quarterly split. All outputs go to the gitignored `forecasts/cycle3/scenario/` (see `docs/cycle3_output_dictionary.md`).
- `scripts/cycle3_backtest_scenario.py`: as-of backtests. Layer 1: drivers alone lose to persistence; adding the national year effect brings FTB-wide error to 0.5–0.8 pts. Layer 2: shares misplace 7–12% of county need over 1–3 years; the ridge challenger is modestly better at 1–2 years.
- Data now comes from the private repo `lyu-Andrea/ftb-pro-bono-data`, cloned as `data/`.

## 2026-11-21 — Cycle 3: distribution data pipeline (branch `cycle3-data-pipeline`)

FTB distribution pounds totalled per census tract and county per quarter, from Ceres (2018 → Jul 2025) and NetSuite (Aug 2025 →). Agencies are only a stepping stone to tracts; nothing is output per agency.

- **Extracts:** `sql/extract/*.sql` (run in Snowsight; CSVs go to gitignored `data/raw/<date>/`). How to run them is in `docs/cycle3_data_export_guide.md`.
- **Pipeline:** `src/data/cycle3_make_dataset.py` → `data/processed/distribution_tract_quarter`, `distribution_county_quarter`, `seasonal_quarter_shares` (parquet), plus a run log with aggregates only. `src/data/cycle3_validate.py` runs the checks. Columns and rules are in `docs/cycle3_data_dictionary.md`. These are new files: the cycle 1–2 `make_dataset.py` / `validate.py` are untouched.
- **NetSuite pounds** come from item fulfillments (order weight). The `CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY` table is a partial backfill, and `DELETEDRECORD` has no record IDs, so deletions can't be removed.
- **Cutover 2025-08-01:** on Aug 1–8 2025 both systems ran, and 94% of Ceres's pounds also appear in NetSuite for the same agency and day. Each month now comes from one source.
- **Tract mapping (fallbacks):** `FTB.GOLD.AGENCIES.GEOID_AGENCES` mixes 2010 and 2020 tract codes, and some codes are neither. Where there is no geocoded address, a GOLD tract is used only when it fits the ship-to ZIP; otherwise the ZIP's main tract, and failing that, the county from the agency number. Public Census 2020 relationship files go in `data/external/census/`.
- **Geocoding:** `src/data/cycle3_geocode.py` sends street addresses (anonymous row number, street, city, ZIP only) to the public Census Geocoder. The geocoded tract is the first location rule. 970 addresses (NetSuite ship-to + Ceres customer table) matched 91%. FY25-26: 89% of pounds placed by street address, 1% by GOLD tract, 6% by ZIP, 4% county only. GOLD's tract agreed with the geocoded tract for only 6% of agencies. Customers without a reliable tract, and how each is matched, are documented in the data dictionary. The per-customer QA list stays local (gitignored).
- **All customers, agencies separable:** outputs keep every customer and add `pounds_agency`, `pounds_agency_local`, `pounds_other_institution`, `pounds_type_unknown` (NetSuite customer category; for Ceres-only customers, the Ceres agency category mapped via the customers in both systems). The 80.0M check compares the agency total: 80.9M (+1.2%). All customers total 89.9M (schools, internal, ...).
- **Open:** confirm with FTB that its 80.0M counts agencies only. The hub exclusions (HILL0135, CITR0013, HILL0051) are pending FTB confirmation.

## 2026-11-20 — Cycle 3: census-tract food insecurity forecast (rolled up to county)

`scripts/cycle3_tract_model.py` forecasts 2025–2027 food insecurity for FTB's 1,169 census tracts and 10 counties, using only public data (Map the Meal Gap tract data 2020–2024 plus county ALICE; no Moody's).

- **Why tracts first:** the only multi-year MMG history (Snowflake `FTB.SILVER` / `MMG_Consolidated.xlsx`) covers just FTB's 10 counties. A county-only model would have ~60 rows; tracts give ~5,800, and county numbers are population-weighted roll-ups.
- **Two-part forecast:** rate = service-area level + tract gap from that level. A ridge model predicts the gap from the prior rate, MMG drivers and ALICE. The level is a scenario, not a fit: `flat` (held at 2024's 15.4%) or `recent_trend` (2022–2024 slope, about +0.6 pts/yr). A fitted level just replays the 2021→2022 post-pandemic jump (11.2% → 14.2%).
- **Validation (as-of backtest, `results/cycle3/tract_backtest_as_of.csv`):** trains only on data known at each origin year. With the true area-wide level, the model ties or beats persistence (county MAE 0.3–0.8 pts). With a flat level it only matches persistence, and every method missed 2021→2022 by about 3 pts (the post-pandemic jump). 3-year horizons can't be tested honestly with 2020–2024 tract data. The earlier 2024 holdout for the 3-year model trained on the 2020→2023 change and was optimistic; the as-of backtest supersedes it. Older national county MMG releases are needed for long-horizon backtests (see `docs/cycle3_mle_plan.md` §5).
- **Outputs:** `results/cycle3/`, `forecasts/cycle3/` (tract and county, both scenarios; gitignored, regenerate by running the script), `models/cycle3/` (one JSON per horizon). Inputs go in `data/external/` (not committed).

## 2026-08-06 — Ceres (Business Central) historical data → ftb.bronze

One-time load of a full database extract from Ceres, the company's former Business Central ERP (now retired/no longer in use), into `ftb.bronze`. 1,404 tables, ~59.6M rows, loaded completely unchanged — raw source table and column names preserved exactly via quoted identifiers, types taken straight from Snowflake's Parquet schema inference. No renaming, cleanup, or type coercion; a separate future script will handle a bronze → silver transform.

- **Storage**: parquet files (one per table, from the original DB export) live permanently in S3 at `s3://snowflakestaging1-975050112931-us-east-1-an/raw/ceres/<table-name>/<table-name>.parquet`. Uploaded via the S3 console (browser drag-and-drop of the local per-table folder structure), not the AWS CLI.
- **Snowflake objects created**: external stage `ftb.bronze.ceres_stage` (uses the existing `s3_snowflakestaging1` storage integration, scoped to the `raw/ceres` prefix — deliberately a new stage rather than widening the existing `ftb.bronze.s3_stage`, which is scoped to `raw/mmg` and backs a different, unrelated pipe-based pipeline) and file format `ftb.bronze.parquet_ff` (`TYPE = PARQUET, BINARY_AS_TEXT = FALSE` — needed because several BC tables have a raw SQL Server rowversion `timestamp` column with no logical Parquet type; Snowflake's default `BINARY_AS_TEXT = TRUE` tries to decode that as UTF-8 text and fails).
- **Load script**: `ingestion/load_ceres_bronze.py` — for each table, `CREATE OR REPLACE TABLE ... USING TEMPLATE (INFER_SCHEMA(...))` followed by `COPY INTO ... MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE`. Re-runnable if ever needed (idempotent via `CREATE OR REPLACE`), but not something that runs on a schedule.
- **No ingestion pipeline was established.** The existing bronze pattern for recurring sources (e.g. the `census_tract`/`county`/`zip_code` tables) uses `auto_ingest=true` Snowpipes that fire automatically when new files land in S3. Ceres is a historical, one-time extract from a decommissioned system — no new files will ever arrive at `raw/ceres/`, so a pipe would just be a permanent, inert object. A plain `COPY INTO` accomplished the same load without that overhead.

## 2026-08-03 — Snowflake → Power BI connection

Power BI connects to Snowflake as a dedicated service user, reading from `FTB.GOLD` only.

- **User**: `POWER_BI_SVC_USER`
- **Auth**: RSA key-pair (`SNOWFLAKE_JWT` authenticator), not a password. Private key: `power_bi_snowflake_key.p8`.
- **Role**: `POWER_BI_READER_ROLE` — read-only. Granted `USAGE` on database `FTB` and schema `FTB.GOLD`, plus `SELECT` on all current tables in `FTB.GOLD` and a **future grant** (`SELECT ON FUTURE TABLES/VIEWS/DYNAMIC TABLES IN SCHEMA FTB.GOLD`). The future grant means any new table landing in `GOLD` is automatically readable by Power BI — no follow-up grant needed when new gold tables are created.
- **Warehouse**: `POWER_BI_WH`
- **Account identifier**: use the full org-account form (`a6484859099771-occ89006`), not the bare account locator (`OCC89066`) — the locator alone 404s on login. Same issue was hit independently while setting up `FTB_LOADER_ROLE` on 2026-08-05.

This role is intentionally read-only and kept separate from `FTB_LOADER_ROLE` (used for CSV/Snowflake loading work), so a compromised or misbehaving BI connection can't write or drop anything.

Object names here (`FTB`, `GOLD`, `POWER_BI_*`) are kept uppercase to match what's actually deployed in the account already — see the note at the top of `docs/infrastructure.md`'s Consumption section on why this differs from the lowercase convention used for the newer Ceres pipeline above.

### Key rotation

RSA key-pair auth on Snowflake supports two active public keys per user (`RSA_PUBLIC_KEY` and `RSA_PUBLIC_KEY_2`), which allows zero-downtime rotation:

1. **Generate a new key pair** locally, without touching the currently active one:
   ```
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out power_bi_snowflake_key_new.p8
   openssl rsa -in power_bi_snowflake_key_new.p8 -pubout -out power_bi_snowflake_key_new.pub
   ```
2. **Add the new public key as the secondary slot** (the old key keeps working):
   ```sql
   ALTER USER POWER_BI_SVC_USER SET RSA_PUBLIC_KEY_2 = '<contents of power_bi_snowflake_key_new.pub, header/footer stripped>';
   ```
3. **Point Power BI at the new private key** (wherever the Snowflake connector/gateway credential is configured) and confirm it connects successfully. The old key is still valid as a fallback at this point, so this step is safe to test without an outage.
4. **Remove the old key** once the new one is confirmed working:
   ```sql
   ALTER USER POWER_BI_SVC_USER UNSET RSA_PUBLIC_KEY;
   ```
5. **Securely delete the old private key file** from disk (and anywhere else it was copied) — don't just leave it lying around after it's retired.

Optionally, promote the new key from the secondary to the primary slot afterward for tidiness (`SET RSA_PUBLIC_KEY = <new key>` then `UNSET RSA_PUBLIC_KEY_2`) — functionally identical either way, Snowflake accepts either slot at login.

Recommended cadence: rotate on a fixed schedule (e.g. every 90–180 days) or immediately if the key material is ever suspected to have leaked.

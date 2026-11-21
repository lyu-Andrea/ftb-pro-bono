# Cycle 3 data dictionary: distribution tables

Built by `src/data/cycle3_make_dataset.py` from the Snowflake extracts described in
`docs/cycle3_data_export_guide.md`; checked by `src/data/cycle3_validate.py`. Files are written to
`data/processed/` (gitignored, never committed).

```
python -m src.data.cycle3_make_dataset --raw data/raw/<date>
python -m src.data.cycle3_validate     --raw data/raw/<date>
```

**Caveat for every use:** pounds distributed are an *estimate* of where need is served, not a measure of need.
Pounds are placed where the **agency** is, not where its clients live. Mobile pantries, school programs and
hubs that pass food on to other pantries make the gap between the two larger.

**No agency-level output.** Agencies are only used to reach tracts. Small-cell warning: about half of the
tract-quarter rows come from a single agency, so a tract row can reveal that agency's volume. Keep the
tables internal (FTB and the project team). Before publishing, roll them up to county or suppress rows
where `n_agencies` < 3.

## Keys and conventions
- `county_fips`: 5-digit text (`12057`). `tract_geoid`: 11-digit text, **2020 Census tracts** (the 1,169 FTB
  tracts used in the cycle 3 model).
- `quarter`: calendar quarter as text (`2025Q3`). `quarter_start`: first day of the quarter.
- `fiscal_year`: FTB fiscal year, July–June (`FY25-26` = Jul 2025–Jun 2026). `fiscal_quarter`: 1 = Jul–Sep … 4 = Apr–Jun.
- `n_months`: months of data in that quarter across the whole dataset. 3 = complete; the latest quarter
  can be partial (2026Q4 has only October).
- Pounds are gross pounds shipped, with returns and credits **not** netted out.

## Sources and rules
| Step | Rule |
|---|---|
| Ceres (BC) | `FTB.BRONZE."Feeding Tampa Bay_Sales Shipment Line"`, item lines (`Type = 2`), `"Ext_ Gross Weight"`. Used for months **before 2025-08**. Covers 2018-01 → 2025-08-08. |
| NetSuite | `ITEMFULFILLMENT` from sales orders, not voided, `CUSTBODY_MHI_HFB_TOT_ORDER_WEIGHT`, UTC dates, latest Stitch version. Used for months **from 2025-08**. |
| Cutover | 2025-08-01. On Aug 1–8 2025 both systems ran; 94% of Ceres's pounds in those days appear in NetSuite for the same agency and day, so NetSuite is kept and Ceres's Aug 1–8 (0.68M lbs) dropped. NetSuite's Apr–Jul 2025 fulfillments (0.51M lbs) are dropped as a partial duplicate of Ceres. |
| Deleted records | NetSuite `DELETEDRECORD` has no record IDs, so deleted transactions can't be removed: a known gap. |
| Future dates | Only complete months, up to the month before the run date. |
| Duplicates | Repeated customer-month rows within one source are dropped, not summed. |
| Non-local service | `pounds_local` excludes customers flagged `disaster` (DISA-*, category DISASTER), `transfer_food_bank` (FBxx-*, category FOODBANK) and `hub`. Hubs are listed in `HUBS` in the script; currently HILL0135, CITR0013, HILL0051, **pending FTB confirmation**. `pounds_all` keeps everything. |

### Agency vs. other institutions
All customers are kept. `pounds_all` = `pounds_agency` + `pounds_other_institution` + `pounds_type_unknown`
(checked by the validator).
- **The split** comes from the NetSuite customer category. `AGENCY_CATEGORIES` in the script is `{"Agency"}`;
  every other category is "other institution".
- **Agency numbers can't tell the two apart:** schools also have numbers like `HILL0421`.
- **Customers only in Ceres** (closed before NetSuite) use the Ceres `"FBC Agency Category Code"` from
  D10b, shown as `Ceres: <code>`. The mapping comes from the customers that are in both systems:
  - **agency:** `PANTRY`, `SOUP KITCH`, `GROUP HOME`, `SENIOR`, `DISABLED`, `SCHOOL`. PANTRY → NetSuite
    Agency for 289 of 316.
  - **other institution:** `CHILDREN` (→ SCHOOLS/SFSP for 100 of 109), `HEALTH` (→ Food Pharmacy),
    `DISASTER`, `FOODBANK`.
  - **unknown:** blank, or anything else.
  - The sets `CERES_AGENCY_CATEGORIES` / `CERES_OTHER_CATEGORIES` are in the script.
  - With this, `pounds_type_unknown` is ≤ 0.2M lbs in every year.
- **FY25-26 (Aug–Jun), run of 2026-11-21:**

  | Group | Customers | lbs |
  |---|---|---|
  | agency | 328 | 80.9M |
  | other institution | 161 | 8.8M |
  | unknown | 2 | 0.2M |

  The 80.0M check compares the **agency** total with FTB's figure (+1.2%).

### Customer → tract (first rule that applies)
The `method` column of the QA list records which rule placed each customer; `precision` groups them.

| # | `method` | `precision` | Rule |
|---|---|---|---|
| 1 | `geocoded_address` | 1 street address | Street address geocoded by the Census Geocoder (`src/data/cycle3_geocode.py`, 2020 tracts). Exact matches are always used; inexact or tie matches only when the tract lies inside the agency's ZIP. |
| 2 | `gold_tract`, `gold_tract_2010_translated` | 2 GOLD tract | `FTB.GOLD.AGENCIES.GEOID_AGENCES`, used only if it lies inside the ship-to ZIP (or there's no ZIP to check). 2010 codes are translated to the 2020 tract with the largest land overlap. |
| 3 | `zip_override_gold`, `zip_dominant_tract`, `ceres_zip_dominant_tract` | 3 ZIP main tract | The 2020 tract with the largest land overlap with the ZIP (ZCTA), within FTB counties. ZIP order: NetSuite ship-to, then GOLD, then Ceres customer ZIP. `zip_override_gold` = the GOLD tract was outside the ZIP. |
| 4 | `county_from_agency_no`, `not_in_d6_county_from_agency_no` | 4 county or none | No tract. County comes from the code in the agency number (`HILL0113`, `MP-HILL`, `DISA-HILL` → Hillsborough). These pounds are in the county table only. |
| 5 | `unmapped`, `not_in_d6_unmapped` | 4 county or none | No county either (e.g. numeric shopper accounts). These pounds are in the county table as `county_fips = unknown`. |

`not_in_d6_*` = a customer with distribution but no row in D6 or D10b.

### Why GOLD's tract isn't trusted (checked 2026-11-21)
- **Code vintages:** of 291 GOLD agencies, 174 `GEOID_AGENCES` codes are 2020 tracts, 29 are 2010 tracts and
  88 are neither.
- **ZIP fit:** valid 2020 codes lie inside the agency's own GOLD ZIP only 45% of the time.
- **Against the geocoder:** for the 271 agencies with both a GOLD tract and a street address, the GOLD tract
  matches the geocoded tract only **6%** of the time. The ZIP main tract matches it only about 10%.
- **Geocoder is reliable:** exact geocoder matches lie inside the agency's ZIP 99% of the time; inexact ones
  69%, hence the ZIP check in rule 1.
- The county is right in about 97–100% of cases whichever method is used. Treat any tract not placed by
  rule 1 as approximate.
- **Likely causes:**
  - Many codes are old: 87 are neither 2010 nor 2020 tracts. Numbers like `950500` / `970200` look like older
    census numbering.
  - Up to 10 agencies share one GOLD tract.
  - When the county is right, the GOLD tract number is usually a near neighbour of the real one (median
    distance 3 in the tract number).
  - So the column was probably filled from a coarse or old location (an older tract vintage, or a city/area
    point), not from each agency's street address.
  - It isn't the ZIP's main tract (0% match) or other agencies' tracts shifted by a row.

### Customers without a reliable tract (run of 2026-11-21)
Customers active in FY25-26 (Aug 2025–Jun 2026), by `precision`. 970 street addresses were sent (NetSuite
D6b, the first D6 export, Ceres D10c); 882 matched (91%, 707 exact).

| precision | customers | agency | other institution | unknown | FY25-26 lbs | share |
|---|---|---|---|---|---|---|
| 1 street address | 408 | 282 | 126 | 0 | 79.6M | 89% |
| 2 GOLD tract (fits ZIP) | 9 | 9 | 0 | 0 | 1.3M | 1% |
| 3 ZIP main tract | 48 | 28 | 19 | 1 | 5.5M | 6% |
| 4 county or none | 27 | 9 | 17 | 1 | 3.5M | 4% |

- **Tiers 3–4** are mostly addresses the geocoder couldn't match: PO boxes, building names ("Student Services
  Building", "Suite 314"), the FTB warehouse, or typos in the ZIP. Tier 4 also holds mobile pantries
  (`MP-*`), the `INDIV-*` accounts and out-of-state food banks.
- **Mobile pantries** have no fixed site, so county level is the honest answer for them.
- **Across all years (2018 →):** 775 customers by street address, 9 by GOLD tract, 98 by ZIP and 110 by
  county or none.
- **Privacy:** with precise tracts, **73% of tract-quarter rows come from a single agency**. Keep the
  small-cell rule below in mind.

**The per-customer list** is `data/processed/cycle3_agency_location_qa.csv`. It's **internal**: gitignored,
never commit or share it outside FTB and the team. Columns:
- `agency_no`, `category`
- `method`, `precision`
- `tract_geoid`, `county_fips`
- `first_month`, `last_month`
- `active_fy2526`

It has no pounds per agency. Sort by `precision` to see who still needs an address.

### How to improve the matches (each refresh)
1. Export street addresses:
   - [`sql/extract/d6b_customer_addresses.sql`](../sql/extract/d6b_customer_addresses.sql) for NetSuite
     customers (taken from the latest ship-to address; no contact names, no individuals);
   - query D10c in [`d10_ceres_pounds_agency_month.sql`](../sql/extract/d10_ceres_pounds_agency_month.sql)
     for Ceres-era customers.
2. Geocode:
   `python -m src.data.cycle3_geocode --raw data/local_only/<date> --out data/raw/<date> --addresses d6b_customer_addresses.csv d10c_ceres_customer_addresses.csv`.
   Address files are personal-ish data (some pantries are run from homes), so they live in
   `data/local_only/`, which isn't shared. Only `d6_geocoded.csv` (agency number → tract) goes into `raw/`.
   - Only an anonymous row number, street, city, state and ZIP are sent to
     `geocoding.geo.census.gov`. Agency numbers and names stay local.
   - It writes `d6_geocoded.csv`, which `cycle3_make_dataset.py` uses automatically.
3. Rerun `cycle3_make_dataset` and `cycle3_validate`. Check the tier table in the run log (`fy2526_precision`).
4. For the tier-3/4 Agencies left over, ask FTB for the site address. A fix at the source (the NetSuite
   default shipping address, or correcting `GEOID_AGENCES` in `FTB.GOLD.AGENCIES`) helps every later run.

Public reference files (in `data/external/census/`):
- `tab20_zcta520_tract20_natl.txt`: 2020 ZCTA–tract relationship file
- `tab20_tract20_tract10_natl.txt`: 2020–2010 tract relationship file

Both come from www2.census.gov/geo/docs/maps-data/data/rel2020/.

## distribution_tract_quarter
One row per tract × calendar quarter that has pounds mapped to the tract. Tracts with no agency don't
appear: treat them as 0.

| Column | Type | Description |
|---|---|---|
| tract_geoid | text(11) | 2020 Census tract |
| county_fips | text(5) | county of the tract |
| quarter | text | calendar quarter, `YYYYQn` |
| quarter_start | date | first day of the quarter |
| fiscal_year | text | FTB fiscal year, `FYyy-yy` |
| fiscal_quarter | int | 1–4, Jul-based |
| pounds_all | float | all pounds placed in the tract |
| pounds_local | float | pounds excluding disaster, food-bank transfers and hubs |
| pounds_agency | float | part of `pounds_all` going to Agency-category customers |
| pounds_agency_local | float | `pounds_agency` excluding hubs (the agency part of `pounds_local`) |
| pounds_other_institution | float | part going to other institutions: SCHOOLS, SFSP, Food Pharmacy, Internal, DISASTER, FOODBANK, ... |
| pounds_type_unknown | float | part going to customers with no category (mostly Ceres-era customers not in NetSuite) |
| n_agencies | int | number of customers contributing (for the small-cell rule) |
| n_months | int | months of data in the quarter (3 = complete) |

Key: (`tract_geoid`, `quarter`).

## distribution_county_quarter
One row per county × calendar quarter. Holds **every** pound, including pounds known only at county level.

| Column | Type | Description |
|---|---|---|
| county_fips | text(5) | county; `unknown` if no county could be found |
| county_name | text | county name, or `outside/unknown` |
| quarter, quarter_start, fiscal_year, fiscal_quarter, n_months | | as above |
| pounds_all | float | all pounds in the county |
| pounds_local | float | excluding disaster, transfers and hubs |
| pounds_agency, pounds_agency_local, pounds_other_institution, pounds_type_unknown | float | as in the tract table |
| pounds_all_tract_mapped | float | part of `pounds_all` that is also in the tract table |
| n_agencies | int | customers contributing |

Key: (`county_fips`, `quarter`). `pounds_all − pounds_all_tract_mapped` = pounds known only at county level.

## seasonal_quarter_shares
Share of a calendar year's `pounds_local` that falls in each quarter, averaged over full calendar years.
2020 is skipped (COVID); years used are listed in `years_used`.

| Column | Type | Description |
|---|---|---|
| scope | text | `FTB` (all 10 counties) or a `county_fips` |
| cal_quarter | int | 1 = Jan–Mar … 4 = Oct–Dec |
| share_mean | float | mean share across years (the 4 quarters sum to 1) |
| share_min, share_max | float | range across years |
| n_years | int | number of years averaged |
| years_used | text | comma-separated years |

Key: (`scope`, `cal_quarter`).

## cycle3_make_dataset_log.json
Run log with aggregates only (no agency identifiers):
- rows and pounds dropped (duplicates, cutover, future dates);
- agencies and pounds by mapping method;
- FY25-26 totals by flag and by customer category;
- hubs excluded and seasonal years used.

`cycle3_validate.py` reads it.

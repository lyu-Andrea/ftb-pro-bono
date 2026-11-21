# Cycle 3 scenario model: output files (contract for the dashboard)

Produced by `python scripts/cycle3_scenarios.py` into `forecasts/cycle3/scenario/`. The folder is gitignored:
planned pounds derive from FTB's own distribution data, and tract distribution can reveal single agencies.
Share these files inside the project team / FTB only. Outside the team, show county level, or tracts where
`shareable_outside_team` is true.

**Caveat for every view:** distribution is an *estimate* of where need is served, not a measure of need. It is
capped by supply and agency capacity and is counted where the agency sits, not where clients live.

Keys: `county_fips` = 5-digit text, `tract_geoid` = 11-digit text (2020 tracts). `year` = calendar year.
Rates are decimals (0.163 = 16.3%); `*_pts` columns are percentage points.

## need_county.csv: one row per scenario × year × county
| Column | Meaning |
|---|---|
| scenario | `baseline`, `economic_stress`, `historical_swing` (defined in `config/cycle3_assumptions.yaml`) |
| year, county_fips, county_name | |
| base_fi_rate | MMG food insecurity rate in the base year (the anchor) |
| unemployment_change_pts | Change vs. the base year: BLS observed (latest year estimated from year-to-date), plus the scenario extra |
| contrib_unemployment_pts / contrib_poverty_pts | coefficient × change, in rate points |
| common_shift_pts | S, the area-wide policy/price shift assumed |
| fi_rate | base_fi_rate + (contributions + S) / 100 |
| population | UF BEBR population (April 1), scenario's series |
| fi_people | fi_rate × population |
| meals_per_fi_person | Base-year MMG annual meal gap ÷ FI persons, per county (~190) |
| coverage_used | Share of meals needed that FTB plans to supply (config `coverage.mode`) |
| meals_needed | fi_people × meals_per_fi_person (the whole gap, from all sources) |
| ftb_planned_meals / ftb_planned_lbs | meals_needed × coverage_used / × 1.2 lbs per meal |
| hurricane_extra_lbs | Layer 4 overlay (0 unless enabled) |
| ftb_planned_lbs_total | ftb_planned_lbs + hurricane_extra_lbs |

Note on people counts: MMG's own persons use ACS 5-year population, which lags. This model uses BEBR's current
estimates, so `fi_people` is higher than MMG's published count even at the same rate.

## need_tract.csv: one row per scenario × year × tract
Same Layer 1–4 columns, plus `share` (tract share of county FI persons in the base year) and
`share_missing_fi` (true = MMG has no FI estimate for the tract, so its share is 0). `fi_people` = county fi_people × share.

## need_county_quarterly.csv / need_tract_quarterly.csv
Annual `ftb_planned_meals` and `ftb_planned_lbs_total` × `season_share` for each calendar `quarter` (e.g. 2027Q1).
Shares are FTB-wide averages of 2018–2025 (2020 excluded). **This splits the annual plan across quarters; it isn't a
separate quarterly forecast.**

## tract_distribution_vs_need.csv: baseline, year of the last 4 complete distribution quarters
| Column | Meaning |
|---|---|
| fi_people, baseline_planned_lbs | Baseline need in that year |
| pounds_last_4q | Local-service pounds from agencies located in the tract (disaster, food-bank transfers, hubs excluded) |
| n_agencies | Number of agencies behind those pounds |
| need_share_of_county / distribution_share_of_county | Each tract's share of its county's need / of its county's tract-mapped pounds |
| high_need_tract | In the top 20% of tracts in its county by FI people |
| flag_high_need_no_agency_in_tract | High-need tract with no agency located in it (its residents may still be served nearby) |
| shareable_outside_team | False when 1–2 agencies make up the tract's pounds (would reveal an agency) |

## scenario_summary_county.csv, scenario_sensitivity.csv
County + "FTB total" rows of the main measures per scenario/year, and the effect of +1 pt unemployment / poverty / S
on people, meals needed and FTB pounds (baseline, the second-to-last target year).

## assumptions_used.json, model_run_metadata.json
The full config used, plus derived values: last BLS year, distribution quarters, current coverage FTB-wide and by
county, meals per FI person, number of tracts with share 0. Run time, code commit, row counts.

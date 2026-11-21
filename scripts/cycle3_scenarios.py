#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cycle 3 scenario model: food need by county and census tract, per scenario, in people, meals and pounds.

    Layer 0  config/cycle3_assumptions.yaml (every forward-looking number)
    Layer 1  county rate = MMG base rate + b_unemp x d_unemp + b_pov x d_pov + S ;  people = rate x BEBR population
    Layer 2  tract people = county people x tract share of county FI persons (base-year MMG tracts)
    Layer 3  meals needed = people x meals per FI person (MMG) ; FTB meals = x coverage ; lbs = x 1.2
    Layer 4  optional hurricane overlay ; quarterly split by seasonal shares

Usage:
    python scripts/cycle3_scenarios.py [--config config/cycle3_assumptions.yaml]

Outputs (the fixed contract for the dashboard teammate; see docs/cycle3_output_dictionary.md):
    forecasts/cycle3/scenario/  need_county.csv, need_tract.csv, need_county_quarterly.csv, need_tract_quarterly.csv,
                                tract_distribution_vs_need.csv, scenario_summary_county.csv, scenario_sensitivity.csv,
                                assumptions_used.json, model_run_metadata.json
All outputs are gitignored: planned pounds derive from FTB's own distribution, and tract distribution can reveal
single agencies (see the data repo README). This code repo is public.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.scenario import inputs as I  # noqa: E402
from src.scenario import access as A  # noqa: E402
from src.scenario import layers as L  # noqa: E402

TOP_SHARE = 0.20  # "high-need tract" = top 20% of tracts by FI people within the county


def tract_distribution_vs_need(tract: pd.DataFrame, dist: pd.DataFrame, year: int) -> pd.DataFrame:
    """Baseline need vs. pounds delivered by agencies located in the tract over the last 4 complete quarters.

    Distribution is counted where the agency sits, not where its clients live, and is capped by supply, so this
    is a prompt to investigate, never a verdict. Shares are within county, so unmapped pounds don't bias the ratio.
    """
    t = tract[(tract["scenario"] == "baseline") & (tract["year"] == year)][
        ["tract_geoid", "county_fips", "county_name", "fi_people", "ftb_planned_lbs"]].copy()
    t = t.merge(dist[["tract_geoid", "pounds_last_4q", "n_agencies"]], on="tract_geoid", how="left")
    t[["pounds_last_4q", "n_agencies"]] = t[["pounds_last_4q", "n_agencies"]].fillna(0)
    g = t.groupby("county_fips")
    t["need_share_of_county"] = t["ftb_planned_lbs"] / g["ftb_planned_lbs"].transform("sum")
    t["distribution_share_of_county"] = t["pounds_last_4q"] / g["pounds_last_4q"].transform("sum")
    t["need_rank_in_county"] = g["fi_people"].rank(ascending=False, method="first")
    t["high_need_tract"] = t["need_rank_in_county"] <= (g["fi_people"].transform("size") * TOP_SHARE).round()
    t["flag_high_need_no_agency_in_tract"] = t["high_need_tract"] & (t["pounds_last_4q"] == 0)
    t["shareable_outside_team"] = t["n_agencies"].eq(0) | t["n_agencies"].ge(3)
    return t.rename(columns={"ftb_planned_lbs": "baseline_planned_lbs"})


def sensitivity(county: pd.DataFrame, cfg: dict, year: int) -> pd.DataFrame:
    """Effect of +1 pt in each driver, for the baseline scenario in `year`, per county and FTB-wide."""
    b = county[(county["scenario"] == "baseline") & (county["year"] == year)]  # already carries Layer 3 columns
    rows = []
    for driver, pts_per_pt in (("unemployment +1 pt", cfg["coefficients"]["unemployment"]),
                               ("poverty +1 pt", cfg["coefficients"]["poverty"]),
                               ("common shift S +1 pt", 1.0)):
        d_people = b["population"] * pts_per_pt / 100
        d_meals = d_people * b["meals_per_fi_person"]
        part = pd.DataFrame({"driver": driver, "county_name": b["county_name"], "fi_rate_change_pts": pts_per_pt,
                             "extra_fi_people": d_people, "extra_meals_needed": d_meals,
                             "extra_ftb_lbs": d_meals * b["coverage_used"] * cfg["lbs_per_meal"]})
        total = part.sum(numeric_only=True)
        total["fi_rate_change_pts"] = pts_per_pt
        rows += [part, pd.DataFrame([{"driver": driver, "county_name": "FTB total", **total.to_dict()}])]
    return pd.concat(rows, ignore_index=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "cycle3_assumptions.yaml")
    args = p.parse_args(argv)
    cfg = I.load_config(args.config)
    path = lambda rel: cfg["_root"] / rel  # noqa: E731

    # Inputs
    mmg_county, mmg_tract = I.load_mmg_county(), I.load_mmg_tract()
    unemp, last_bls = I.load_bls_unemployment(path(cfg["unemployment_history"]))
    anchors = I.load_bebr_population(path(cfg["population"]["estimates"]), path(cfg["population"]["projections"]))
    population = I.population_path(anchors, cfg["target_years"])
    pounds, quarters = I.load_distribution_last_year(path(cfg["coverage"]["distribution"]), cfg["coverage"]["pounds_column"])
    seasonal = I.load_seasonal_shares(path(cfg["seasonal_shares"]), cfg["seasonal_scope"])

    # Layers
    cpi = I.load_national_cpi(cfg)
    county = L.layer1_county_need(cfg, mmg_county, unemp, last_bls, population, cpi)
    dist_year = int(pd.Period(quarters[-1]).year)  # the calendar year the last 4 quarters mostly fall in
    coverage = L.coverage_by_county(cfg, mmg_county, pounds, county, dist_year)
    county = L.layer4_hurricane(L.layer3_convert(county, coverage, cfg["lbs_per_meal"]), cfg["hurricane"])
    shares = L.tract_shares(mmg_tract, cfg["base_year"])
    tract = L.layer4_hurricane(L.layer3_convert(L.layer2_allocate(county, shares), coverage, cfg["lbs_per_meal"]),
                               cfg["hurricane"])
    ids_c, ids_t = ["scenario", "year", "county_fips", "county_name"], ["scenario", "year", "county_fips", "tract_geoid"]
    county_q = L.quarterly(county, seasonal, cfg["seasonal_scope"], ids_c)
    tract_q = L.quarterly(tract, seasonal, cfg["seasonal_scope"], ids_t)

    tract_dist = I.load_tract_distribution(path("data/processed/distribution_tract_quarter.parquet"), quarters,
                                           cfg["coverage"]["pounds_column"])
    tdist = tract_distribution_vs_need(tract, tract_dist, dist_year)
    # Service-area access (E2SFCA): baseline need in the distribution year vs. pounds from agencies within reach
    acc_cfg = cfg["access"]
    access = A.tract_access(tract[(tract["scenario"] == "baseline") & (tract["year"] == dist_year)],
                            tract_dist.rename(columns={"pounds_last_4q": "pounds"}),
                            A.load_tract_centroids(path(acc_cfg["centroids"])), radii=tuple(acc_cfg["radii_miles"]),
                            main_radius=acc_cfg["main_radius_miles"], low_ratio=acc_cfg["low_access_ratio"])
    tdist = tdist.merge(access.drop(columns=["county_fips", "fi_people", "high_need_tract"]), on="tract_geoid", how="left")
    sens = sensitivity(county, cfg, max(cfg["target_years"]) - 1)

    # Outputs
    out = PROJECT_ROOT / "forecasts" / "cycle3" / "scenario"
    out.mkdir(parents=True, exist_ok=True)
    county.to_csv(out / "need_county.csv", index=False)
    tract.drop(columns=["county_name"]).to_csv(out / "need_tract.csv", index=False)
    county_q.to_csv(out / "need_county_quarterly.csv", index=False)
    tract_q.to_csv(out / "need_tract_quarterly.csv", index=False)
    tdist.to_csv(out / "tract_distribution_vs_need.csv", index=False)

    summary_cols = ids_c + ["fi_rate", "population", "fi_people", "meals_needed", "ftb_planned_meals", "ftb_planned_lbs_total"]
    summary = county[summary_cols]
    ftb = summary.groupby(["scenario", "year"])[["population", "fi_people", "meals_needed", "ftb_planned_meals",
                                                 "ftb_planned_lbs_total"]].sum().reset_index()
    ftb["fi_rate"], ftb["county_fips"], ftb["county_name"] = ftb["fi_people"] / ftb["population"], "all", "FTB total"
    pd.concat([summary, ftb[summary_cols]]).round(4).to_csv(out / "scenario_summary_county.csv", index=False)
    sens.round(1).assign(fi_rate_change_pts=sens["fi_rate_change_pts"]).to_csv(out / "scenario_sensitivity.csv", index=False)

    derived = {
        "last_cpi_year": cpi.attrs["last_year"], "last_cpi_year_months": cpi.attrs["last_year_months"],
        "last_bls_year": last_bls, "last_bls_year_is_estimate": bool(unemp.loc[unemp["year"] == last_bls, "is_estimate"].any()),
        "distribution_quarters": quarters,
        "coverage_ftb_wide_current": round(coverage.attrs["ftb_wide_current"], 4),
        "coverage_by_county_current": coverage.set_index("county_fips")["coverage_current"].round(4).to_dict(),
        "meals_per_fi_person_by_county": coverage.set_index("county_fips")["meals_per_fi_person"].round(1).to_dict(),
        "tracts_with_missing_fi_share0": int(shares["share_missing_fi"].sum()),
    }
    with open(out / "assumptions_used.json", "w") as f:
        json.dump({"config": {k: v for k, v in cfg.items() if not k.startswith("_")}, "derived": derived}, f, indent=2, default=str)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = None
    with open(out / "model_run_metadata.json", "w") as f:
        json.dump({"run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_commit": commit,
                   "config": str(args.config.relative_to(PROJECT_ROOT)), "base_year": cfg["base_year"],
                   "target_years": cfg["target_years"], "scenarios": list(cfg["scenarios"]),
                   "rows": {"need_county": len(county), "need_tract": len(tract)}}, f, indent=2)

    # Console summary
    pd.set_option("display.width", 200)
    print(f"Base year {cfg['base_year']} | BLS through {last_bls} | distribution quarters {quarters[0]}..{quarters[-1]}")
    print(f"Coverage (FTB-wide, current): {derived['coverage_ftb_wide_current']:.1%} of baseline need in {dist_year}")
    print("\nFTB total by scenario:")
    show = ftb.assign(fi_rate=ftb["fi_rate"] * 100, fi_people=ftb["fi_people"] / 1e3,
                      ftb_planned_meals=ftb["ftb_planned_meals"] / 1e6, ftb_planned_lbs_total=ftb["ftb_planned_lbs_total"] / 1e6)
    print(show[["scenario", "year", "fi_rate", "fi_people", "ftb_planned_meals", "ftb_planned_lbs_total"]]
          .rename(columns={"fi_rate": "rate_%", "fi_people": "people_K", "ftb_planned_meals": "FTB_meals_M",
                           "ftb_planned_lbs_total": "FTB_lbs_M"}).round(2).to_string(index=False))
    r = acc_cfg["main_radius_miles"]
    print(f"\nService-area access (E2SFCA, {r:g} mi): FTB average {access.attrs['ftb_avg_lbs_per_fi_person']:.0f} lbs per FI person"
          f" (tract-mapped pounds)")
    print(f"High-need tracts: {int(tdist['high_need_tract'].sum())} | no agency inside the tract: "
          f"{int(tdist['flag_high_need_no_agency_in_tract'].sum())} | low access within {r:g} mi (<"
          f"{acc_cfg['low_access_ratio']:.0%} of average): {int(tdist['flag_high_need_low_access'].sum())}"
          f" | low at 5, 10 and 15 mi: {int((tdist['flag_low_access_all_radii'] & tdist['high_need_tract']).sum())}")
    by_c = tdist.assign(low=tdist["flag_low_access"] * tdist["fi_people"]).groupby("county_name")[["low", "fi_people"]].sum()
    print("Share of FI people in low-access tracts, by county:",
          (by_c["low"] / by_c["fi_people"]).round(2).sort_values(ascending=False).to_dict())


if __name__ == "__main__":
    main()

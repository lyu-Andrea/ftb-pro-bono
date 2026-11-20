#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cycle 3: census-tract food insecurity forecast for the Feeding Tampa Bay service area,
rolled up to county.

Why tract-first: the only multi-year MMG history available (Snowflake FTB.SILVER /
MMG_Consolidated.xlsx) covers FTB's 10 counties, so a county-only model would have ~60 rows.
The same 10 counties hold ~1,169 tracts per year, so we model tracts and aggregate
(population-weighted) to county. Public / FTB-owned data only (MMG + ALICE), no Moody's.

Inputs (not committed; drop them into data/external/):
  MMG_Consolidated.xlsx                 'Census Tract' sheet, FTB tracts 2020-2023
  MMG_2026_2024-data.xlsx               'Census Tract' sheet, national 2024 (filtered to FTB counties)
  2025_ALICE_Florida_Data_Sheet.xlsx    'County' sheet, Florida counties 2010-2023

Method -- the forecast is split into two parts:
  rate(tract, t+k) = area_level(t+k) + relative(tract, t+k)
* relative: a population-weighted ridge regression predicts each tract's gap from the
  service-area average in year t+k, from its year-t gap, year-t drivers and county ALICE shares
  (latest ALICE year <= t), all centred on that year's area average. This is the part the data can
  learn: tract-to-tract correlation is ~0.91 year over year.
* area_level: NOT learned. The area average jumped 11.2% -> 14.2% between 2021 and 2022 (end of
  pandemic aid), so a model fit on 1-2 transitions just replays that shock. Instead the level is
  a scenario: "flat" (held at the last observed year) and "recent_trend" (2022-2024 slope
  continued). Swap in an external outlook (e.g. state unemployment forecast) when FTB has one.
Validation holds out target year 2024 (train on targets <= 2023), compares to a persistence
baseline (rate_{t+k} = rate_t), and also scores with the true 2024 level ("oracle_level") to show
how well the tract pattern alone is predicted. Alpha is picked by leave-one-county-out CV on the
training pairs only. Final models are refit on all pairs and score from source year 2024,
giving 2025 (k=1), 2026 (k=2) and 2027 (k=3).

Usage:
    python scripts/cycle3_tract_model.py            # writes results/cycle3/, forecasts/cycle3/, models/cycle3/
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features.build_features import clip_rate  # noqa: E402
from src.models.train_model import fit_weighted_ridge, predict_weighted_ridge, weighted_metrics  # noqa: E402

EXT = PROJECT_ROOT / "data" / "external"
HORIZONS = (1, 2, 3)
HOLDOUT_TARGET_YEAR = 2024
ALPHAS = (0.1, 1.0, 10.0, 100.0, 1000.0, 1e4, 1e5)
TREND_WINDOW = (2022, 2024)  # post-shock years used for the "recent_trend" level scenario

TRACT_COLS = {
    "Tract ID": "tract_id",
    "County FIPS": "county_fips",
    "County, State": "county_name",
    "Year": "year",
    "Total Population (5 Yr ACS)": "population",
    "Overall Food Insecurity Rate": "food_insecurity_rate",
    "# of Food Insecure Persons Overall": "food_insecure_persons",
    "Unemployment Rate (5 Yr ACS)": "unemployment_rate",
    "Poverty Rate (5 Yr ACS)": "poverty_rate",
    "Percent Black (all ethnicities) (5 Yr ACS)": "black_rate",
    "Percent Hispanic (any race) (5 Yr ACS)": "hispanic_rate",
    "Median Income (5 Yr ACS)": "median_income",
    "Homeownership Rate (5 Yr ACS)": "homeownership_rate",
    "Disability Rate (5 Yr ACS)": "disability_rate",
}
TRACT_DRIVERS = ["unemployment_rate", "poverty_rate", "black_rate", "hispanic_rate",
                 "log_median_income", "homeownership_rate", "disability_rate"]
ALICE_FEATURES = ["alice_below_line_rate", "alice_poverty_rate"]
FEATURES = ["lag_food_insecurity_rate"] + TRACT_DRIVERS + ALICE_FEATURES


def load_tract_panel(consolidated: Path, latest: Path) -> pd.DataFrame:
    hist = pd.read_excel(consolidated, sheet_name="Census Tract")
    new = pd.read_excel(latest, sheet_name="Census Tract")
    new = new[new["County FIPS"].isin(hist["County FIPS"].unique())]
    panel = pd.concat([hist, new], ignore_index=True).rename(columns=TRACT_COLS)[list(TRACT_COLS.values())]
    panel = panel.drop_duplicates(["tract_id", "year"], keep="first")
    panel["log_median_income"] = np.log(panel["median_income"].clip(lower=1))
    return panel


def load_alice(path: Path) -> pd.DataFrame:
    a = pd.read_excel(path, sheet_name="County").rename(columns={"GEO id2": "county_fips", "Year": "alice_year"})
    a["alice_poverty_rate"] = a["Poverty Households"] / a["Households"]
    a["alice_below_line_rate"] = (a["Poverty Households"] + a["ALICE Households"]) / a["Households"]
    return a[["county_fips", "alice_year"] + ALICE_FEATURES]


def attach_alice(panel: pd.DataFrame, alice: pd.DataFrame) -> pd.DataFrame:
    """As-of join: each tract-year gets its county's most recent ALICE year <= that year."""
    out = []
    for year, grp in panel.groupby("year"):
        latest = alice[alice["alice_year"] <= year].sort_values("alice_year").groupby("county_fips").tail(1)
        out.append(grp.merge(latest, on="county_fips", how="left"))
    return pd.concat(out, ignore_index=True)


def area_levels(panel: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Population-weighted service-area mean of each column, by year."""
    def agg(g):
        return pd.Series({c: np.average(g[c].dropna(), weights=g.loc[g[c].notna(), "population"].clip(lower=1)) for c in cols})
    return panel.groupby("year").apply(agg, include_groups=False)


def centre(panel: pd.DataFrame) -> pd.DataFrame:
    """Subtract each year's area mean from the rate and every driver (the "relative" frame)."""
    cols = ["food_insecurity_rate"] + TRACT_DRIVERS + ALICE_FEATURES
    lv = area_levels(panel, cols)
    out = panel.copy()
    for c in cols:
        out[c] = out[c] - out["year"].map(lv[c])
    return out


def make_pairs(panel: pd.DataFrame, k: int) -> pd.DataFrame:
    """Pairs on the relative scale: features from year t, target = relative rate in t+k."""
    rel = centre(panel)
    src = rel.rename(columns={"food_insecurity_rate": "lag_food_insecurity_rate"})
    src = src[["tract_id", "county_fips", "year"] + FEATURES]
    src["raw_lag_rate"] = panel["food_insecurity_rate"].values
    tgt = rel[["tract_id", "year", "population", "food_insecurity_rate", "county_name"]].copy()
    tgt["actual_rate"] = panel["food_insecurity_rate"].values
    tgt["year"] = tgt["year"] - k
    pairs = src.merge(tgt, on=["tract_id", "year"]).rename(columns={"year": "source_year"})
    pairs["target_year"] = pairs["source_year"] + k
    return pairs.dropna(subset=FEATURES + ["food_insecurity_rate", "population"])


def level_scenarios(levels: pd.Series, source_year: int, k: int) -> dict:
    """Area-level rate for source_year + k, using only years <= source_year."""
    lo, hi = TREND_WINDOW[0], min(TREND_WINDOW[1], source_year)
    yrs = levels.loc[lo:hi]
    slope = np.polyfit(yrs.index, yrs.values, 1)[0] if len(yrs) >= 2 else 0.0
    return {"flat": levels.loc[source_year], "recent_trend": levels.loc[source_year] + slope * k}


def pick_alpha(train: pd.DataFrame) -> float:
    """Leave-one-county-out CV on training pairs only."""
    best, best_mae = None, np.inf
    for alpha in ALPHAS:
        preds = []
        for c in train["county_fips"].unique():
            tr, te = train[train["county_fips"] != c], train[train["county_fips"] == c].copy()
            m = fit_weighted_ridge(tr[FEATURES], tr["food_insecurity_rate"], tr["population"], alpha=alpha)
            te["pred"] = clip_rate(predict_weighted_ridge(m, te[FEATURES]))
            preds.append(te)
        mae = weighted_metrics(pd.concat(preds), "food_insecurity_rate", "pred")["weighted_mae"]
        if mae < best_mae:
            best, best_mae = alpha, mae
    return best


def county_rollup(frame: pd.DataFrame, rate_cols: list) -> pd.DataFrame:
    def agg(g):
        w = g["population"]
        row = {"population": w.sum()}
        for c in rate_cols:
            row[c] = np.average(g[c], weights=w)
        return pd.Series(row)
    return frame.groupby(["county_fips", "county_name"]).apply(agg, include_groups=False).reset_index()


def model_payload(m: dict, k: int, alpha: float, train_years: list) -> dict:
    return {
        "description": f"Cycle 3 FTB tract model: rate(t+{k}) from rate(t) + drivers(t) + county ALICE(t)",
        "horizon": k, "alpha": alpha, "train_target_years": train_years, "features": m["features"],
        "means": m["means"].tolist(), "stds": m["stds"].tolist(), "coef": np.asarray(m["coef"]).tolist(),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--consolidated", type=Path, default=EXT / "MMG_Consolidated.xlsx")
    p.add_argument("--latest", type=Path, default=EXT / "MMG_2026_2024-data.xlsx")
    p.add_argument("--alice", type=Path, default=EXT / "2025_ALICE_Florida_Data_Sheet.xlsx")
    args = p.parse_args(argv)

    res_dir, fc_dir, mdl_dir = (PROJECT_ROOT / d / "cycle3" for d in ("results", "forecasts", "models"))
    for d in (res_dir, fc_dir, mdl_dir):
        d.mkdir(parents=True, exist_ok=True)

    panel = attach_alice(load_tract_panel(args.consolidated, args.latest), load_alice(args.alice))
    print("Tract panel rows by year:", panel.groupby("year").size().to_dict())
    fi_levels = area_levels(panel, ["food_insecurity_rate"])["food_insecurity_rate"]
    print("Service-area food insecurity by year:", fi_levels.round(4).to_dict())

    metrics, county_metrics, forecasts = [], [], []
    for k in HORIZONS:
        pairs = make_pairs(panel, k)
        train = pairs[pairs["target_year"] < HOLDOUT_TARGET_YEAR]
        test = pairs[pairs["target_year"] == HOLDOUT_TARGET_YEAR].copy()
        alpha = pick_alpha(train)
        m = fit_weighted_ridge(train[FEATURES], train["food_insecurity_rate"], train["population"], alpha=alpha)
        rel_pred = predict_weighted_ridge(m, test[FEATURES])
        src_year = HOLDOUT_TARGET_YEAR - k
        lv = level_scenarios(fi_levels, src_year, k)
        test["pred_model_flat"] = clip_rate(lv["flat"] + rel_pred)
        test["pred_model_oracle_level"] = clip_rate(fi_levels.loc[HOLDOUT_TARGET_YEAR] + rel_pred)
        test["pred_persistence"] = test["raw_lag_rate"]
        # Fair benchmark for the relative model: keep each tract's gap, apply the true level shift.
        test["pred_persistence_oracle_level"] = clip_rate(
            test["raw_lag_rate"] - fi_levels.loc[src_year] + fi_levels.loc[HOLDOUT_TARGET_YEAR])

        for name in ("model_flat", "model_oracle_level", "persistence", "persistence_oracle_level"):
            row = weighted_metrics(test, "actual_rate", f"pred_{name}")
            metrics.append({"horizon_years": k, "method": name, "alpha": alpha if name.startswith("model") else None,
                            "train_target_years": sorted(train["target_year"].unique().tolist()),
                            "n_train": len(train), **row})
        cty = county_rollup(test, ["actual_rate", "pred_model_flat", "pred_model_oracle_level", "pred_persistence",
                                   "pred_persistence_oracle_level"])
        cty["horizon_years"] = k
        county_metrics.append(cty)

        # Final refit on every available pair, then score from the latest observed year.
        final = fit_weighted_ridge(pairs[FEATURES], pairs["food_insecurity_rate"], pairs["population"], alpha=alpha)
        with open(mdl_dir / f"tract_h{k}_ridge.json", "w") as f:
            json.dump(model_payload(final, k, alpha, sorted(pairs["target_year"].unique().tolist())), f, indent=2)
        src_year = int(panel["year"].max())
        rel = centre(panel)
        src = rel[rel["year"] == src_year].rename(columns={"food_insecurity_rate": "lag_food_insecurity_rate"}).copy()
        src["observed_rate_source_year"] = panel.loc[panel["year"] == src_year, "food_insecurity_rate"].values
        src = src.dropna(subset=FEATURES)
        rel_pred = predict_weighted_ridge(final, src[FEATURES])
        for scen, level in level_scenarios(fi_levels, src_year, k).items():
            f = src.copy()
            f["scenario"], f["area_level"] = scen, level
            f["predicted_food_insecurity_rate"] = clip_rate(level + rel_pred)
            f["predicted_food_insecure_persons"] = (f["predicted_food_insecurity_rate"] * f["population"]).round()
            f["source_year"], f["forecast_year"] = src_year, src_year + k
            forecasts.append(f)

    metrics = pd.DataFrame(metrics)
    metrics.to_csv(res_dir / "tract_holdout_metrics.csv", index=False)
    county_val = pd.concat(county_metrics)
    county_val.to_csv(res_dir / "county_rollup_holdout_2024.csv", index=False)

    fc = pd.concat(forecasts)
    keep = ["tract_id", "county_fips", "county_name", "scenario", "source_year", "forecast_year", "population",
            "observed_rate_source_year", "area_level", "predicted_food_insecurity_rate", "predicted_food_insecure_persons"]
    fc[keep].sort_values(["scenario", "forecast_year", "tract_id"]).to_csv(fc_dir / "tract_forecast_2025_2027.csv", index=False)

    cty_fc = fc.groupby(["scenario", "forecast_year"]).apply(
        lambda g: county_rollup(g, ["observed_rate_source_year", "predicted_food_insecurity_rate"]), include_groups=False
    ).reset_index(level=[0, 1]).rename(columns={"observed_rate_source_year": "observed_rate_2024"})
    cty_fc["predicted_food_insecure_persons"] = (cty_fc["predicted_food_insecurity_rate"] * cty_fc["population"]).round()
    cty_fc.to_csv(fc_dir / "county_forecast_2025_2027.csv", index=False)

    pd.set_option("display.width", 200)
    print("\nHoldout (target year 2024), tract level, population-weighted:")
    print(metrics[["horizon_years", "method", "alpha", "n_train", "n", "weighted_mae", "weighted_rmse", "weighted_mean_error"]].round(4).to_string(index=False))
    print("\nCounty roll-up MAE on 2024 holdout:")
    for k, g in county_val.groupby("horizon_years"):
        mae = {c: np.mean(abs(g[c] - g.actual_rate)) for c in ("pred_model_flat", "pred_model_oracle_level", "pred_persistence",
                                                         "pred_persistence_oracle_level")}
        print(f"  k={k}: " + "  ".join(f"{c[5:]} {v:.4f}" for c, v in mae.items()))
    for scen, g in cty_fc.groupby("scenario"):
        print(f"\nCounty forecast -- level scenario '{scen}':")
        print(g.pivot(index="county_name", columns="forecast_year", values="predicted_food_insecurity_rate")
              .join(g.groupby("county_name")["observed_rate_2024"].first()).round(3).to_string())


if __name__ == "__main__":
    main()

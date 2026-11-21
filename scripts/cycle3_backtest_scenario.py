#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""As-of backtests for the cycle 3 scenario model. Each forecast uses only what was known at the origin year.

Layer 1 (county level, MMG 2019-2024): does "anchor + published sensitivities" track FTB's counties?
    persistence            rate[s]                                               (likely FTB's current method)
    anchor_drivers         rate[s] + b_unemp x d_unemp + b_pov x d_pov, S = 0   (actual BLS / ACS changes)
    anchor_drivers_natS    the above + actual change in MMG's national year effect (S from history)
    persistence_ftb_shift  rate[s] + actual FTB-wide change                     (best case for the level)
  The "actual" driver and S changes are what a perfect scenario would have assumed, so these rows test the
  MECHANISM, not the assumptions. 2023 is flagged: FA's known error (2022 unemployment used) until corrected.

Layer 2 (tract allocation, MMG 2020-2024): with the county total known, how well do shares place the need?
    shares_persistence     tract share of county FI persons in year s
    ridge_challenger       shares from scripts/cycle3_tract_model.py (as-of trained), x population in year s
  Metrics: misallocated share (% of county need put in the wrong tracts), top-20% tract hit rate, Spearman.

Usage:  python scripts/cycle3_backtest_scenario.py   -> results/cycle3/backtest_layer1_county.csv, backtest_layer2_tract.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (PROJECT_ROOT, PROJECT_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from src.models.train_model import fit_weighted_ridge, predict_weighted_ridge  # noqa: E402
from src.scenario import inputs as I  # noqa: E402
import cycle3_tract_model as T  # noqa: E402

HORIZONS = (1, 2, 3)
TOP = 0.20


def layer1(cfg: dict) -> pd.DataFrame:
    c = I.load_mmg_county()
    unemp, _ = I.load_bls_unemployment(cfg["_root"] / cfg["unemployment_history"])
    c = c.merge(unemp[["county_fips", "year", "unemployment_pct"]], on=["county_fips", "year"], how="left")
    b_un, b_pov = cfg["coefficients"]["unemployment"], cfg["coefficients"]["poverty"]
    fe = {int(k): v for k, v in cfg["common_shift_history"].items()}
    w = c.pivot(index="county_fips", columns="year", values="population")
    rate = c.pivot(index="county_fips", columns="year", values="fi_rate")
    un = c.pivot(index="county_fips", columns="year", values="unemployment_pct")
    pov = c.pivot(index="county_fips", columns="year", values="poverty_rate") * 100
    years = sorted(c["year"].unique())
    rows = []
    for s in years:
        for k in HORIZONS:
            t = s + k
            if t not in years:
                continue
            ftb_level = lambda y: np.average(rate[y], weights=w[y])  # noqa: E731
            preds = {
                "persistence": rate[s],
                "anchor_drivers": rate[s] + (b_un * (un[t] - un[s]) + b_pov * (pov[t] - pov[s])) / 100,
            }
            preds["anchor_drivers_natS"] = preds["anchor_drivers"] + (fe.get(t, 0.0) - fe.get(s, 0.0)) / 100
            preds["persistence_ftb_shift"] = rate[s] + ftb_level(t) - ftb_level(s)
            for name, p in preds.items():
                err = (p - rate[t]) * 100
                rows.append({"horizon_years": k, "origin_year": s, "target_year": t, "method": name,
                             "county_mae_pts": float(err.abs().mean()), "county_bias_pts": float(err.mean()),
                             "ftb_level_error_pts": float((np.average(p, weights=w[t]) - ftb_level(t)) * 100),
                             "involves_2023_error": 2023 in (s, t)})
    return pd.DataFrame(rows)


def _alloc_metrics(pred_share: pd.Series, actual: pd.DataFrame) -> dict:
    """pred_share and actual (tract_geoid, county_fips, fi_persons) -> allocation metrics, weighted by county need."""
    a = actual.set_index("tract_geoid")
    a = a.assign(pred=pred_share.reindex(a.index).fillna(0.0))
    a["pred"] = a["pred"] / a.groupby("county_fips")["pred"].transform("sum")
    a["act"] = a["fi_persons"] / a.groupby("county_fips")["fi_persons"].transform("sum")
    mis, hit, rho, wts = [], [], [], []
    for _, g in a.groupby("county_fips"):
        n_top = max(1, int(round(len(g) * TOP)))
        top_act = set(g["act"].nlargest(n_top).index)
        top_pred = set(g["pred"].nlargest(n_top).index)
        mis.append(0.5 * (g["pred"] - g["act"]).abs().sum())
        hit.append(len(top_act & top_pred) / n_top)
        rho.append(g["pred"].rank().corr(g["act"].rank()))  # Spearman = Pearson on ranks (no scipy needed)
        wts.append(g["fi_persons"].sum())
    return {"misallocated_share": float(np.average(mis, weights=wts)), "top20_hit_rate": float(np.average(hit, weights=wts)),
            "spearman": float(np.average(rho, weights=wts))}


def layer2() -> pd.DataFrame:
    tr = I.load_mmg_tract().dropna(subset=["fi_persons"])
    tr = tr[tr["population"] > 0]
    panel = T.attach_alice(T.load_tract_panel(T.EXT / "MMG_Consolidated.xlsx", T.EXT / "MMG_2026_2024-data.xlsx"),
                           T.load_alice(T.EXT / "2025_ALICE_Florida_Data_Sheet.xlsx"))
    fi_levels = T.area_levels(panel, ["food_insecurity_rate"])["food_insecurity_rate"]
    rows = []
    for k in HORIZONS:
        pairs = T.make_pairs(panel, k)
        for s in sorted(tr["year"].unique()):
            t = s + k
            if t not in tr["year"].values:
                continue
            actual = tr[tr["year"] == t][["tract_geoid", "county_fips", "fi_persons"]]
            base = tr[tr["year"] == s].set_index("tract_geoid")
            methods = {"shares_persistence": base["fi_persons"]}
            train = pairs[pairs["target_year"] <= s]
            test = pairs[pairs["source_year"] == s]
            if not train.empty and not test.empty:
                alpha = T.pick_alpha(train)
                m = fit_weighted_ridge(train[T.FEATURES], train["food_insecurity_rate"], train["population"], alpha=alpha)
                rate = (fi_levels.loc[s] + predict_weighted_ridge(m, test[T.FEATURES])).clip(lower=0)
                pred = pd.Series((rate * test["population"]).values,
                                 index=test["tract_id"].astype("int64").astype(str).str.zfill(11).values)
                methods["ridge_challenger"] = pred
            for name, share in methods.items():
                rows.append({"horizon_years": k, "origin_year": s, "target_year": t, "method": name,
                             **_alloc_metrics(share, actual), "involves_2023": 2023 in (s, t)})
    return pd.DataFrame(rows)


def main():
    cfg = I.load_config(PROJECT_ROOT / "config" / "cycle3_assumptions.yaml")
    res = PROJECT_ROOT / "results" / "cycle3"
    res.mkdir(parents=True, exist_ok=True)
    l1, l2 = layer1(cfg), layer2()
    l1.round(4).to_csv(res / "backtest_layer1_county.csv", index=False)
    l2.round(4).to_csv(res / "backtest_layer2_tract.csv", index=False)
    pd.set_option("display.width", 220)
    print("LAYER 1 (county). Mean over origins, excluding transitions touching 2023 (FA error):")
    ok = l1[~l1["involves_2023_error"]]
    print(ok.groupby(["horizon_years", "method"])[["county_mae_pts", "county_bias_pts", "ftb_level_error_pts"]]
          .agg(lambda x: x.abs().mean() if x.name == "ftb_level_error_pts" else x.mean()).round(2).to_string())
    print("\nLayer 1 by transition (FTB-wide level error, pts):")
    print(l1.pivot_table(index=["origin_year", "target_year"], columns="method", values="ftb_level_error_pts").round(2).to_string())
    print("\nLAYER 2 (tract allocation, county totals known). Mean over origins:")
    print(l2.groupby(["horizon_years", "method"])[["misallocated_share", "top20_hit_rate", "spearman"]].mean().round(3).to_string())


if __name__ == "__main__":
    main()

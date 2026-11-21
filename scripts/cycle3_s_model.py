#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S model: what moves MMG's national year effect (the area-wide shift S)?

Target: MMG's year fixed effects 2009-2024 (Technical Brief 2026, Appendix Table 1). These come from CPS survey
data, i.e. real measurements, unlike MMG county/tract rates (see cycle3_check_mmg_circularity.py).
Model, on year-to-year changes (no intercept):
    dS[t] = b_food x d ln(relative food-at-home price)[t] x 100  +  b_aid x d aid[t]
    relative food price = CPI food at home / CPI all items (BLS, national, annual average)
    aid = 1 in 2020-2021 (stimulus payments, SNAP emergency allotments, expanded child tax credit)

With 15 changes this is a CALIBRATION of the scenario menu, not a forecaster: in an as-of test it barely beats
"S unchanged". It gives (a) the food-price part of S from observed CPI, and (b) the size of a large benefit change.
Outputs (public data only): results/cycle3/s_model_history.csv, s_model_coefficients.csv, s_model_backtest.csv.
Usage:  python scripts/cycle3_s_model.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.scenario import inputs as I  # noqa: E402

AID_YEARS = (2020, 2021)
FEATURES = ["d_food_rel_pct", "d_aid"]


def history(cfg: dict) -> pd.DataFrame:
    a = I.load_national_cpi(cfg)
    fe = {int(k): float(v) for k, v in cfg["common_shift_history"].items()}
    fe.setdefault(2009, 0.0)
    a["S"] = pd.Series(fe)
    a["aid"] = a.index.isin(AID_YEARS).astype(float)
    a["d_S"] = a["S"].diff()
    a["d_food_rel_pct"] = 100 * np.log(a["food_rel"]).diff()
    a["d_aid"] = a["aid"].diff()
    return a


def fit(d: pd.DataFrame) -> np.ndarray:
    b, *_ = np.linalg.lstsq(d[FEATURES].to_numpy(float), d["d_S"].to_numpy(float), rcond=None)
    return b


def main():
    cfg = I.load_config(ROOT / "config" / "cycle3_assumptions.yaml")
    h = history(cfg)
    train = h.loc[2010:2024].dropna(subset=["d_S"])
    b = fit(train)
    fitted = train[FEATURES].to_numpy(float) @ b
    r2 = 1 - ((train["d_S"] - fitted) ** 2).sum() / ((train["d_S"] - train["d_S"].mean()) ** 2).sum()

    bt = []
    for y in range(2015, 2025):  # as-of: fit on changes before y, predict the change in y
        bb = fit(train.loc[:y - 1])
        bt.append({"year": y, "actual_dS": train.loc[y, "d_S"],
                   "pred_dS_model": float(train.loc[y, FEATURES].to_numpy(float) @ bb), "pred_dS_unchanged": 0.0})
    bt = pd.DataFrame(bt)
    bt["abs_err_model"] = (bt["pred_dS_model"] - bt["actual_dS"]).abs()
    bt["abs_err_unchanged"] = bt["actual_dS"].abs()

    res = ROOT / "results" / "cycle3"
    res.mkdir(parents=True, exist_ok=True)
    h.round(4).to_csv(res / "s_model_history.csv")
    pd.DataFrame([{"b_food_per_1pct_relative_price": b[0], "b_aid_on": b[1], "r2_changes": r2, "n_changes": len(train),
                   "asof_mae_model": bt["abs_err_model"].mean(), "asof_mae_unchanged": bt["abs_err_unchanged"].mean()}]
                 ).round(3).to_csv(res / "s_model_coefficients.csv", index=False)
    bt.round(3).to_csv(res / "s_model_backtest.csv", index=False)

    d22 = train.loc[2022]
    print(f"dS = {b[0]:.3f} x (% change in relative food price) + {b[1]:.2f} x (change in aid)   R2 = {r2:.2f}, n = {len(train)}")
    print(f"As-of MAE 2015-24: model {bt['abs_err_model'].mean():.2f} vs S unchanged {bt['abs_err_unchanged'].mean():.2f} pts")
    print(f"2022 decomposition: food prices {b[0] * d22['d_food_rel_pct']:+.2f} + aid ending {b[1] * d22['d_aid']:+.2f}"
          f" = {b[0] * d22['d_food_rel_pct'] + b[1] * d22['d_aid']:+.2f} (actual {d22['d_S']:+.2f})")
    base = cfg["base_year"]
    for y in [y for y in h.index if y > base]:
        print(f"Food-price part of S, {y} vs {base}: {b[0] * 100 * np.log(h.loc[y, 'food_rel'] / h.loc[base, 'food_rel']):+.2f} pts"
              f" (relative food price {100 * (h.loc[y, 'food_rel'] / h.loc[base, 'food_rel'] - 1):+.1f}%)")
    print(f"\n-> put b_food = {b[0]:.3f} in config/cycle3_assumptions.yaml (s_model.food_price_coef)")


if __name__ == "__main__":
    main()

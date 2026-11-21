#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Why cycle 3 doesn't fit its own food-insecurity regression on MMG county data.

MMG county rates are not survey measurements: Feeding America computes them from its state-level model
(MMG 2026 Technical Brief, Step 1-2, Appendix Table 1). Regressing MMG county FI on the same drivers therefore
recovers Feeding America's published coefficients with R^2 ~ 1. A "Tampa Bay model" fit to MMG data would only
re-learn that formula. Run:  python scripts/cycle3_check_mmg_circularity.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = {
    "Unemployment Rate (1 Yr BLS)": "unemployment", "Non-Undergrad Poverty Rate (5 Yr ACS)": "poverty",
    "Disability Rate (5 Yr ACS)": "disability", "Homeownership Rate (5 Yr ACS)": "homeownership",
    "Median Income (5 Yr ACS)": "median_income_k", "Percent Hispanic (any race) (5 Year ACS)": "hispanic",
    "Percent Black (5 Yr ACS)": "black",
}
PUBLISHED = {"unemployment": 0.471, "poverty": 0.289, "disability": 0.319, "homeownership": -0.120,
             "median_income_k": -0.001, "hispanic": 0.087, "black": -0.088}  # Appendix Table 1, full population


def fit(d: pd.DataFrame, state_effects: bool) -> tuple[float, dict]:
    X = d[list(DRIVERS.values())].astype(float)
    parts = [np.ones(len(X)), X.values]
    if state_effects:
        parts.append(pd.get_dummies(d["State"], drop_first=True, dtype=float).values)
    A, y = np.column_stack(parts), d["Overall Food Insecurity Rate"].astype(float).values
    b, *_ = np.linalg.lstsq(A, y, rcond=None)
    r2 = 1 - ((y - A @ b) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    return r2, dict(zip(X.columns, b[1:1 + X.shape[1]]))


def main():
    df = pd.read_excel(ROOT / "data" / "external" / "MMG_2026_2024-data.xlsx", sheet_name="County").rename(columns=DRIVERS)
    df = df.dropna(subset=list(DRIVERS.values()) + ["Overall Food Insecurity Rate"])
    df["median_income_k"] = df["median_income_k"] / 1000
    rows = []
    for scope, d, fe in (("Florida counties", df[df["State"] == "FL"], False), ("All US counties", df, True)):
        r2, coef = fit(d, fe)
        rows.append({"scope": scope, "n": len(d), "r2": round(r2, 4), **{k: round(v, 3) for k, v in coef.items()}})
    rows.append({"scope": "MMG published (state CPS 2009-2024)", "n": 816, "r2": None, **PUBLISHED})
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    out.to_csv(ROOT / "results" / "cycle3" / "mmg_circularity_check.csv", index=False)


if __name__ == "__main__":
    main()

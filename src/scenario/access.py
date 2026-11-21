# -*- coding: utf-8 -*-
"""Service-area access to FTB food per census tract: enhanced two-step floating catchment area (E2SFCA).

    Step 1  for each supply tract j (agencies located there, pounds S_j):  R_j = S_j / sum_k W(d_kj) x P_k
    Step 2  for each tract i:                                             A_i = sum_j W(d_ij) x R_j
    P = food-insecure people (demand), d = distance between tract centroids (miles), W = distance-band weight
    within the catchment radius (Luo & Qi 2009 bands: 1.00 / 0.68 / 0.22 for the inner / middle / outer third).
A_i = pounds per food-insecure person per year reachable from tract i. Compared with the FTB-wide average it
shows where need is far from where food is delivered, without assuming people only use agencies in their own tract.

Limits: agencies are placed at their tract's centroid; pounds known only at county level are left out; agencies
outside FTB's 10 counties aren't counted (tracts on the edge may look worse than they are). Distribution is capped
by supply and capacity, so low access is a prompt to investigate, not proof of unmet need.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BANDS = ((1 / 3, 1.00), (2 / 3, 0.68), (1.0, 0.22))


def load_tract_centroids(path: Path) -> pd.DataFrame:
    g = pd.read_csv(path, sep="\t", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    return g.rename(columns={"GEOID": "tract_geoid", "INTPTLAT": "lat", "INTPTLONG": "lon"})[["tract_geoid", "lat", "lon"]]


def _miles(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * np.arcsin(np.sqrt(a))


def _weights(d: np.ndarray, radius: float) -> np.ndarray:
    w = np.zeros_like(d)
    lower = 0.0
    for frac, wt in BANDS:
        w[(d >= lower) & (d <= radius * frac)] = wt
        lower = radius * frac + 1e-9
    return w


def e2sfca(demand: pd.DataFrame, supply: pd.DataFrame, centroids: pd.DataFrame, radius: float) -> pd.Series:
    """demand: tract_geoid, fi_people ; supply: tract_geoid, pounds. Returns A_i (lbs per FI person) by tract."""
    dm = demand.merge(centroids, on="tract_geoid")
    sp = supply[supply["pounds"] > 0].merge(centroids, on="tract_geoid")
    d = _miles(dm["lat"].values[:, None], dm["lon"].values[:, None], sp["lat"].values[None, :], sp["lon"].values[None, :])
    w = _weights(d, radius)                                  # demand x supply
    pop_in_reach = (w * dm["fi_people"].values[:, None]).sum(axis=0)
    ratio = np.divide(sp["pounds"].values, pop_in_reach, out=np.zeros(len(sp)), where=pop_in_reach > 0)
    return pd.Series(w @ ratio, index=dm["tract_geoid"].values)


def tract_access(need_tract: pd.DataFrame, supply: pd.DataFrame, centroids: pd.DataFrame,
                 radii=(5.0, 10.0, 15.0), main_radius: float = 10.0, low_ratio: float = 0.5) -> pd.DataFrame:
    """Access per tract at several radii, relative to the FTB-wide average pounds per FI person."""
    out = need_tract[["tract_geoid", "county_fips", "fi_people"]].copy()
    avg = supply["pounds"].sum() / out["fi_people"].sum()
    for r in radii:
        a = e2sfca(out, supply, centroids, r)
        out[f"access_lbs_per_fi_person_{int(r)}mi"] = out["tract_geoid"].map(a).fillna(0.0)
        out[f"access_ratio_{int(r)}mi"] = out[f"access_lbs_per_fi_person_{int(r)}mi"] / avg
    out["missing_centroid"] = ~out["tract_geoid"].isin(centroids["tract_geoid"])
    key = f"access_ratio_{int(main_radius)}mi"
    g = out.groupby("county_fips")["fi_people"]
    out["high_need_tract"] = g.rank(ascending=False, method="first") <= (g.transform("size") * 0.2).round()
    out["flag_low_access"] = out[key] < low_ratio
    out["flag_high_need_low_access"] = out["high_need_tract"] & out["flag_low_access"]
    out["flag_low_access_all_radii"] = np.logical_and.reduce([out[f"access_ratio_{int(r)}mi"] < low_ratio for r in radii])
    out.attrs["ftb_avg_lbs_per_fi_person"] = avg
    return out

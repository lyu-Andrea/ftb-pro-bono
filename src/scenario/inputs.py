# -*- coding: utf-8 -*-
"""Load the inputs of the cycle 3 scenario model: MMG county/tract, BLS unemployment, BEBR population,
distribution and seasonal shares. Rates come back as decimals (0.152), and keys as text
(5-digit county FIPS, 11-digit tract GEOID)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXT = PROJECT_ROOT / "data" / "external"

FTB_COUNTIES = {
    "12017": "Citrus", "12049": "Hardee", "12053": "Hernando", "12055": "Highlands", "12057": "Hillsborough",
    "12081": "Manatee", "12101": "Pasco", "12103": "Pinellas", "12105": "Polk", "12119": "Sumter",
}

COUNTY_COLS = {
    "FIPS": "county_fips", "Year": "year",
    "Total Population (5 Year ACS)": "population",
    "Overall Food Insecurity Rate": "fi_rate",
    "# of Food Insecure Persons Overall": "fi_persons",
    "Non-Undergrad Poverty Rate (5 Yr ACS)": "poverty_rate",
    "Unemployment Rate (1 Yr BLS)": "mmg_unemployment_rate",
    "Annual Meal Gap": "annual_meal_gap",
}
TRACT_COLS = {
    "Tract ID": "tract_geoid", "County FIPS": "county_fips", "Year": "year",
    "Total Population (5 Yr ACS)": "population",
    "Overall Food Insecurity Rate": "fi_rate",
    "# of Food Insecure Persons Overall": "fi_persons",
}


def load_config(path: Path) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = PROJECT_ROOT
    return cfg


def _path(cfg: dict, rel: str) -> Path:
    return cfg["_root"] / rel


def _keys(df: pd.DataFrame) -> pd.DataFrame:
    df["county_fips"] = df["county_fips"].astype("int64").astype(str).str.zfill(5)
    if "tract_geoid" in df:
        df["tract_geoid"] = df["tract_geoid"].astype("int64").astype(str).str.zfill(11)
    return df


def load_mmg_county(consolidated: Path = EXT / "MMG_Consolidated.xlsx",
                    latest: Path = EXT / "MMG_2026_2024-data.xlsx") -> pd.DataFrame:
    """FTB counties, 2019-2024. NOTE: 2023 in MMG_Consolidated has Feeding America's known error
    (2022 BLS unemployment used), so `flag_2023_error` marks those rows until the corrected file arrives."""
    hist = pd.read_excel(consolidated, sheet_name="County")
    new = pd.read_excel(latest, sheet_name="County")
    df = pd.concat([hist, new], ignore_index=True).rename(columns=COUNTY_COLS)[list(COUNTY_COLS.values())]
    df = _keys(df)
    df = df[df["county_fips"].isin(FTB_COUNTIES)].drop_duplicates(["county_fips", "year"], keep="last")
    df["county_name"] = df["county_fips"].map(FTB_COUNTIES)
    df["flag_2023_error"] = df["year"].eq(2023)
    return df.sort_values(["county_fips", "year"]).reset_index(drop=True)


def load_mmg_tract(consolidated: Path = EXT / "MMG_Consolidated.xlsx",
                   latest: Path = EXT / "MMG_2026_2024-data.xlsx") -> pd.DataFrame:
    hist = pd.read_excel(consolidated, sheet_name="Census Tract")
    new = pd.read_excel(latest, sheet_name="Census Tract")
    df = pd.concat([hist, new], ignore_index=True).rename(columns=TRACT_COLS)[list(TRACT_COLS.values())]
    df = _keys(df)
    df = df[df["county_fips"].isin(FTB_COUNTIES)].drop_duplicates(["tract_geoid", "year"], keep="first")
    return df.sort_values(["tract_geoid", "year"]).reset_index(drop=True)


def load_bls_unemployment(path: Path) -> tuple[pd.DataFrame, int]:
    """County annual unemployment (%), from BLS LAUS monthly rates.

    Complete years are plain annual averages of the available months. The latest, partial year is estimated as
    the previous year's annual average + (this year's year-to-date average - the same months last year),
    which cancels out seasonality. Returns (table of county_fips, year, unemployment_pct, is_estimate; last year).
    """
    with open(path) as f:
        series = json.load(f)["Results"]["series"]
    rows = []
    for s in series:
        fips = s["seriesID"][5:10]
        for d in s["data"]:
            if d["period"].startswith("M") and d["period"] != "M13":
                try:
                    rows.append((fips, int(d["year"]), int(d["period"][1:]), float(d["value"])))
                except ValueError:  # "-": e.g. Oct 2025, not collected during the federal funding lapse
                    continue
    m = pd.DataFrame(rows, columns=["county_fips", "year", "month", "rate"])
    last_year = int(m["year"].max())
    months_last = sorted(m.loc[m["year"] == last_year, "month"].unique())
    annual = m[m["year"] < last_year].groupby(["county_fips", "year"])["rate"].mean().rename("unemployment_pct").reset_index()
    annual["is_estimate"] = False
    if len(months_last) < 12:
        ytd = m[m["month"].isin(months_last) & m["year"].isin([last_year - 1, last_year])]
        ytd = ytd.groupby(["county_fips", "year"])["rate"].mean().unstack()
        prev = annual[annual["year"] == last_year - 1].set_index("county_fips")["unemployment_pct"]
        est = (prev + ytd[last_year] - ytd[last_year - 1]).rename("unemployment_pct").reset_index()
        est["year"], est["is_estimate"] = last_year, True
        annual = pd.concat([annual, est], ignore_index=True)
    else:
        extra = m[m["year"] == last_year].groupby("county_fips")["rate"].mean().rename("unemployment_pct").reset_index()
        extra["year"], extra["is_estimate"] = last_year, False
        annual = pd.concat([annual, extra], ignore_index=True)
    return annual.sort_values(["county_fips", "year"]).reset_index(drop=True), last_year


def _county_code(name: str):
    name = str(name).strip().lower().replace(" county", "")
    for fips, n in FTB_COUNTIES.items():
        if n.lower() == name:
            return fips
    return None


def load_bebr_population(estimates: Path, projections: Path) -> pd.DataFrame:
    """Population anchor points per county: 2020 census, 2025 and 2026 estimates (all series),
    2030 projections (low / medium / high). Returns long table county_fips, series, year, population."""
    est = pd.read_excel(estimates, header=None)
    anchors = []
    for _, r in est.iterrows():
        label = str(r[0])
        if label.strip().endswith("County") and _county_code(label):
            fips = _county_code(label)
            anchors += [(fips, s, 2020, float(r[3])) for s in ("low", "medium", "high")]
            anchors += [(fips, s, 2026, float(r[1])) for s in ("low", "medium", "high")]
    proj = pd.read_excel(projections, header=None)
    current = None
    for _, r in proj.iterrows():
        label = str(r[0]).strip()
        if _county_code(label) and label.isupper():
            current = _county_code(label)
            anchors += [(current, s, 2025, float(r[1])) for s in ("low", "medium", "high")]
        elif current and label.lower() in ("low", "medium", "high"):
            anchors.append((current, label.lower(), 2030, float(r[3])))
        elif label.isupper() and label not in ("", "NAN"):
            current = None
    df = pd.DataFrame(anchors, columns=["county_fips", "series", "year", "population"])
    missing = set(FTB_COUNTIES) - set(df["county_fips"])
    if missing:
        raise ValueError(f"BEBR population missing for {missing}")
    return df


def population_path(anchors: pd.DataFrame, years: list) -> pd.DataFrame:
    """Population on April 1 of each year.

    Up to 2026: linear interpolation of the 2020 census and the 2025 / 2026 estimates. After 2026: the 2026
    estimate grown at the series' projected annual rate (2025 estimate -> 2030 projection). The 2026 estimates
    are newer than the 2030 projections, so interpolating straight to 2030 could bend the path (e.g. Hardee).
    """
    out = []
    for (fips, series), g in anchors.groupby(["county_fips", "series"]):
        p = g.set_index("year")["population"]
        rate = (p[2030] / p[2025]) ** (1 / 5)
        known = p.loc[[2020, 2025, 2026]]
        for y in years:
            pop = float(np.interp(y, known.index, known.values)) if y <= 2026 else float(p[2026] * rate ** (y - 2026))
            out.append((fips, series, y, pop))
    return pd.DataFrame(out, columns=["county_fips", "series", "year", "population"])


def load_distribution_last_year(path: Path, column: str) -> tuple[pd.DataFrame, list]:
    """County pounds over the last 4 complete quarters (local service by default)."""
    d = pd.read_parquet(path)
    d = d[d["county_fips"].isin(FTB_COUNTIES)]
    complete = sorted(d.loc[d["n_months"] == 3, "quarter"].unique())[-4:]
    pounds = d[d["quarter"].isin(complete)].groupby("county_fips")[column].sum().rename("pounds_last_4q").reset_index()
    return pounds, complete


def load_tract_distribution(path: Path, quarters: list, column: str = "pounds_local") -> pd.DataFrame:
    t = pd.read_parquet(path)
    t = t[t["quarter"].isin(quarters)]
    return t.groupby(["tract_geoid", "county_fips"]).agg(
        pounds_last_4q=(column, "sum"), n_agencies=("n_agencies", "max")).reset_index()


def load_seasonal_shares(path: Path, scope: str) -> pd.DataFrame:
    """Quarter shares of a year's local pounds, renormalized to sum to exactly 1 (FTB-wide or per county)."""
    s = pd.read_parquet(path)
    if scope == "FTB":
        s = s[s["scope"] == "FTB"].copy()
        s["share"] = s["share_mean"] / s["share_mean"].sum()
    else:
        s = s[s["scope"].isin(FTB_COUNTIES)].rename(columns={"scope": "county_fips"}).copy()
        s["share"] = s["share_mean"] / s.groupby("county_fips")["share_mean"].transform("sum")
    return s

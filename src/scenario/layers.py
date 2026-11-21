# -*- coding: utf-8 -*-
"""Cycle 3 scenario model, Layers 1-4. Every step is a plain multiplication/addition that can be checked in a
spreadsheet. Rates are decimals inside the code; assumptions in the config are percentage points."""
import pandas as pd

PTS = 100.0  # percentage points -> decimal


def unemployment_change(unemp: pd.DataFrame, last_bls_year: int, base_year: int, year: int, extra_pts: float) -> pd.Series:
    """Change in county unemployment (pts) from the base year to `year`: the observed BLS change up to the last BLS
    year, flat after that, plus the scenario's extra for years beyond the BLS data."""
    u = unemp.pivot(index="county_fips", columns="year", values="unemployment_pct")
    observed = u[min(year, last_bls_year)] - u[base_year]
    return observed + (extra_pts if year > last_bls_year else 0.0)


def layer1_county_need(cfg: dict, mmg_county: pd.DataFrame, unemp: pd.DataFrame, last_bls_year: int,
                       population: pd.DataFrame) -> pd.DataFrame:
    """rate[c,T] = MMG rate[c,base] + b_unemp * d_unemp + b_pov * d_pov + S ; people = rate x population."""
    base_year, coef = cfg["base_year"], cfg["coefficients"]
    base = mmg_county[mmg_county["year"] == base_year].set_index("county_fips")
    rows = []
    for name, sc in cfg["scenarios"].items():
        for year in cfg["target_years"]:
            d_un = unemployment_change(unemp, last_bls_year, base_year, year, sc["unemployment_extra"])
            pop = population[(population["series"] == sc["population_series"]) & (population["year"] == year)]
            pop = pop.set_index("county_fips")["population"]
            for fips, b in base.iterrows():
                contrib_un = coef["unemployment"] * d_un[fips]
                contrib_pov = coef["poverty"] * sc["poverty_change"]
                rate = b["fi_rate"] + (contrib_un + contrib_pov + sc["common_shift"]) / PTS
                rows.append({
                    "scenario": name, "year": year, "county_fips": fips, "county_name": b["county_name"],
                    "base_fi_rate": b["fi_rate"], "unemployment_change_pts": d_un[fips],
                    "contrib_unemployment_pts": contrib_un, "contrib_poverty_pts": contrib_pov,
                    "common_shift_pts": sc["common_shift"], "fi_rate": rate,
                    "population": pop[fips], "fi_people": rate * pop[fips],
                })
    return pd.DataFrame(rows)


def tract_shares(mmg_tract: pd.DataFrame, year: int) -> pd.DataFrame:
    """Each tract's share of its county's food-insecure persons in `year`. Tracts with missing FI get share 0."""
    t = mmg_tract[mmg_tract["year"] == year][["tract_geoid", "county_fips", "fi_persons", "fi_rate", "population"]].copy()
    t["share_missing_fi"] = t["fi_persons"].isna()
    t["fi_persons"] = t["fi_persons"].fillna(0.0)
    t["share"] = t["fi_persons"] / t.groupby("county_fips")["fi_persons"].transform("sum")
    return t


def layer2_allocate(county: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    keep = ["scenario", "year", "county_fips", "county_name", "fi_people"]
    t = county[keep].merge(shares[["tract_geoid", "county_fips", "share", "share_missing_fi"]], on="county_fips")
    t["fi_people"] = t["fi_people"] * t["share"]
    return t


def coverage_by_county(cfg: dict, mmg_county: pd.DataFrame, pounds_last_4q: pd.DataFrame,
                       county_need: pd.DataFrame, period_year: int) -> pd.DataFrame:
    """Coverage = FTB local-service pounds in the last 4 complete quarters / (baseline meals needed in the same
    period x lbs per meal). Using the same period means "keep current coverage" reproduces today's pounds in that
    year and only grows with need afterwards. Meals per FI person = base-year MMG meal gap / FI persons."""
    lbs_per_meal = cfg["lbs_per_meal"]
    base = mmg_county[mmg_county["year"] == cfg["base_year"]][["county_fips", "annual_meal_gap", "fi_persons"]].copy()
    base["meals_per_fi_person"] = base["annual_meal_gap"] / base["fi_persons"]
    now = county_need[(county_need["scenario"] == "baseline") & (county_need["year"] == period_year)]
    c = base.merge(now[["county_fips", "fi_people"]], on="county_fips").merge(pounds_last_4q, on="county_fips", how="left")
    c["need_lbs_same_period"] = c["fi_people"] * c["meals_per_fi_person"] * lbs_per_meal
    c["coverage_current"] = c["pounds_last_4q"] / c["need_lbs_same_period"]
    ftb_wide = c["pounds_last_4q"].sum() / c["need_lbs_same_period"].sum()
    mode = cfg["coverage"]["mode"]
    if mode == "ftb_wide_current":
        c["coverage_used"] = ftb_wide
    elif mode == "by_county_current":
        c["coverage_used"] = c["coverage_current"]
    elif mode == "fixed":
        c["coverage_used"] = float(cfg["coverage"]["fixed_value"])
    else:
        raise ValueError(f"unknown coverage mode {mode}")
    c.attrs["ftb_wide_current"] = ftb_wide
    return c.drop(columns=["fi_people"])


def layer3_convert(df: pd.DataFrame, coverage: pd.DataFrame, lbs_per_meal: float) -> pd.DataFrame:
    """people -> meals needed -> FTB planned meals -> FTB planned lbs."""
    out = df.merge(coverage[["county_fips", "meals_per_fi_person", "coverage_used"]], on="county_fips")
    out["meals_needed"] = out["fi_people"] * out["meals_per_fi_person"]
    out["ftb_planned_meals"] = out["meals_needed"] * out["coverage_used"]
    out["ftb_planned_lbs"] = out["ftb_planned_meals"] * lbs_per_meal
    return out


def layer4_hurricane(df: pd.DataFrame, cfg_h: dict) -> pd.DataFrame:
    """extra lbs = planned lbs x footprint share x (uplift - 1) x weeks / 52, for counties in the footprint."""
    out = df.copy()
    out["hurricane_extra_lbs"] = 0.0
    if cfg_h.get("enabled"):
        hit = out["county_fips"].isin([str(c) for c in cfg_h["counties"]])
        factor = cfg_h["footprint_share"] * (cfg_h["uplift"] - 1) * cfg_h["weeks"] / 52
        out.loc[hit, "hurricane_extra_lbs"] = out.loc[hit, "ftb_planned_lbs"] * factor
    out["ftb_planned_lbs_total"] = out["ftb_planned_lbs"] + out["hurricane_extra_lbs"]
    return out


def quarterly(df: pd.DataFrame, shares: pd.DataFrame, scope: str, id_cols: list) -> pd.DataFrame:
    """Split annual planned lbs/meals into calendar quarters by seasonal share. An allocation, not a forecast."""
    s = shares.rename(columns={"share": "season_share"})
    if scope == "FTB":
        q = df.merge(s[["cal_quarter", "season_share"]], how="cross")
    else:
        q = df.merge(s[["county_fips", "cal_quarter", "season_share"]], on="county_fips")
    for c in ("ftb_planned_meals", "ftb_planned_lbs_total"):
        q[c] = q[c] * q["season_share"]
    q["quarter"] = q["year"].astype(str) + "Q" + q["cal_quarter"].astype(str)
    return q[id_cols + ["quarter", "season_share", "ftb_planned_meals", "ftb_planned_lbs_total"]]

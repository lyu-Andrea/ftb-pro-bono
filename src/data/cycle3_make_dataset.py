# -*- coding: utf-8 -*-
"""Cycle 3 distribution data: raw Snowflake CSVs -> tract / county x quarter parquet.

    python -m src.data.cycle3_make_dataset --raw data/raw/2026-11-21

Inputs (data/raw/<date>/, see docs/cycle3_data_export_guide.md):
    d5_ns_pounds_agency_month.csv        NetSuite pounds per customer x month
    d10_ceres_pounds_agency_month.csv    Ceres (BC) pounds per customer x month (optional)
    d6_agency_location.csv               customer -> GOLD tract / ZIP
    d7_exclusions.csv                    disaster / transfer / hub flags
    data/external/census/tab20_zcta520_tract20_natl.txt   Census 2020 ZCTA-tract relationship file

Outputs (data/processed/, gitignored; documented in docs/cycle3_data_dictionary.md):
    distribution_tract_quarter.parquet, distribution_county_quarter.parquet,
    seasonal_quarter_shares.parquet, cycle3_make_dataset_log.json

Agencies are only a stepping stone to tracts: nothing per agency is written out.
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

FTB_COUNTIES = {
    "12017": "Citrus", "12049": "Hardee", "12053": "Hernando", "12055": "Highlands",
    "12057": "Hillsborough", "12081": "Manatee", "12101": "Pasco", "12103": "Pinellas",
    "12105": "Polk", "12119": "Sumter",
}
# Agency numbers carry a county code: HILL0113, MP-HILL, DISA-HILL, INDIV-HILL ...
PREFIX_COUNTY = {"CITR": "12017", "HARD": "12049", "HERN": "12053", "HIGH": "12055", "HILL": "12057",
                 "MANA": "12081", "PASC": "12101", "PINE": "12103", "POLK": "12105", "SUMT": "12119"}
PREFIX_RE = re.compile(r"(?:^|-)(" + "|".join(PREFIX_COUNTY) + r")(?:\d|$|-)")

# Ceres for months before the cutover, NetSuite from it. The overlap check showed NetSuite's
# Aug 1-8 2025 fulfillments already contain the Ceres shipments of those days (same agency, same day).
CUTOVER = pd.Timestamp("2025-08-01")

# Volume that isn't local client service. "hub" applies only to hub_candidate rows listed in HUBS.
NONLOCAL_FLAGS = {"disaster", "transfer_food_bank", "hub"}
HUBS = {"HILL0135", "CITR0013", "HILL0051"}  # PENDING FTB confirmation (plan decision #7)
NONLOCAL_PREFIX = {"DISA": "disaster"}        # plus FBxx- = other food banks
SEASONAL_SKIP_YEARS = {2020}                  # COVID year distorts the seasonal pattern

# NetSuite customer categories counted as "agency"; every other known category is "other_institution"
# (SCHOOLS, SFSP, Food Pharmacy, Internal, DISASTER, FOODBANK, ...). No category -> "unknown".
AGENCY_CATEGORIES = {"Agency"}
# Ceres "FBC Agency Category Code", used only for customers with no NetSuite category (closed before
# 2025-08). Mapping read off the 2026-11-21 crosstab of customers in both systems: PANTRY -> Agency 289/316,
# CHILDREN -> SCHOOLS/SFSP 100/109, HEALTH -> Food Pharmacy 3/3, DISASTER/FOODBANK one-to-one.
CERES_AGENCY_CATEGORIES = {"PANTRY", "SOUP KITCH", "GROUP HOME", "SENIOR", "DISABLED", "SCHOOL"}
CERES_OTHER_CATEGORIES = {"CHILDREN", "HEALTH", "DISASTER", "FOODBANK"}   # anything else -> unknown


def customer_group(category):
    if pd.isna(category):
        return "unknown"
    if category.startswith("Ceres: "):
        c = category[len("Ceres: "):]
        return ("agency" if c in CERES_AGENCY_CATEGORIES
                else "other_institution" if c in CERES_OTHER_CATEGORIES else "unknown")
    return "agency" if category in AGENCY_CATEGORIES else "other_institution"

# How precise each location method is (for the QA list and the log)
PRECISION = {
    "geocoded_address": "1 street address",
    "gold_tract": "2 GOLD tract (fits ZIP)", "gold_tract_2010_translated": "2 GOLD tract (fits ZIP)",
    "zip_override_gold": "3 ZIP main tract", "zip_dominant_tract": "3 ZIP main tract",
    "ceres_zip_dominant_tract": "3 ZIP main tract",
}


def read_csv(path):
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    if "agency_no" in df:
        df["agency_no"] = df["agency_no"].str.strip().str.upper()
    return df


def clean_geoid(s, width):
    """Text code of fixed width; handles '12057004100', '12057004100.0', '1.2057E10'."""
    num = pd.to_numeric(s, errors="coerce")
    return num.map(lambda v: None if pd.isna(v) else str(int(round(v))).zfill(width))


def county_from_agency_no(agency_no):
    m = PREFIX_RE.search(agency_no or "")
    return PREFIX_COUNTY[m.group(1)] if m else None


def last_complete_month(today=None):
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    return today.to_period("M").start_time - pd.offsets.MonthBegin(1)


# ---------------------------------------------------------------------------------------------
def load_distribution(raw, log):
    ns = read_csv(raw / "d5_ns_pounds_agency_month.csv").assign(source="netsuite")
    parts = [ns]
    if (raw / "d10_ceres_pounds_agency_month.csv").exists():
        parts.append(read_csv(raw / "d10_ceres_pounds_agency_month.csv").assign(source="ceres"))
    d = pd.concat(parts, ignore_index=True)[["agency_no", "month", "pounds", "source"]]
    d["month"] = pd.to_datetime(d["month"].str[:10])
    d["pounds"] = pd.to_numeric(d["pounds"])

    # Exact repeats of an agency-month within one source (seen when an extract joined to a
    # table with two rows per agency) are dropped, not summed.
    dup = d.duplicated(["source", "agency_no", "month"])
    log["dropped_duplicate_rows"] = int(dup.sum())
    log["dropped_duplicate_lbs"] = float(d.loc[dup, "pounds"].sum())
    d = d[~dup]

    # Cutover: each month comes from exactly one source.
    wrong_side = ((d.source == "netsuite") & (d.month < CUTOVER)) | ((d.source == "ceres") & (d.month >= CUTOVER))
    log["cutover"] = str(CUTOVER.date())
    log["dropped_by_cutover_lbs"] = d[wrong_side].groupby("source")["pounds"].sum().round().to_dict()
    d = d[~wrong_side]

    lcm = last_complete_month()
    future = d.month > lcm
    log["last_complete_month"] = str(lcm.date())
    log["dropped_future_rows"] = int(future.sum())
    d = d[~future]
    log["months"] = [str(d.month.min().date()), str(d.month.max().date())]
    log["source_by_month_overlap"] = int(d.groupby("month")["source"].nunique().gt(1).sum())
    return d


def dominant_tract_by_zip(xwalk_path):
    x = pd.read_csv(xwalk_path, sep="|", dtype=str, encoding="utf-8-sig",
                    usecols=["GEOID_ZCTA5_20", "GEOID_TRACT_20", "AREALAND_PART"])
    x = x.dropna(subset=["GEOID_ZCTA5_20"])
    x = x[x.GEOID_TRACT_20.str[:5].isin(FTB_COUNTIES)]
    x["AREALAND_PART"] = pd.to_numeric(x.AREALAND_PART)
    tracts_in_zip = x.groupby("GEOID_ZCTA5_20")["GEOID_TRACT_20"].agg(set)
    dominant = (x.sort_values("AREALAND_PART", ascending=False)
                 .drop_duplicates("GEOID_ZCTA5_20").set_index("GEOID_ZCTA5_20")["GEOID_TRACT_20"])
    return dominant, tracts_in_zip, set(x.GEOID_TRACT_20)


def tract2010_to_2020(rel_path):
    """2010 tract -> the 2020 tract with the largest land overlap (FTB counties only)."""
    r = pd.read_csv(rel_path, sep="|", dtype=str, encoding="utf-8-sig",
                    usecols=["GEOID_TRACT_20", "GEOID_TRACT_10", "AREALAND_PART"])
    r = r[r.GEOID_TRACT_20.str[:5].isin(FTB_COUNTIES)]
    r["AREALAND_PART"] = pd.to_numeric(r.AREALAND_PART)
    return (r.sort_values("AREALAND_PART", ascending=False)
             .drop_duplicates("GEOID_TRACT_10").set_index("GEOID_TRACT_10")["GEOID_TRACT_20"])


def load_locations(raw, xwalk_path, rel_path):
    """One row per customer: tract (may be None), county, method. Rules in the data dictionary."""
    d6 = read_csv(raw / "d6_agency_location.csv").drop_duplicates("agency_no")
    gold_tract = clean_geoid(d6.get("gold_tract_geoid"), 11)
    ns_zip = d6.get("ship_zip", d6.get("ns_zip"))
    ns_zip = clean_geoid(ns_zip, 5) if ns_zip is not None else pd.Series(None, index=d6.index)
    gold_zip = clean_geoid(d6.get("gold_zip"), 5)
    dominant, tracts_in_zip, tracts2020 = dominant_tract_by_zip(xwalk_path)
    t10 = tract2010_to_2020(rel_path)

    # Street-address geocodes (src/data/cycle3_geocode.py), if present: exact matches always,
    # inexact / tie matches only when the tract lies inside the agency's ZIP.
    geo = {}
    gpath = raw / "d6_geocoded.csv"
    if gpath.exists():
        g = read_csv(gpath).dropna(subset=["geocode_tract_geoid"])
        geo = dict(zip(g.agency_no, zip(g.geocode_tract_geoid, g.geocode_match)))

    rows = []
    for a, gt, nz, gz, cat in zip(d6.agency_no, gold_tract, ns_zip, gold_zip,
                                  d6.get("category", pd.Series(None, index=d6.index))):
        tract, method = None, None
        gt_geo, match = geo.get(a, (None, None))
        z_any = nz if nz in tracts_in_zip else gz if gz in tracts_in_zip else None
        if gt_geo in tracts2020 and (match == "Exact" or (z_any and gt_geo in tracts_in_zip[z_any])):
            rows.append((a, gt_geo, gt_geo[:5], "geocoded_address", cat))
            continue
        gold_method = "gold_tract"
        if gt is not None and gt not in tracts2020 and gt in t10:   # GOLD mixes 2010 and 2020 codes
            gt, gold_method = t10[gt], "gold_tract_2010_translated"
        gold_ok = gt in tracts2020
        if gold_ok and nz in tracts_in_zip and gt not in tracts_in_zip[nz]:
            tract, method = dominant[nz], "zip_override_gold"     # GOLD tract outside the ship-to ZIP
        elif gold_ok:
            tract, method = gt, gold_method
        else:
            z = nz if nz in dominant else gz if gz in dominant else None
            if z:
                tract, method = dominant[z], "zip_dominant_tract"
        county = tract[:5] if tract else county_from_agency_no(a)
        if not tract:
            method = "county_from_agency_no" if county else "unmapped"
        rows.append((a, tract, county, method, cat))

    # Ceres-era customers missing from D6 (closed before NetSuite): ZIP from the Ceres customer table
    d10b = raw / "d10b_ceres_customer_location.csv"
    if d10b.exists():
        c = read_csv(d10b)
        c = c[~c.agency_no.isin(set(d6.agency_no))]
        for a, z in zip(c.agency_no, clean_geoid(c.ceres_zip, 5)):
            gt_geo, match = geo.get(a, (None, None))
            if gt_geo in tracts2020 and (match == "Exact" or (z in tracts_in_zip and gt_geo in tracts_in_zip[z])):
                rows.append((a, gt_geo, gt_geo[:5], "geocoded_address", None))
            elif z in dominant:
                rows.append((a, dominant[z], dominant[z][:5], "ceres_zip_dominant_tract", None))
    return pd.DataFrame(rows, columns=["agency_no", "tract_geoid", "county_fips", "method", "category"])


def load_flags(raw):
    d7 = read_csv(raw / "d7_exclusions.csv")[["agency_no", "flag"]].drop_duplicates("agency_no")
    d7["flag"] = d7.flag.where(d7.flag != "hub_candidate", None)
    d7.loc[d7.agency_no.isin(HUBS), "flag"] = "hub"
    return d7.dropna(subset=["flag"]).set_index("agency_no")["flag"]


def prefix_flag(agency_no):
    if re.match(r"^FB[A-Z]{2}-", agency_no or ""):
        return "transfer_food_bank"
    return NONLOCAL_PREFIX.get((agency_no or "").split("-")[0]) if "-" in (agency_no or "") else None


# ---------------------------------------------------------------------------------------------
def add_periods(df):
    q = df.month.dt.to_period("Q")
    df["quarter"] = q.astype(str)                       # calendar quarter, e.g. 2025Q3
    df["quarter_start"] = q.dt.start_time
    fy_end = df.month.dt.year + (df.month.dt.month >= 7)  # FTB fiscal year runs Jul-Jun
    df["fiscal_year"] = "FY" + (fy_end - 1).astype(str).str[-2:] + "-" + fy_end.astype(str).str[-2:]
    df["fiscal_quarter"] = ((df.month.dt.month - 7) % 12) // 3 + 1
    return df


def build(raw, out, xwalk_path, rel_path):
    log = {"raw_dir": str(raw)}
    dist = load_distribution(raw, log)
    loc = load_locations(raw, xwalk_path, rel_path)
    flags = load_flags(raw)

    df = dist.merge(loc, on="agency_no", how="left")
    missing = df.method.isna()
    df.loc[missing, "county_fips"] = df.loc[missing, "agency_no"].map(county_from_agency_no)
    df.loc[missing, "method"] = df.loc[missing, "county_fips"].map(
        lambda c: "not_in_d6_county_from_agency_no" if c else "not_in_d6_unmapped")
    df["flag"] = df.agency_no.map(flags).fillna(df.agency_no.map(prefix_flag))
    df["local"] = ~df.flag.isin(NONLOCAL_FLAGS)
    df["pounds_local"] = df.pounds.where(df.local, 0.0)
    # Agency vs other institutions (schools, SFSP, food pharmacy, internal ...), from the customer category
    d10b = raw / "d10b_ceres_customer_location.csv"
    if d10b.exists():
        cb = read_csv(d10b)
        if "ceres_agency_category" in cb:
            cb = cb.dropna(subset=["ceres_agency_category"])
            ceres_cat = dict(zip(cb.agency_no, "Ceres: " + cb.ceres_agency_category.str.strip().str.upper()))
            df["category"] = df.category.fillna(df.agency_no.map(ceres_cat))
    df["customer_group"] = df.category.map(customer_group)
    is_agency = df.customer_group == "agency"
    df["pounds_agency"] = df.pounds.where(is_agency, 0.0)
    df["pounds_agency_local"] = df.pounds_local.where(is_agency, 0.0)
    df["pounds_other_institution"] = df.pounds.where(df.customer_group == "other_institution", 0.0)
    df["pounds_type_unknown"] = df.pounds.where(df.customer_group == "unknown", 0.0)
    df = add_periods(df)

    # INTERNAL QA list (gitignored, never publish/commit): how each customer was placed, so the
    # ones without a reliable tract can be fixed at the source. No pounds per agency in it.
    qa = (df.groupby("agency_no")
            .agg(method=("method", "first"), category=("category", "first"),
                 customer_group=("customer_group", "first"),
                 tract_geoid=("tract_geoid", "first"), county_fips=("county_fips", "first"),
                 first_month=("month", "min"), last_month=("month", "max"))
            .reset_index())
    qa["precision"] = qa.method.map(PRECISION).fillna("4 county or none")
    qa["active_fy2526"] = qa.agency_no.isin(df.loc[df.fiscal_year == "FY25-26", "agency_no"])
    out.mkdir(parents=True, exist_ok=True)
    qa.sort_values(["precision", "agency_no"]).to_csv(out / "cycle3_agency_location_qa.csv", index=False)

    # quarter completeness: how many months of data the quarter has
    months_per_q = df.groupby("quarter")["month"].nunique().rename("n_months")

    split = dict(pounds_agency=("pounds_agency", "sum"), pounds_agency_local=("pounds_agency_local", "sum"),
                 pounds_other_institution=("pounds_other_institution", "sum"),
                 pounds_type_unknown=("pounds_type_unknown", "sum"))

    # ---- tract x quarter (only what maps to a tract)
    t = df[df.tract_geoid.notna()]
    tract_q = (t.groupby(["tract_geoid", "county_fips", "quarter", "quarter_start", "fiscal_year", "fiscal_quarter"])
                 .agg(pounds_all=("pounds", "sum"), pounds_local=("pounds_local", "sum"), **split,
                      n_agencies=("agency_no", "nunique"))
                 .reset_index().join(months_per_q, on="quarter"))

    # ---- county x quarter (everything, incl. pounds known only at county level)
    df["county_fips"] = df.county_fips.fillna("unknown")
    df["tract_mapped"] = df.tract_geoid.notna()
    county_q = (df.assign(pounds_tract_mapped=df.pounds.where(df.tract_mapped, 0.0))
                  .groupby(["county_fips", "quarter", "quarter_start", "fiscal_year", "fiscal_quarter"])
                  .agg(pounds_all=("pounds", "sum"), pounds_local=("pounds_local", "sum"), **split,
                       pounds_all_tract_mapped=("pounds_tract_mapped", "sum"),
                       n_agencies=("agency_no", "nunique"))
                  .reset_index().join(months_per_q, on="quarter"))
    county_q.insert(1, "county_name", county_q.county_fips.map(FTB_COUNTIES).fillna("outside/unknown"))

    # ---- seasonal shares: share of each full calendar year's local pounds by quarter
    df["year"] = df.month.dt.year
    full_years = [y for y, n in df.groupby("year")["month"].nunique().items()
                  if n == 12 and y not in SEASONAL_SKIP_YEARS]
    s = df[df.year.isin(full_years) & df.county_fips.isin(FTB_COUNTIES)].copy()
    s["cal_quarter"] = s.month.dt.quarter
    by = pd.concat([s.assign(scope="FTB"), s.assign(scope=s.county_fips)])
    yq = by.groupby(["scope", "year", "cal_quarter"])["pounds_local"].sum()
    share = (yq / yq.groupby(level=["scope", "year"]).transform("sum")).rename("share").reset_index()
    seasonal = (share.groupby(["scope", "cal_quarter"])["share"]
                     .agg(share_mean="mean", share_min="min", share_max="max", n_years="count").reset_index())
    seasonal["years_used"] = ",".join(map(str, full_years))

    out.mkdir(parents=True, exist_ok=True)
    tract_q.to_parquet(out / "distribution_tract_quarter.parquet", index=False)
    county_q.to_parquet(out / "distribution_county_quarter.parquet", index=False)
    seasonal.to_parquet(out / "seasonal_quarter_shares.parquet", index=False)

    # ---- log: aggregates only (counts of agencies, never agency identifiers)
    fy = df[df.fiscal_year == "FY25-26"]
    fy_aug_jun = fy[fy.month >= "2025-08-01"]
    agencies = df.drop_duplicates("agency_no")
    log.update({
        "rows_in": int(len(dist)),
        "total_lbs": float(df.pounds.sum()),
        "mapping_agencies": agencies.method.value_counts().to_dict(),
        "fy2526_precision": (fy_aug_jun.assign(p=fy_aug_jun.method.map(PRECISION).fillna("4 county or none"))
                             .groupby("p").agg(agencies=("agency_no", "nunique"), lbs=("pounds", "sum"))
                             .round().to_dict(orient="index")),
        "mapping_lbs": df.groupby("method")["pounds"].sum().round().to_dict(),
        "lbs_not_tract_mapped": float(df.loc[~df.tract_mapped, "pounds"].sum()),
        "lbs_no_county": float(df.loc[df.county_fips == "unknown", "pounds"].sum()),
        "lbs_outside_ftb_counties": float(df.loc[~df.county_fips.isin(FTB_COUNTIES) & (df.county_fips != "unknown"), "pounds"].sum()),
        "fy2526_aug_jun_lbs_all": float(fy_aug_jun.pounds.sum()),
        "fy2526_aug_jun_lbs_local": float(fy_aug_jun.pounds_local.sum()),
        "fy2526_aug_jun_lbs_by_group": fy_aug_jun.groupby("customer_group")["pounds"].sum().round().to_dict(),
        "fy2526_aug_jun_agencies_by_group": fy_aug_jun.groupby("customer_group")["agency_no"].nunique().to_dict(),
        "fy2526_aug_jun_lbs_by_flag": fy_aug_jun.groupby(fy_aug_jun.flag.fillna("local"))["pounds"].sum().round().to_dict(),
        "fy2526_aug_jun_lbs_by_category": fy_aug_jun.groupby(fy_aug_jun.category.fillna("(not in D6)"))["pounds"].sum().round().to_dict(),
        "hubs_excluded": sorted(HUBS),
        "seasonal_years": full_years,
        "rows_out": {"distribution_tract_quarter": len(tract_q), "distribution_county_quarter": len(county_q),
                     "seasonal_quarter_shares": len(seasonal)},
    })
    (out / "cycle3_make_dataset_log.json").write_text(json.dumps(log, indent=2, default=str))
    return log


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw", required=True, type=Path, help="data/raw/<date> folder with the CSV extracts")
    p.add_argument("--out", type=Path, default=ROOT / "data" / "processed")
    p.add_argument("--xwalk", type=Path, default=ROOT / "data" / "external" / "census" / "tab20_zcta520_tract20_natl.txt")
    p.add_argument("--rel", type=Path, default=ROOT / "data" / "external" / "census" / "tab20_tract20_tract10_natl.txt")
    a = p.parse_args()
    log = build(a.raw, a.out, a.xwalk, a.rel)
    print(json.dumps({k: log[k] for k in ["months", "rows_out", "fy2526_precision", "mapping_agencies",
                                           "fy2526_aug_jun_lbs_all", "fy2526_aug_jun_lbs_local"]}, indent=2, default=str))


if __name__ == "__main__":
    main()

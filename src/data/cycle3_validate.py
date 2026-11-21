# -*- coding: utf-8 -*-
"""Checks on the cycle 3 distribution tables written by src/data/cycle3_make_dataset.py.

    python -m src.data.cycle3_validate --raw data/raw/2026-11-21

Prints one line per check (PASS / WARN / FAIL) and exits 1 if any check FAILs.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FTB_FY2526_TARGET_LBS = 80.0e6   # FTB's reported FY25-26 figure (definition to be confirmed with FTB)
TARGET_TOLERANCE = 0.05


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw", type=Path, help="raw folder (for the Ceres/NetSuite overlap check file)")
    p.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    a = p.parse_args()
    d = a.processed
    tract = pd.read_parquet(d / "distribution_tract_quarter.parquet")
    county = pd.read_parquet(d / "distribution_county_quarter.parquet")
    seas = pd.read_parquet(d / "seasonal_quarter_shares.parquet")
    log = json.loads((d / "cycle3_make_dataset_log.json").read_text())
    results = []

    def check(name, status, detail):
        results.append(status)
        print(f"[{status}] {name}: {detail}")

    # 1. unique keys
    for name, df, key in [("tract_quarter", tract, ["tract_geoid", "quarter"]),
                          ("county_quarter", county, ["county_fips", "quarter"]),
                          ("seasonal", seas, ["scope", "cal_quarter"])]:
        n = int(df.duplicated(key).sum())
        check(f"unique keys {name}", "PASS" if n == 0 else "FAIL", f"{n} duplicate {key}")
    bad = (~tract.tract_geoid.str.fullmatch(r"\d{11}")).sum() + (tract.tract_geoid.str[:5] != tract.county_fips).sum()
    check("tract GEOID 11-digit text, matches county", "PASS" if bad == 0 else "FAIL", f"{bad} bad rows")

    # 2. no future dates
    lcm = pd.Timestamp(log["last_complete_month"])
    late = (county.quarter_start > lcm).sum()
    check("no future dates", "PASS" if late == 0 else "FAIL",
          f"latest month {log['months'][1]} <= last complete month {lcm.date()}; "
          f"{log['dropped_future_rows']} future rows dropped at load")

    # 3. totals conserved: county table holds every pound loaded; tract table <= county table
    tot_c, tot_t = county.pounds_all.sum(), tract.pounds_all.sum()
    ok = abs(tot_c - log["total_lbs"]) < 1
    check("county table = all pounds loaded", "PASS" if ok else "FAIL", f"{tot_c:,.0f} vs {log['total_lbs']:,.0f}")
    check("share of pounds mapped to a tract", "WARN" if tot_t / tot_c < 0.9 else "PASS",
          f"{tot_t / tot_c:.1%} ({tot_c - tot_t:,.0f} lbs county-level only; "
          f"{log['lbs_no_county']:,.0f} lbs no county; {log['lbs_outside_ftb_counties']:,.0f} lbs outside FTB counties)")

    # 4. FY25-26 (Aug 2025 - Jun 2026) ~ 80.0M lbs. Compared on Agency-category customers: that matches
    #    FTB's figure; all customers (schools, internal, ...) are reported alongside. To confirm with FTB.
    fy = log["fy2526_aug_jun_lbs_all"]
    grp = log["fy2526_aug_jun_lbs_by_group"]
    ag = grp.get("agency", 0.0)
    dev = ag / FTB_FY2526_TARGET_LBS - 1
    check("FY25-26 Aug-Jun agency total vs FTB's 80.0M", "PASS" if abs(dev) <= TARGET_TOLERANCE else "FAIL",
          f"agency {ag / 1e6:.2f}M ({dev:+.1%}); all customers {fy / 1e6:.2f}M "
          f"(other institutions {grp.get('other_institution', 0) / 1e6:.2f}M, type unknown {grp.get('unknown', 0) / 1e6:.2f}M); "
          f"by category (M): " + ", ".join(f"{k} {v / 1e6:.1f}" for k, v in
                                          sorted(log["fy2526_aug_jun_lbs_by_category"].items(), key=lambda x: -x[1])[:6]))

    # 4b. agency + other institutions + unknown type = all, in both tables
    for name, df in [("tract", tract), ("county", county)]:
        gap = (df.pounds_agency + df.pounds_other_institution + df.pounds_type_unknown - df.pounds_all).abs().max()
        check(f"customer-group split adds up ({name})", "PASS" if gap < 1e-6 else "FAIL", f"max gap {gap:.6f} lbs")

    # 5. NetSuite vs Ceres overlap not double counted
    over = log["source_by_month_overlap"]
    check("each month from one source only", "PASS" if over == 0 else "FAIL",
          f"cutover {log['cutover']}; months with both sources: {over}; "
          f"dropped by cutover (lbs): {log['dropped_by_cutover_lbs']}; "
          f"duplicate rows dropped: {log['dropped_duplicate_rows']} ({log['dropped_duplicate_lbs']:,.0f} lbs)")
    if a.raw and (a.raw / "d5_d10_overlap_check.csv").exists():
        o = pd.read_csv(a.raw / "d5_d10_overlap_check.csv")
        o.columns = o.columns.str.lower()
        w = o[(o.day >= "2025-08-01") & (o.day <= "2025-08-08")].fillna(0)
        same = w.ns_lbs_same_agency_day.sum() / max(w.bc_lbs.sum(), 1)
        check("Aug 1-8 2025: Ceres shipments also in NetSuite", "PASS" if same > 0.8 else "WARN",
              f"Ceres {w.bc_lbs.sum():,.0f} lbs; NetSuite {w.ns_lbs.sum():,.0f} lbs, of which "
              f"{w.ns_lbs_same_agency_day.sum():,.0f} on the same agency-day ({same:.0%} of Ceres) -> NetSuite kept")

    # 6. share excluded as non-local
    by = log["fy2526_aug_jun_lbs_by_flag"]
    excl = sum(v for k, v in by.items() if k in ("disaster", "transfer_food_bank", "hub"))
    check("FY25-26 share excluded as non-local", "PASS",
          f"{excl / fy:.1%} excluded ({', '.join(f'{k} {v / 1e6:.2f}M' for k, v in by.items() if k != 'local')}); "
          f"hubs treated as non-local: {', '.join(log['hubs_excluded']) or 'none'}")

    # 7. seasonal shares sum to 1
    s = seas.groupby("scope").share_mean.sum()
    check("seasonal shares sum to 1", "PASS" if ((s - 1).abs() < 1e-6).all() else "FAIL",
          f"years used: {log['seasonal_years']}")

    print(f"\n{results.count('PASS')} pass, {results.count('WARN')} warn, {results.count('FAIL')} fail")
    sys.exit(1 if "FAIL" in results else 0)


if __name__ == "__main__":
    main()

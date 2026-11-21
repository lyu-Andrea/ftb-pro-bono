# -*- coding: utf-8 -*-
"""Geocode agency street addresses to 2020 census tracts with the public Census Geocoder.

    python -m src.data.cycle3_geocode --raw data/raw/2026-11-21 --addresses d6_agency_addresses.csv

Input: a CSV in the raw folder with agency_no, address, city, zip (organisations only, no individuals).
Only an anonymous row number, street, city, state and ZIP are sent to geocoding.geo.census.gov (batch
endpoint, at most 10,000 rows per call); agency numbers and names stay local.
Output: <raw>/d6_geocoded.csv with agency_no, geocode_tract_geoid, geocode_match. cycle3_make_dataset.py
uses it as the first location rule when it exists.
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
COLS = ["row_id", "input_address", "match", "match_type", "matched_address", "lon_lat",
        "tiger_line_id", "side", "state_fips", "county_fips", "tract", "block"]


def geocode(addr):
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.csv", Path(tmp) / "out.csv"
        addr[["row_id", "address", "city", "state", "zip"]].to_csv(src, index=False, header=False)
        subprocess.run(["curl", "-sS", "-f", "--max-time", "600", "-o", str(out),
                        "-F", f"addressFile=@{src}",
                        "--form-string", "benchmark=Public_AR_Current",
                        "--form-string", "vintage=Census2020_Current", URL], check=True)
        res = pd.read_csv(out, header=None, names=COLS, dtype=str)
    res["row_id"] = res.row_id.astype(int)
    ok = res.match.eq("Match")
    res["geocode_tract_geoid"] = (res.state_fips + res.county_fips + res.tract).where(ok)
    res["geocode_match"] = res.match_type.where(ok, res.match)
    return res[["row_id", "geocode_tract_geoid", "geocode_match"]]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw", required=True, type=Path, help="folder holding the address CSVs (kept local only)")
    p.add_argument("--out", type=Path, help="folder for d6_geocoded.csv (default: --raw)")
    p.add_argument("--addresses", required=True, nargs="+",
                   help="CSV(s) in the raw folder with agency_no,address,city,zip; the first file wins per agency")
    a = p.parse_args()
    frames = []
    for f in a.addresses:
        x = pd.read_csv(a.raw / f, dtype=str, encoding="utf-8-sig")
        x.columns = x.columns.str.lower()
        x = x.rename(columns={"ns_zip": "zip", "ship_zip": "zip", "ship_address": "address", "ship_city": "city"})
        frames.append(x[[c for c in ["agency_no", "address", "city", "zip", "is_person"] if c in x]])
    d = pd.concat(frames, ignore_index=True)
    if "is_person" in d:
        d = d[d.is_person.ne("T")]                      # never send individuals' addresses
    # some address fields hold "street\rcity": keep the street line only
    d["address"] = d.address.str.split(r"[\r\n]+").str[0].str.strip()
    d["agency_no"] = d.agency_no.str.strip().str.upper()
    d = d.dropna(subset=["agency_no", "address"]).drop_duplicates("agency_no").reset_index(drop=True)
    d["row_id"] = d.index + 1
    d["state"] = "FL"
    d["zip"] = d.zip.fillna("").str[:5]
    res = d[["row_id", "agency_no"]].merge(geocode(d), on="row_id", how="left").drop(columns="row_id")
    res.to_csv((a.out or a.raw) / "d6_geocoded.csv", index=False)
    n = res.geocode_tract_geoid.notna()
    print(f"sent {len(d)} addresses; matched {n.sum()} ({n.mean():.0%}); "
          f"match types: {res.geocode_match.value_counts(dropna=False).to_dict()}")


if __name__ == "__main__":
    main()

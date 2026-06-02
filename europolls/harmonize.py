"""Apply per-country party-name → partyfacts_id mapping.

Reads the long-format poll-party CSVs and the country mapping YAML, then
writes harmonized CSVs to data/interim/harmonized/{COUNTRY}.csv.

Usage:
    python -m europolls.harmonize IT
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "data" / "interim"
OUT_DIR = ROOT / "data" / "interim" / "harmonized"
MAP_DIR = ROOT / "config" / "party_mappings"


def load_mapping(country: str) -> dict:
    path = MAP_DIR / f"{country}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no mapping file at {path}")
    with path.open() as f:
        return yaml.safe_load(f)["mappings"]


def harmonize(country: str) -> None:
    mapping = load_mapping(country)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    in_files = sorted(IN_DIR.glob(f"{country}_*.csv"))
    if not in_files:
        raise FileNotFoundError(f"no input files for {country} in {IN_DIR}")

    n_in = 0
    n_drop = 0
    n_kept = 0
    n_unmapped = 0
    unmapped_shorts: dict[str, int] = {}

    out_path = OUT_DIR / f"{country}_long_harmonized.csv"
    writer = None
    f_out = out_path.open("w", newline="")
    try:
        for in_path in in_files:
            with in_path.open() as f_in:
                reader = csv.DictReader(f_in)
                if writer is None:
                    fieldnames = list(reader.fieldnames) + ["partyfacts_id", "is_dropped_meta"]
                    writer = csv.DictWriter(f_out, fieldnames=fieldnames)
                    writer.writeheader()
                for row in reader:
                    n_in += 1
                    short = row["party_short"]
                    m = mapping.get(short)
                    if m is None:
                        n_unmapped += 1
                        unmapped_shorts[short] = unmapped_shorts.get(short, 0) + 1
                        row["partyfacts_id"] = ""
                        row["is_dropped_meta"] = ""
                        writer.writerow(row)
                        n_kept += 1
                        continue
                    if m.get("drop"):
                        n_drop += 1
                        row["partyfacts_id"] = ""
                        row["is_dropped_meta"] = "1"
                        writer.writerow(row)
                        continue
                    pfid = m.get("partyfacts_id")
                    row["partyfacts_id"] = str(pfid) if pfid is not None else ""
                    row["is_dropped_meta"] = ""
                    if m.get("coalition"):
                        row["is_coalition"] = "True"
                    writer.writerow(row)
                    n_kept += 1
    finally:
        f_out.close()

    print(f"{country}: harmonized {n_in:,} input rows")
    print(f"  kept (written):    {n_kept:,}")
    print(f"  dropped meta:      {n_drop:,}")
    print(f"  unmapped shorts:   {n_unmapped:,}  (across {len(unmapped_shorts)} distinct shorts)")
    if unmapped_shorts:
        top_unmapped = sorted(unmapped_shorts.items(), key=lambda kv: -kv[1])[:15]
        print(f"  top unmapped:")
        for short, n in top_unmapped:
            print(f"    {short!r:<35} {n:>5}")
    print(f"  → {out_path.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("countries", nargs="+")
    args = ap.parse_args()
    for c in args.countries:
        harmonize(c)


if __name__ == "__main__":
    main()

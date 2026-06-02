"""Run the full Europolls pipeline end to end.

Steps (each idempotent):
  1. fetch  — pull article wikitext for every (country, cycle) in
             config/countries.yaml, saving to data/raw/{COUNTRY}/{CYCLE}/
  2. parse  — extract long-format poll-party rows to data/interim/
  3. pivot  — produce wide CSVs in data/processed/{COUNTRY}_polls_wide.csv
  4. concat — concatenate all long CSVs into data/processed/polls_long.csv
  5. harmonize — for every country with a config/party_mappings/{COUNTRY}.yaml,
                 produce a harmonized long CSV in data/interim/harmonized/

Usage:
    python scripts/build_all.py
    python scripts/build_all.py --skip-fetch    # parse/pivot existing wikitext
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from europolls import fetch as fetch_mod    # noqa: E402
from europolls import parse as parse_mod    # noqa: E402
from europolls import pivot_wide            # noqa: E402
from europolls import harmonize             # noqa: E402


def step_fetch() -> None:
    for country, cfg in parse_mod.COUNTRY_CONFIG.items():
        cycles = list(cfg["cycles"].keys())
        try:
            fetch_mod.fetch_country(country, cycles)
        except Exception as e:
            print(f"  ! {country} fetch failed: {e}")


def step_parse() -> None:
    for country in parse_mod.COUNTRY_CONFIG:
        try:
            parse_mod.run_country(country)
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ! {country} parse failed: {e}")


def step_pivot() -> None:
    for country in parse_mod.COUNTRY_CONFIG:
        try:
            pivot_wide.pivot_one(country)
        except Exception as e:
            print(f"  ! {country} pivot failed: {e}")


def step_concat_long() -> None:
    interim = ROOT / "data" / "interim"
    files = sorted(p for p in interim.glob("*.csv") if p.is_file())
    if not files:
        print("  no interim CSVs to concatenate")
        return
    dfs = [pd.read_csv(p, low_memory=False) for p in files]
    long = pd.concat(dfs, ignore_index=True)
    if "_table_idx" in long.columns:
        long = long.drop(columns=["_table_idx"])
    out = ROOT / "data" / "processed" / "polls_long.csv"
    long.to_csv(out, index=False)
    print(f"  wrote {len(long):,} rows × {len(long.columns)} cols -> {out.relative_to(ROOT)}")


def step_harmonize() -> None:
    map_dir = ROOT / "config" / "party_mappings"
    for mapping in sorted(map_dir.glob("*.yaml")):
        country = mapping.stem
        try:
            harmonize.harmonize(country)
        except Exception as e:
            print(f"  ! {country} harmonize failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true",
                    help="skip Wikipedia fetch step (use existing data/raw/ snapshots)")
    ap.add_argument("--only", choices=["fetch", "parse", "pivot", "concat", "harmonize"],
                    help="run only one step")
    args = ap.parse_args()

    steps = [
        ("fetch", step_fetch),
        ("parse", step_parse),
        ("pivot", step_pivot),
        ("concat", step_concat_long),
        ("harmonize", step_harmonize),
    ]
    for name, fn in steps:
        if args.only and args.only != name:
            continue
        if name == "fetch" and args.skip_fetch:
            continue
        print(f"\n=== {name} ===")
        t0 = time.time()
        fn()
        print(f"  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()

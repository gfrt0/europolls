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

    raw_count = len(long)
    long = _dedup_polls(long)
    dropped = raw_count - len(long)

    out = ROOT / "data" / "processed" / "polls_long.csv"
    long.to_csv(out, index=False)
    print(f"  wrote {len(long):,} rows × {len(long.columns)} cols "
          f"(deduped {dropped:,} of {raw_count:,}) -> {out.relative_to(ROOT)}")


def _dedup_polls(long: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate poll-party rows.

    A duplicate is two rows sharing (country, polldate_mid, pollster,
    party_short). Two sources:
      1. Wikipedia editors copy each prior election's result into the next
         cycle's polling article as a baseline (cross-cycle dup).
      2. Some polling articles list the same poll in multiple wikitables —
         'Voting intention', 'Bloc support', 'Latest polls' (within-cycle dup).

    Keep one row per key, preferring the cycle whose election year is
    closest to the poll's mid-date year — that's the 'canonical' article
    for the poll. Ties broken by larger sample_size (more reliable), then
    by higher wiki_revid (more recent edit). Undated rows are passed
    through untouched (no key to dedup on).
    """
    if long.empty:
        return long

    # Split: undated rows can't be deduped on date; keep them as-is.
    has_date = long["polldate_mid"].notna()
    undated = long[~has_date]
    dated = long[has_date].copy()

    # Compute the 'cycle year' for each row from its cycle string. Use the
    # cycle's leading 4-digit year ('2024', '2021-Nov', 'current' is special).
    def _cycle_year(c: object) -> int:
        s = str(c)
        if s[:4].isdigit():
            return int(s[:4])
        # 'current' or other non-year-prefixed cycle: place far in the future
        # so it loses to any concrete cycle in the proximity tiebreaker.
        return 9999

    dated["_cycle_year"] = dated["cycle"].map(_cycle_year)
    poll_year = pd.to_datetime(dated["polldate_mid"], errors="coerce").dt.year
    dated["_year_dist"] = (dated["_cycle_year"] - poll_year).abs()
    # Tiebreakers: prefer larger sample, newer revid.
    dated["_neg_sample"] = -pd.to_numeric(dated["sample_size"], errors="coerce").fillna(0)
    dated["_neg_revid"] = -pd.to_numeric(dated["wiki_revid"], errors="coerce").fillna(0)
    dated = dated.sort_values(
        ["_year_dist", "_neg_sample", "_neg_revid"], kind="stable",
    )
    key = ["country", "polldate_mid", "pollster", "party_short"]
    dated = dated.drop_duplicates(subset=key, keep="first")
    dated = dated.drop(columns=["_cycle_year", "_year_dist", "_neg_sample", "_neg_revid"])

    return pd.concat([dated, undated], ignore_index=True)


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

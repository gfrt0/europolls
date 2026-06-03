"""Pivot any country's long poll-party CSVs into a wide poll-level table.

Usage:
    python -m europolls.pivot_wide IT DE ES UK FR
    python -m europolls.pivot_wide all
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "data" / "interim"
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

KEY_COLS = [
    "cycle", "polldate_start", "polldate_end", "polldate_mid",
    "pollster", "sample_size", "source_url",
    "wiki_article", "wiki_revid", "wiki_url",
]


def pivot_one(country: str) -> None:
    frames = []
    for path in sorted(IN_DIR.glob(f"{country}_*.csv")):
        frames.append(pd.read_csv(path, low_memory=False,
                                  keep_default_na=False, na_values=[""]))
    if not frames:
        print(f"  {country}: no input files in {IN_DIR}")
        return
    long_df = pd.concat(frames, ignore_index=True)
    long_df["is_election_result"] = (
        long_df["pollster"].astype(str).str.lower().isin({"election results", "general election", "election"})
    )

    for col in KEY_COLS:
        if col in long_df.columns:
            long_df[col] = long_df[col].astype(object).where(long_df[col].notna(), "")

    party_df = long_df[~long_df["is_coalition"].fillna(False)]
    coal_df = long_df[long_df["is_coalition"].fillna(False)]

    def _pivot(sub: pd.DataFrame) -> pd.DataFrame | None:
        if sub.empty:
            return None
        grouped = (
            sub.groupby(KEY_COLS + ["is_election_result", "party_short"], observed=True, dropna=False)
               ["vote_share"].first()
        )
        return grouped.unstack("party_short").reset_index()

    wide_parties = _pivot(party_df)
    wide_coal = _pivot(coal_df)

    if wide_coal is not None:
        coal_cols = [c for c in wide_coal.columns if c not in KEY_COLS + ["is_election_result"]]
        wide_coal = wide_coal.rename(columns={c: f"coal_{c}" for c in coal_cols})
        wide = wide_parties.merge(wide_coal, on=KEY_COLS + ["is_election_result"], how="outer")
    else:
        wide = wide_parties

    # Drop any column whose name ended up as NaN (rare: party_short was missing).
    wide = wide.loc[:, [c for c in wide.columns if pd.notna(c)]]
    all_data_cols = [c for c in wide.columns if c not in set(KEY_COLS + ["is_election_result"])]
    party_cols = [c for c in all_data_cols if not str(c).startswith("coal_")]
    coal_cols = [c for c in all_data_cols if str(c).startswith("coal_")]
    party_counts = wide[party_cols].notna().sum().sort_values(ascending=False) if party_cols else pd.Series(dtype=int)
    coal_counts = wide[coal_cols].notna().sum().sort_values(ascending=False) if coal_cols else pd.Series(dtype=int)
    ordered = list(party_counts.index) + list(coal_counts.index)
    wide = wide[KEY_COLS + ["is_election_result"] + ordered]

    wide["polldate_mid_dt"] = pd.to_datetime(wide["polldate_mid"], errors="coerce")
    wide = wide.sort_values(["polldate_mid_dt", "cycle", "pollster"], na_position="last").drop(columns=["polldate_mid_dt"])

    out_path = OUT_DIR / f"{country}_polls_wide.csv"
    wide.to_csv(out_path, index=False)
    n_parties = len(party_cols)
    print(f"  {country}: {len(wide):>6,} polls × {n_parties:>3} party cols  → {out_path.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("countries", nargs="+")
    args = ap.parse_args()
    if args.countries == ["all"]:
        # Country prefix = everything before the last underscore (so FR_LEG_2024
        # stays under "FR_LEG", not collapsed to "FR").
        countries = sorted({"_".join(p.stem.split("_")[:-1]) for p in IN_DIR.glob("*.csv")})
    else:
        countries = args.countries
    for c in countries:
        pivot_one(c)


if __name__ == "__main__":
    main()

"""Export the long polls + party colors into per-country JSON files for the web/ page.

Reads:
  data/processed/polls_long.csv
  config/party_colors.yaml

Writes:
  web/countries.json           — index: [{code, n_polls, span_start, span_end, top_parties}]
  web/polls_{COUNTRY}.json     — array of poll-party objects, trimmed
  web/colors.json              — country → {party_short: hex}
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
LONG_CSV = ROOT / "data" / "processed" / "polls_long.csv"
COLORS_YAML = ROOT / "config" / "party_colors.yaml"
WEB = ROOT / "web"
WEB.mkdir(exist_ok=True)


def main() -> None:
    if not LONG_CSV.exists():
        raise SystemExit(f"missing {LONG_CSV.relative_to(ROOT)}; run scripts/build_all.py first")
    df = pd.read_csv(LONG_CSV, low_memory=False)
    print(f"loaded {len(df):,} long rows, {df['country'].nunique()} countries")

    # Trim to web-relevant columns + drop dateless rows (the page is time-axis-keyed).
    df = df.dropna(subset=["polldate_mid"])
    df = df[df["vote_share"].notna()]
    df["sample_size"] = pd.to_numeric(df["sample_size"], errors="coerce").astype("Int64")

    colors_raw = yaml.safe_load(COLORS_YAML.read_text())

    countries_summary = []
    for country, sub in df.groupby("country"):
        # Compute summary: n polls, date span, top 8 parties by observation count
        n_polls = len(sub.drop_duplicates(["polldate_mid", "pollster"]))
        span_start = sub["polldate_mid"].min()
        span_end = sub["polldate_mid"].max()
        parties = (sub[~sub["is_coalition"].fillna(False)]
                   .groupby("party_short").size()
                   .sort_values(ascending=False))
        top_parties = parties.head(10).index.tolist()

        countries_summary.append({
            "code": country,
            "n_polls": int(n_polls),
            "n_party_obs": int(len(sub)),
            "span_start": span_start,
            "span_end": span_end,
            "top_parties": top_parties,
        })

        # Per-country file: compact column names to keep the JSON small
        # (browser-loaded, no gzip guarantee). Drop source_url/wiki_url to
        # keep payload tight — users can hit the CSV release for full data.
        keep = sub[[
            "polldate_mid", "pollster", "sample_size",
            "party_short", "is_coalition", "vote_share",
        ]].copy()
        keep["polldate_mid"] = pd.to_datetime(keep["polldate_mid"]).dt.strftime("%Y-%m-%d")
        keep["is_coalition"] = keep["is_coalition"].fillna(False).astype(bool)
        keep["sample_size"] = keep["sample_size"].astype(object).where(keep["sample_size"].notna(), None)
        # Rename to single-char keys.
        compact = keep.rename(columns={
            "polldate_mid": "d",
            "pollster":     "p",
            "sample_size":  "n",
            "party_short":  "k",
            "is_coalition": "c",
            "vote_share":   "v",
        })
        out_path = WEB / f"polls_{country}.json"
        compact.to_json(out_path, orient="records", date_format=None, indent=None)
        size_kb = out_path.stat().st_size / 1024
        print(f"  {country:<8}  {len(compact):>6,} party-poll rows  → polls_{country}.json ({size_kb:.0f} KB)")

    # Index + colors.
    countries_summary.sort(key=lambda r: -r["n_polls"])
    (WEB / "countries.json").write_text(json.dumps(countries_summary, indent=2))
    (WEB / "colors.json").write_text(json.dumps(colors_raw, indent=2))
    print(f"\nwrote countries.json ({len(countries_summary)} entries)")
    print(f"wrote colors.json")


if __name__ == "__main__":
    main()

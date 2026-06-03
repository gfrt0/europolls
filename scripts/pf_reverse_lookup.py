"""Reverse-lookup Party Facts for each unmapped (country, party_short).

For every poll-row party that lacks a partyfacts_id in
data/processed/polls_long.csv, search PF's core + external party tables
restricted to the same country and rank candidate matches by fuzzy
string similarity against name_short / name / name_english / name_other.

Output: data/interim/pf_reverse_lookup.csv with one row per
(country, party_short, candidate) for the top-K candidates per pair,
sorted by score. Hand-review the high-confidence rows, then patch them
into the per-country YAMLs.

Usage:
    python scripts/pf_reverse_lookup.py [--pf-dir <path>] [--top 5] \
        [--min-rows 50] [--out <path>]

Default PF source is ../italgov/data/raw/partyfacts/. Skips pairs whose
poll-row count is below --min-rows (long-tail noise).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PF_DIR = ROOT.parent / "italgov" / "data" / "raw" / "partyfacts"
DEFAULT_OUT = ROOT / "data" / "interim" / "audit" / "pf_reverse_lookup.csv"
DEFAULT_LONG = ROOT / "data" / "processed" / "polls_long.csv"

ISO2_TO_ISO3 = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "CH": "CHE", "CY": "CYP",
    "CZ": "CZE", "DE": "DEU", "DK": "DNK", "EE": "EST", "ES": "ESP",
    "FI": "FIN", "FR": "FRA", "FR_LEG": "FRA", "GR": "GRC", "HR": "HRV",
    "HU": "HUN", "IE": "IRL", "IS": "ISL", "IT": "ITA", "LT": "LTU",
    "LU": "LUX", "LV": "LVA", "MT": "MLT", "NL": "NLD", "NO": "NOR",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "SE": "SWE", "SI": "SVN",
    "SK": "SVK", "UK": "GBR",
}

NAME_COLS_CORE = ["name_short", "name", "name_english", "name_other"]
NAME_COLS_EXT = ["name_short", "name", "name_english"]


def build_candidate_pool(core: pd.DataFrame, ext: pd.DataFrame, iso3: str) -> pd.DataFrame:
    """Return one row per (partyfacts_id, candidate_string, candidate_source).

    Excludes PF's technical placeholder IDs (``technical`` non-null in
    core-parties — these are the country-specific 'alliance' / '1-perc' /
    'indep' / 'unknown' buckets that external datasets bin loose entries
    to). Matching against those produces confident-looking false
    positives.
    """
    placeholder_ids = set(core.loc[core["technical"].notna(), "partyfacts_id"].astype(int))
    rows = []
    for _, r in core[(core["country"] == iso3) & core["technical"].isna()].iterrows():
        for col in NAME_COLS_CORE:
            v = r.get(col)
            if pd.isna(v) or not str(v).strip():
                continue
            rows.append({
                "partyfacts_id": int(r["partyfacts_id"]),
                "candidate": str(v).strip(),
                "candidate_field": f"core:{col}",
                "year_first": r.get("year_first"),
                "year_last": r.get("year_last"),
            })
    for _, r in ext[ext["country"] == iso3].iterrows():
        if pd.isna(r["partyfacts_id"]):
            continue
        if int(r["partyfacts_id"]) in placeholder_ids:
            continue
        for col in NAME_COLS_EXT:
            v = r.get(col)
            if pd.isna(v) or not str(v).strip():
                continue
            rows.append({
                "partyfacts_id": int(r["partyfacts_id"]),
                "candidate": str(v).strip(),
                "candidate_field": f"ext:{r['dataset_key']}:{col}",
                "year_first": pd.NA,
                "year_last": pd.NA,
            })
    return pd.DataFrame(rows)


def score(query: str, candidate: str) -> float:
    """Length-aware fuzzy score.

    Refuses to credit a match when one side is a very short abbreviation
    (≤2 chars) unless both are short and exactly equal — otherwise PF's
    one-letter ``name_short`` entries (``S``, ``P``, ``F``) flood the
    top-K via partial_ratio's substring lenience.

    For acronym-like queries (≤6 chars no spaces), the relevant signals
    are exact match against name_short, initials of name/name_english.
    For longer queries, ratio + token_set_ratio.
    """
    q, c = query.strip().lower(), candidate.strip().lower()
    if not q or not c:
        return 0.0
    if len(q) <= 2 or len(c) <= 2:
        return 100.0 if q == c else 0.0
    # If the candidate is a multi-word phrase and the query is an
    # acronym, build the candidate's initials and reward an exact match.
    acronym_bonus = 0.0
    if " " in c and len(q) <= 6 and " " not in q:
        initials = "".join(w[0] for w in c.split() if w)
        if q == initials:
            acronym_bonus = 95.0
    return max(
        fuzz.ratio(q, c),
        fuzz.token_set_ratio(q, c),
        acronym_bonus,
    )


def topk_for_pair(query: str, pool: pd.DataFrame, k: int) -> list[dict]:
    """Top-K (partyfacts_id, candidate, score) for a query against the country's pool.

    Aggregates to one row per partyfacts_id (keeps the best-matching
    candidate string).
    """
    if pool.empty:
        return []
    pool = pool.copy()
    pool["score"] = pool["candidate"].apply(lambda c: score(query, c))
    pool = pool.sort_values("score", ascending=False)
    # Best candidate per pf_id
    best = pool.drop_duplicates(subset=["partyfacts_id"], keep="first")
    return best.head(k).to_dict("records")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pf-dir", type=Path, default=DEFAULT_PF_DIR)
    ap.add_argument("--long", type=Path, default=DEFAULT_LONG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--min-rows", type=int, default=20,
                    help="skip (country, party_short) pairs with fewer poll rows")
    args = ap.parse_args()

    print(f"loading PF from {args.pf_dir}")
    core = pd.read_csv(args.pf_dir / "core-parties.csv", low_memory=False)
    ext = pd.read_csv(args.pf_dir / "external-parties.csv", low_memory=False)
    print(f"  core: {len(core):,} rows; external: {len(ext):,} rows")

    print(f"loading {args.long}")
    d = pd.read_csv(args.long, low_memory=False)
    real = d[d["is_dropped_meta"].fillna(0).astype(int) == 0]
    miss = real[real["partyfacts_id"].isna() & real["party_short"].notna()]
    counts = miss.groupby(["country", "party_short"]).size().sort_values(ascending=False)
    counts = counts[counts >= args.min_rows]
    print(f"  unmapped pairs ≥{args.min_rows} rows: {len(counts):,}")

    # Pool per country (cached)
    pools: dict[str, pd.DataFrame] = {}

    out_rows = []
    for (country, short), n_rows in counts.items():
        iso3 = ISO2_TO_ISO3.get(country)
        if not iso3:
            continue
        if country not in pools:
            pools[country] = build_candidate_pool(core, ext, iso3)
        pool = pools[country]
        if pool.empty:
            continue
        top = topk_for_pair(short, pool, args.top)
        for rank, t in enumerate(top, 1):
            out_rows.append({
                "country": country,
                "party_short": short,
                "n_poll_rows": int(n_rows),
                "rank": rank,
                "score": float(t["score"]),
                "partyfacts_id": int(t["partyfacts_id"]),
                "candidate": t["candidate"],
                "candidate_field": t["candidate_field"],
                "year_first": t.get("year_first"),
                "year_last": t.get("year_last"),
            })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"  wrote {len(out_rows):,} candidate rows → {args.out}")


if __name__ == "__main__":
    main()

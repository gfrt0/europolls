"""For each (country, party_short) still in gemini_no_match, surface
all occurrences of the party_short in the country's raw wikitext with
~120 chars of surrounding context. Editors often mention the party in
running prose ("The newly-formed XYZ party (PS) ...") even when the
table column header isn't a clean wikilink.

Output: ``data/interim/audit/unmapped_context.md`` — one section per
pair, top occurrences with context excerpts and source-URL samples.

Usage:
    python scripts/grep_unmapped_context.py [--min-rows 10] [--top 5]
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
DEFAULT_LONG = ROOT / "data" / "processed" / "polls_long.csv"
DEFAULT_OUT = ROOT / "data" / "interim" / "audit" / "unmapped_context.md"

CONTEXT_CHARS = 120


def find_contexts(country: str, party_short: str) -> list[tuple[str, str, str]]:
    """Return [(cycle, kind, snippet)] for occurrences of party_short.

    kind ∈ {'header_link', 'table_cell', 'prose'} to give the reader a
    quick triage signal.
    """
    out: list[tuple[str, str, str]] = []
    country_dir = RAW_DIR / country
    if not country_dir.exists():
        return out
    # Word-boundary search; tolerate exact match (party shortcodes vary
    # in punctuation, so we escape and require non-alphanumeric edges).
    escaped = re.escape(party_short)
    pat = re.compile(rf"(?:^|[^A-Za-z0-9_])({escaped})(?=[^A-Za-z0-9_]|$)")
    for wikitext in sorted(country_dir.glob("*/article.wikitext")):
        cycle = wikitext.parent.name
        try:
            text = wikitext.read_text(encoding="utf-8")
        except OSError:
            continue
        seen_snippets: set[str] = set()
        for m in pat.finditer(text):
            start = max(0, m.start() - CONTEXT_CHARS)
            end = min(len(text), m.end() + CONTEXT_CHARS)
            snippet = text[start:end].replace("\n", " ⏎ ").strip()
            # Normalize duplicate identical contexts (same snippet across
            # repeated table rows) into one.
            key = snippet[:160]
            if key in seen_snippets:
                continue
            seen_snippets.add(key)
            kind = "prose"
            if "[[" in text[m.end():m.end()+10] or "|" + party_short + "]]" in text[start:end]:
                kind = "header_link"
            elif text[max(0, m.start()-3):m.start()].lstrip().startswith("|"):
                kind = "table_cell"
            out.append((cycle, kind, snippet))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", type=Path, default=DEFAULT_LONG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-rows", type=int, default=10)
    ap.add_argument("--top", type=int, default=4,
                    help="max context snippets per pair")
    args = ap.parse_args()

    print(f"loading {args.long}")
    d = pd.read_csv(args.long, low_memory=False,
                    keep_default_na=False, na_values=[""])
    real = d[d["is_dropped_meta"].fillna(0).astype(int) == 0]
    miss = real[
        real["partyfacts_id"].isna()
        & real["party_short"].notna()
        & (real["partyfacts_id_source"] == "gemini_no_match")
    ]
    counts = (
        miss.groupby(["country", "party_short"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values("n", ascending=False)
    )
    counts = counts[counts["n"] >= args.min_rows]
    print(f"  pairs ≥{args.min_rows} rows: {len(counts):,}")

    # Sample 2 source_urls per pair (most recent polls)
    miss_sorted = miss.sort_values("polldate_mid", ascending=False)
    sample_urls = (
        miss_sorted.groupby(["country", "party_short"])["source_url"]
        .apply(lambda s: [u for u in s.dropna().unique()[:2] if str(u).startswith("http")])
        .to_dict()
    )
    sample_pollsters = (
        miss_sorted.groupby(["country", "party_short"])["pollster"]
        .apply(lambda s: list(s.dropna().unique()[:3]))
        .to_dict()
    )
    sample_dates = (
        miss_sorted.groupby(["country", "party_short"])["polldate_mid"]
        .apply(lambda s: (str(s.min()), str(s.max())))
        .to_dict()
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        f.write("# Unmapped party_short — wikitext context excerpts\n\n")
        f.write(f"Source: gemini_no_match rows in polls_long.csv (≥{args.min_rows} rows / pair).\n\n")
        for _, r in counts.iterrows():
            c, s, n = r["country"], r["party_short"], int(r["n"])
            f.write(f"## {c} `{s}` — {n} rows\n\n")
            dates = sample_dates.get((c, s), ("?", "?"))
            polls = sample_pollsters.get((c, s), [])
            urls = sample_urls.get((c, s), [])
            f.write(f"- date range: {dates[0]} → {dates[1]}\n")
            f.write(f"- pollsters: {', '.join(polls)}\n")
            for u in urls:
                f.write(f"- source url: {u}\n")
            ctxs = find_contexts(c, s)[:args.top]
            if not ctxs:
                f.write("\n*(no occurrences in raw wikitext)*\n\n")
                continue
            f.write("\n```\n")
            for cycle, kind, snip in ctxs:
                f.write(f"[{cycle}/{kind}] …{snip}…\n\n")
            f.write("```\n\n")

    print(f"  wrote → {args.out}")


if __name__ == "__main__":
    main()

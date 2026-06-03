"""Disambiguate ambiguous party_short labels via Wikipedia table headers.

For each unmapped ``(country, party_short)`` in polls_long.csv, grep
``data/raw/{COUNTRY}/{CYCLE}/article.wikitext`` for occurrences of the
pattern ``[[ArticleTitle|party_short]]``. The ``ArticleTitle`` is the
actual Wikipedia article for the party — usually the canonical English
name. Cross-reference each title against Party Facts'
``core-parties.csv`` ``wikipedia`` column (which stores
``https://en.wikipedia.org/wiki/Article_Title`` URLs) for a direct
``partyfacts_id`` hit.

Output: ``data/interim/wikilink_disambiguation.csv``, one row per
``(country, party_short, candidate_article_title, partyfacts_id_hit)``.

Usage:
    python scripts/disambiguate_via_wikilinks.py [--min-rows 20] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import re
import urllib.parse
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PF_DIR = ROOT.parent / "italgov" / "data" / "raw" / "partyfacts"
DEFAULT_OUT = ROOT / "data" / "interim" / "audit" / "wikilink_disambiguation.csv"
DEFAULT_LONG = ROOT / "data" / "processed" / "polls_long.csv"
RAW_DIR = ROOT / "data" / "raw"


def wikilink_pattern(party_short: str) -> re.Pattern:
    """Match ``[[Article Title|party_short]]`` with optional formatting noise.

    Allows the short to appear after the pipe, possibly with surrounding
    whitespace, with the article-title-side being any non-pipe / non-]
    text. Escapes the short for regex safety.
    """
    short_re = re.escape(party_short)
    return re.compile(rf"\[\[([^\[\]|]+?)\|\s*{short_re}\s*\]\]")


def title_to_wiki_url(title: str) -> str:
    """Convert ``Brothers of Italy`` → ``https://en.wikipedia.org/wiki/Brothers_of_Italy``."""
    slug = urllib.parse.quote(title.replace(" ", "_"))
    return f"https://en.wikipedia.org/wiki/{slug}"


def build_pf_wikipedia_index(core: pd.DataFrame) -> dict[str, list[dict]]:
    """``{wiki_url_lower: [{pf_id, country, name, year_first, year_last}, ...]}``."""
    idx: dict[str, list[dict]] = {}
    for _, r in core[core["wikipedia"].notna()].iterrows():
        url = str(r["wikipedia"]).strip().lower()
        idx.setdefault(url, []).append({
            "pf_id": int(r["partyfacts_id"]),
            "country": r["country"],
            "name": r["name"],
            "name_english": r["name_english"],
            "year_first": r["year_first"],
            "year_last": r["year_last"],
        })
    return idx


def grep_wikilinks(country: str, party_short: str) -> Counter:
    """Return ``Counter({article_title: occurrences})`` for the country's wikitext."""
    counts: Counter = Counter()
    country_dir = RAW_DIR / country
    if not country_dir.exists():
        return counts
    pat = wikilink_pattern(party_short)
    for wikitext in country_dir.glob("*/article.wikitext"):
        try:
            text = wikitext.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in pat.finditer(text):
            title = m.group(1).strip()
            # Normalize stray markup like {{nowrap|X}} or template wrappers.
            title = re.sub(r"^\{\{[^|}]+\|", "", title).rstrip("}")
            counts[title] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pf-dir", type=Path, default=DEFAULT_PF_DIR)
    ap.add_argument("--long", type=Path, default=DEFAULT_LONG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-rows", type=int, default=20)
    args = ap.parse_args()

    print(f"loading PF core from {args.pf_dir}")
    core = pd.read_csv(args.pf_dir / "core-parties.csv", low_memory=False)
    pf_idx = build_pf_wikipedia_index(core)
    print(f"  PF entries with a Wikipedia URL: {len(pf_idx):,} distinct URLs")

    print(f"loading {args.long}")
    d = pd.read_csv(args.long, low_memory=False)
    real = d[d["is_dropped_meta"].fillna(0).astype(int) == 0]
    miss = real[real["partyfacts_id"].isna() & real["party_short"].notna()]
    counts = miss.groupby(["country", "party_short"]).size().sort_values(ascending=False)
    counts = counts[counts >= args.min_rows]
    print(f"  unmapped pairs ≥{args.min_rows} rows: {len(counts):,}")

    out_rows: list[dict] = []
    for (country, short), n_rows in counts.items():
        found = grep_wikilinks(country, short)
        if not found:
            out_rows.append({
                "country": country, "party_short": short, "n_poll_rows": int(n_rows),
                "wiki_article": "", "wiki_occurrences": 0,
                "pf_wikipedia_hit_id": "", "pf_name": "",
                "pf_year_first": "", "pf_year_last": "",
                "pf_country": "",
            })
            continue
        for title, occ in found.most_common(5):
            url = title_to_wiki_url(title).lower()
            hits = pf_idx.get(url, [])
            if hits:
                for h in hits:
                    out_rows.append({
                        "country": country, "party_short": short,
                        "n_poll_rows": int(n_rows),
                        "wiki_article": title, "wiki_occurrences": occ,
                        "pf_wikipedia_hit_id": h["pf_id"],
                        "pf_name": h["name_english"] or h["name"],
                        "pf_year_first": h["year_first"],
                        "pf_year_last": h["year_last"],
                        "pf_country": h["country"],
                    })
            else:
                out_rows.append({
                    "country": country, "party_short": short,
                    "n_poll_rows": int(n_rows),
                    "wiki_article": title, "wiki_occurrences": occ,
                    "pf_wikipedia_hit_id": "", "pf_name": "",
                    "pf_year_first": "", "pf_year_last": "",
                    "pf_country": "",
                })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"  wrote {len(out_rows):,} rows → {args.out}")


if __name__ == "__main__":
    main()

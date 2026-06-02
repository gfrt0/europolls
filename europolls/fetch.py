"""Fetch Wikipedia 'Opinion polling for ...' articles for a country.

Usage:
    python -m europolls.fetch IT 2006 2008 2013 2018 2022 current
    python -m europolls.fetch DE 2009 2013 2017 2021 current
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = "italgov-research/0.1 (giuseppe.forte@gmail.com)"

# Per-country adjective used in the EN Wikipedia article title.
# 'Opinion polling for the next {ADJ} general election'
# 'Opinion polling for the {YEAR} {ADJ} general election'
COUNTRY_ADJ = {
    "IT": "Italian",
    "DE": "German federal",        # 'German federal election'
    "FR": "French presidential",   # semi-presidential — legislative is separate; presidential is the workhorse
    "FR_LEG": "French legislative",
    "ES": "Spanish general",
    "UK": "United Kingdom general",
    "PT": "Portuguese legislative",
    "NL": "Dutch general",
    "BE": "Belgian federal",
    "SE": "Swedish general",
    "DK": "Danish general",
    "FI": "Finnish parliamentary",
    "GR": "Greek legislative",
    "IE": "Irish general",
    "AT": "Austrian legislative",
    "LU": "Luxembourgian general",
    "CY": "Cypriot legislative",
    "MT": "Maltese general",
}


def _title_candidates(country: str, cycle: str) -> list[str]:
    adj = COUNTRY_ADJ.get(country, country)
    if cycle == "current":
        return [f"Opinion polling for the next {adj} election"]
    return [
        f"Opinion polling for the {cycle} {adj} election",
        # Some older articles use a slightly different phrasing.
        f"Opinion polling for the {cycle} {adj} elections",
    ]


def _fetch_page(title: str, lang: str = "en") -> dict | None:
    api = (
        f"https://{lang}.wikipedia.org/w/api.php?action=parse&page="
        + urllib.parse.quote(title)
        + "&prop=wikitext|revid&format=json&formatversion=2&redirects=true"
    )
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if "error" in data:
        return None
    return data["parse"]


def fetch_country(country: str, cycles: list[str]) -> None:
    out_root = ROOT / "data" / "raw" / country
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"=== {country} ===")
    for cycle in cycles:
        cycle_dir = out_root / cycle
        cycle_dir.mkdir(parents=True, exist_ok=True)
        parse = None
        used_title = None
        for title in _title_candidates(country, cycle):
            try:
                parse = _fetch_page(title)
            except Exception as e:
                print(f"  ! {cycle:<10} fetch failed for {title!r}: {e}")
                parse = None
            if parse is not None:
                used_title = title
                break
        if parse is None:
            print(f"  ✗ {cycle:<10} no page found")
            continue
        wikitext = parse["wikitext"]
        (cycle_dir / "article.wikitext").write_text(wikitext)
        meta = {
            "title": used_title,
            "revid": parse["revid"],
            "fetched_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lang": "en",
        }
        (cycle_dir / "source.json").write_text(json.dumps(meta, indent=2))
        print(f"  ✓ {cycle:<10} {len(wikitext):>7} chars  revid={parse['revid']}  ({used_title})")
        time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("country", help="ISO2 country code (IT, DE, FR, ...)")
    ap.add_argument("cycles", nargs="+", help="cycle identifiers, e.g. 2017 2021 current")
    args = ap.parse_args()
    fetch_country(args.country, args.cycles)


if __name__ == "__main__":
    main()

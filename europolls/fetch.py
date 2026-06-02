"""Fetch Wikipedia "Opinion polling for ..." articles per country/cycle.

Usage:
    python -m europolls.fetch IT 2006 2008 2013 2018 2022 current
    python -m europolls.fetch all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = "europolls/0.1 (https://github.com/gfrt0/europolls)"
COUNTRIES_YAML = ROOT / "config" / "countries.yaml"


def _load_countries() -> dict:
    return yaml.safe_load(COUNTRIES_YAML.read_text())


def _title_for(country: str, cycle: str, cfg: dict) -> str | None:
    """Resolve a Wikipedia article title for (country, cycle) from config."""
    spec = cfg["cycles"].get(cycle)
    if spec is None:
        return None
    if isinstance(spec, dict) and "title" in spec:
        return spec["title"]
    if cycle == "current":
        return cfg.get("title_current")
    pattern = cfg.get("title_for_cycle")
    return pattern.format(cycle=cycle) if pattern else None


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


def fetch_country(country: str, cycles: list[str], countries: dict | None = None) -> None:
    countries = countries or _load_countries()
    cfg = countries.get(country)
    if cfg is None:
        print(f"  ! no config entry for {country}")
        return
    out_root = ROOT / "data" / "raw" / country
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"=== {country} ===")
    for cycle in cycles:
        cycle_dir = out_root / cycle
        cycle_dir.mkdir(parents=True, exist_ok=True)
        title = _title_for(country, cycle, cfg)
        if title is None:
            print(f"  ! {cycle:<10} no title resolvable for cycle {cycle!r}")
            continue
        try:
            parse = _fetch_page(title)
        except Exception as e:
            print(f"  ! {cycle:<10} fetch failed for {title!r}: {e}")
            continue
        if parse is None:
            print(f"  ✗ {cycle:<10} no page found ({title})")
            continue
        wt = parse["wikitext"]
        (cycle_dir / "article.wikitext").write_text(wt)
        (cycle_dir / "source.json").write_text(json.dumps({
            "title": title,
            "revid": parse["revid"],
            "fetched_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lang": "en",
        }, indent=2))
        print(f"  ✓ {cycle:<10} {len(wt):>7} chars  revid={parse['revid']}  ({title})")
        time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("country", help="ISO2 country code, or 'all'")
    ap.add_argument("cycles", nargs="*", help="cycle ids (default: all from countries.yaml)")
    args = ap.parse_args()
    countries = _load_countries()
    if args.country == "all":
        targets = list(countries.keys())
    else:
        targets = [args.country]
    for c in targets:
        cycles = args.cycles or list(countries[c]["cycles"].keys())
        fetch_country(c, cycles, countries)


if __name__ == "__main__":
    main()

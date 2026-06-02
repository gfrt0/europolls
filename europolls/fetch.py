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
from urllib.error import HTTPError

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


def _fetch_page(title: str, lang: str = "en", max_retries: int = 6) -> dict | None:
    """Fetch a Wikipedia article's wikitext via the parse API.

    Retries with exponential backoff on HTTP 429 (rate limit) and 503; CI
    runs hit Wikipedia faster than locally and were silently losing whole
    countries' fetches to a single 429 burst. Honours Retry-After when
    present.
    """
    api = (
        f"https://{lang}.wikipedia.org/w/api.php?action=parse&page="
        + urllib.parse.quote(title)
        + "&prop=wikitext|revid&format=json&formatversion=2&redirects=true"
    )
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    delay = 1.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if "error" in data:
                return None
            return data["parse"]
        except HTTPError as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = float(e.headers.get("Retry-After", delay))
                print(f"      [{e.code}] retry in {wait:.1f}s")
                time.sleep(wait)
                delay = min(delay * 2, 30.0)
                continue
            raise
    return None


def _election_title_from(polling_title: str) -> str | None:
    """Derive the election-article title from the dedicated polling title.

    'Opinion polling for the 2016 Icelandic parliamentary election' →
        '2016 Icelandic parliamentary election'
    'Opinion polling for the next Polish parliamentary election' → None
        (no election article yet for unscheduled future cycles).
    """
    prefix = "Opinion polling for the "
    if not polling_title.startswith(prefix):
        return None
    rest = polling_title[len(prefix):]
    if rest.startswith("next "):
        return None
    return rest


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
        source_kind = "polling_article"
        used_title = title
        if parse is None:
            # Fallback: the dedicated polling article doesn't exist. Try the
            # election article itself — many smaller cycles (e.g. IS 2016,
            # CY 2016, LT, LU) host the polling table directly on the
            # election page under an '== Opinion polls ==' section.
            fb = _election_title_from(title)
            if fb:
                try:
                    parse = _fetch_page(fb)
                except Exception as e:
                    print(f"  ! {cycle:<10} fallback fetch failed for {fb!r}: {e}")
                    parse = None
                if parse is not None:
                    used_title = fb
                    source_kind = "election_article"
        if parse is None:
            print(f"  ✗ {cycle:<10} no page found ({title})")
            continue
        wt = parse["wikitext"]
        (cycle_dir / "article.wikitext").write_text(wt)
        (cycle_dir / "source.json").write_text(json.dumps({
            "title": used_title,
            "revid": parse["revid"],
            "fetched_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lang": "en",
            "source_kind": source_kind,
        }, indent=2))
        marker = "✓" if source_kind == "polling_article" else "↪"
        print(f"  {marker} {cycle:<10} {len(wt):>7} chars  revid={parse['revid']}  ({used_title})")
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

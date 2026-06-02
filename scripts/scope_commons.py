"""Recon: enumerate SVG opinion-poll charts hosted on Wikimedia Commons and
work out which ones point at Wikipedia articles we don't already cover.

Output: data/scope/commons_polls.csv with columns
    country, year, file_title, en_articles, has_dedicated_polling_article

For each file the script asks Commons' globalusage API which Wikipedia
articles use it. If at least one of those articles is an
"Opinion polling for the YYYY X election" page, we already cover it (or
have config for it). If only the *election* article references the
chart (e.g. '2016 Icelandic parliamentary election'), the polls likely
live on the election page itself — that's the gap the
opinionpollsbycountry branch is targeting.

Read-only. Run as:
    python scripts/scope_commons.py
"""
from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "scope"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ROOT_CAT    = "Category:Opinion_polls_by_country"
USER_AGENT  = "europolls-scope/0.1 (https://github.com/gfrt0/europolls)"

# Match patterns like '2016 Cypriot legislative election', and dedicated
# polling articles like 'Opinion polling for the 2016 Cypriot legislative
# election'. The polling-article pattern is the wider one — it always
# wraps an election-article title, so we test the polling form first.
RX_POLLING = re.compile(
    r"^Opinion polling for the (?P<year>\d{4})(?:[ -]\w+)? (?P<rest>.+)$"
)
RX_ELECTION = re.compile(r"^(?P<year>\d{4})(?:[ -]\w+)? (?P<rest>.+? election)$")


def api_get(params: dict, max_retries: int = 6) -> dict:
    """Issue a GET to Commons' MediaWiki API and return parsed JSON.

    Retries with exponential backoff on HTTP 429 (rate limit) or 503.
    Re-raises any other error after final retry.
    """
    params = {**params, "format": "json", "formatversion": 2}
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    url = f"{COMMONS_API}?{qs}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    delay = 1.0
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = float(e.headers.get("Retry-After", delay))
                print(f"    [{e.code}] retry in {wait:.1f}s")
                time.sleep(wait)
                delay *= 2
                continue
            raise
        except URLError:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


def iter_category_members(cat: str, types=("file", "subcat")) -> list[dict]:
    """Yield every (immediate) member of a Commons category."""
    out, cont = [], None
    while True:
        params = {
            "action": "query", "list": "categorymembers",
            "cmtitle": cat, "cmtype": "|".join(types), "cmlimit": 500,
        }
        if cont:
            params["cmcontinue"] = cont
        data = api_get(params)
        out.extend(data.get("query", {}).get("categorymembers", []))
        if "continue" in data:
            cont = data["continue"].get("cmcontinue")
            time.sleep(0.4)
        else:
            return out


def all_files_under(root_cat: str) -> list[tuple[str, str]]:
    """Walk subcategories once and collect (country_cat, file_title) pairs."""
    pairs: list[tuple[str, str]] = []
    subcats = iter_category_members(root_cat, types=("subcat",))
    print(f"  {len(subcats)} country subcategories under {root_cat}")
    for sub in subcats:
        sub_title = sub["title"]
        files = iter_category_members(sub_title, types=("file",))
        for f in files:
            pairs.append((sub_title, f["title"]))
        # Some country subcategories have year-keyed subcats. Recurse one level.
        for inner in iter_category_members(sub_title, types=("subcat",)):
            for f in iter_category_members(inner["title"], types=("file",)):
                pairs.append((sub_title, f["title"]))
        print(f"    {sub_title}: {len(files)} files at top level")
    return pairs


CACHE_DIR = OUT_DIR / "globalusage_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(file_title: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file_title)[:200]
    return CACHE_DIR / f"{safe}.json"


def en_articles_using(file_title: str) -> list[str]:
    """List English-Wikipedia articles that embed this Commons file.

    Cached on disk by file title so reruns are essentially free.
    """
    cache = _cache_path(file_title)
    if cache.exists():
        return json.loads(cache.read_text())
    out, cont = [], None
    while True:
        params = {
            "action": "query", "prop": "globalusage",
            "titles": file_title, "gulimit": 500, "gusite": "enwiki",
        }
        if cont:
            params["gucontinue"] = cont
        data = api_get(params)
        pages = data.get("query", {}).get("pages", [])
        for p in pages:
            for u in p.get("globalusage", []):
                if u.get("wiki") == "en.wikipedia.org":
                    out.append(u["title"])
        if "continue" in data:
            cont = data["continue"].get("gucontinue")
            time.sleep(0.4)
        else:
            cache.write_text(json.dumps(out))
            return out


def classify_article(title: str) -> tuple[str | None, str | None, bool]:
    """Return (country_adjective, year, is_dedicated_polling_article)."""
    m = RX_POLLING.match(title)
    if m:
        return m.group("rest"), m.group("year"), True
    m = RX_ELECTION.match(title)
    if m:
        return m.group("rest"), m.group("year"), False
    return None, None, False


def main() -> None:
    print(f"Walking {ROOT_CAT}...")
    pairs = all_files_under(ROOT_CAT)
    print(f"\n{len(pairs)} files discovered.")
    rows = []
    for i, (country_cat, file_title) in enumerate(pairs, 1):
        try:
            articles = en_articles_using(file_title)
        except Exception as e:
            print(f"  ! {file_title}: {e}")
            articles = []
        country_adj, year, has_polling = None, None, False
        for a in articles:
            adj, yr, dedicated = classify_article(a)
            if adj and yr:
                country_adj = adj
                year = yr
                if dedicated:
                    has_polling = True
        rows.append({
            "country_category": country_cat,
            "file_title": file_title,
            "en_articles": " | ".join(articles),
            "inferred_country_topic": country_adj or "",
            "inferred_year": year or "",
            "has_dedicated_polling_article": "1" if has_polling else "0",
            "election_article_only": "1" if (articles and not has_polling and year) else "0",
        })
        if i % 25 == 0:
            print(f"  ...{i}/{len(pairs)}")
        time.sleep(0.3)

    out_path = OUT_DIR / "commons_polls.csv"
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} rows -> {out_path.relative_to(ROOT)}")

    # Quick summary of the gap.
    only_election = [r for r in rows if r["election_article_only"] == "1"]
    print(f"\n{len(only_election)} files reference an *election* article")
    print(f"  but not a dedicated 'Opinion polling for…' article.")
    from collections import Counter
    by_topic = Counter(r["inferred_country_topic"] for r in only_election)
    print("\nTop election-article-only topics (potential new coverage):")
    for topic, n in by_topic.most_common(25):
        print(f"  {n:>3}  {topic}")


if __name__ == "__main__":
    main()

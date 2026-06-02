"""Country-agnostic Wikipedia opinion-polling parser.

Generalized from ``parse_italy.py``: reads
``data/raw/wikipedia_polls/{COUNTRY}/{cycle}/article.wikitext`` and writes a long
CSV per cycle to ``data/interim/{COUNTRY}_{cycle}.csv``.

Per-country configuration lives in :data:`COUNTRY_CONFIG` below. Each entry
provides the cycle → fallback-year mapping and an optional set of column names
that should be tagged as coalition aggregates (e.g. CDX/CSX/Unione in IT).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import wikitextparser as wtp

from europolls.lib import (
    parse_share, parse_sample, parse_fieldwork,
    strip_wikitext, extract_external_url,
)

ROOT = Path(__file__).resolve().parents[1]

RAW_ROOT = ROOT / "data" / "raw"
OUT_ROOT = ROOT / "data" / "interim"
OUT_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Header classification — kept country-agnostic.

# Substring-based header classification — more robust to formatting variations
# ('Date(s)conducted', 'Pollster/client(s)', 'Polling firm/Commissioner', etc.)


def _norm(s: str) -> str:
    """Lowercased header with whitespace and most punctuation collapsed."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def is_date_header(h: str) -> bool:
    n = _norm(h)
    if not n:
        return False
    if "update" in n:
        return False
    # 'publication' is the article's pub date and not the poll date — skip.
    # But standalone 'Published' on older election-article tables (LU 2013)
    # IS the poll's release date, so allow it.
    if "publication" in n:
        return False
    if any(k in n for k in (
        "fieldwork", "dateconducted", "datesconducted", "administered",
        "polldate", "enddate", "dateofpoll", "polldate", "fieldperiod",
        "lastdateofpolling", "lastdate", "periodofpolling",
        "releasedate",       # IS 2016
        "released",          # MT 2013 'Date(s) Released'
        "pollingperiod",     # NO 2013, some FI cycles
        "surveydate",        # HU 2014 'Survey dates'
    )):
        return True
    return n in {"date", "dates", "published"}


def is_pollster_header(h: str) -> bool:
    n = _norm(h)
    if not n:
        return False
    # Standalone 'Source' is the pollster column on some older election-article
    # tables (IS 2009 etc.); a column containing 'result' is not.
    if n == "source":
        return True
    return any(k in n for k in (
        "pollster", "pollingfirm", "pollinghouse",
        "pollingorganisation", "pollingorganization",
        "organisation", "organization", "company",
        "pollsource", "pollingsource",
        "institute",       # IS 2016 election-article fallback
        "institution",     # CH 2015 election-article fallback
        "agency",          # AT 2013 'Agency/Source'
        "newspaper",       # BE 2014 election-article fallback
    )) and "result" not in n


def is_sample_header(h: str) -> bool:
    n = _norm(h)
    return n in {"samplesize", "sample", "n"}


def is_meta_header(h: str) -> bool:
    if is_date_header(h) or is_pollster_header(h) or is_sample_header(h):
        return True
    n = _norm(h)
    return n in {
        "updated", "publication", "lead", "margin", "majority", "mode",
        "abs", "turnout", "type", "client", "commissioner", "orderedby",
        "fieldmethod", "method", "result", "area", "ref", "reference",
        "source", "notes", "note", "polling", "pollingaggregator",
    }

# ---------------------------------------------------------------------------
# Per-country config — loaded from config/countries.yaml at module import.

import yaml as _yaml  # noqa: E402

_COUNTRIES_YAML = ROOT / "config" / "countries.yaml"


def _load_countries() -> dict:
    """Load countries.yaml; normalize cycles to {cycle: default_year}.

    A cycle value is either:
      - int           — the cycle's default fallback year
      - {year, title?} — explicit override; we just take `year`
    """
    with _COUNTRIES_YAML.open() as f:
        raw = _yaml.safe_load(f)
    out = {}
    for country, cfg in raw.items():
        cycles_norm: dict[str, int] = {}
        for cycle_id, spec in (cfg.get("cycles") or {}).items():
            cycles_norm[cycle_id] = spec["year"] if isinstance(spec, dict) else int(spec)
        out[country] = {
            "cycles": cycles_norm,
            "coalition_shorts_lc": {str(s).lower() for s in (cfg.get("coalition_shorts") or [])},
        }
    return out


COUNTRY_CONFIG = _load_countries()


# Legacy literal kept as a comment for reference but no longer the source of truth.


def _has_date_and_pollster(headers: list[str]) -> bool:
    return any(is_date_header(h) for h in headers) and any(is_pollster_header(h) for h in headers)


def _resolve_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]] | None:
    """Detect the real header row(s).

    Three cases handled:

    1. Single-row header (the common case) — row 0 contains both a date and
       a pollster column; rows 1+ are data.

    2. Header in row 1 — row 0 is a section caption or artifact, row 1 has
       the meta columns.

    3. Multi-row header (the merged-cell case) — row 0 has meta columns plus
       group labels for the party region ('Parties', 'Government', 'Opposition'
       or per-column ?px image / link= artifacts). One or more subsequent
       header rows refine the party labels until the actual party shorts
       appear (IS 2024 has 4 such rows). Detected by: row 0 has date+pollster,
       AND the next row's meta cells repeat row 0's meta cell values rather
       than being data. The effective party label per column is the deepest
       non-empty value across the header span; meta columns use row 0.
    """
    if not rows:
        return None
    h0 = [strip_wikitext(c) for c in rows[0]]

    if _has_date_and_pollster(h0):
        return _resolve_with_meta_header(rows, h0)

    if len(rows) >= 2:
        h1 = [strip_wikitext(c) for c in rows[1]]
        if _has_date_and_pollster(h1):
            merged = [r1 if r1 else r0 for r0, r1 in zip(h0, h1)]
            data = [r for r in rows[2:] if r and [strip_wikitext(c) for c in r] != h1]
            return merged, data
    return None


def _resolve_with_meta_header(
    rows: list[list[str]], h0: list[str],
) -> tuple[list[str], list[list[str]]]:
    """Given row 0 confirmed to be a meta-bearing header, find how many
    subsequent rows are also part of the (multi-row) header by checking
    whether their meta cells repeat h0's meta values. Build the effective
    header by taking the deepest non-empty cell per column.
    """
    meta_indices = [i for i, c in enumerate(h0) if is_meta_header(c)]
    header_depth = 1
    for r in rows[1:]:
        if not r:
            break
        rh = [strip_wikitext(c) for c in r]
        # A row counts as part of the header if every meta column either
        # repeats h0's value or is empty. Empty cells are artefacts of
        # vertical cell-spanning in the underlying wikitable.
        if all(
            i < len(rh) and (rh[i] == h0[i] or rh[i] == "")
            for i in meta_indices
        ):
            header_depth += 1
        else:
            break

    if header_depth == 1:
        # Standard single-row header.
        data = [r for r in rows[1:] if r and [strip_wikitext(c) for c in r] != h0]
        return h0, data

    # Multi-row header: pick the deepest non-empty cell per column, except
    # for meta columns where row 0 is canonical.
    merged: list[str] = []
    for i in range(len(h0)):
        if i in meta_indices:
            merged.append(h0[i])
            continue
        value = h0[i]
        for ri in range(header_depth):
            if ri < len(rows) and i < len(rows[ri]):
                cell = strip_wikitext(rows[ri][i])
                if cell:
                    value = cell
        merged.append(value)
    data = [r for r in rows[header_depth:] if r and [strip_wikitext(c) for c in r] != h0]
    return merged, data


def _is_party_col_meaningful(h: str) -> bool:
    if not h:
        return False
    h_lc = h.lower()
    if h_lc.endswith("px") or h_lc.startswith("link="):
        return False
    if is_meta_header(h):
        return True
    return True


def _find_party_columns(header: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, h in enumerate(header):
        if not h or is_meta_header(h):
            continue
        out.append((i, h))
    return out


def _year_for_table(wikitext: str, table_start: int, fallback: int) -> int:
    preceding = wikitext[:table_start]
    matches = list(re.finditer(r"={2,4}\s*([^=\n]+?)\s*={2,4}\s*$", preceding, re.MULTILINE))
    for h in reversed(matches):
        m = re.search(r"\b(19\d{2}|20\d{2})\b", h.group(1))
        if m:
            return int(m.group(1))
    return fallback


# Section-heading patterns that indicate a polling subsection on an
# election article. Used only when source_kind == "election_article" to
# avoid sucking in results / candidate / nominee tables from the same
# page. Match level 2-4 headings whose text contains 'poll' or 'opinion'.
_POLLING_HEADING_RE = re.compile(
    r"^(={2,4})\s*([^=\n]*?(?:[Oo]pinion poll|[Pp]olling|[Pp]olls|[Pp]oll)[^=\n]*?)\s*={2,4}\s*$",
    re.MULTILINE,
)
_ANY_HEADING_RE = re.compile(r"^={2,4}\s*[^=\n]+?\s*={2,4}\s*$", re.MULTILINE)


def _heading_depth(h: str) -> int:
    """Number of leading '=' characters on a Wikipedia heading line."""
    m = re.match(r"^(={2,4})", h)
    return len(m.group(1)) if m else 2


_PRESIDENTIAL_HEADING_RE = re.compile(
    r"^={2,4}\s*[^=\n]*?[Pp]residen[a-z]*[^=\n]*?\s*={2,4}\s*$",
    re.MULTILINE,
)


def _polling_section_spans(wikitext: str) -> list[tuple[int, int]]:
    """Return a list of (start, end) byte ranges in wikitext that fall under a
    polling-related section heading. Each range runs from the end of the
    matched heading to the next heading at the same or shallower level.
    Used only when reading an election-article fallback page.

    Sub-spans whose own heading mentions 'president' / 'presidential' are
    excluded — they leak presidential-race polling into parliamentary cycles
    (seen on BG 2021-Nov: Radev approval polls at 60-87%).
    """
    if not _POLLING_HEADING_RE.search(wikitext):
        return []
    headings = list(_ANY_HEADING_RE.finditer(wikitext))
    spans: list[tuple[int, int]] = []
    for i, h in enumerate(headings):
        if not _POLLING_HEADING_RE.match(wikitext, h.start()):
            continue
        depth = _heading_depth(h.group(0))
        end = len(wikitext)
        for h2 in headings[i + 1:]:
            if _heading_depth(h2.group(0)) <= depth:
                end = h2.start()
                break
        # Within this polling-section, find any presidential subsection and
        # carve out its [start, end) range to exclude.
        presidential_holes: list[tuple[int, int]] = []
        for ph in headings[i + 1:]:
            ph_start = ph.start()
            if ph_start >= end:
                break
            if not _PRESIDENTIAL_HEADING_RE.match(wikitext, ph_start):
                continue
            pdepth = _heading_depth(ph.group(0))
            phend = end
            for h3 in headings:
                if h3.start() <= ph_start:
                    continue
                if _heading_depth(h3.group(0)) <= pdepth:
                    phend = h3.start()
                    break
            presidential_holes.append((ph_start, phend))
        # Emit the polling section minus the presidential holes.
        cur = h.end()
        for ph_start, ph_end in presidential_holes:
            if ph_start > cur:
                spans.append((cur, ph_start))
            cur = ph_end
        if cur < end:
            spans.append((cur, end))
    return spans


def parse_cycle(country: str, cycle: str, default_year: int, coalition_lc: set[str]) -> list[dict]:
    cycle_dir = RAW_ROOT / country / cycle
    wikitext = (cycle_dir / "article.wikitext").read_text()
    src_meta = json.loads((cycle_dir / "source.json").read_text())
    article_title = src_meta["title"]
    article_revid = src_meta["revid"]
    article_lang = src_meta.get("lang", "en")
    article_url_rev = f"https://{article_lang}.wikipedia.org/w/index.php?oldid={article_revid}"
    source_kind = src_meta.get("source_kind", "polling_article")

    parsed = wtp.parse(wikitext)
    out: list[dict] = []
    skipped_tables = 0
    skipped_rows = 0

    # For election-article fallbacks, only consider wikitables that live
    # inside a section whose heading mentions polls/polling. If no such
    # section exists, no tables are emitted for this cycle.
    polling_spans: list[tuple[int, int]] | None = None
    if source_kind == "election_article":
        polling_spans = _polling_section_spans(wikitext)
        if not polling_spans:
            print(f"  {country} {cycle:<10} (election-article fallback, no polling section found)")
            return out

    for ti, table in enumerate(parsed.tables):
        if polling_spans is not None:
            ts = table.span[0]
            if not any(s <= ts < e for s, e in polling_spans):
                skipped_tables += 1
                continue
        table_year = _year_for_table(wikitext, table.span[0], default_year)
        try:
            rows = table.data()
        except Exception:
            skipped_tables += 1
            continue
        resolved = _resolve_header(rows)
        if resolved is None:
            skipped_tables += 1
            continue
        header, data_rows = resolved
        if len(header) < 4:
            skipped_tables += 1
            continue

        i_pollster = next((i for i, h in enumerate(header) if is_pollster_header(h)), -1)
        i_admin = next((i for i, h in enumerate(header) if is_date_header(h)), -1)
        i_sample = next((i for i, h in enumerate(header) if is_sample_header(h)), -1)
        party_cols = _find_party_columns(header)
        if not party_cols:
            skipped_tables += 1
            continue

        for row in data_rows:
            if len(row) != len(header):
                skipped_rows += 1
                continue
            pollster_raw = row[i_pollster] if i_pollster >= 0 else ""
            pollster = strip_wikitext(pollster_raw)
            source_url = extract_external_url(pollster_raw)
            if not pollster:
                skipped_rows += 1
                continue
            fw = parse_fieldwork(row[i_admin], table_year) if i_admin >= 0 else None
            sample = parse_sample(row[i_sample]) if i_sample >= 0 else None
            mid = None
            if fw and fw.start and fw.end:
                from datetime import date
                try:
                    s = date.fromisoformat(fw.start)
                    e = date.fromisoformat(fw.end)
                    if e < s:
                        s, e = e, s
                    mid = (s + (e - s) / 2).isoformat()
                except ValueError:
                    mid = None

            # Sanity gate: if any party cell in this row exceeds 100, it's a
            # seat-projection table (UK MRP polls forecast seat counts up to
            # ~650, not vote shares). Drop the whole row.
            row_shares = [parse_share(row[pi]) for pi, _ in party_cols]
            if any(s is not None and s > 100 for s in row_shares):
                skipped_rows += 1
                continue

            for (pi, pshort), share in zip(party_cols, row_shares):
                if share is None:
                    continue
                is_coal = pshort.lower() in coalition_lc
                out.append({
                    "country": country,
                    "cycle": cycle,
                    "polldate_start": fw.start if fw else None,
                    "polldate_end": fw.end if fw else None,
                    "polldate_mid": mid,
                    "pollster": pollster,
                    "source_url": source_url,
                    "sample_size": sample,
                    "party_short": pshort,
                    "is_coalition": is_coal,
                    "vote_share": share,
                    "wiki_article": article_title,
                    "wiki_revid": article_revid,
                    "wiki_url": article_url_rev,
                    "_table_idx": ti,
                })
    print(f"  {country} {cycle:<10} {len(out):>6} rows  "
          f"({skipped_tables} tables skipped, {skipped_rows} rows skipped)")
    return out


def run_country(country: str) -> None:
    cfg = COUNTRY_CONFIG[country]
    coalition_lc = cfg["coalition_shorts_lc"]
    total = 0
    for cycle, default_year in cfg["cycles"].items():
        cycle_dir = RAW_ROOT / country / cycle
        if not (cycle_dir / "article.wikitext").exists():
            print(f"  {country} {cycle:<10} (no wikitext on disk, skipping)")
            continue
        rows = parse_cycle(country, cycle, default_year, coalition_lc)
        if not rows:
            continue
        out_path = OUT_ROOT / f"{country}_{cycle}.csv"
        with out_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        total += len(rows)
    print(f"  -> {country}: {total:,} total rows\n")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("countries", nargs="+", help="ISO2 codes; use 'all' for everything configured")
    args = ap.parse_args()
    countries = list(COUNTRY_CONFIG.keys()) if args.countries == ["all"] else args.countries
    for c in countries:
        if c not in COUNTRY_CONFIG:
            print(f"!! no config for {c}, skipping")
            continue
        run_country(c)


if __name__ == "__main__":
    main()

"""Export the long polls + party colors into per-country JSON files for the web/ page.

Reads:
  data/processed/polls_long.csv
  config/party_colors.yaml

Writes:
  web/countries.json           — index: [{code, n_polls, span_start, span_end, top_parties, wide_parties}]
  web/polls_{COUNTRY}.json     — array of poll-party objects, trimmed
  web/colors.json              — country → {party_short: hex}

Includes party/pollster normalization to fold variants of the same entity.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml


def normalize_pollster(name) -> str:
    """Strip commissioner / parenthetical / 'for X' suffixes so 'YouGov/The Sun',
    'YouGov/Sunday Times', 'YouGov (MRP)', 'YouGov for The Observer' all collapse
    to 'YouGov'."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = str(name).strip()
    # Drop everything from first '/', '(', or '–'.
    s = re.split(r"[/(–]| - ", s, maxsplit=1)[0]
    # Drop trailing ' for X', ' fur X', ' für X'.
    s = re.sub(r"\s+(for|fur|für|per|de)\s+.*$", "", s, flags=re.IGNORECASE)
    # Drop company suffixes.
    s = re.sub(r"\b(s\.r\.l\.?|srl|s\.p\.a\.?|spa|gmbh|ltd|institut)\b\.?", "", s, flags=re.IGNORECASE)
    # Strip trailing footnote markers ('*', '**', ' †').
    s = re.sub(r"\s*[\*†‡]+\s*$", "", s)
    return s.strip().rstrip(",").strip()


def mode_winner_canonical(series: pd.Series) -> dict:
    """Group values by case/whitespace-collapsed key, pick the highest-count
    variant as canonical. Returns {variant → canonical}."""
    def key(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""
    counts = series.value_counts()
    groups: dict[str, list[tuple[str, int]]] = {}
    for variant, n in counts.items():
        if pd.isna(variant):
            continue
        k = key(variant)
        if not k:
            continue
        groups.setdefault(k, []).append((variant, n))
    canonical: dict[str, str] = {}
    for k, variants in groups.items():
        winner = max(variants, key=lambda x: x[1])[0]
        for v, _ in variants:
            canonical[v] = winner
    return canonical


def normalize_party_shorts(series: pd.Series) -> pd.Series:
    """Within a country, fold party_short variants by case-insensitive grouping.
    Canonical form = the variant with highest observation count.

    Examples (per-country):
      SE: 'Sd'(342) → 'SD'(934) wins → all become 'SD'
      ES: 'CS'(255), "C's"(549), 'Cs'(761) → 'Cs' wins
      GR: 'Syriza'(296), 'SYRIZA'(1632) → 'SYRIZA' wins
    """
    def key(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""
    # Count per variant
    counts = series.value_counts()
    # Group by normalized key
    groups: dict[str, list[tuple[str, int]]] = {}
    for variant, n in counts.items():
        if pd.isna(variant):
            continue
        k = key(variant)
        if not k:
            continue
        groups.setdefault(k, []).append((variant, n))
    # Pick canonical per group
    canonical: dict[str, str] = {}
    for k, variants in groups.items():
        winner = max(variants, key=lambda x: x[1])[0]
        for v, _ in variants:
            canonical[v] = winner
    return series.map(lambda v: canonical.get(v, v))

ROOT = Path(__file__).resolve().parents[1]
LONG_CSV = ROOT / "data" / "processed" / "polls_long.csv"
COLORS_YAML = ROOT / "config" / "party_colors.yaml"
COUNTRIES_YAML = ROOT / "config" / "countries.yaml"
POLLSTER_ALIASES_YAML = ROOT / "config" / "pollster_aliases.yaml"
PARTY_ALIASES_DIR = ROOT / "config" / "party_aliases"
PARTY_NAMES_YAML = ROOT / "config" / "party_names.yaml"
WEB = ROOT / "web"
WEB.mkdir(exist_ok=True)


def load_pollster_aliases() -> dict[str, str]:
    if not POLLSTER_ALIASES_YAML.exists():
        return {}
    data = yaml.safe_load(POLLSTER_ALIASES_YAML.read_text()) or {}
    return data.get("aliases", {}) or {}


def load_party_aliases(country: str) -> dict[str, str]:
    path = PARTY_ALIASES_DIR / f"{country}.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("aliases", {}) or {}


def load_party_drops(country: str) -> tuple[set, list]:
    """Return (exact-match drop set, list of compiled regex patterns)."""
    path = PARTY_ALIASES_DIR / f"{country}.yaml"
    if not path.exists():
        return set(), []
    data = yaml.safe_load(path.read_text()) or {}
    exact = set(data.get("drop", []) or [])
    regex_patterns = [re.compile(p) for p in (data.get("drop_regex", []) or [])]
    return exact, regex_patterns


def load_meta_aliases() -> dict[str, str]:
    """Cross-country meta-label normalization (don't know / abstain / etc.)."""
    path = PARTY_ALIASES_DIR / "_meta.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("aliases", {}) or {}


def main() -> None:
    if not LONG_CSV.exists():
        raise SystemExit(f"missing {LONG_CSV.relative_to(ROOT)}; run scripts/build_all.py first")
    df = pd.read_csv(LONG_CSV, low_memory=False,
                     keep_default_na=False, na_values=[""])
    print(f"loaded {len(df):,} long rows, {df['country'].nunique()} countries")

    # Trim to voting-intention rows: build_all already removes hard-drop
    # categories (approval/lead/response_rate/total/presidential_candidate
    # /parse_artifact); filter the remaining flag-only meta (others / dont_
    # know / undecided / abstention buckets) so the chart shows party
    # shares only.
    if "is_dropped_meta" in df.columns:
        before = len(df)
        df = df[df["is_dropped_meta"].fillna(0).astype(int) == 0]
        print(f"  filtered {before - len(df):,} meta rows (is_dropped_meta=1)")

    # Trim to web-relevant columns + drop dateless rows (the page is time-axis-keyed).
    df = df.dropna(subset=["polldate_mid"])
    df = df[df["vote_share"].notna()]
    df["sample_size"] = pd.to_numeric(df["sample_size"], errors="coerce").astype("Int64")

    # Fall back to party_short for any row without a party_canonical
    # (build_all sets canonical=short when no partyfacts_name resolved,
    # but older CSVs and missing partyfacts_names.yaml degrade safely).
    if "party_canonical" not in df.columns:
        df["party_canonical"] = df["party_short"]
    else:
        df["party_canonical"] = df["party_canonical"].where(
            df["party_canonical"].notna() & (df["party_canonical"] != ""),
            df["party_short"],
        )

    # Normalize pollster names (strip commissioner/parenthetical suffixes).
    df["pollster"] = df["pollster"].apply(normalize_pollster)
    # Mode-winner case/whitespace folding (Yougov → YouGov, etc.), then
    # manual cross-country alias mapping (Techne → Tecnè, etc.).
    pollster_canon = mode_winner_canonical(df["pollster"])
    df["pollster"] = df["pollster"].map(lambda v: pollster_canon.get(v, v))
    pollster_aliases = load_pollster_aliases()
    if pollster_aliases:
        df["pollster"] = df["pollster"].map(lambda v: pollster_aliases.get(v, v))

    colors_raw = yaml.safe_load(COLORS_YAML.read_text())
    countries_cfg = yaml.safe_load(COUNTRIES_YAML.read_text())

    countries_summary = []
    meta_aliases = load_meta_aliases()
    # Re-keyed lookups: canonical → hex / canonical → display name.
    # Built incrementally per country from the existing party_short-keyed
    # YAMLs (colors.yaml, party_names.yaml) by mapping each canonical to
    # its most-observed party_short variant.
    colors_canonical: dict[str, dict[str, str]] = {}
    dominant_short_per_canonical: dict[str, dict[str, str]] = {}
    for country, sub in df.groupby("country"):
        sub = sub.copy()
        # Cross-country meta-label normalization first (defensive — most of
        # these are already filtered as is_dropped_meta=1 upstream, but the
        # alias map catches anything that slipped through).
        if meta_aliases:
            sub["party_canonical"] = sub["party_canonical"].map(
                lambda v: meta_aliases.get(v, v))
        # Normalize residual variants within the country (case folding etc.).
        sub["party_canonical"] = normalize_party_shorts(sub["party_canonical"])
        # Per-country manual alias map as a safety net for parties without
        # a partyfacts_id (the PF mapping already handles the well-covered
        # majority).
        country_aliases = load_party_aliases(country)
        if country_aliases:
            sub["party_canonical"] = sub["party_canonical"].map(
                lambda v: country_aliases.get(v, v))
        drop_exact, drop_regex = load_party_drops(country)
        if drop_exact:
            sub = sub[~sub["party_canonical"].isin(drop_exact)]
        for rx in drop_regex:
            sub = sub[~sub["party_canonical"].astype(str).str.contains(rx, regex=True, na=False)]

        # For each canonical name, find the most-observed party_short
        # variant — used to re-key colors.yaml / party_names.yaml below.
        dominant = (
            sub.groupby("party_canonical")["party_short"]
            .agg(lambda s: s.value_counts().idxmax() if len(s) else None)
            .to_dict()
        )
        dominant_short_per_canonical[country] = dominant
        # Materialize per-country canonical-keyed colors by looking up the
        # dominant variant in colors.yaml.
        country_colors = colors_raw.get(country) or {}
        colors_canonical[country] = {
            canon: country_colors[short]
            for canon, short in dominant.items()
            if short in country_colors
        }
        # Flag coalition aggregates per countries.yaml. coalition_shorts is
        # keyed by party_short; mask on the raw short, then OR onto
        # is_coalition (which is already populated by build_all.py from the
        # parse-time flag + YAML is_coalition).
        coal_shorts = set(countries_cfg.get(country, {}).get("coalition_shorts") or [])
        coal_shorts_lc = {str(s).lower() for s in coal_shorts}
        if coal_shorts_lc:
            mask = sub["party_short"].astype(str).str.lower().isin(coal_shorts_lc)
            sub.loc[mask, "is_coalition"] = True

        # Map any party_short → its canonical (lookup built above) so curated
        # countries.yaml entries (still party_short-keyed) translate.
        short_to_canon = {
            short: canon for canon, short in dominant.items() if short
        }

        # Compute summary: n polls, date span, top parties by observation count.
        n_polls = len(sub.drop_duplicates(["polldate_mid", "pollster"]))
        span_start = sub["polldate_mid"].min()
        span_end = sub["polldate_mid"].max()
        parties = (sub[~sub["is_coalition"].fillna(False)]
                   .groupby("party_canonical").size()
                   .sort_values(ascending=False))
        top_parties = parties.head(10).index.tolist()

        # "Wide-table" parties — sustained vote-intention parties only.
        # Filter heuristics:
        #  (a) mean observed share ≥ 2% AND observed in ≥ 30 polls (filters one-shot
        #      anomalies and parties only seen in single tables);
        #  (b) drop entries that are obviously *not* vote intention — leader-approval
        #      columns ('Theresa May', 'Merz'), meta-aggregates ('Coalitions',
        #      'Voting intentions', 'Seat projections'), Don't-know / Not-sure
        #      buckets, concatenated coalition names ('UnionSPDGrüne'), etc.
        #  (c) cap at the top 14 by mean share so the wide table stays readable.
        non_coal = sub[~sub["is_coalition"].fillna(False)]
        stats = non_coal.groupby("party_canonical")["vote_share"].agg(["count", "mean"])

        # Use the curated per-country wide_parties from countries.yaml when
        # present (most countries). Falls back to the heuristic below for any
        # country that doesn't have an entry. countries.yaml is still party_
        # short-keyed; translate to canonical via the dominant-variant map.
        curated_raw = countries_cfg.get(country, {}).get("wide_parties")
        curated = [short_to_canon.get(p, p) for p in curated_raw] if curated_raw else None
        if curated:
            available = set(stats.index)
            wide_parties = [p for p in curated if p in available]
            countries_summary.append({
                "code": country,
                "n_polls": int(n_polls),
                "n_party_obs": int(len(sub)),
                "span_start": span_start,
                "span_end": span_end,
                "top_parties": top_parties,
                "wide_parties": wide_parties,
            })
            # Skip the heuristic block (continue with per-country JSON write).
            _skip_heuristic = True
        else:
            _skip_heuristic = False

        # Single-token meta names we always drop.
        META_DROP = {
            "approve", "approval", "disapprove", "don't know", "dontknow",
            "don't  know", "not sure", "no opinion", "none of these",
            "none of the above", "none", "all", "voting intentions",
            "coalitions", "ideologies", "blocs", "seat projections", "seats",
            "percentage", "overall", "above threshold", "abs.", "abs",
            "abstention", "would not vote", "would not vote/refused",
            "refused", "lead", "margin", "others", "other", "oth.",
            "others/abroad", "no reply", "noreply", "no vote", "novote",
            "notvoting", "spoilt", "blank", "various", "uncertain",
            "neither", "neither / none", "no opinion", "n|neither",
            "no|no opinion", "o|other", "opp.", "gov.", "red", "blue",
            "above", "below",
        }
        # Substring-flagged content that's never a vote-intention party.
        DROP_SUBSTR = (
            " coalition", "coalition ", "cabinet", "approval", "scenario",
            "marginof error", "first round", "ideologies",
        )
        # Two-or-more space-separated Title-cased words = a person name.
        PERSON_RE = re.compile(r"^[A-ZÅÄÖÜÉÈÊÁÍÓÚÑ][a-zåäöüéèêáíóúñ'\-]+(?:\s+[A-ZÅÄÖÜÉÈÊÁÍÓÚÑ][a-zåäöüéèêáíóúñ'\-\.]+)+$")
        # Concatenated coalition names like UnionSPDGrüne, KurzÖVP, PSOECs.
        CONCAT_RE = re.compile(r"^[A-ZÅÄÖÜ][a-zåäöüé]+[A-ZÅÄÖÜ].*$")

        def _is_party(name: str) -> bool:
            n = str(name).strip()
            nl = n.lower()
            if not n or len(n) > 25:        # extremely long strings are footnoted columns
                return False
            if nl in META_DROP: return False
            if any(s in nl for s in DROP_SUBSTR): return False
            if PERSON_RE.match(n): return False
            if CONCAT_RE.match(n) and not any(c in n for c in "&-/+ "):
                # 'UnionSPDGrüne' style; allow 'CDU/CSU' because of the /.
                return False
            if "(" in n or "%" in n: return False
            return True

        if not _skip_heuristic:
            eligible = [p for p in stats.index
                        if stats.loc[p, "mean"] >= 2.0
                        and stats.loc[p, "count"] >= 30
                        and _is_party(p)]
            eligible.sort(key=lambda p: -stats.loc[p, "mean"])
            wide_parties = eligible[:12]
            countries_summary.append({
                "code": country,
                "n_polls": int(n_polls),
                "n_party_obs": int(len(sub)),
                "span_start": span_start,
                "span_end": span_end,
                "top_parties": top_parties,
                "wide_parties": wide_parties,
            })

        # Per-country file: compact column names to keep the JSON small.
        # `k` is now the canonical party key (PF name when mapped, else
        # party_short). Lega + LN collapse via PF id 1221, etc.
        keep = sub[[
            "polldate_mid", "pollster", "sample_size",
            "party_canonical", "is_coalition", "vote_share",
        ]].copy()
        # After PF mapping + aliases, the same (poll, canonical) may appear
        # twice if two variant shorts mapped to the same canonical. Collapse
        # with mean.
        keep = (keep
                .groupby(["polldate_mid", "pollster", "sample_size",
                          "party_canonical", "is_coalition"], as_index=False, dropna=False)
                ["vote_share"].mean())
        keep["polldate_mid"] = pd.to_datetime(keep["polldate_mid"]).dt.strftime("%Y-%m-%d")
        keep["is_coalition"] = keep["is_coalition"].fillna(False).astype(bool)
        keep["sample_size"] = keep["sample_size"].astype(object).where(keep["sample_size"].notna(), None)
        compact = keep.rename(columns={
            "polldate_mid":     "d",
            "pollster":         "p",
            "sample_size":      "n",
            "party_canonical":  "k",
            "is_coalition":     "c",
            "vote_share":       "v",
        })
        out_path = WEB / f"polls_{country}.json"
        compact.to_json(out_path, orient="records", date_format=None, indent=None)
        size_kb = out_path.stat().st_size / 1024
        print(f"  {country:<8}  {len(compact):>6,} party-poll rows  → polls_{country}.json ({size_kb:.0f} KB)")

    # Index + colors. Colors are now keyed by party_canonical, computed
    # per country by mapping each canonical to its most-observed party_
    # short variant and looking that up in colors.yaml.
    countries_summary.sort(key=lambda r: -r["n_polls"])
    (WEB / "countries.json").write_text(json.dumps(countries_summary, indent=2))
    (WEB / "colors.json").write_text(json.dumps(colors_canonical, indent=2))

    # Party names lookup for tooltips, also re-keyed to party_canonical.
    # Sources, in priority order:
    #   1. party_names.yaml (existing hand-curated rich names by party_short),
    #      remapped via dominant_short_per_canonical;
    #   2. PF's display name (already the canonical key itself when partyfacts_
    #      id is present — no extra lookup needed for that case).
    # Country-specific entries win over the _generic block.
    if PARTY_NAMES_YAML.exists():
        names_raw = yaml.safe_load(PARTY_NAMES_YAML.read_text()) or {}
        generic = names_raw.pop("_generic", {}) or {}
        names_out: dict[str, dict[str, str]] = {}
        for cc, dominant in dominant_short_per_canonical.items():
            short_to_canon = {short: canon for canon, short in dominant.items() if short}
            curated_for_country = names_raw.get(cc) or {}
            merged = {}
            # Generic entries: keys here are typically meta labels (Others,
            # Don't know, Neither, Abstain) which were filtered upstream,
            # so they likely never appear. Apply anyway as a safety net.
            for short, full in generic.items():
                canon = short_to_canon.get(short, short)
                merged[canon] = full
            # Country-specific overrides.
            for short, full in curated_for_country.items():
                canon = short_to_canon.get(short, short)
                merged[canon] = full
            names_out[cc] = merged
        (WEB / "party_names.json").write_text(json.dumps(names_out))
        print(f"wrote party_names.json ({len(names_out)} countries, "
              f"{sum(len(v) for v in names_out.values())} entries)")

    # Cache-buster: build identifier consumed by index.html so all JSON
    # asset URLs get a ?v=<build> suffix that changes every deploy. Prefer
    # the GitHub commit SHA when available (stable, short), else a UTC
    # timestamp. version.json itself is fetched with cache: 'no-store'.
    build = os.environ.get("GITHUB_SHA", "")[:12] or \
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    (WEB / "version.json").write_text(json.dumps({"build": build}))
    print(f"\nwrote countries.json ({len(countries_summary)} entries)")
    print(f"wrote colors.json")
    print(f"wrote version.json (build={build})")


if __name__ == "__main__":
    main()

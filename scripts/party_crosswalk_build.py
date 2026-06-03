"""Export the per-country party_short → partyfacts_id crosswalk in the YAML
shape europolls expects at config/party_mappings/{COUNTRY}.yaml.

Sources (in priority order):
  1. extend.py's Gemini output (data/processed/polls_crosswalk_gemini.csv)
     with reasoning, confidence, is_coalition flag — most informative.
  2. Layered base (data/processed/polls_partyfacts_crosswalk.csv) — Party
     Facts external-parties + WhoGov-minister-derived matches.
  3. europolls' own existing IT.yaml (kept as the highest-priority source
     for IT so we don't regress that file).

Hand-curated entries already present in the target YAML are PRESERVED on
rerun: any entry with ``source`` in HAND_SOURCES or with ``drop: true``
survives untouched. This lets you re-run the auto pipeline against a
fresher PF / Gemini snapshot without losing the manual classification
work documented in entry ``notes:`` fields. IT.yaml and any YAML named
with a leading underscore (``_meta.yaml``) are skipped entirely.

Output:
  config/party_mappings/{COUNTRY}.yaml

Each mapping carries `partyfacts_id`, `confidence` (high/medium/low/null),
`source` (gemini_high|gemini_medium|gemini_low|partyfacts_external|whogov_
minister_match|manual_review|verified_not_in_pf|europolls_harmonized),
`is_coalition` (bool), and a `notes` field with provenance / reasoning.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
GEMINI_CSV = REPO / "data" / "interim" / "polls_crosswalk_gemini.csv"
BASE_CSV = REPO / "data" / "interim" / "party_crosswalk_seed.csv"
EXTENDED_CSV = REPO / "data" / "interim" / "polls_partyfacts_crosswalk_extended.csv"
DEST_DIR = REPO / "config" / "party_mappings"

# Entries with these `source` values were placed by hand (or by a
# follow-up audit script) and represent judgment work the auto pipeline
# cannot reproduce. They are preserved on every rebuild.
HAND_SOURCES = frozenset({
    "manual_review",
    "verified_not_in_pf",
    "hand_curated",
    "europolls_harmonized",
})

# YAMLs to skip entirely. IT.yaml uses a different dict-of-mappings
# shape predating this pipeline; _meta.yaml is the global meta-label
# drop list, not a country.
SKIP_STEMS = frozenset({"IT", "_meta"})


def main() -> int:
    if not GEMINI_CSV.exists():
        print(f"missing {GEMINI_CSV}", file=__import__("sys").stderr)
        return 1
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    gemini = pd.read_csv(GEMINI_CSV)
    base = pd.read_csv(BASE_CSV) if BASE_CSV.exists() else pd.DataFrame(
        columns=["country", "party_short", "partyfacts_id", "source"])

    # Index gemini by (country, party_short) — most expressive.
    gemini_idx = {(r.country, r.party_short): r for _, r in gemini.iterrows()}
    base_idx = {(r.country, r.party_short): r for _, r in base.iterrows()}

    # Master set of pairs to emit.
    pairs = sorted(set(gemini_idx) | set(base_idx))

    by_country: dict[str, list[dict]] = {}
    for country, party_short in pairs:
        g = gemini_idx.get((country, party_short))
        b = base_idx.get((country, party_short))
        # Determine partyfacts_id + source + confidence + notes.
        pf_id = None
        confidence = None
        source = None
        is_coalition = False
        notes = ""
        if g is not None and pd.notna(g.matched_partyfacts_id):
            pf_id = int(g.matched_partyfacts_id)
            confidence = g.confidence
            source = f"gemini_{g.confidence}"
            is_coalition = bool(g.is_coalition) if pd.notna(g.is_coalition) else False
            notes = (g.matched_name or "") + (
                f" — {g.reasoning}" if pd.notna(g.reasoning) and g.reasoning else "")
        elif b is not None and pd.notna(b.partyfacts_id):
            pf_id = int(b.partyfacts_id)
            confidence = None
            source = str(b.source) if pd.notna(b.source) else "base"
        else:
            # No partyfacts_id from either side. Record the no_match for
            # downstream: useful for europolls to know we tried.
            if g is not None and g.confidence == "no_match":
                source = "gemini_no_match"
                notes = g.reasoning if pd.notna(g.reasoning) else ""
            else:
                continue  # nothing to emit
        by_country.setdefault(country, []).append({
            "party_short": party_short,
            "partyfacts_id": pf_id,
            "source": source,
            "confidence": confidence,
            "is_coalition": is_coalition,
            "notes": notes.strip(),
        })

    header = ("# Auto-generated from italgov's data/scripts/party_crosswalk/\n"
              "#\n"
              "# Built by layering:\n"
              "#   1. extend.py — Gemini-based (country, party_short) → partyfacts_id\n"
              "#      with confidence scores. Coverage of opposition + coalition parties.\n"
              "#   2. Party Facts external-parties.csv — base layer of historically\n"
              "#      recognized parties.\n"
              "#   3. WhoGov-minister-matched — high-signal entries derived from\n"
              "#      cabinet ministers' partyfacts_id in WhoGov v3.1.\n"
              "#\n"
              "# `source` values: gemini_{high,medium,low,no_match} for Gemini calls,\n"
              "# partyfacts_external/whogov_minister_match/europolls_harmonized for\n"
              "# the layered base. Use confidence + is_coalition to triage.\n\n")

    for country in sorted(by_country):
        if country in SKIP_STEMS:
            continue
        auto_rows = by_country[country]
        fp = DEST_DIR / f"{country}.yaml"
        merged_rows, n_preserved = _merge_with_existing(fp, auto_rows)
        merged_rows.sort(key=lambda r: r["party_short"].lower())
        body = yaml.safe_dump({"mappings": merged_rows}, sort_keys=False,
                              allow_unicode=True, default_flow_style=False)
        fp.write_text(header + body)
        n_mapped = sum(1 for r in merged_rows if r["partyfacts_id"] is not None)
        print(f"  {country}: {len(merged_rows)} pairs "
              f"({n_mapped} mapped, {n_preserved} hand-curated preserved) "
              f"→ {fp.relative_to(REPO)}")
    return 0


def _is_hand_entry(entry: dict) -> bool:
    """An entry is preserved across rebuilds when:

      * its ``source`` is in HAND_SOURCES (manual_review, verified_not_in_pf,
        hand_curated, europolls_harmonized), OR
      * it carries ``drop: true`` (an explicit hard/soft drop with a
        ``drop_reason`` recorded in the notes).
    """
    if entry.get("drop"):
        return True
    return str(entry.get("source") or "") in HAND_SOURCES


def _merge_with_existing(path: Path, auto_rows: list[dict]) -> tuple[list[dict], int]:
    """Load any hand-curated entries from ``path`` and overlay them on
    top of the auto-generated rows. Hand entries fully replace any
    colliding ``party_short`` from the auto layer; new hand-only entries
    are appended.

    Returns ``(merged_rows, n_preserved)`` where ``n_preserved`` is the
    count of hand entries kept.
    """
    if not path.exists():
        return list(auto_rows), 0
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        print(f"  ! could not parse existing {path.name}: {e}; rewriting from auto")
        return list(auto_rows), 0
    existing = loaded.get("mappings")
    if not isinstance(existing, list):
        return list(auto_rows), 0

    hand_by_short: dict[str, dict] = {}
    for entry in existing:
        if not isinstance(entry, dict):
            continue
        short = entry.get("party_short")
        if short is None:
            continue
        if _is_hand_entry(entry):
            hand_by_short[short] = entry

    out: list[dict] = []
    seen: set[str] = set()
    for row in auto_rows:
        short = row["party_short"]
        if short in hand_by_short:
            out.append(hand_by_short[short])
        else:
            out.append(row)
        seen.add(short)
    # Hand entries with no auto counterpart (e.g. _meta-driven labels
    # the auto pipeline never saw) get appended.
    for short, entry in hand_by_short.items():
        if short not in seen:
            out.append(entry)
    return out, len(hand_by_short)


if __name__ == "__main__":
    raise SystemExit(main())

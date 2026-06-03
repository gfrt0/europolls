"""Export the per-country party_short → partyfacts_id crosswalk in the YAML
shape europolls expects at config/party_mappings/{COUNTRY}.yaml.

Sources (in priority order):
  1. extend.py's Gemini output (data/processed/polls_crosswalk_gemini.csv)
     with reasoning, confidence, is_coalition flag — most informative.
  2. Layered base (data/processed/polls_partyfacts_crosswalk.csv) — Party
     Facts external-parties + WhoGov-minister-derived matches.
  3. europolls' own existing IT.yaml (kept as the highest-priority source
     for IT so we don't regress that file).

Output:
  data/processed/party_mappings_for_europolls/{COUNTRY}.yaml

Each mapping carries `partyfacts_id`, `confidence` (high/medium/low/null),
`source` (gemini_high|gemini_medium|gemini_low|partyfacts_external|whogov_
minister_match|europolls_harmonized), `is_coalition` (bool), and a `notes`
field with provenance / reasoning. Format compatible with europolls'
existing config/party_mappings/IT.yaml schema.
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
        rows = sorted(by_country[country], key=lambda r: r["party_short"].lower())
        fp = DEST_DIR / f"{country}.yaml"
        body = yaml.safe_dump({"mappings": rows}, sort_keys=False,
                              allow_unicode=True, default_flow_style=False)
        fp.write_text(header + body)
        n_mapped = sum(1 for r in rows if r["partyfacts_id"] is not None)
        print(f"  {country}: {len(rows)} pairs ({n_mapped} mapped) → {fp.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the full Europolls pipeline end to end.

Steps (each idempotent):
  1. fetch  — pull article wikitext for every (country, cycle) in
             config/countries.yaml, saving to data/raw/{COUNTRY}/{CYCLE}/
  2. parse  — extract long-format poll-party rows to data/interim/
  3. pivot  — produce wide CSVs in data/processed/{COUNTRY}_polls_wide.csv
  4. concat — concatenate all long CSVs into data/processed/polls_long.csv,
              joining config/party_mappings/{COUNTRY}.yaml to add
              partyfacts_id + provenance columns
  5. harmonize — for every country with a config/party_mappings/{COUNTRY}.yaml,
                 produce a harmonized long CSV in data/interim/harmonized/

Usage:
    python scripts/build_all.py
    python scripts/build_all.py --skip-fetch    # parse/pivot existing wikitext
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from europolls import harmonize             # noqa: E402


def step_fetch() -> None:
    from europolls import fetch as fetch_mod
    from europolls import parse as parse_mod
    for country, cfg in parse_mod.COUNTRY_CONFIG.items():
        cycles = list(cfg["cycles"].keys())
        try:
            fetch_mod.fetch_country(country, cycles)
        except Exception as e:
            print(f"  ! {country} fetch failed: {e}")


def step_parse() -> None:
    from europolls import parse as parse_mod
    for country in parse_mod.COUNTRY_CONFIG:
        try:
            parse_mod.run_country(country)
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ! {country} parse failed: {e}")


def step_pivot() -> None:
    from europolls import parse as parse_mod
    from europolls import pivot_wide
    for country in parse_mod.COUNTRY_CONFIG:
        try:
            pivot_wide.pivot_one(country)
        except Exception as e:
            print(f"  ! {country} pivot failed: {e}")


def step_concat_long() -> None:
    interim = ROOT / "data" / "interim"
    # Match {COUNTRY}_{CYCLE}.csv (country = 2-6 alphabetic) — avoids
    # picking up audit/report CSVs that other scripts drop in interim/.
    import re
    pattern = re.compile(r"^[A-Z][A-Z_]{1,5}_.+\.csv$")
    files = sorted(p for p in interim.glob("*.csv")
                   if p.is_file() and pattern.match(p.name))
    if not files:
        print("  no interim CSVs to concatenate")
        return
    # NB: keep_default_na=False so the literal party_short "NA"
    # (Greek 'New Left', GR_current 273 rows) is not silently turned
    # into pandas NaN. We then re-introduce only the empty string as
    # null for numeric-coerced columns later if needed.
    dfs = [pd.read_csv(p, low_memory=False, keep_default_na=False,
                       na_values=[""])
           for p in files]
    long = pd.concat(dfs, ignore_index=True)
    if "_table_idx" in long.columns:
        long = long.drop(columns=["_table_idx"])

    raw_count = len(long)
    long = _dedup_polls(long)
    dropped = raw_count - len(long)

    # Drop rows where the parser captured a vote_share but no
    # party_short — without a party label the row cannot be joined
    # downstream. Concentrated in GR_current / SI / LV / DE_current /
    # CH 2023 tables with non-standard column-header markup; see
    # europolls/parse.py for the upstream root cause.
    n_null_short = int(long["party_short"].isna().sum())
    if n_null_short:
        long = long[long["party_short"].notna()].copy()
        print(f"  dropped {n_null_short:,} rows with null party_short")

    long = _attach_partyfacts(long)

    out = ROOT / "data" / "processed" / "polls_long.csv"
    long.to_csv(out, index=False)
    print(f"  wrote {len(long):,} rows × {len(long.columns)} cols "
          f"(deduped {dropped:,} of {raw_count:,}) -> {out.relative_to(ROOT)}")


def _attach_partyfacts(long: pd.DataFrame) -> pd.DataFrame:
    """Join per-country party_mappings YAMLs onto the long frame.

    Adds partyfacts_id, partyfacts_id_source, partyfacts_id_confidence,
    partyfacts_id_notes, is_dropped_meta, dropped_meta_reason. ORs the YAML
    is_coalition flag onto the parse-time value.

    Two-stage drop semantics:
      * `drop: true`  → row stays in polls_long but flagged
                        ``is_dropped_meta = 1``.
      * `hard_drop: true` (in addition to drop) → row removed entirely
                        (currently: Resp.|Response rate).
      * Special-case BG ``Total`` → removed only when the same poll has
                        any non-meta party_short row alongside it (i.e.
                        the components are present and Total is
                        redundant).
    """
    mappings = harmonize.load_all_mappings()
    rows = []
    for country, m in mappings.items():
        for short, entry in m.items():
            rows.append({
                "country": country,
                "party_short": short,
                "partyfacts_id": entry["partyfacts_id"],
                "partyfacts_id_source": entry["source"],
                "partyfacts_id_confidence": entry["confidence"],
                "partyfacts_id_notes": entry["notes"] or None,
                "_yaml_is_coalition": entry["is_coalition"],
                "_yaml_drop": entry["drop"],
                "_yaml_drop_reason": entry["drop_reason"] or None,
            })
    if not rows:
        return long
    pf = pd.DataFrame(rows)
    merged = long.merge(pf, on=["country", "party_short"], how="left")

    yaml_coal = merged["_yaml_is_coalition"].fillna(False).astype(bool)
    parse_coal = merged["is_coalition"].fillna(False).astype(bool)
    # Preserve the parse-time flag — used by _apply_hard_drops to decide
    # which rows are aggregates reported alongside their components and
    # therefore safe to collapse. The merged column ORs parse + YAML for
    # downstream consumers (taxonomic 'is this a coalition?').
    merged["_parse_is_coalition"] = parse_coal
    merged["is_coalition"] = parse_coal | yaml_coal

    is_drop = merged["_yaml_drop"].fillna(False).astype(bool)
    merged["is_dropped_meta"] = is_drop.astype("Int64")
    merged["dropped_meta_reason"] = merged["_yaml_drop_reason"]
    # Drop columns that map to a real party_short while still keeping
    # provenance columns empty (a dropped meta row has no PF id).
    merged.loc[is_drop, ["partyfacts_id", "partyfacts_id_source",
                        "partyfacts_id_confidence", "partyfacts_id_notes"]] = pd.NA

    merged = merged.drop(columns=["_yaml_is_coalition", "_yaml_drop",
                                  "_yaml_drop_reason"])

    before = len(merged)
    merged = _apply_hard_drops(merged)
    after = len(merged)
    if before != after:
        print(f"  hard-dropped {before - after:,} meta rows")

    merged = _attach_canonical_name(merged)
    return merged


def _attach_canonical_name(merged: pd.DataFrame) -> pd.DataFrame:
    """Add partyfacts_name + party_canonical columns.

    ``partyfacts_name`` is PF's English name (falls back to native /
    name_short) for rows that carry a ``partyfacts_id``. Looked up from
    ``config/partyfacts_names.yaml`` (slim, repo-committed lookup; see
    ``scripts/build_partyfacts_names.py``).

    ``party_canonical`` is the **stable aggregation key** for downstream
    charts and joins: ``partyfacts_name`` when present, else
    ``party_short``. Lega + LN (both pf_id 1221) collapse to one
    canonical line; AVS (no PF id) stays as ``"AVS"``.
    """
    names_path = ROOT / "config" / "partyfacts_names.yaml"
    if not names_path.exists():
        # Pipeline can run without the lookup; degrade gracefully.
        merged["partyfacts_name"] = pd.NA
        merged["party_canonical"] = merged["party_short"]
        return merged

    with names_path.open() as f:
        import yaml
        data = yaml.safe_load(f) or {}
    name_by_id: dict[int, str] = {
        int(k): (v.get("name") or "")
        for k, v in (data.get("names") or {}).items()
    }
    merged["partyfacts_name"] = (
        merged["partyfacts_id"]
        .map(lambda v: name_by_id.get(int(v)) if pd.notna(v) else None)
    )
    merged["party_canonical"] = merged["partyfacts_name"].where(
        merged["partyfacts_name"].notna() & (merged["partyfacts_name"] != ""),
        merged["party_short"],
    )
    return merged


# Non-voting-intention drop_reasons: always remove from polls_long.csv.
# (approval / disapproval / lead / response_rate / total / coalition
# preference / parser artifacts / single-presidential-candidate columns
# are not party-level voting-intention signal.)
HARD_DROP_REASONS = {
    "approval", "lead", "response_rate", "total",
    "presidential_candidate", "parse_artifact",
}


def _apply_hard_drops(merged: pd.DataFrame) -> pd.DataFrame:
    """Prune polls_long to single-party voting-intention rows.

    Three rules:
      1. Drop rows whose YAML drop_reason is in HARD_DROP_REASONS
         (approval / lead / response_rate / total).
      2. Drop is_coalition=True rows when the same poll already has at
         least one single-party voting-intention row (non-coalition,
         not flagged is_dropped_meta). Polls with only coalition rows
         (e.g. DK Red/Blue bloc-only polls) are kept.
    """
    reason = merged["dropped_meta_reason"]
    hard_reason = reason.isin(HARD_DROP_REASONS)

    poll_key = ["country", "polldate_mid", "pollster", "wiki_revid"]
    is_drop = merged["is_dropped_meta"].fillna(0).astype(bool)
    # Collapse uses parse-time flag only — bloc labels (AVS, UnitedRight)
    # are taxonomically coalitions but reported as single observables in
    # the poll table, so YAML-only is_coalition must not trigger collapse.
    is_coal_for_collapse = merged["_parse_is_coalition"].fillna(False).astype(bool)
    is_coal_any = merged["is_coalition"].fillna(False).astype(bool)

    single_party = (~is_coal_any) & (~is_drop) & (~hard_reason) & merged["party_short"].notna()
    polls_with_singles = merged.loc[single_party, poll_key].drop_duplicates()
    polls_with_singles["_has_singles"] = True

    keyed = merged.merge(polls_with_singles, on=poll_key, how="left")
    has_singles = keyed["_has_singles"].fillna(False).astype(bool).to_numpy()
    coal_redundant = is_coal_for_collapse.to_numpy(dtype=bool) & has_singles

    drop_mask = hard_reason.to_numpy(dtype=bool) | coal_redundant
    out = merged.loc[~drop_mask].reset_index(drop=True)
    return out.drop(columns=["_parse_is_coalition"])


def _dedup_polls(long: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate poll-party rows.

    A duplicate is two rows sharing (country, polldate_mid, pollster,
    party_short). Two sources:
      1. Wikipedia editors copy each prior election's result into the next
         cycle's polling article as a baseline (cross-cycle dup).
      2. Some polling articles list the same poll in multiple wikitables —
         'Voting intention', 'Bloc support', 'Latest polls' (within-cycle dup).

    Keep one row per key, preferring the cycle whose election year is
    closest to the poll's mid-date year — that's the 'canonical' article
    for the poll. Ties broken by larger sample_size (more reliable), then
    by higher wiki_revid (more recent edit). Undated rows are passed
    through untouched (no key to dedup on).
    """
    if long.empty:
        return long

    # Split: undated rows can't be deduped on date; keep them as-is.
    has_date = long["polldate_mid"].notna()
    undated = long[~has_date]
    dated = long[has_date].copy()

    # Compute the 'cycle year' for each row from its cycle string. Use the
    # cycle's leading 4-digit year ('2024', '2021-Nov', 'current' is special).
    def _cycle_year(c: object) -> int:
        s = str(c)
        if s[:4].isdigit():
            return int(s[:4])
        # 'current' or other non-year-prefixed cycle: place far in the future
        # so it loses to any concrete cycle in the proximity tiebreaker.
        return 9999

    dated["_cycle_year"] = dated["cycle"].map(_cycle_year)
    poll_year = pd.to_datetime(dated["polldate_mid"], errors="coerce").dt.year
    dated["_year_dist"] = (dated["_cycle_year"] - poll_year).abs()
    # Tiebreakers: prefer larger sample, newer revid.
    dated["_neg_sample"] = -pd.to_numeric(dated["sample_size"], errors="coerce").fillna(0)
    dated["_neg_revid"] = -pd.to_numeric(dated["wiki_revid"], errors="coerce").fillna(0)
    dated = dated.sort_values(
        ["_year_dist", "_neg_sample", "_neg_revid"], kind="stable",
    )
    key = ["country", "polldate_mid", "pollster", "party_short"]
    dated = dated.drop_duplicates(subset=key, keep="first")
    dated = dated.drop(columns=["_cycle_year", "_year_dist", "_neg_sample", "_neg_revid"])

    return pd.concat([dated, undated], ignore_index=True)


def step_harmonize() -> None:
    map_dir = ROOT / "config" / "party_mappings"
    for mapping in sorted(map_dir.glob("*.yaml")):
        country = mapping.stem
        try:
            harmonize.harmonize(country)
        except Exception as e:
            print(f"  ! {country} harmonize failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true",
                    help="skip Wikipedia fetch step (use existing data/raw/ snapshots)")
    ap.add_argument("--only", choices=["fetch", "parse", "pivot", "concat", "harmonize"],
                    help="run only one step")
    args = ap.parse_args()

    steps = [
        ("fetch", step_fetch),
        ("parse", step_parse),
        ("pivot", step_pivot),
        ("concat", step_concat_long),
        ("harmonize", step_harmonize),
    ]
    for name, fn in steps:
        if args.only and args.only != name:
            continue
        if name == "fetch" and args.skip_fetch:
            continue
        print(f"\n=== {name} ===")
        t0 = time.time()
        fn()
        print(f"  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()

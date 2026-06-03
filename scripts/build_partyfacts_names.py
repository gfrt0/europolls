"""Build a slim PF id → display-name lookup for the ids referenced in
config/party_mappings/*.yaml.

Writes config/partyfacts_names.yaml with one entry per partyfacts_id
actually used by our crosswalk. The file is committed so the pipeline
runs without needing the full Party Facts dump on every machine.

Usage:
    python scripts/build_partyfacts_names.py \
        --pf-dir ../italgov/data/raw/partyfacts
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PF_DIR = ROOT.parent / "italgov" / "data" / "raw" / "partyfacts"
MAP_DIR = ROOT / "config" / "party_mappings"
OUT = ROOT / "config" / "partyfacts_names.yaml"


def used_pf_ids() -> set[int]:
    """Collect every non-null partyfacts_id from the per-country YAMLs."""
    ids: set[int] = set()
    for path in sorted(MAP_DIR.glob("*.yaml")):
        if path.stem == "_meta":
            continue
        doc = yaml.safe_load(path.read_text()) or {}
        mappings = doc.get("mappings")
        if not mappings:
            continue
        # Both list and dict shapes.
        entries = (
            mappings.values() if isinstance(mappings, dict) else mappings
        )
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            pf = entry.get("partyfacts_id")
            if pf is None:
                continue
            try:
                ids.add(int(pf))
            except (TypeError, ValueError):
                continue
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pf-dir", type=Path, default=DEFAULT_PF_DIR)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    core_path = args.pf_dir / "core-parties.csv"
    if not core_path.exists():
        raise SystemExit(f"missing {core_path}")
    core = pd.read_csv(core_path, low_memory=False)

    needed = used_pf_ids()
    print(f"  {len(needed):,} distinct partyfacts_id values used by crosswalk")

    sub = core[core["partyfacts_id"].isin(needed)].copy()
    missing = needed - set(sub["partyfacts_id"].astype(int))
    if missing:
        print(f"  ! {len(missing)} pf_ids referenced but missing from core: "
              f"{sorted(missing)[:10]}...")

    entries: dict[int, dict] = {}
    def _str(v: object) -> str | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        return s or None

    for _, r in sub.iterrows():
        pf_id = int(r["partyfacts_id"])
        # name_english is the most consumer-friendly; fall back to
        # name (native) when absent. name_short is included for chart
        # ticks (compact label).
        name_english = _str(r.get("name_english"))
        name = _str(r.get("name"))
        name_short = _str(r.get("name_short"))
        # display = name_english if present, else name (native), else
        # name_short, else null.
        display = name_english or name or name_short
        entries[pf_id] = {
            "name": display,
            "name_native": name,
            "name_short": name_short,
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Party Facts id → display-name lookup, slim to ids used in\n"
        "# config/party_mappings/*.yaml. Regenerate with:\n"
        "#   python scripts/build_partyfacts_names.py\n"
        "#\n"
        "# `name` field rule: name_english if PF has it, else native name,\n"
        "# else name_short. Used by build_all.py's concat step to populate\n"
        "# the partyfacts_name + party_canonical columns on polls_long.csv.\n\n"
    )
    body = yaml.safe_dump(
        {"names": dict(sorted(entries.items()))},
        sort_keys=False, allow_unicode=True, default_flow_style=False, width=999,
    )
    args.out.write_text(header + body)
    print(f"  wrote {len(entries):,} pf-id entries → {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

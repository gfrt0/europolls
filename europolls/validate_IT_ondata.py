"""Validate the Italian Wikipedia poll scrape against ondata's
sondaggipoliticoelettorali.it mirror as ground truth.

Strategy:
1. Map both sides to a common (year-month, pollster_norm, partyfacts_id) key.
2. Intersect; on overlapping cells, compare vote shares.
3. Report match rate, mean absolute discrepancy, and outliers.

Why this key:
- Dates differ between sources (Wikipedia uses fieldwork dates, ondata uses
  filing date with the government register). Year-month is the coarsest key
  that still pins each poll to a unique pollster×month slot.
- Pollster names differ in form (`SWG` vs `swg`, `Tecnè` vs `tecnè srl`).
  We lowercase + drop ' srl' / spaces.
- Party names differ in language. ondata uses Italian full names; we map
  them to partyfacts_id via a small hard-coded dictionary and reuse the
  Wikipedia harmonized partyfacts_id on the other side.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WIKI_LONG = ROOT / "data" / "interim" / "harmonized" / "IT_long_harmonized.csv"
ONDATA_ANAG = ROOT / "data" / "raw" / "ondata_italian_polls" / "anagrafica.csv"
ONDATA_RIS = ROOT / "data" / "raw" / "ondata_italian_polls" / "risultati.csv"

# Italian full-name → partyfacts_id (mirrors the IT YAML's pf ids).
ONDATA_PARTY_MAP = {
    "Partito Democratico":         802,
    "Movimento 5 Stelle":          2046,
    "Forza Italia":                8058,
    "Lega":                        1221,
    "Fratelli d'Italia":           2280,
    "+Europa":                     6155,
    "Italia Viva":                 8641,
    "Azione":                      9082,
    "Alleanza Verdi Sinistra":     None,   # not yet in PF (AVS, 2022 coalition)
    "Sinistra Ecologia Libertà":   7031,
    "Unione di Centro":            1758,
    "Scelta Civica":               2281,
    "Unione Popolare":             None,   # UP 2022 — not yet linked
    "Pace Terra Dignità":          None,   # PTD — not yet linked
    "Altri":                       None,   # 'Others' — meta, drop
    "Italexit":                    9081,
    "Articolo 1":                  8361,
    "Liberi e Uguali":             8246,
    "Coraggio Italia":             None,
}


def normalize_pollster(name: str) -> str:
    """Coarse pollster normalization for cross-source matching."""
    n = name.lower().strip()
    # Drop common Italian company suffixes.
    n = re.sub(r"\b(srl|s\.r\.l\.|s\.p\.a\.|spa|s\.a\.s\.|sas)\b", "", n)
    # Drop punctuation and extra whitespace.
    n = re.sub(r"[^\w]+", "", n)
    return n.strip()


def load_wiki() -> dict[tuple, float]:
    """Returns {(yyyy-mm, pollster_norm, partyfacts_id): vote_share}."""
    out: dict[tuple, list[float]] = defaultdict(list)
    with WIKI_LONG.open() as f:
        for row in csv.DictReader(f):
            if row.get("is_dropped_meta") == "1":
                continue
            if not row.get("partyfacts_id"):
                continue
            ym = row.get("polldate_mid", "")[:7]
            if not ym:
                continue
            pollster_n = normalize_pollster(row["pollster"])
            if not pollster_n:
                continue
            try:
                share = float(row["vote_share"])
            except (TypeError, ValueError):
                continue
            key = (ym, pollster_n, int(row["partyfacts_id"]))
            out[key].append(share)
    # If a (key) has multiple shares (multi-poll-in-month), average them.
    return {k: sum(v) / len(v) for k, v in out.items()}


def load_ondata() -> dict[tuple, float]:
    # First, anagrafica: n → (pollster_normalized, ym)
    poll_meta = {}
    with ONDATA_ANAG.open() as f:
        for row in csv.DictReader(f):
            ym = row["data_inserimento"][:7]
            pollster_n = normalize_pollster(row.get("realizzatore_normalizzato") or row["realizzatore"])
            poll_meta[row["n"]] = (ym, pollster_n)
    # Then risultati: aggregate to common key.
    out: dict[tuple, list[float]] = defaultdict(list)
    with ONDATA_RIS.open() as f:
        for row in csv.DictReader(f):
            meta = poll_meta.get(row["n"])
            if meta is None:
                continue
            ym, pollster_n = meta
            pfid = ONDATA_PARTY_MAP.get(row["partito"])
            if pfid is None:
                continue
            try:
                share = float(row["valore"])
            except ValueError:
                continue
            out[(ym, pollster_n, pfid)].append(share)
    return {k: sum(v) / len(v) for k, v in out.items()}


def main() -> None:
    wiki = load_wiki()
    onda = load_ondata()
    print(f"Wikipedia keys (year-month × pollster × partyfacts_id): {len(wiki):,}")
    print(f"ondata    keys: {len(onda):,}")

    common = set(wiki) & set(onda)
    print(f"common keys: {len(common):,}")
    print()

    if not common:
        print("no overlap — check key normalization")
        return

    discrepancies = []
    for k in common:
        d = wiki[k] - onda[k]
        discrepancies.append((k, wiki[k], onda[k], d))

    import statistics
    abs_d = [abs(x[3]) for x in discrepancies]
    print(f"=== match statistics ===")
    print(f"  mean abs discrepancy: {statistics.mean(abs_d):.3f} pp")
    print(f"  median:               {statistics.median(abs_d):.3f} pp")
    print(f"  max:                  {max(abs_d):.3f} pp")
    print(f"  cells with |Δ|<=0.5 pp: {sum(1 for d in abs_d if d<=0.5)} ({sum(1 for d in abs_d if d<=0.5)/len(abs_d)*100:.1f}%)")
    print(f"  cells with |Δ|<=1.0 pp: {sum(1 for d in abs_d if d<=1.0)} ({sum(1 for d in abs_d if d<=1.0)/len(abs_d)*100:.1f}%)")
    print(f"  cells with |Δ|<=2.0 pp: {sum(1 for d in abs_d if d<=2.0)} ({sum(1 for d in abs_d if d<=2.0)/len(abs_d)*100:.1f}%)")
    print()

    discrepancies.sort(key=lambda x: -abs(x[3]))
    print(f"=== top 15 outliers ===")
    print(f"  {'YearMonth':<8} {'Pollster':<25} {'PFID':>5}  {'WIKI':>6}  {'ONDATA':>6}  {'Δ':>6}")
    for (ym, pn, pf), w, o, d in discrepancies[:15]:
        print(f"  {ym:<8} {pn[:25]:<25} {pf:>5}  {w:>6.1f}  {o:>6.1f}  {d:>+6.1f}")


if __name__ == "__main__":
    main()

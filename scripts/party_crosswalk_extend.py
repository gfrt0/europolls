"""Boost the (country, party_short) → partyfacts_id crosswalk for europolls.

Pipeline:

  1. Filter out non-party noise (Others, Abstention, Response rate, etc.) —
     these get a status='non_party' marker, NOT a partyfacts_id.

  2. For each remaining unmapped (country, party_short), ask Gemini 2.5 Flash
     on Vertex to match against the country's known partyfacts_id universe
     (from Party Facts external-parties.csv). The model returns one of:
       - high  : confident match (party_short clearly maps to one partyfacts_id)
       - medium: plausible match (likely but not certain)
       - low   : weak match (worth manual review)
       - no_match: nothing fits (likely new party not yet in Party Facts)

  3. Emit:
       data/processed/polls_partyfacts_crosswalk_extended.csv
         all (country, party_short, partyfacts_id, source, confidence) rows,
         union of the existing crosswalk + new Gemini matches.
       data/processed/polls_crosswalk_review_queue.csv
         low-confidence cases only, for human spot-check before publishing.

Outputs are designed to be contributed back to europolls'
config/party_mappings/ once reviewed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import random
import sys
import time
from enum import Enum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

REPO = Path(__file__).resolve().parents[1]
# europolls' processed polls table; carries one row per (poll, party, vote_share).
POLLS_SRC = REPO / "data" / "processed" / "polls_long.csv"
# Optional seed: an existing partyfacts crosswalk produced by earlier passes
# (e.g., the Party Facts external-parties baseline + WhoGov-minister-matched
# entries imported from italgov's pipeline). Pass via --seed-crosswalk.
EXISTING_CW = REPO / "data" / "interim" / "party_crosswalk_seed.csv"
# Party Facts external-parties file. Download from
# https://partyfacts.herokuapp.com/download/external-parties-csv/
EXTERNAL_PARTIES = REPO / "data" / "raw" / "partyfacts" / "external-parties.csv"
DEST = REPO / "data" / "interim"

MODEL = "gemini-2.5-flash"
MAX_RETRIES = 3

ISO2_TO_ISO3 = {"AT":"AUT","BE":"BEL","BG":"BGR","CH":"CHE","CY":"CYP",
                "CZ":"CZE","DE":"DEU","DK":"DNK","EE":"EST","ES":"ESP",
                "FI":"FIN","FR":"FRA","FR_LEG":"FRA","GR":"GRC",
                "HR":"HRV","HU":"HUN","IE":"IRL","IS":"ISL","IT":"ITA",
                "LT":"LTU","LU":"LUX","LV":"LVA","MT":"MLT","NL":"NLD",
                "NO":"NOR","PL":"POL","PT":"PRT","RO":"ROU","SE":"SWE",
                "SI":"SVN","SK":"SVK","UK":"GBR"}

# Pattern-based exclusions: not parties (poll-table metadata).
NON_PARTY_SHORT = {
    "Others", "Others.", "Other", "Oth.", "Oth", "O", "Others / Don't know",
    "Others/Don't know", "Others /Don't know", "Don't know", "DK",
    "Abstention", "Abs.", "Abs", "Abst.", "Resp.", "Response rate", "Response",
    "Sample", "Lead", "Margin", "Spread", "Diff", "Difference",
    "None", "NA", "N/A", "Undecided", "Refused", "Und.", "Und",
    "Approve", "Disapprove", "Approval", "Don't vote", "Won't vote",
    "Spoiled", "Blank", "Invalid", "Total", "Sum",
    "No opinion", "Neither", "Not vote", "Net", "Gov.", "Gov",
    "P",  # single-letter "P" is poll-table noise in multiple countries
}

# Substring patterns that suggest non-party metadata (catch "|Abstention",
# "|Response rate", "|Don't know" composite labels).
NON_PARTY_SUBSTRINGS = (
    "abstention", "response rate", "don't know", "no opinion",
    "undecided", "refused", "blank", "invalid",
)

# Coalitions / aggregate labels that represent multiple parties.
COALITION_HINTS = ("Coalition", "Coalición", "Alliance",
                   "Block", "Bloque", "Union", "Unión")


def _is_non_party(s: str) -> bool:
    if s.strip() in NON_PARTY_SHORT:
        return True
    low = s.lower()
    return any(sub in low for sub in NON_PARTY_SUBSTRINGS)


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    no_match = "no_match"


class CrosswalkSuggestion(BaseModel):
    country: str
    party_short: str = Field(..., description="Verbatim from the prompt.")
    matched_partyfacts_id: int | None = Field(
        None,
        description="The chosen partyfacts_id. Null when no_match.",
    )
    matched_name: str | None = Field(
        None,
        description="The Party Facts canonical party name corresponding to "
                    "matched_partyfacts_id. Null when no_match.",
    )
    confidence: Confidence = Field(
        ...,
        description=("high   : same party with near-certain mapping (incl. renames "
                     "where the political continuity is well-established, e.g. "
                     "PdL → FI in Italy).\n"
                     "medium : likely the same party but some ambiguity "
                     "(could be a related faction or coalition member).\n"
                     "low    : weak match, needs human review.\n"
                     "no_match: not in Party Facts (likely new post-2020 party or "
                     "a coalition aggregate that should be left unmapped)."),
    )
    is_coalition: bool = Field(
        False,
        description="True if the abbreviation refers to a coalition or alliance "
                    "aggregate (e.g., Spanish 'Unidas Podemos', Polish 'UnitedRight'). "
                    "When True, matched_partyfacts_id may point to the dominant member.",
    )
    reasoning: str = Field(default="", description="One-sentence justification.")


def _looks_like_coalition(s: str) -> bool:
    return any(h in s for h in COALITION_HINTS)


def _candidate_universe(country: str) -> list[dict]:
    """All partyfacts entries for a country, deduped by partyfacts_id."""
    iso3 = ISO2_TO_ISO3.get(country)
    if iso3 is None:
        return []
    ep = pd.read_csv(EXTERNAL_PARTIES, low_memory=False)
    ep = ep[(ep.country == iso3) & ep.partyfacts_id.notna()].copy()
    ep["partyfacts_id"] = ep.partyfacts_id.astype(int)
    # Per partyfacts_id, take the row with the most informative name set.
    ep["info_score"] = ep[["name_short", "name", "name_english"]].notna().sum(axis=1)
    ep = ep.sort_values("info_score", ascending=False).drop_duplicates("partyfacts_id")
    cols = ["partyfacts_id", "name_short", "name", "name_english",
            "year_first", "year_last", "description"]
    return ep[cols].fillna("").to_dict("records")


def _build_prompt(country: str, party_short: str, examples: list[dict],
                  candidates: list[dict]) -> str:
    lines = [
        f"Match a Wikipedia-derived party abbreviation to a Party Facts ID.",
        f"",
        f"Country: {country} (ISO2)",
        f"Party-short label seen in europolls: {party_short!r}",
        f"",
        f"Polling-context evidence for this label (sample of recent polls):",
    ]
    for ex in examples[:5]:
        lines.append(f"  • {ex['polldate_mid']} | {ex['pollster']} | "
                     f"vote_share={ex['vote_share']}")
    lines += [
        "",
        f"Candidate Party Facts entries for {country} (choose the best match if any):",
    ]
    for c in candidates[:80]:
        yr = f"{c['year_first']}-{c['year_last']}" if c['year_first'] else ""
        lines.append(f"  • id={c['partyfacts_id']} | short={c['name_short']!r} | "
                     f"name={c['name']!r} | en={c['name_english']!r} | {yr}")
    lines += [
        "",
        f"Decide which Party Facts ID (if any) is the right semantic match for "
        f"{party_short!r}.",
        f"",
        f"Guidance:",
        f"  - Apply political-continuity reasoning: a renamed party should still",
        f"    match its predecessor's ID (e.g., Italian PdL → FI continuity).",
        f"  - For FR presidential polls, labels often glue candidate name to",
        f"    party abbreviation: 'RoyalPS' = Ségolène Royal + PS (Socialiste)",
        f"    → match to PS. 'BayrouUDF' = Bayrou + UDF → match UDF.",
        f"    'LepageCap21' = Lepage + Cap21 → match Cap21. Strip the candidate",
        f"    prefix and identify the party suffix.",
        f"  - For coalition or alliance aggregates, set is_coalition=true and",
        f"    map to the dominant member, or 'no_match' if no clear dominant.",
        f"  - Set confidence='no_match' only for genuinely new parties not in",
        f"    the candidate list (e.g., post-2022 platforms).",
        f"  - Set confidence='low' when you have a guess but real ambiguity",
        f"    (this flags it for human review).",
    ]
    return "\n".join(lines)


def _crosswalk_one(client, country: str, party_short: str,
                   examples: list[dict], candidates: list[dict],
                   retries: int = MAX_RETRIES) -> CrosswalkSuggestion:
    prompt = _build_prompt(country, party_short, examples, candidates)
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = _api_call_with_backoff(client, prompt)
            return resp.parsed  # type: ignore
        except ValidationError as e:
            last_err = e
            prompt += f"\n\nPREVIOUS RESPONSE FAILED VALIDATION: {e}. Re-emit."
    raise RuntimeError(f"{country}/{party_short}: {last_err}")


def _api_call_with_backoff(client, prompt: str):
    for attempt in range(7):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": CrosswalkSuggestion,
                    "temperature": 0,
                    "max_output_tokens": 4096,
                    "thinking_config": {"thinking_budget": 0},
                },
            )
        except ValidationError:
            raise
        except Exception as e:
            s = str(e).lower()
            if attempt == 6 or not any(t in s for t in ("429", "quota", "503", "500", "deadline")):
                raise
            time.sleep(4.0 * (2 ** attempt) + random.uniform(0, 2))
    raise RuntimeError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="us-central1")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only-country", help="Limit to one ISO2 (debugging)")
    ap.add_argument("--limit", type=int, help="Process only first N pairs")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute unmapped set + noise filter, do not call Gemini")
    ap.add_argument("--retry-only", action="store_true",
                    help="Only retry pairs that previously hit no_match or low; "
                         "keep existing high/medium gemini matches.")
    args = ap.parse_args()

    polls = pd.read_csv(POLLS_SRC, low_memory=False,
                        parse_dates=["polldate_mid"])
    existing = pd.read_csv(EXISTING_CW)
    mapped_set = set(zip(existing.country, existing.party_short))

    # In --retry-only mode, also subtract previously-matched gemini high pairs.
    prev_gemini_path = DEST / "polls_crosswalk_gemini.csv"
    prev_high_set: set = set()
    if args.retry_only and prev_gemini_path.exists():
        prev = pd.read_csv(prev_gemini_path)
        prev_high = prev[prev.confidence.isin(["high", "medium"])
                         & prev.matched_partyfacts_id.notna()]
        prev_high_set = set(zip(prev_high.country, prev_high.party_short))
        print(f"--retry-only: skipping {len(prev_high_set):,} pairs already matched (high/medium)")
        mapped_set = mapped_set | prev_high_set

    polls["_pair"] = list(zip(polls.country, polls.party_short))
    unmapped_rows = polls[~polls["_pair"].isin(mapped_set)].copy()

    # Per (country, party_short) aggregate: count + sample.
    pairs = (unmapped_rows.groupby(["country", "party_short"])
                          .size().reset_index(name="n_rows"))

    # Apply noise filter.
    pairs["is_non_party"] = pairs.party_short.apply(_is_non_party)
    pairs["looks_coalition"] = pairs.party_short.apply(_looks_like_coalition)
    non_party = pairs[pairs.is_non_party]
    parties = pairs[~pairs.is_non_party]

    print(f"unmapped pairs:           {len(pairs):,}")
    print(f"  noise (non-party):      {len(non_party):,} ({non_party.n_rows.sum():,} poll rows)")
    print(f"  remaining to crosswalk: {len(parties):,} ({parties.n_rows.sum():,} poll rows)")

    if args.only_country:
        parties = parties[parties.country == args.only_country]
        print(f"  --only-country {args.only_country}: {len(parties):,}")
    parties = parties.sort_values("n_rows", ascending=False)
    if args.limit:
        parties = parties.head(args.limit)

    if args.dry_run:
        parties.to_csv(DEST / "polls_crosswalk_unmapped.csv", index=False)
        print(f"→ data/processed/polls_crosswalk_unmapped.csv ({len(parties):,} rows)")
        return 0

    try:
        from google import genai
    except ImportError:
        print("error: pip install google-genai", file=sys.stderr)
        return 1
    client = genai.Client(vertexai=True, project=args.project, location=args.location)

    # Pre-load candidate universes per country (avoid re-reading external-parties).
    print("\nloading candidate universes per country...")
    universes = {}
    for c in parties.country.unique():
        universes[c] = _candidate_universe(c)
        print(f"  {c}: {len(universes[c])} candidates")

    # Build per-pair example sets (5 sample poll rows each).
    def examples_for(country, party_short):
        sub = unmapped_rows[(unmapped_rows.country == country)
                            & (unmapped_rows.party_short == party_short)]
        sample = sub.sample(min(5, len(sub)), random_state=0)
        return sample[["polldate_mid", "pollster", "vote_share"]].to_dict("records")

    print(f"\ncrosswalking {len(parties):,} pairs via Gemini Flash...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_pair = {}
        for _, r in parties.iterrows():
            future = pool.submit(_crosswalk_one, client, r.country, r.party_short,
                                 examples_for(r.country, r.party_short),
                                 universes.get(r.country, []))
            future_to_pair[future] = (r.country, r.party_short, r.n_rows)
        for fut in concurrent.futures.as_completed(future_to_pair):
            country, party_short, n_rows = future_to_pair[fut]
            try:
                s = fut.result()
            except Exception as e:
                print(f"  ! {country}/{party_short}: {e}", file=sys.stderr)
                results.append({"country": country, "party_short": party_short,
                                "n_rows": n_rows, "matched_partyfacts_id": None,
                                "matched_name": "", "confidence": "no_match",
                                "is_coalition": False, "reasoning": f"error: {e}"[:200]})
                continue
            results.append({
                "country": country, "party_short": party_short, "n_rows": n_rows,
                "matched_partyfacts_id": s.matched_partyfacts_id,
                "matched_name": s.matched_name or "",
                "confidence": s.confidence.value,
                "is_coalition": s.is_coalition,
                "reasoning": s.reasoning,
            })

    new_cw = pd.DataFrame(results)
    # In retry mode, merge with previous gemini results (keep prior high/medium).
    if args.retry_only and prev_gemini_path.exists():
        prev = pd.read_csv(prev_gemini_path)
        keep_prev = prev[~prev.set_index(["country", "party_short"]).index.isin(
            set(zip(new_cw.country, new_cw.party_short)))]
        new_cw = pd.concat([keep_prev, new_cw], ignore_index=True)
    new_cw.to_csv(DEST / "polls_crosswalk_gemini.csv", index=False)
    print(f"\ngemini results: {len(new_cw)} total")
    print(new_cw.confidence.value_counts().to_string())

    # Build the unified extended crosswalk: existing + new high/medium matches.
    accept = new_cw[(new_cw.confidence.isin(["high", "medium"]))
                    & new_cw.matched_partyfacts_id.notna()].copy()
    accept = accept.rename(columns={"matched_partyfacts_id": "partyfacts_id"})
    accept["source"] = "gemini_" + accept.confidence
    accept["partyfacts_id"] = accept.partyfacts_id.astype(int)
    ext = pd.concat([existing.assign(source=existing.get("source", "existing"))
                             [["country", "party_short", "partyfacts_id", "source"]],
                     accept[["country", "party_short", "partyfacts_id", "source"]]],
                    ignore_index=True).drop_duplicates(["country", "party_short"], keep="first")
    ext.to_csv(DEST / "polls_partyfacts_crosswalk_extended.csv", index=False)

    review = new_cw[new_cw.confidence == "low"].sort_values("n_rows", ascending=False)
    review.to_csv(DEST / "polls_crosswalk_review_queue.csv", index=False)
    print(f"\nextended crosswalk: {len(ext):,} pairs")
    print(f"review queue (low confidence): {len(review):,}")
    print(f"\n→ {DEST.relative_to(REPO)}/polls_partyfacts_crosswalk_extended.csv")
    print(f"→ {DEST.relative_to(REPO)}/polls_crosswalk_review_queue.csv")
    print(f"→ {DEST.relative_to(REPO)}/polls_crosswalk_gemini.csv (all suggestions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

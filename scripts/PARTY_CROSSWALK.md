# Party crosswalk → partyfacts_id

`scripts/party_crosswalk_extend.py` + `scripts/party_crosswalk_build.py`
derive a `(country, party_short) → partyfacts_id` mapping for every party
that appears in `data/processed/polls_long.csv`, and emit per-country
YAML files at `config/party_mappings/{COUNTRY}.yaml`.

This is the bridge that lets europolls consumers join poll rows to
external party-level datasets keyed by Party Facts (PopuList, MARPOR,
CHES, V-Dem, V-Party, manifesto, etc.).

## Status

Initial seed (this commit): **31 country YAMLs** (everything except IT,
which is hand-curated upstream) auto-generated from the original italgov
pipeline. ~5,400 mapped pairs out of ~6,000; the rest are genuine
`no_match` cases (mostly post-2022 platforms Party Facts hasn't yet
ingested) and 2 low-confidence Gemini calls flagged for human review.

Coverage on actual poll signal:

  - **≥95%** of poll rows mapped: LT, NL, SE, DE, ES, HU, IE, FI
  - **85–94%**: GR, LU, FR_LEG, BE, FR, IT, CZ, SK, UK, IS, BG
  - **75–84%**: LV, DK, NO, AT, RO, SI, PT, HR, PL
  - **<75%**: EE, CH, MT, CY (small countries with idiosyncratic party-
    label conventions)

Overall **88.8% of poll rows** carry a partyfacts_id at this seed.

## Files

```
config/party_mappings/
    IT.yaml         hand-curated upstream (predates this work)
    {31 others}     auto-generated; see header comments for provenance
```

Each entry:

```yaml
- party_short: FdI
  partyfacts_id: 2280
  source: gemini_high      # or partyfacts_external, whogov_minister_match, etc.
  confidence: high         # null when from non-LLM source
  is_coalition: false
  notes: "Brothers of Italy — direct match"
```

## Regenerating

The extract pipeline runs on Gemini 2.5 Flash via Vertex AI:

```bash
# 1. Drop Party Facts external-parties.csv into data/raw/partyfacts/
curl -o data/raw/partyfacts/external-parties.csv \
    "https://partyfacts.herokuapp.com/download/external-parties-csv/"

# 2. (Optional) Seed crosswalk: drop a CSV of confident (country,
#    party_short, partyfacts_id) at data/interim/party_crosswalk_seed.csv
#    to skip Gemini calls for already-known pairs. Format:
#      country,party_short,partyfacts_id,source

# 3. Run Gemini extension on every unmapped pair.
python scripts/party_crosswalk_extend.py \
    --project YOUR_GCP_PROJECT --location us-central1

# 4. Build per-country YAMLs.
python scripts/party_crosswalk_build.py
```

Cost: ~$2 for full 32-country sweep (Gemini 2.5 Flash on Vertex).

## Caveats

  - `is_coalition: true` rows point at the dominant member's
    partyfacts_id — lossy approximation. Downstream consumers should
    expand or flag-filter these.
  - `source: gemini_*` entries should be spot-checked. They're high-
    recall but Gemini can hallucinate matches when the candidate list
    is sparse (especially for very new parties).
  - `IT.yaml` was hand-curated before this auto-pipeline existed. Diff
    against an auto-generated version before any overwrite.

## Provenance

This pipeline was developed inside the [italgov](https://github.com/gfrt0/italgov)
repo's cabinet-month polling panel. The italgov-specific dependency was a
WhoGov-minister-matched seed crosswalk derived from cabinet ministers'
`partyfacts_id` in WhoGov v3.1. That seed has been removed from the
europolls version; the same coverage can be approximated by running the
Gemini step alone or by feeding a seed CSV.

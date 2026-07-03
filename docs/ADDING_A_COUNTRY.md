# Adding a country to Europolls

A step-by-step recipe for extending Europolls to a new country (e.g. Norway, Switzerland, Iceland, Czechia, Hungary, Poland, Slovenia, Croatia, Romania, Bulgaria, Estonia, Latvia, Lithuania, or any of the others listed at https://en.wikipedia.org/wiki/Category:Opinion_polling_by_country).

You're done when:
- The country appears as a tab on https://gfrt0.github.io/europolls/
- Its top parties match what you'd expect from a glance at the source Wikipedia article
- The wide-table view shows clean party columns
- The chart shows colored lines per party

The whole process is **3 config files + (optionally) one HTML constant** — no Python edits required for a typical addition. Optional alias / drop / coalition / tooltip-name config can be added incrementally as Wikipedia table quirks surface.

**YAML gotcha:** if your ISO2 code is `NO` (Norway), quote it as `"NO":` in YAML — unquoted it parses as boolean `False` and the entire country block is silently dropped at config load.

---

## Step 1 — find the Wikipedia article naming convention

For each EU/euro-adjacent country, English Wikipedia uses a consistent article title for opinion polling:

> "Opinion polling for the {YEAR} {Adjective} {election_type} election"

Examples (test these by pasting into the Wikipedia search bar):
- Norway: `Opinion polling for the 2025 Norwegian parliamentary election`
- Switzerland: `Opinion polling for the 2023 Swiss federal election`
- Iceland: `Opinion polling for the 2024 Icelandic parliamentary election`
- Poland: `Opinion polling for the 2023 Polish parliamentary election`
- Hungary: `Opinion polling for the 2026 Hungarian parliamentary election`
- Czechia: `Opinion polling for the 2025 Czech parliamentary election`

The "next" cycle (post-most-recent-election) is at:

> "Opinion polling for the next {Adjective} {election_type} election"

For each candidate country, write down:
1. The exact adjective Wikipedia uses (Norwegian / Swiss / Icelandic / Polish / Hungarian / Czech / …).
2. The election type word (parliamentary / federal / legislative / general).
3. The years of the **last 4–5 elections**. The article only exists for actual past elections (and the upcoming one).

If a country has had **two elections in a single year** (Greece 2012, 2015, 2023; Spain 2019), use cycle IDs like `2023-May` / `2023-Jun` and supply explicit titles per cycle — see Greece's entry in `config/countries.yaml` for the template.

If a country has **separate presidential and parliamentary** pages (France presidential vs FR_LEG legislative), pick the parliamentary one (it's what feeds into cabinet politics).

---

## Step 2 — add the country to `config/countries.yaml`

Use this template:

```yaml
# CC = ISO2 code (Wikipedia uses the country adjective rather than ISO codes,
# but we key by ISO2 for the dataset.)
CC:
  title_for_cycle: "Opinion polling for the {cycle} ADJECTIVE ELECTION_TYPE election"
  title_current:   "Opinion polling for the next ADJECTIVE ELECTION_TYPE election"
  # Optional. List of party_short labels that should be treated as coalition
  # aggregates rather than individual parties. Lower-case.
  coalition_shorts: []
  cycles:
    current: 2026                       # fallback year for "current" cycle
    "YYYY": YYYY                        # past elections; cycle_id = year string,
                                        # value = year int (same year)
    # ...
    # Multi-election year? Use explicit title overrides:
    "YYYY-MonthA":
      year: YYYY
      title: "Opinion polling for the Month A YYYY ADJECTIVE ELECTION_TYPE election"
    "YYYY-MonthB":
      year: YYYY
      title: "Opinion polling for the Month B YYYY ADJECTIVE ELECTION_TYPE election"
```

**Concrete example — Norway:**

```yaml
NO:
  title_for_cycle: "Opinion polling for the {cycle} Norwegian parliamentary election"
  title_current:   "Opinion polling for the next Norwegian parliamentary election"
  cycles:
    current: 2029
    "2025": 2025
    "2021": 2021
    "2017": 2017
    "2013": 2013
```

**Concrete example — Poland (with single-year 2007/2011/2015 etc.):**

```yaml
PL:
  title_for_cycle: "Opinion polling for the {cycle} Polish parliamentary election"
  title_current:   "Opinion polling for the next Polish parliamentary election"
  cycles:
    current: 2027
    "2023": 2023
    "2019": 2019
    "2015": 2015
    "2011": 2011
    "2007": 2007
```

---

## Step 3 — add party colors in `config/party_colors.yaml`

Each party gets a hex color (no `#`). Wikipedia uses standard party colors documented at e.g. https://en.wikipedia.org/wiki/Template:Political_party (search "Norwegian political parties color" or similar).

```yaml
NO:
  Ap:      D2122F   # Labour
  H:       0064B0   # Conservatives
  FrP:     191970   # Progress
  Sp:      006A33   # Centre
  SV:      9F1B1B   # Socialist Left
  R:       8B0000   # Red
  V:       5BAD3A   # Liberal
  KrF:     0067B3   # Christian Democrats
  MDG:     578C30   # Greens
  INP:     2E6FDB   # Industry & Business
  Others:  AAAAAA
```

Long-tail parties without an explicit color get a deterministic hash-based fallback in JS, so undocumented parties will still render.

---

## Step 4 — register the country's display name in `web/index.html`

Find the `COUNTRY_NAMES` constant near the top of the `<script>` block and add an entry:

```js
const COUNTRY_NAMES = {
  IT: "Italy",
  // ...
  NO: "Norway",          // <- add this
  PL: "Poland",
};
```

This controls the human-readable label on the country tab.

---

## Step 5 — first-pass build and curation

Locally:

```bash
# Fetch this one country to verify the titles resolve
python -m europolls.fetch NO  # 'all' to do every country

# Parse it
python -m europolls.parse NO

# Inspect the long output
python - <<EOF
import pandas as pd
df = pd.read_csv("data/processed/polls_long.csv", low_memory=False)
sub = df[df.country == "NO"]
print(f"NO rows: {len(sub):,}, parties: {sub.party_short.nunique()}")
print(sub.groupby("party_short").size().sort_values(ascending=False).head(20))
EOF
```

Use this to sanity-check that party shorts look right and to add colors / display names for any that aren't already covered — see Steps 3 and 4. The web UI picks its default-visible party set automatically (top-6 by lifetime obs count ∪ top-6 by mean vote share in the last 365 days), so no per-country column list is needed.

---

## Step 6 — verify locally, then push

```bash
# Pivot wides + concat polls_long
python scripts/build_all.py --skip-fetch     # if you've already fetched

# Build web JSON
python scripts/build_web.py

# Open web/index.html in a browser, switch to the new country tab,
# check that the chart looks sensible and the wide table is clean.
```

When happy:

```bash
git add config/countries.yaml config/party_colors.yaml web/index.html
git commit -m "Add NO (Norway) to coverage"
git push
```

CI will rebuild the page on the live URL within ~3 minutes.

---

## Known patterns and pitfalls

### Multi-election years
Greece has had two elections in a single year (May/June 2012, January/September 2015, May/June 2023). Same for Spain in 2019 (April + November). Use explicit per-cycle titles — see Greece in `config/countries.yaml`. The parser supports any cycle ID string.

### Semi-presidential countries
For France, presidential polls drown out legislative polls. We keep both — `FR` for presidential, `FR_LEG` for legislative — and route to the relevant one depending on the analysis. Other semi-presidential countries (Portugal, Finland, Austria for federal) usually have only the parliamentary article, which is what you want.

### Federated / regional countries
Belgium has regional Flanders/Wallonia polls in addition to federal. We use only the federal page (`{cycle} Belgian federal election`). Same logic for Germany (federal not state) and Spain (general not autonomous).

### Stub pages
Some "Opinion polling for the next X election" articles are stubs — Wikipedia editors haven't created them yet. The fetcher prints `✗` and moves on; nothing breaks. Once the article exists, your next CI run will pick it up automatically. No code changes needed.

### Election-article fallback
When a cycle's dedicated `Opinion polling for the YYYY X election` returns 404, the fetcher automatically retries against the bare election article (`YYYY Adjective X election`) and parses any wikitable under its `Opinion polls` / `Polling` section heading. `source.json` records `source_kind: election_article` for these. This covers ~12k poll-party rows across cycles where the dedicated article doesn't exist (mostly older cycles, plus smaller countries like BG, LT, LU).

The fetcher logs the fallback hop with a `↪` marker (vs `✓` for direct hits). Election articles often have polling sections too short to host a wikitable (CY 2011, CZ 2025, RO 2024 — the section is a one-paragraph link to an SVG chart). These produce zero rows; nothing breaks.

### Fallback contamination
Election articles can host polling subsections for separate races (e.g. BG 2021-Nov has presidential polling for Rumen Radev inside the `Opinion polls` section). The parser carves out any subsection whose heading mentions `president` / `presidential`. If a similar pattern appears for other contests (regional, EP, etc.) on a country you're adding, surface it as a `drop:` entry in `config/party_aliases/{CC}.yaml`.

### New parties appearing mid-cycle
Nothing to configure. The default-visible set is recomputed on every build and mixes lifetime obs count with the last-365-days share ranking, so fast risers (BSW in DE 2024, CH in Portugal, etc.) surface without a code change. Users can also toggle any party on/off from the chart controls.

### Color exhaustion
Add a hex for any new party in `config/party_colors.yaml`; parties without one get a deterministic hash-based fallback. Long-tail parties (dozens per country) still appear as togglable rows in the chart controls.

---

## Quick reference — what each config file controls

| File | Purpose |
|---|---|
| `config/countries.yaml` | Per-country: Wikipedia title patterns, list of cycles, coalition labels (raw + post-alias) |
| `config/party_colors.yaml` | Per-country: party hex colors for the chart and the table dots |
| `config/party_names.yaml` | Per-country: full party names used for the hover tooltips on the chart and tables |
| `config/pollster_aliases.yaml` | Global pollster-name canonicalization (Techne → Tecnè, TNS Sofres → Kantar, etc.) plus footnote-marker stripping |
| `config/party_aliases/{CC}.yaml` | Per-country `aliases:` (variant → canonical), `drop:` (exact-match leader/cabinet column drops), `drop_regex:` (pattern-match drops). `_meta.yaml` holds cross-country folds for Don't-know / Abstain / Others / Neither |
| `config/party_mappings/{CC}.yaml` | (Optional, advanced) Per-country `party_short` → `partyfacts_id` for cross-dataset joins. Only Italy currently has this |
| `web/index.html` | `COUNTRY_NAMES` constant — human-readable label per ISO2 code |

That's it. Adding a country is ~50 lines of YAML + one JS constant.

## Twelve cycles that currently produce zero rows

These cycles fetch successfully but don't yield wikitable-parseable data. Listed for transparency; each needs either a different data source or a structural parser change.

| Cycle | Why it's empty |
|---|---|
| LV 2011, LV 2014, NO 2005 | Election article has no polling section |
| CY 2011, CZ 2025, RO 2024, SE 2010 | Polling section is chart-only / no wikitable |
| NO 2013 | Only coalition-aggregate tables (bloc / gov vs opp) |
| NL 2010, SK 2012 | Transposed layout — parties as rows, dates as columns |
| LT 2012, LT 2016 | Group-label header without a meta column to anchor multi-row detection |

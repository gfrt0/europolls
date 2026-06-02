# Europolls

A Wikipedia-direct opinion polling dataset covering **32 European countries** — the EU-27 plus the UK, Norway, Iceland, Switzerland, and Bulgaria's separate cycles.

Europolls scrapes the per-country `Opinion polling for the X election` articles from English Wikipedia, parses the polling tables into long and wide formats, joins to a stable party identifier (`partyfacts_id`), and ships the result as a public, reproducible dataset.

When a dedicated polling article doesn't exist for a given cycle (common for smaller countries and pre-2010 cycles), the fetcher **falls back to the election article itself** and parses any wikitable under its `Opinion polls` / `Polling` section. This recovers ~12k poll-party observations across 39 of 51 attempted fallback cycles.

The pipeline is deterministic: every row carries (i) the source URL of the original poll publication as cited in Wikipedia and (ii) the Wikipedia article's permanent `?oldid=<revid>` permalink, so revisions can't silently drift the dataset.

Inspired by [Pitas (2023) Europepolls](https://arxiv.org/abs/2307.10022), which is no longer available; rebuilt from scratch with reproducible API-based scraping and several improvements (long format, source-URL provenance, partyfacts harmonization, election-article fallback).

## Coverage

32 countries, ~33k polls, ~296k party-poll observations.

| Country | Polls | Span | Top parties |
|---|---|---|---|
| UK (United Kingdom) | 5,143 | 2005–2026 | Con, Lab, LD, Grn |
| IT (Italy) | 5,014 | 2005–2026 | PD, Lega, M5S, FdI |
| DE (Germany) | 4,568 | 2009–2026 | SPD, Grüne, FDP, Linke, Union |
| ES (Spain) | 2,883 | 2004–2026 | PSOE, PP, PNV, Vox, Cs |
| PL (Poland) | 1,678 | 2005–2026 | PiS, KO, Konfederacja, Lewica |
| GR (Greece) | 1,625 | 2007–2026 | SYRIZA, ND, KKE, PASOK |
| NO (Norway) | 1,450 | 2009–2026 | FrP, KrF, H, SV, Sp |
| SE (Sweden) | 1,113 | 2010–2024 | S, M, SD, C, V |
| AT (Austria) | 978 | 2012–2026 | ÖVP, SPÖ, FPÖ, Grüne, NEOS |
| FR (France) — presidential | 835 | 2002–2022 | Le Pen, Sarkozy, Macron, Mélenchon |
| PT (Portugal) | 830 | 2005–2026 | PSD, PS, BE, CDU, CH |
| DK (Denmark) | 823 | 2007–2031 | A, V, O, B, Ø |
| EE (Estonia) | 684 | 2010–2026 | KE, RE, EKRE, SDE, E200 |
| CZ (Czechia) | 650 | 2006–2026 | ANO, ODS, SPOLU, Piráti, STAN |
| NL (Netherlands) | 641 | 2010–2026 | VVD, PVV, GL–PvdA, CDA, D66 |
| HU (Hungary) | 559 | 2009–2026 | Fidesz, Tisza, DK, Jobbik |
| SI (Slovenia) | 459 | 2011–2026 | SDS, SD, GS, Levica |
| IE (Ireland) | 449 | 2011–2026 | FF, FG, SF, Lab, GP |
| SK (Slovakia) | 392 | 2012–2026 | SMER-SD, PS, Hlas-SD, OĽaNO |
| IS (Iceland) | 375 | 2007–2024 | D, B, S, V, P |
| FI (Finland) | 360 | 2011–2026 | KESK, KOK, SDP, PS, VIHR |
| BG (Bulgaria) | 270 | 2013–2024 | GERB, PP–DB, DPS, BSP, ITN |
| LT (Lithuania) | 245 | 2016–2024 | LSDP, TS-LKD, LVŽS, LRLS |
| FR_LEG (France) — legislative | 241 | 2002–2024 | RN, LFI, NFP, ENS, LR |
| RO (Romania) | 168 | 2012–2026 | PSD, PNL, USR, AUR |
| LV (Latvia) | 145 | 2014–2022 | JV, ZZS, Saskaņa, NA |
| BE (Belgium) | 112 | 2007–2024 | N-VA, VB, CD&V, Vooruit, MR |
| MT (Malta) | 110 | 2012–2022 | PL, PN, ADPD |
| CY (Cyprus) | 71 | 2016–2023 | (presidential candidates) |
| CH (Switzerland) | 55 | 2011–2023 | SVP, SP, FDP, Die Mitte |
| HR (Croatia) | 48 | 2015–2016 | HDZ, SDP, Most |
| LU (Luxembourg) | 20 | 2013–2023 | CSV, LSAP, DP |

A small number of older cycles (LV 2011/2014, NO 2005, NL 2010, SK 2012, etc.) produce no rows because the election article has no polling section, uses a chart-only / transposed layout, or otherwise can't be parsed by the wikitable-based pipeline. See `docs/ADDING_A_COUNTRY.md#known-patterns-and-pitfalls`.

## Schema

### Long format — `data/processed/polls_long.csv`

| column | type | description |
|---|---|---|
| `country` | str | ISO2 country code |
| `cycle` | str | electoral cycle identifier (e.g. `2022`, `2019a`, `current`) |
| `polldate_start`, `polldate_end`, `polldate_mid` | date | fieldwork dates |
| `pollster` | str | polling firm name |
| `source_url` | str | URL of the original poll publication (from Wikipedia cell) |
| `sample_size` | int (nullable) | reported sample size |
| `party_short` | str | party abbreviation as shown in Wikipedia |
| `is_coalition` | bool | true if the column is a coalition aggregate |
| `vote_share` | float (0–100) | reported vote share |
| `wiki_article` | str | source Wikipedia article title (either the dedicated polling article or the election article when fallback fired) |
| `wiki_revid` | int | pinned revision id |
| `wiki_url` | str | `?oldid=<revid>` permalink |

The provenance of each row's article (polling article vs election-article fallback) is recorded per-cycle in `data/raw/{COUNTRY}/{CYCLE}/source.json` as `source_kind: polling_article|election_article`.

Cross-cycle and within-cycle duplicate rows — same `(country, polldate_mid, pollster, party_short)` appearing twice because Wikipedia editors copy prior election results into new polling articles, or list the same poll in multiple wikitables — are collapsed during the concat step. The surviving row is the one whose cycle's election year is closest to the poll's own year, with ties broken by larger sample size then by newer revid.

### Wide format — `data/processed/{COUNTRY}_polls_wide.csv`

One row per poll. Meta columns first, then one column per `party_short`, sorted by observation count. Coalition aggregates prefixed `coal_`.

### Harmonized format — `data/interim/harmonized/{COUNTRY}_long_harmonized.csv`

Long format augmented with `partyfacts_id` from [Party Facts](https://partyfacts.herokuapp.com/) (where available) — enables joins to ParlGov, MARPOR, PopuList, CHES, and similar datasets. Currently populated for IT only; other countries fall through with empty `partyfacts_id`.

## Validation

The Italian scrape was validated against the official Italian government poll register (via ondata's [liberiamoli-tutti](https://github.com/ondata/liberiamoli-tutti/tree/main/italian_polls) mirror of sondaggipoliticoelettorali.it):

- **2,523 overlapping (year-month × pollster × party) cells**
- **92.9% agree within ±0.5 pp**
- **96.9% agree within ±1.0 pp**
- **98.9% agree within ±2.0 pp**
- Mean absolute discrepancy: 0.21 pp; median 0.05 pp.

The largest outliers (~10 cells) trace to coalition-vs-individual-party reporting splits, not scraping bugs.

## Usage

### Consume pre-built data

Pre-built CSVs are published as GitHub Release assets (the repo itself ships only the code and config — data is reproducible). Download the latest release from https://github.com/gfrt0/europolls/releases.

The CI build also uploads `data/processed/polls_long.csv` as a per-commit artifact (30-day retention) — downloadable from the Actions tab without needing to re-run the pipeline locally.

### Reproduce locally

```bash
git clone https://github.com/gfrt0/europolls.git
cd europolls
python -m venv ~/venvs/europolls
~/venvs/europolls/bin/pip install -e .

# Full pipeline (fetches all wikitext, parses, pivots, dedups, concatenates, harmonizes):
python scripts/build_all.py

# Or a single country:
python -m europolls.fetch IT 2006 2008 2013 2018 2022 current
python -m europolls.parse IT
python -m europolls.pivot_wide IT
python -m europolls.harmonize IT
```

Outputs land under:
- `data/raw/{COUNTRY}/{CYCLE}/article.wikitext` — Wikipedia snapshots
- `data/raw/{COUNTRY}/{CYCLE}/source.json` — title, revid, fetched_iso, source_kind
- `data/interim/{COUNTRY}_{CYCLE}.csv` — long format per (country, cycle)
- `data/processed/{COUNTRY}_polls_wide.csv` — wide pivot per country
- `data/processed/polls_long.csv` — single concatenated + deduped long file
- `data/interim/harmonized/{COUNTRY}_long_harmonized.csv` — `partyfacts_id`-augmented

The fetcher rate-limits itself (1.5s between requests) and retries on HTTP 429 / 503 with exponential backoff; a full sweep runs in ~6 min.

## Extending coverage

Adding a new country takes ~50 lines of YAML + one JS constant. See [`docs/ADDING_A_COUNTRY.md`](docs/ADDING_A_COUNTRY.md) for a step-by-step recipe with concrete templates and known pitfalls. Candidates beyond the current 32 are listed at [Wikipedia's opinion-polling-by-country category](https://en.wikipedia.org/wiki/Category:Opinion_polling_by_country).

The presentation layer (chart and tables on the [live site](https://gfrt0.github.io/europolls)) draws on a small family of normalization config files:

- `config/pollster_aliases.yaml` — global pollster-name canonicalization (Techne → Tecnè, Kantar Public / TNS Sofres → Kantar, etc.).
- `config/party_aliases/{CC}.yaml` — per-country party-short canonicalization, optional `drop:` list for leader-approval or cabinet-rating columns, and `drop_regex:` for pattern matches. `_meta.yaml` folds Don't-know / Abstain / Others / Neither across all countries.
- `config/party_names.yaml` — full party names used for the chart's hover tooltips.

## Roadmap

- **`<ref>`-footnote URL extraction.** The current `source_url` column captures the poll's external link only when Wikipedia writes inline `[url Pollster]` markup (~69% of rows). Articles that cite via `<ref name="...">{{cite web|url=...}}</ref>` footnotes — notably ES, LV, IE (0%), and partially GR/NL/FI — lose their citations because the parser strips `<ref>` blocks as HTML tags before URL extraction. Adding a pre-strip pass that finds `<ref>` adjacent to the pollster cell and pulls URLs from the citation template would meaningfully bump coverage.
- Long-tail wide-parties curation across the newly-added Central/Eastern European and Baltic countries — the lists are first-pass guesses that haven't been hand-validated.
- Cross-dataset joins beyond Italy: `partyfacts_id` mapping files for the other 31 countries (`config/party_mappings/{CC}.yaml`).
- Twelve fallback cycles still produce zero rows because the election article uses chart-only polling sections, transposed party-row layouts, or no polling section at all — see the per-cycle list at the end of `docs/ADDING_A_COUNTRY.md`. Most need either a different data source or a structural parser change.

## Citation

> Forte, G. (2026). *Europolls: a Wikipedia-direct opinion polling dataset for 32 European countries*. https://github.com/gfrt0/europolls

If you use individual polls, please also cite the original polling firm and the underlying Wikipedia article (both are referenced per-row in the dataset).

## Licences

- Code: MIT (see [`LICENSE`](LICENSE))
- Data: CC-BY-SA 4.0 (see [`LICENSE-DATA`](LICENSE-DATA)), inherited from Wikipedia

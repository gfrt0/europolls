# Europolls

A Wikipedia-direct opinion polling dataset for the European Union and the United Kingdom.

Europolls scrapes the per-country "Opinion polling for the X election" articles from English Wikipedia, parses the polling tables into long and wide formats, joins to a stable party identifier (`partyfacts_id`), and ships the result as a public, reproducible dataset.

The pipeline is deterministic: every row carries (i) the source URL of the original poll publication as cited in Wikipedia and (ii) the Wikipedia article's permanent `?oldid=<revid>` permalink, so revisions can't silently drift the dataset.

Inspired by [Pitas (2023) Europepolls](https://arxiv.org/abs/2307.10022), which is no longer available; rebuilt from scratch with reproducible API-based scraping and several improvements (long format, source-URL provenance, partyfacts harmonization).

## Coverage

| Country | Polls | Span | Top parties (sanity) |
|---|---|---|---|
| IT | 5,189 | 2005–2026 | PD, M5S, FdI, FI, Lega |
| UK | 5,730 | 2005–2026 | Con, Lab, LD, Grn, SNP |
| DE | 4,904 | 2009–2025 | SPD, Union, Grüne, FDP, AfD |
| ES | 2,992 | 2004–2026 | PSOE, PP, Vox, Sumar |
| GR | 1,881 | 2007–2026 | ND, PASOK, Syriza, KKE |
| SE | 1,418 | 2010–2026 | M, S, SD, C, V |
| PT | 1,288 | 2005–2026 | PS, PSD, BE, CDU |
| FR | 1,298 | 2002–2022 | (presidential candidate-keyed) |
| AT | 927 | 2013–2026 | ÖVP, SPÖ, FPÖ, Grüne |
| NL | 656 | 2016–2023 | VVD, D66, CDA, PVV |
| DK | 487 | 2015–2026 | A, V, O, B, Ø |
| FI | 394 | 2011–2026 | KESK, KOK, SDP |
| BE | 338 | 2014–2024 | N-VA, PS, Vlaams Belang |
| FR_LEG | 328 | 2002–2022 | (legislative — sparse) |
| IE | 311 | 2017–2024 | FG, FF, SF, GP |
| MT | 117 | 2013–2022 | PL, PN |
| CY | 49 | 2022–2023 | (presidential anchor only) |
| LU | 0 | — | no Wikipedia polling article exists |

**Total: 27,407 polls across 17 country codes.**

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
| `wiki_article` | str | source Wikipedia article title |
| `wiki_revid` | int | pinned revision id |
| `wiki_url` | str | `?oldid=<revid>` permalink |

### Wide format — `data/processed/{COUNTRY}_polls_wide.csv`

One row per poll. Meta columns first, then one column per `party_short`, sorted by observation count. Coalition aggregates prefixed `coal_`.

### Harmonized format — `data/processed/{COUNTRY}_long_harmonized.csv`

Long format augmented with `partyfacts_id` from [Party Facts](https://partyfacts.herokuapp.com/) (where available) — enables joins to ParlGov, MARPOR, PopuList, CHES, and similar datasets.

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

### Reproduce locally

```bash
git clone https://github.com/gfrt0/europolls.git
cd europolls
python -m venv ~/venvs/europolls
~/venvs/europolls/bin/pip install -e .

# Full pipeline (fetches all wikitext, parses, pivots, concatenates, harmonizes):
python scripts/build_all.py

# Or a single country:
python -m europolls.fetch IT 2006 2008 2013 2018 2022 current
python -m europolls.parse IT
python -m europolls.pivot_wide IT
python -m europolls.harmonize IT
```

Outputs land under:
- `data/raw/{COUNTRY}/{CYCLE}/article.wikitext` — Wikipedia snapshots
- `data/interim/{COUNTRY}_{CYCLE}.csv` — long format per (country, cycle)
- `data/processed/{COUNTRY}_polls_wide.csv` — wide pivot per country
- `data/processed/polls_long.csv` — single concatenated long file
- `data/interim/harmonized/{COUNTRY}_long_harmonized.csv` — `partyfacts_id`-augmented

## Citation

> Forte, G. (2026). *Europolls: a Wikipedia-direct opinion polling dataset for the European Union and the United Kingdom*. https://github.com/gfrt0/europolls

If you use individual polls, please also cite the original polling firm and the underlying Wikipedia article (both are referenced per-row in the dataset).

## Licences

- Code: MIT (see [`LICENSE`](LICENSE))
- Data: CC-BY-SA 4.0 (see [`LICENSE-DATA`](LICENSE-DATA)), inherited from Wikipedia

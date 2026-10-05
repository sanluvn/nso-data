# NSO Data Acquisition Pipeline

A focused acquisition project for collecting and validating public data from Vietnam's National Statistics Office (NSO). 
The repository intentionally stops at the raw/validated-data boundary: it contains no database schema/loaders, analytical marts, 
or downstream metric extraction jobs.

## What the project collects

Two independent source branches are maintained:

1. **PX-Web** — discovers the PX catalog and acquires tables through either the JSON API or the HTML-form/JSON-stat route.
2. **NSO website monthly socioeconomic releases** — discovers releases, maintains stable release identity, downloads/reconciles artifacts, and preserves immutable artifact revisions.

The current PX corpus contains 492 catalogued tables (127 JSON API and 365 HTML/JSON-stat in the validated baseline). Baseline counts are evidence from the current corpus, not permanent assumptions about the live source.

## Setup

Use Python 3.11+ from the project root:

```bat
python -m pip install -r requirements.txt
```

## Project layout

```text
nso_pipeline/
├── scripts/                 # Ordered entry points
├── src/                     # Reusable acquisition/state/QC modules
├── data/
│   ├── raw/                 # Acquired source artifacts
│   └── registry/            # Catalog and persistent acquisition state
├── logs/                    # Execution history
├── reports/                 # Regenerable QC/profiling outputs
├── docs/                    # Acquisition architecture documentation
├── requirements.txt
└── README.md
```

## Run order

The scripts are numbered by normal workflow:

```text
01_build_catalog.py          Rediscover/classify the PX-Web catalog (not routine)
02_download_api.py           Acquire/resume/refresh PX JSON API tables
03_download_html.py          Acquire/resume/refresh PX HTML/JSON-stat tables
04_download_website.py       Acquire monthly NSO website releases/artifacts
05_qc_pxweb.py               Validate persisted PX artifacts
06_profile_pxweb.py          Generate inventory/metadata/dimension profiles
07_validate_acquired_data.py Read-only structural validation of the full corpus
```

### PX-Web normal operation

Run API and HTML writers sequentially because both update the same state registry.

```bat
python scripts\02_download_api.py --mode resume
python scripts\03_download_html.py --mode resume
```

`resume` validates local state and skips valid current artifacts. To check the remote source again:

```bat
python scripts\02_download_api.py --mode refresh
python scripts\03_download_html.py --mode refresh
```

A refresh acquires a candidate, validates it, compares semantic content, and publishes only validated changes. Failed refreshes must not replace the last good artifact.

To diagnose one table, supply both exact catalog identifiers:

```bat
python scripts\02_download_api.py --mode refresh --database "Công nghiệp" --table-id V07.01.px
python scripts\03_download_html.py --mode refresh --database "Chỉ số giá" --table-id V11.01.px
```

`01_build_catalog.py` is discovery, not a routine refresh command. Review catalog differences before replacing the canonical catalog because new/removed/reclassified tables can require state reconciliation.

### NSO website releases

```bat
python scripts\04_download_website.py
```

This branch maintains its own release registry, manifests and immutable artifact revisions. Its lifecycle differs from PX-Web current-state acquisition.

### Validation and profiling

```bat
python scripts\05_qc_pxweb.py
python scripts\06_profile_pxweb.py
python scripts\07_validate_acquired_data.py
```

The final validator is read-only and performs no network requests. It checks persisted PX coordinate structure and representative website workbook structure/readability. Historical baseline counts may need review after genuine source changes.

## Source-of-truth rules

- PX identity is `language + database + table_id`; titles and filenames are descriptive, not identities.
- `data/registry/pxweb_acquisition_state.json` is the current PX acquisition-state authority; logs are execution history only.
- `value_raw` preserves source notation; numeric `value` may be missing.
- PX dimensions are table-scoped and variable in count; do not assume a fixed schema across tables.
- Raw source artifacts are published only after validation; refresh errors must leave the last good artifact intact.
- PX v1 keeps one current filesystem artifact per table. Website artifacts use immutable revisions.

## Scope boundary

This repository is deliberately **acquisition-only**. Database design/loading, SQL files, normalized analytical datasets, and metric-specific downstream extraction belong in a separate project if needed later.

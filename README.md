# NSO Data Acquisition Pipeline

A reproducible pipeline for acquiring and validating public data from Vietnam's National Statistics Office (NSO).

The project covers data acquisition only. Database loading, transformation and analytics are outside its scope.

## Data sources

The pipeline collects data from two NSO sources:

- **PX-Web** — discovers the table catalog and acquires data through the JSON API or HTML/JSON-stat interface.
- **NSO website** — collects monthly socioeconomic releases and preserves their downloadable artifacts and revisions.

The validated PX-Web baseline contains 492 tables: 127 API tables and 365 HTML/JSON-stat tables. These counts describe the validated corpus and are not hardcoded source assumptions.

## Setup

Python 3.11+ is recommended.

```bat
python -m pip install -r requirements.txt
```

## Project structure

| Location | Purpose |
| --- | --- |
| `scripts/` | Seven numbered entry points |
| `src/` | Shared modules |
| `docs/` | Architecture |
| `data/`, `logs/`, `reports/` | Local outputs; generated as needed, excluded from Git |

## Workflow

```bat
python scripts\01_catalog.py
python scripts\02_api.py
python scripts\03_html.py
python scripts\04_web.py
python scripts\05_qc.py
python scripts\06_profile.py
python scripts\07_validate.py
```

These are separate commands, not a requirement to rerun every stage. Existing data needs no reacquisition.

### PX-Web

For normal incremental acquisition:

```bat
python scripts\02_api.py --mode resume
python scripts\03_html.py --mode resume
```

Run the API and HTML branches sequentially because they share the same acquisition-state registry.

Use `refresh` to check the source again:

```bat
python scripts\02_api.py --mode refresh
python scripts\03_html.py --mode refresh
```

To refresh a specific table:

```bat
python scripts\02_api.py --mode refresh --database "Công nghiệp" --table-id V07.01.px
python scripts\03_html.py --mode refresh --database "Chỉ số giá" --table-id V11.01.px
```

Run `scripts\01_catalog.py` only when intentionally rediscovering the PX-Web catalog.

### NSO website

```bat
python scripts\04_web.py
```

This branch maintains a release registry and immutable artifact revisions independently from PX-Web.

### Quality checks

```bat
python scripts\05_qc.py
python scripts\06_profile.py
python scripts\07_validate.py
```

`scripts\07_validate.py` is read-only and makes no network requests. It checks Website artifact hashes and sizes, then performs bounded workbook structural checks. A structural PASS does not certify historical encoding or semantic correctness.

### Compact edition

This edition has 15 Python files. Profiling and quality checks remain included. Legacy path migration, exact-original repair, font auditing and the bundled test suite are omitted. Website downloads still allocate chronological short names automatically.

For a source-only ZIP upgrade, close running scripts and extract directly into the existing project root, choosing Replace for existing files. The ZIP starts with scripts/, src/ and docs/; there is no wrapper directory and no data/ payload.

When upgrading from the compact run.py edition, delete the obsolete run.py, src/web.py and src/validate.py. When upgrading from older maintenance editions, also remove scripts/08_layout.py (or scripts/08_web_layout.py), src/web_audit.py, src/jsonstat.py, tests/ and docs/maintenance.md if present. The JSON-stat decoder is now included in src/px_html.py. Keep data/ and all its contents. No acquisition or migration rerun is needed.

Raw data, registry and manifest contents are unchanged by this code consolidation. Keep data/raw/ and data/registry/. Reports and logs are generated when their commands run. Historical font conversion remains outside acquisition.

## Design principles

- PX tables are identified by `language + database + table_id`.
- Acquisition state is authoritative; logs record execution history.
- Source notation is preserved in `value_raw`.
- PX dimensions are table-specific and may vary in number.
- New data is validated before replacing the current artifact.
- Failed refreshes preserve the last valid artifact.
- Website artifacts retain immutable revisions.

See [architecture](docs/architecture.md).

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
| `run.py` | One command entry point |
| `src/` | Shared modules |
| `docs/` | Architecture |
| `data/`, `logs/`, `reports/` | Local outputs; generated as needed, excluded from Git |

## Workflow

```bat
python run.py catalog
python run.py api
python run.py html
python run.py web
python run.py qc
python run.py profile
python run.py validate
```

These are separate commands, not a requirement to rerun every stage. Existing data needs no reacquisition.

### PX-Web

For normal incremental acquisition:

```bat
python run.py api --mode resume
python run.py html --mode resume
```

Run the API and HTML branches sequentially because they share the same acquisition-state registry.

Use `refresh` to check the source again:

```bat
python run.py api --mode refresh
python run.py html --mode refresh
```

To refresh a specific table:

```bat
python run.py api --mode refresh --database "Công nghiệp" --table-id V07.01.px
python run.py html --mode refresh --database "Chỉ số giá" --table-id V11.01.px
```

Run `run.py catalog` only when intentionally rediscovering the PX-Web catalog.

### NSO website

```bat
python run.py web
```

This branch maintains a release registry and immutable artifact revisions independently from PX-Web.

### Quality checks

```bat
python run.py qc
python run.py profile
python run.py validate
```

`run.py validate` is read-only and makes no network requests. It checks Website artifact hashes and sizes, then performs bounded workbook structural checks. A structural PASS does not certify historical encoding or semantic correctness.

### Compact edition

This edition has 11 Python files. Profiling and quality checks remain included. Legacy path migration, exact-original repair, font auditing and the bundled test suite are omitted. Website downloads still allocate chronological short names automatically.

For the full ZIP, extract into a new empty project directory. It contains code and the existing data/registry; do not rerun acquisition to install it. Do not merge it into an old checkout, which would retain obsolete scripts. A GitHub clone contains source only; copy your existing data/ directory to use your local corpus.

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

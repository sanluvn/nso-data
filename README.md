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
| `scripts/` | Acquisition and maintenance entry points |
| `src/` | Shared modules |
| `tests/` | Offline regression tests |
| `docs/` | Architecture and maintenance guide |
| `data/`, `logs/`, `reports/` | Local outputs; generated as needed, excluded from Git |

## Workflow

```text
01_catalog.py    Discover and classify PX-Web tables
02_api.py        Acquire PX-Web API tables
03_html.py       Acquire PX-Web HTML/JSON-stat tables
04_web.py        Acquire NSO website releases
05_qc.py         Run PX-Web quality checks
06_profile.py    Profile the acquired PX-Web corpus
07_validate.py   Validate the persisted corpus
08_layout.py     Inspect, repair or migrate Website paths
```

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

Run `01_catalog.py` only when intentionally rediscovering the PX-Web catalog.

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

`07_validate.py` is read-only and makes no network requests. It checks Website artifact hashes and sizes, then performs bounded workbook structural checks. A structural PASS does not certify historical encoding or semantic correctness.

### Website maintenance

```bat
python scripts\08_layout.py
```

The default command checks integrity and writes a path plan without changing raw files or accessing the network. Use the maintenance guide for exact-hash repair, chronological migration, recovery and optional font auditing. Existing migrated data needs no repeated migration.

### Offline regression tests

```bat
python -m unittest discover -s tests
```

Tests use temporary directories and mock HTTP downloads. They do not contact NSO or modify local raw data.


## Design principles

- PX tables are identified by `language + database + table_id`.
- Acquisition state is authoritative; logs record execution history.
- Source notation is preserved in `value_raw`.
- PX dimensions are table-specific and may vary in number.
- New data is validated before replacing the current artifact.
- Failed refreshes preserve the last valid artifact.
- Website artifacts retain immutable revisions.

See [architecture](docs/architecture.md) and [maintenance](docs/maintenance.md).

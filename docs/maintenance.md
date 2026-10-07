# Website maintenance

Run commands from the acquisition project root using its Python environment. Stop all acquisition/maintenance writers and close source documents before repair or migration. Do not edit code or re-run acquisition to perform maintenance.

| Command | Behavior |
| --- | --- |
| `python scripts/08_layout.py` | Offline integrity check and chronological path plan; raw unchanged |
| `python scripts/08_layout.py --restore-originals` | Downloads only mismatched files; accepts only exact original SHA-256/size, with verified local backup |
| `python scripts/08_layout.py --apply` | Verified copy-and-swap migration; refuses integrity mismatch or path collision |
| `python scripts/08_layout.py --recover` | Recovers an interrupted migration using its journal |
| `python scripts/08_layout.py --audit-fonts` | Reads current Excel/Word artifacts and writes font evidence/errors; does not convert text |

Already migrated data does not need another migration. Existing source filenames and immutable acquisition hashes remain recorded in manifests. Downstream projects should resolve paths from manifests; indexes storing old paths need rebuilding.

## Retained versus disposable local data

| Location | Action |
| --- | --- |
| `data/raw/`, `data/registry/` | Keep current artifacts, manifests, catalog and acquisition state; never delete as routine cache cleanup |
| `data/raw/nso_web/.web_layout_backup/` | Can remove after successful migration and current integrity verification; this removes rollback to the old layout |
| `data/local_backups/web_changed_*/` | Can remove after exact-original restoration and confirming no local edits need retaining |
| `data/raw/nso_web/monthly_socioeconomic/_legacy_originals/` | Inspect before removal; delete only if all needed artifacts are retained elsewhere |
| `reports/`, `logs/` | Regenerable outputs/history; omit from source ZIP and Git. Keep locally if audit history matters |
| `__pycache__/`, `*.pyc`, `.pytest_cache/` | Disposable Python/test caches |
| Migration journal/staging paths | Not ordinary trash. Recover interrupted work first, then inspect leftovers |
| `.git/` in the local checkout | Keep; contains local Git history and remote configuration. Omit from distribution ZIP |

The clean source ZIP contains only code, dependencies, tests and durable documentation. It intentionally excludes raw data, acquisition state, generated reports, logs, caches, local backups and Git internals. Unpacking source code does not delete old files from an existing checkout.

When upgrading from the layout patch, replace `scripts/08_web_layout.py` with `scripts/08_layout.py` and `tests/test_web_layout.py` with `tests/test_layout.py`. Remove the old two filenames after copying their replacements. The patch-specific `docs/APPLY_WEB_LAYOUT_VI.md` and `docs/WEB_LAYOUT_REVIEW.md` are superseded by this guide and architecture.md; retain a separate copy if you need their historical audit findings.

## Validation limits

The main validator verifies Website hashes/sizes and samples recent XLSX workbook structure. Font auditing is separate. Legacy fonts may be declared but unused; lack of detected legacy fonts does not certify Unicode correctness. XLS reader failures require investigation, not silent skipping or forced decoding. No automatic TCVN3/VNI conversion is included.

## Short names (short-v2)

`--apply` migrates both original UUID paths and chronological-v1 paths to short-v2. Fresh downloads allocate short names automatically; no migration is needed on a clean clone. Release folders are at the same level. Allocation is stable once saved in a manifest. Current file extension and original source filename remain separate concepts; revisions retain their own stored bytes and paths.

## Packaged cleanup — 2026-10-07

The full ZIP includes 321 production releases/648 Website files and all 619 PX artifacts referenced by the acquisition state. All source hashes verified. Three changed local XLS files (2000-01, 2010-08, 2011-01) were restored from exact-hash originals in the previously supplied ZIP; no acquisition hashes were rewritten. The uploaded ZIP retains the original local copies.

Duplicate prototype files were removed only after matching their hashes to production artifacts. Backup trees, generated reports/logs, caches and patch-only documentation are omitted. No font conversion was performed.

Migration/idempotence succeeded over the full Website corpus. Ten offline regression tests passed, including duplicate period/file allocation, stable names across fresh downloads with different generated IDs, reload, revision handling, repair and recovery. Network operations were mocked; this environment used import stubs for unavailable requests/bs4 dependencies. Live discovery/download and GitHub publication were not performed in this check.

To use the full ZIP, extract into a NEW empty project directory. Do not merge its raw tree into old UUID folders. It is already migrated; do not rerun acquisition or migration just to install it. Keep the old local checkout until downstream paths are updated; its Git metadata is not included in the ZIP. Publish only the source files; data/state remain ignored.

# Acquisition Architecture v1

## Scope

This repository owns source discovery, acquisition, persistent acquisition state, raw artifact preservation, QC and structural profiling for NSO public data. It ends at the validated acquisition boundary and intentionally contains no database implementation or downstream analytical transformation layer.

## Source branches

### PX-Web

PX-Web tables are catalogued first and routed to one of two acquisition mechanisms:

- JSON API
- HTML form followed by JSON-stat export

The two mechanisms share a persistent state contract but retain mechanism-specific fetch/validation logic. Identity is `language + database + table_id`.

`resume` validates current local state and reacquires only missing/invalid artifacts. `refresh` fetches a candidate, validates it, compares content, and publishes a changed artifact atomically. A failed refresh never replaces the last validated artifact.

Persistent state lives in `data/registry/pxweb_acquisition_state.json`. Execution logs are not current-state authority.

### NSO website monthly releases

The website branch discovers releases, resolves stable release identity, reconciles downloadable artifacts and maintains immutable artifact revisions. It uses a separate release registry and manifests because its publication lifecycle differs from PX-Web.

## Storage contract

| Local path | Contents |
| --- | --- |
| `data/raw/pxweb/vi/api/` | PX API artifacts |
| `data/raw/pxweb/vi/html_jsonstat/` | PX HTML/JSON-stat artifacts |
| `data/raw/nso_web/monthly_socioeconomic/` | Website release folders and manifests |
| `data/registry/` | Catalog and independent PX/Website state registries |


`logs/` stores execution history. `reports/` stores regenerable QC, inventory, metadata and profiling evidence.

## Publication and provenance principles

1. Never treat file existence alone as proof that an artifact is valid/current.
2. Validate candidates before publication.
3. Use SHA-256/state metadata to verify current artifacts.
4. Keep the last good artifact when acquisition or validation fails.
5. Do not fabricate source-native identifiers that a source mechanism does not expose reliably.
6. Preserve raw source values and source URLs/provenance.
7. Run PX API and HTML writers sequentially because they share one state registry.
8. Do not infer missing website release semantics or reconstruct suspicious source URLs.

## Validation baseline

The validated migration baseline contains 492 PX tables (127 API, 365 HTML/JSON-stat) and 484,784 persisted observations, with 2–5 dimensions per table. Website validation inventories production manifests/current Excel artifacts and structurally inspects a bounded set of recent XLSX workbooks. These numbers document the validated corpus at a point in time; live-source changes may legitimately change them.

## Reopen this architecture when

Review the contract when source API/form behavior changes, catalog membership/routing changes, state schema changes, concurrent writers are required, historical PX filesystem revisions become necessary, or the raw artifact format changes. Any migration should preserve before/after validation evidence rather than silently reinterpret existing state.

## Website chronological layout

Release directories use `YYYY-MM` at one level. Artifact names use `YYYY-MM_bang-so-lieu.xls`, `YYYY-MM_loi-van.doc` or the appropriate document type and extension. Only collisions receive `_02`, `_03` suffixes; UUIDs are not displayed in paths. Revisions use dedicated `revisions/<document stem>/r0002/` directories. Allocated display names are persisted under `local_layout` in each manifest, so later discovery or reordering cannot renumber existing releases or attachments. Full IDs, original source filenames, URLs and hashes remain in manifests. Unknown/conflicting coverage stays `UNDATED`; publication dates are not substituted for reference periods.

The downloader resolves existing folders by manifest release ID. Both original and migrated layouts are supported. Manifest revision paths, not directory-name assumptions, are the downstream interface. The registry has no artifact paths and does not need path rewrites during migration.

Legacy migration/repair tools are not bundled in the compact edition. An interrupted legacy migration journal still blocks acquisition to protect existing data; recovery requires the maintenance version that started that migration.

Source text with legacy fonts is preserved. Conversion to Unicode belongs to transformation. A hash PASS establishes byte integrity relative to acquisition metadata, not semantic correctness.

"""Quality-control checks for NSO PX-Web raw outputs."""

from pathlib import Path

import pandas as pd

from .paths import (
    PXWEB_API_ROOT,
    PXWEB_CATALOG_PATH,
    PXWEB_HTML_ROOT,
    QC_REPORT_ROOT,
)

def qc_api_downloads(catalog, raw_dir):
    """Check expected JSON API Parquet and metadata files."""
    raw_dir = Path(raw_dir)
    api_catalog = catalog.loc[catalog["source"].eq("json_api")].copy()
    checks = []

    for row in api_catalog.itertuples(index=False):
        parquet_path = raw_dir / f"{row.database}__{row.table_id}.parquet"
        metadata_path = raw_dir / f"{row.database}__{row.table_id}.metadata.json"

        exists_parquet = parquet_path.exists()
        exists_metadata = metadata_path.exists()
        n_rows = None

        if exists_parquet:
            try:
                frame = pd.read_parquet(parquet_path)
                n_rows = len(frame)
            except Exception as exc:
                checks.append({
                    "database": row.database,
                    "table_id": row.table_id,
                    "parquet": exists_parquet,
                    "metadata": exists_metadata,
                    "rows": None,
                    "status": f"READ_ERROR: {exc}",
                })
                continue

        if not exists_parquet:
            status = "MISSING_PARQUET"
        elif not exists_metadata:
            status = "MISSING_METADATA"
        elif n_rows == 0:
            status = "EMPTY"
        else:
            status = "OK"

        checks.append({
            "database": row.database,
            "table_id": row.table_id,
            "parquet": exists_parquet,
            "metadata": exists_metadata,
            "rows": n_rows,
            "status": status,
        })

    return pd.DataFrame(checks)


def qc_html_downloads(catalog, html_dir):
    """Run the validated structural QC on expected HTML-branch Parquets."""
    html_dir = Path(html_dir)
    html_catalog = catalog.loc[catalog["source"].eq("html_form")].copy()
    results = []

    for row in html_catalog.itertuples(index=False):
        database = str(row.database)
        table_id = str(row.table_id)
        table_title = str(row.table_title)

        safe_name = (
            f"{database}__{table_id}"
            .replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
        )
        path = html_dir / f"{safe_name}.parquet"

        status = "PASS"
        error = ""
        rows = None
        columns = None
        missing_values = None
        duplicate_rows = None

        try:
            if not path.exists():
                raise FileNotFoundError("Output Parquet file not found")

            frame = pd.read_parquet(path)
            rows = len(frame)
            columns = len(frame.columns)

            if rows == 0:
                raise ValueError("Parquet file contains zero rows")
            if "value_raw" not in frame.columns:
                raise ValueError("Missing required column: value_raw")
            if "value" not in frame.columns:
                raise ValueError("Missing required column: value")

            dimension_columns = [
                column
                for column in frame.columns
                if column not in {"value_raw", "value"}
            ]
            if not dimension_columns:
                raise ValueError("No dimension columns found")

            missing_values = int(frame["value"].isna().sum())
            duplicate_rows = int(
                frame.duplicated(
                    subset=dimension_columns,
                    keep=False,
                ).sum()
            )

            if duplicate_rows > 0:
                raise ValueError(
                    f"Duplicate dimension combinations: "
                    f"{duplicate_rows:,} rows"
                )

        except Exception as exc:
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"

        results.append({
            "database": database,
            "table_id": table_id,
            "table_title": table_title,
            "status": status,
            "rows": rows,
            "columns": columns,
            "missing_values": missing_values,
            "duplicate_rows": duplicate_rows,
            "file": str(path),
            "error": error,
        })

    return pd.DataFrame(results)


def run_pxweb_qc(save_reports=True):
    """Run QC across the standard PX-Web acquisition layout."""

    catalog = pd.read_csv(
        PXWEB_CATALOG_PATH,
        encoding="utf-8-sig",
    )

    api_qc = qc_api_downloads(
        catalog,
        PXWEB_API_ROOT,
    )

    html_qc = qc_html_downloads(
        catalog,
        PXWEB_HTML_ROOT,
    )

    if save_reports:
        QC_REPORT_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        api_qc.to_csv(
            QC_REPORT_ROOT / "api_download_qc_report.csv",
            index=False,
            encoding="utf-8-sig",
        )

        html_qc.to_csv(
            QC_REPORT_ROOT / "html_parquet_qc_report.csv",
            index=False,
            encoding="utf-8-sig",
        )

    return api_qc, html_qc
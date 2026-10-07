"""Reusable inventory and profiling utilities for NSO PX-Web outputs."""

import json
from pathlib import Path

import pandas as pd


def safe_table_filename(database, table_id):
    """Return the filename stem used by the raw PX-Web layer."""
    return (
        f"{database}__{table_id}"
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
    )


def inventory_parquet(catalog_df, output_dir, source_name):
    """Inventory expected Parquet outputs for one PX-Web source branch."""
    output_dir = Path(output_dir)
    records = []

    for row in catalog_df.itertuples(index=False):
        database = str(row.database)
        table_id = str(row.table_id)
        path = output_dir / f"{safe_table_filename(database, table_id)}.parquet"

        status = "PASS"
        error = ""
        rows = n_columns = n_dimensions = missing_values = file_size_mb = None
        dimension_columns = None

        try:
            if not path.exists():
                raise FileNotFoundError("Parquet file not found")

            frame = pd.read_parquet(path)
            rows = len(frame)
            n_columns = len(frame.columns)

            if "value_raw" not in frame.columns:
                raise ValueError("Missing value_raw column")
            if "value" not in frame.columns:
                raise ValueError("Missing value column")

            dimension_cols = [
                col
                for col in frame.columns
                if col not in {"value_raw", "value"}
            ]
            if not dimension_cols:
                raise ValueError("No dimension columns found")

            n_dimensions = len(dimension_cols)
            dimension_columns = "|".join(dimension_cols)
            missing_values = int(frame["value"].isna().sum())
            file_size_mb = path.stat().st_size / (1024 ** 2)

        except Exception as exc:
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"

        records.append({
            "source": source_name,
            "database": database,
            "table_id": table_id,
            "table_title": str(row.table_title),
            "status": status,
            "rows": rows,
            "n_columns": n_columns,
            "n_dimensions": n_dimensions,
            "dimension_columns": dimension_columns,
            "missing_values": missing_values,
            "file_size_mb": file_size_mb,
            "file": str(path),
            "error": error,
        })

    return pd.DataFrame(records)


def build_inventory(catalog, api_dir, html_dir):
    """Build the combined API + HTML Parquet inventory and summaries."""
    api_catalog = catalog.loc[catalog["source"].eq("json_api")].copy()
    html_catalog = catalog.loc[catalog["source"].eq("html_form")].copy()

    api_inventory = inventory_parquet(api_catalog, api_dir, "json_api")
    html_inventory = inventory_parquet(
        html_catalog, html_dir, "html_jsonstat"
    )
    inventory = pd.concat(
        [api_inventory, html_inventory],
        ignore_index=True,
    )

    passed = inventory.loc[inventory["status"].eq("PASS")]

    dimension_summary = (
        passed.groupby(["source", "n_dimensions"])
        .agg(tables=("table_id", "count"), rows=("rows", "sum"))
        .reset_index()
        .sort_values(["source", "n_dimensions"])
    )

    database_summary = (
        passed.groupby(["source", "database"])
        .agg(
            tables=("table_id", "count"),
            rows=("rows", "sum"),
            missing_values=("missing_values", "sum"),
        )
        .reset_index()
        .sort_values(["source", "rows"], ascending=[True, False])
    )

    schema_summary = (
        passed.groupby(["source", "dimension_columns"])
        .agg(tables=("table_id", "count"), rows=("rows", "sum"))
        .reset_index()
        .sort_values(["source", "tables"], ascending=[True, False])
    )

    return inventory, dimension_summary, database_summary, schema_summary


def find_api_metadata(api_dir, database, table_id):
    """Locate metadata saved by the API downloader."""
    api_dir = Path(api_dir)
    candidates = [
        api_dir / f"{database}__{table_id}.metadata.json",
        api_dir / f"{database}__{table_id}.px.metadata.json",
        api_dir / f"{database}__{table_id}.json",
    ]
    return next((path for path in candidates if path.exists()), None)


def inspect_metadata(catalog, api_dir, html_dir):
    """Inspect available API metadata and HTML Parquet dimension labels."""
    api_dir = Path(api_dir)
    html_dir = Path(html_dir)

    api_rows = []
    api_categories = []

    for row in catalog.loc[catalog["source"].eq("json_api")].itertuples(index=False):
        database, table_id = str(row.database), str(row.table_id)
        path = find_api_metadata(api_dir, database, table_id)

        if path is None:
            api_rows.append({
                "database": database,
                "table_id": table_id,
                "metadata_found": False,
                "n_dimensions": None,
                "dimensions": None,
                "metadata_file": None,
            })
            continue

        try:
            with path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)

            dimensions = []
            for variable in metadata.get("variables", []):
                dim_code = variable.get("code")
                dim_text = variable.get("text")
                dimensions.append(dim_text)

                for code, label in zip(
                    variable.get("values", []),
                    variable.get("valueTexts", []),
                ):
                    api_categories.append({
                        "database": database,
                        "table_id": table_id,
                        "dimension_code": dim_code,
                        "dimension_label": dim_text,
                        "category_code": code,
                        "category_label": label,
                    })

            api_rows.append({
                "database": database,
                "table_id": table_id,
                "metadata_found": True,
                "n_dimensions": len(dimensions),
                "dimensions": "|".join(dimensions),
                "metadata_file": str(path),
            })
        except Exception as exc:
            api_rows.append({
                "database": database,
                "table_id": table_id,
                "metadata_found": False,
                "n_dimensions": None,
                "dimensions": None,
                "metadata_file": f"ERROR: {exc}",
            })

    html_rows = []
    html_categories = []

    for row in catalog.loc[catalog["source"].eq("html_form")].itertuples(index=False):
        database, table_id = str(row.database), str(row.table_id)
        path = html_dir / f"{safe_table_filename(database, table_id)}.parquet"

        try:
            if not path.exists():
                raise FileNotFoundError("Parquet file not found")

            frame = pd.read_parquet(path)
            dimensions = [
                col for col in frame.columns
                if col not in {"value_raw", "value"}
            ]

            html_rows.append({
                "database": database,
                "table_id": table_id,
                "parquet_found": True,
                "n_dimensions": len(dimensions),
                "dimensions": "|".join(dimensions),
            })

            for dimension in dimensions:
                for label in (
                    frame[dimension]
                    .dropna()
                    .astype(str)
                    .drop_duplicates()
                ):
                    html_categories.append({
                        "database": database,
                        "table_id": table_id,
                        "dimension_label": dimension,
                        "category_label": label,
                    })

        except Exception as exc:
            html_rows.append({
                "database": database,
                "table_id": table_id,
                "parquet_found": False,
                "n_dimensions": None,
                "dimensions": f"ERROR: {exc}",
            })

    return (
        pd.DataFrame(api_rows),
        pd.DataFrame(api_categories),
        pd.DataFrame(html_rows),
        pd.DataFrame(html_categories),
    )


def profile_dimensions(api_meta, api_categories, html_meta, html_categories):
    """Build table-scoped dimension instances and frequency/cardinality summaries."""
    rows = []

    for _, row in api_meta.iterrows():
        if not row["metadata_found"]:
            continue
        for dimension in str(row["dimensions"]).split("|"):
            subset = api_categories[
                (api_categories["database"] == row["database"])
                & (api_categories["table_id"] == row["table_id"])
                & (api_categories["dimension_label"] == dimension)
            ]
            rows.append({
                "source": "json_api",
                "database": row["database"],
                "table_id": row["table_id"],
                "dimension": dimension,
                "category_count": subset["category_code"].nunique(),
                "has_code": True,
            })

    for _, row in html_meta.iterrows():
        if not row["parquet_found"]:
            continue
        for dimension in str(row["dimensions"]).split("|"):
            subset = html_categories[
                (html_categories["database"] == row["database"])
                & (html_categories["table_id"] == row["table_id"])
                & (html_categories["dimension_label"] == dimension)
            ]
            rows.append({
                "source": "html_jsonstat",
                "database": row["database"],
                "table_id": row["table_id"],
                "dimension": dimension,
                "category_count": len(subset),
                "has_code": False,
            })

    dimensions = pd.DataFrame(rows)

    summary = (
        dimensions.groupby("dimension")
        .agg(
            table_count=("table_id", "nunique"),
            category_instances=("category_count", "sum"),
            min_categories=("category_count", "min"),
            median_categories=("category_count", "median"),
            max_categories=("category_count", "max"),
            api_instances=("has_code", "sum"),
        )
        .reset_index()
        .sort_values(["table_count", "dimension"], ascending=[False, True])
    )
    summary["html_instances"] = summary["table_count"] - summary["api_instances"]

    cardinality = (
        dimensions.groupby("category_count")
        .size()
        .reset_index(name="dimension_instances")
        .sort_values("category_count")
    )

    return dimensions, summary, cardinality


from .paths import (
    INVENTORY_REPORT_ROOT,
    METADATA_REPORT_ROOT,
    PROFILING_REPORT_ROOT,
    PXWEB_API_ROOT,
    PXWEB_CATALOG_PATH,
    PXWEB_HTML_ROOT,
)

def save_csv(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def main():
    catalog = pd.read_csv(
        PXWEB_CATALOG_PATH,
        encoding="utf-8-sig",
    )
    
    raw_dir = PXWEB_API_ROOT
    html_dir = PXWEB_HTML_ROOT
    
    inventory_dir = INVENTORY_REPORT_ROOT
    metadata_dir = METADATA_REPORT_ROOT
    profiling_dir = PROFILING_REPORT_ROOT

    inventory, dimension_summary, database_summary, schema_summary = (
        build_inventory(catalog, raw_dir, html_dir)
    )

    save_csv(inventory, inventory_dir / "pxweb_inventory.csv")
    save_csv(
        dimension_summary,
        inventory_dir / "pxweb_dimension_summary.csv",
    )
    save_csv(
        database_summary,
        profiling_dir / "pxweb_database_summary.csv",
    )
    save_csv(
        schema_summary,
        profiling_dir / "pxweb_schema_summary.csv",
    )

    api_meta, api_categories, html_meta, html_categories = inspect_metadata(
        catalog,
        raw_dir,
        html_dir,
    )

    save_csv(
        api_meta,
        metadata_dir / "pxweb_api_metadata_inventory.csv",
    )
    save_csv(
        api_categories,
        metadata_dir / "pxweb_api_category_inventory.csv",
    )
    save_csv(
        html_meta,
        metadata_dir / "pxweb_html_metadata_inventory.csv",
    )
    save_csv(
        html_categories,
        metadata_dir / "pxweb_html_category_inventory.csv",
    )

    dimensions, dimension_name_summary, cardinality_summary = (
        profile_dimensions(
            api_meta,
            api_categories,
            html_meta,
            html_categories,
        )
    )

    save_csv(
        dimensions,
        profiling_dir / "pxweb_dimension_instances.csv",
    )
    save_csv(
        dimension_name_summary,
        profiling_dir / "pxweb_dimension_name_summary.csv",
    )
    save_csv(
        cardinality_summary,
        profiling_dir / "pxweb_dimension_cardinality_summary.csv",
    )

    passed = int(inventory["status"].eq("PASS").sum())
    errors = int(inventory["status"].eq("ERROR").sum())
    rows = int(inventory["rows"].fillna(0).sum())
    missing = int(inventory["missing_values"].fillna(0).sum())

    print("\nPX-Web profile summary:")
    print(f"Catalog tables      : {len(catalog):,}")
    print(f"Inventory PASS      : {passed:,}")
    print(f"Inventory ERROR     : {errors:,}")
    print(f"Observations        : {rows:,}")
    print(f"Missing values      : {missing:,}")
    print(f"Dimension instances : {len(dimensions):,}")



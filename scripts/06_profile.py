"""Generate reusable inventory, metadata, and dimension-profile reports."""

from pathlib import Path
import sys

import pandas as pd

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from src.profile import (
    build_inventory,
    inspect_metadata,
    profile_dimensions,
)

from src.paths import (
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


if __name__ == "__main__":
    main()

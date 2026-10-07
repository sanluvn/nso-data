"""Rebuild the live NSO PX-Web table catalog.

Run this script only when you intentionally want to refresh the table
catalog from the NSO PX-Web website.
"""

from pathlib import Path
import sys


# Allow execution directly from the repository during the current
# pre-packaging stage. This compatibility block will be removed once
# the project becomes an installable Python package.
PIPELINE_ROOT = Path(__file__).resolve().parents[1]

if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))


from src.catalog import build_classified_catalog
from src.paths import PXWEB_CATALOG_PATH


def main():
    catalog = build_classified_catalog(
        output_path=PXWEB_CATALOG_PATH,
        timeout=30,
        delay=0.05,
        verbose=True,
    )

    print(f"\nCatalog saved: {PXWEB_CATALOG_PATH}")
    print(f"Tables       : {len(catalog):,}")
    print(catalog["source"].value_counts().to_string())


if __name__ == "__main__":
    main()
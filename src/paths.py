"""
Central filesystem paths for the NSO data pipeline.

All project-level directories are derived from the repository root.
No machine-specific absolute paths should be defined in production code.
"""

from pathlib import Path


# ---------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------
# Persistent data
# ---------------------------------------------------------------------

DATA_ROOT = PROJECT_ROOT / "data"

RAW_ROOT = DATA_ROOT / "raw"
REGISTRY_ROOT = DATA_ROOT / "registry"

PXWEB_RAW_ROOT = RAW_ROOT / "pxweb" / "vi"

PXWEB_API_ROOT = PXWEB_RAW_ROOT / "api"
PXWEB_HTML_ROOT = PXWEB_RAW_ROOT / "html_jsonstat"

NSO_WEB_RAW_ROOT = RAW_ROOT / "nso_web"

NSO_WEB_MONTHLY_ROOT = (
    NSO_WEB_RAW_ROOT
    / "monthly_socioeconomic"
)


# ---------------------------------------------------------------------
# Persistent pipeline metadata / registries
# ---------------------------------------------------------------------

PXWEB_CATALOG_PATH = (
    REGISTRY_ROOT
    / "pxweb_table_catalog.csv"
)

PXWEB_ACQUISITION_STATE_PATH = (
    REGISTRY_ROOT
    / "pxweb_acquisition_state.json"
)

NSO_WEB_RELEASE_REGISTRY_PATH = (
    REGISTRY_ROOT
    / "nso_web_monthly_socioeconomic_releases.json"
)


# ---------------------------------------------------------------------
# Operational logs
# ---------------------------------------------------------------------

LOG_ROOT = PROJECT_ROOT / "logs"

PXWEB_API_LOG_PATH = (
    LOG_ROOT
    / "pxweb_api_download.csv"
)

PXWEB_HTML_LOG_PATH = (
    LOG_ROOT
    / "pxweb_html_download.csv"
)


# ---------------------------------------------------------------------
# Generated reports
# ---------------------------------------------------------------------

REPORT_ROOT = PROJECT_ROOT / "reports"

QC_REPORT_ROOT = REPORT_ROOT / "qc"
INVENTORY_REPORT_ROOT = REPORT_ROOT / "inventory"
METADATA_REPORT_ROOT = REPORT_ROOT / "metadata"
PROFILING_REPORT_ROOT = REPORT_ROOT / "profiling"
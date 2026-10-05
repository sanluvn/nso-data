"""Run QC across the downloaded NSO PX-Web API and HTML branches."""

from pathlib import Path
import sys

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from src.qc import run_pxweb_qc


def main():
    api_qc, html_qc = run_pxweb_qc(
        save_reports=True,
    )
    print("\nAPI QC:")
    print(api_qc["status"].value_counts(dropna=False).to_string())

    print("\nHTML QC:")
    print(html_qc["status"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()

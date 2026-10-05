"""Download the NSO PX-Web JSON API branch.

Resume validated local state, or explicitly refresh the source.
"""

from pathlib import Path
import argparse
import sys

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from src.px_api import run_api_download

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("resume", "refresh"), default="resume")
    parser.add_argument("--database")
    parser.add_argument("--table-id")
    args = parser.parse_args()
    log = run_api_download(
        max_retries=3,
        mode=args.mode,
        database=args.database,
        table_id=args.table_id,
    )

    print("\nAPI download summary:")
    print(log["status"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()

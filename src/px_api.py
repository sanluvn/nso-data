"""Download NSO PX-Web tables exposed through the JSON API."""

from __future__ import annotations

import json
import time
import tempfile
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from .px_state import PxState, facts

from .paths import (
    PXWEB_API_LOG_PATH,
    PXWEB_API_ROOT,
    PXWEB_CATALOG_PATH,
)

BASE_API = "https://pxweb.nso.gov.vn/api/v1/vi"


def download_api_table(database, table_id, max_retries=3, session=None):
    """Download one PX-Web JSON API table and return metadata and long data."""
    client = session or requests
    url = f"{BASE_API}/{quote(database, safe='')}/{table_id}"
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = client.get(url, timeout=30)
            response.raise_for_status()
            metadata = json.loads(response.content.decode("utf-8-sig"))

            query = [
                {
                    "code": variable["code"],
                    "selection": {
                        "filter": "item",
                        "values": variable["values"],
                    },
                }
                for variable in metadata["variables"]
            ]

            response = client.post(
                url,
                json={"query": query, "response": {"format": "json"}},
                timeout=120,
            )
            response.raise_for_status()
            data = json.loads(response.content.decode("utf-8-sig"))

            dimensions = {
                variable["code"]: dict(
                    zip(variable["values"], variable["valueTexts"])
                )
                for variable in metadata["variables"]
            }
            dimension_codes = [
                column["code"]
                for column in data["columns"]
                if column["type"] == "d"
            ]
            content_columns = [
                column
                for column in data["columns"]
                if column["type"] == "c"
            ]

            rows = []
            for observation in data["data"]:
                dimension_values = {
                    code: dimensions[code][key]
                    for code, key in zip(dimension_codes, observation["key"])
                }

                for index, content in enumerate(content_columns):
                    value_raw = (
                        observation["values"][index]
                        if index < len(observation["values"])
                        else None
                    )

                    row = dimension_values.copy()
                    if len(content_columns) > 1:
                        row["content"] = content["text"]

                    row["value_raw"] = value_raw
                    row["value"] = pd.to_numeric(value_raw, errors="coerce")
                    rows.append(row)

            return metadata, pd.DataFrame(rows)

        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))

    raise last_error


def build_metadata_record(database, table_id, metadata, n_rows):
    """Build the metadata record used by the existing raw layer."""
    return {
        "database": database,
        "table_id": table_id,
        "title": metadata["title"],
        "source": "json_api",
        "url": f"{BASE_API}/{quote(database, safe='')}/{table_id}",
        "n_rows": n_rows,
        "n_variables": len(metadata["variables"]),
        "variables": [
            {
                "code": variable["code"],
                "text": variable["text"],
                "n_values": len(variable["values"]),
                "values": variable["values"],
                "valueTexts": variable["valueTexts"],
            }
            for variable in metadata["variables"]
        ],
    }


def download_api_catalog(catalog, output_dir, log_path=None, max_retries=3, mode="resume"):
    """Resume validated local tables or refresh and compare source observations."""
    if mode not in {"resume", "refresh"}:
        raise ValueError("mode must be resume or refresh")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = PxState()
    api_catalog = catalog.loc[catalog["source"] == "json_api"].copy()
    results = []

    with requests.Session() as session:
        for position, row in enumerate(api_catalog.itertuples(index=False), start=1):
            database, table_id = row.database, row.table_id
            parquet_path = output_dir / f"{database}__{table_id}.parquet"
            metadata_path = output_dir / f"{database}__{table_id}.metadata.json"
            valid = state.valid(database, table_id, "json_api", parquet_path, metadata_path)
            status, count, error = "error", None, None
            if mode == "resume" and valid:
                status = "skipped"
                count = state.record(database, table_id)["structure"]["row_count"]
            else:
                candidate = metadata_candidate = None
                try:
                    metadata, frame = download_api_table(database, table_id,
                                                         max_retries=max_retries, session=session)
                    structure = facts(frame)
                    record = build_metadata_record(database, table_id, metadata, len(frame))
                    with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".pxweb-",
                                                     suffix=".parquet", delete=False) as f:
                        candidate = Path(f.name)
                    frame.to_parquet(candidate, index=False)
                    if facts(pd.read_parquet(candidate)) != structure:
                        raise ValueError("Candidate Parquet validation failed")
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_dir,
                                                     prefix=".pxweb-", suffix=".json",
                                                     delete=False) as f:
                        metadata_candidate = Path(f.name)
                        json.dump(record, f, ensure_ascii=False, indent=2)
                    if valid and frame.equals(pd.read_parquet(parquet_path)):
                        with metadata_path.open(encoding="utf-8") as f:
                            old_meta = json.load(f)
                        if record == old_meta:
                            state.checked(database, table_id)
                            status = "unchanged"
                    if status != "unchanged":
                        state.publish(database, table_id, "json_api", record["url"],
                                      parquet_path, candidate, structure,
                                      metadata_path, metadata_candidate)
                        status = "success"
                    count = len(frame)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    for temporary in (candidate, metadata_candidate):
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
            results.append({"database": database, "table_id": table_id, "status": status,
                            "rows": count, "error": error})
            print(f"[{position}/{len(api_catalog)}] {status.upper()} {database} | {table_id}"
                  + (f" | {error}" if error else ""))

    log = pd.DataFrame(results)
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        exists = log_path.exists()
        log.to_csv(log_path, mode="a", header=not exists, index=False,
                   encoding="utf-8" if exists else "utf-8-sig")
    return log


def run_api_download(max_retries=3, mode="resume", database=None, table_id=None):
    """Run the API branch against the bootstrapped state registry."""
    catalog = pd.read_csv(PXWEB_CATALOG_PATH, encoding="utf-8-sig")
    if (database is None) != (table_id is None):
        raise ValueError("Specify both database and table_id, or neither")
    if database is not None:
        catalog = catalog.loc[
            catalog["database"].eq(database) & catalog["table_id"].eq(table_id)
            & catalog["source"].eq("json_api")
        ]
        if len(catalog) != 1:
            raise ValueError(f"Expected one API table: {database} | {table_id}; found {len(catalog)}")
    return download_api_catalog(catalog, PXWEB_API_ROOT, PXWEB_API_LOG_PATH,
                                max_retries=max_retries, mode=mode)

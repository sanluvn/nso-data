"""Download NSO PX-Web HTML-form tables through their JSON-stat export."""

import json
import random
import time
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .px_state import PxState, facts

from .paths import (
    PXWEB_CATALOG_PATH,
    PXWEB_HTML_LOG_PATH,
    PXWEB_HTML_ROOT,
)

def decode_nso_jsonstat(data):
    """Decode NSO JSON-stat to dimension-label columns plus raw/numeric values."""
    dataset = data["dataset"]
    dimensions = dataset["dimension"]
    dimension_ids = dimensions["id"]
    dimension_sizes = dimensions["size"]

    categories = {}
    for dimension_id in dimension_ids:
        category = dimensions[dimension_id]["category"]
        labels = category["label"]
        index = category["index"]
        ordered_codes = sorted(index, key=index.get)
        categories[dimension_id] = [
            {"code": code, "label": labels[code]}
            for code in ordered_codes
        ]

    values = dataset["value"]

    expected_count = 1
    for size in dimension_sizes:
        expected_count *= size

    if len(values) != expected_count:
        raise ValueError(
            f"Value count mismatch: {len(values):,} != {expected_count:,}"
        )

    records = []
    for flat_index, value in enumerate(values):
        remainder = flat_index
        coordinates = []

        # NSO JSON-stat: last dimension changes fastest.
        for size in reversed(dimension_sizes):
            coordinates.append(remainder % size)
            remainder //= size
        coordinates.reverse()

        record = {}
        for dimension_id, coordinate in zip(dimension_ids, coordinates):
            record[dimension_id] = categories[dimension_id][coordinate]["label"]

        record["value_raw"] = value
        record["value"] = value
        records.append(record)

    frame = pd.DataFrame(records)
    frame["value"] = pd.to_numeric(frame["value_raw"], errors="coerce")
    return frame


LOG_COLUMNS = [
    "table_key",
    "database",
    "table_id",
    "table_title",
    "status",
    "rows",
    "missing_values",
    "dimensions",
    "expected_rows",
    "attempts",
    "error",
    "output_file",
    "timestamp",
]


def fetch_html_jsonstat_production(url, timeout=90, max_retries=3):
    """Submit one NSO HTML-form table and retrieve its JSON-stat export."""
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            # A fresh session per attempt matches the validated production logic.
            with requests.Session() as session:
                response = session.get(url, timeout=timeout)
                response.raise_for_status()

                soup = BeautifulSoup(response.content, "html.parser")
                form = soup.find("form")
                if form is None:
                    raise RuntimeError("Variable-selection form not found")

                payload = {}

                # Preserve hidden inputs, textareas, and checked controls.
                for element in form.find_all(["input", "textarea"]):
                    name = element.get("name")
                    if not name:
                        continue

                    input_type = (element.get("type") or "").lower()

                    if input_type in {"submit", "button", "image", "reset"}:
                        continue

                    if (
                        input_type in {"checkbox", "radio"}
                        and not element.has_attr("checked")
                    ):
                        continue

                    payload[name] = element.get("value", "")

                # Select all available values for every table dimension.
                multiple_selects = form.select("select[multiple]")
                if not multiple_selects:
                    raise RuntimeError("No multiple-select variables found")

                for select in multiple_selects:
                    name = select.get("name")
                    if not name:
                        continue

                    values = [
                        option["value"]
                        for option in select.find_all("option")
                        if option.has_attr("value")
                    ]
                    if values:
                        payload[name] = values

                submit_name = (
                    "ctl00$ContentPlaceHolderMain$VariableSelector1"
                    "$VariableSelector1$ButtonViewTable"
                )
                payload[submit_name] = "Tiếp tục"

                result = session.post(url, data=payload, timeout=timeout)
                result.raise_for_status()

                result_soup = BeautifulSoup(result.content, "html.parser")

                jsonstat_href = None
                for option in result_soup.find_all("option"):
                    value = option.get("value", "")
                    if "FileTypeJsonStat" in value:
                        jsonstat_href = value
                        break

                if jsonstat_href is None:
                    for element in result_soup.find_all(["option", "a"]):
                        text_value = element.get_text(" ", strip=True)
                        href = element.get("href", "")
                        if "JsonStat" in text_value or "JsonStat" in href:
                            jsonstat_href = href or element.get("value")
                            break

                if not jsonstat_href:
                    raise RuntimeError("JSON-stat export option not found")

                if jsonstat_href.startswith("/"):
                    jsonstat_url = urljoin(url, jsonstat_href)
                elif jsonstat_href.startswith("http"):
                    jsonstat_url = jsonstat_href
                else:
                    jsonstat_url = urljoin(result.url, jsonstat_href)

                json_response = session.get(jsonstat_url, timeout=timeout)
                json_response.raise_for_status()

                raw_text = json_response.content.decode("utf-8-sig")
                raw_text = raw_text.rstrip("\x00")
                data = json.loads(raw_text)

                if "dataset" not in data:
                    raise RuntimeError(
                        "JSON-stat response does not contain 'dataset'"
                    )

                return data

        except Exception as exc:
            last_error = exc

            if attempt < max_retries:
                wait = 2 ** (attempt - 1) + random.uniform(0.5, 1.5)
                time.sleep(wait)

    raise last_error


def download_html_catalog(catalog, output_dir, log_path, timeout=90,
                          max_attempts=3, mode="resume"):
    """Resume validated HTML tables or refresh from JSON-stat and compare."""
    if mode not in {"resume", "refresh"}:
        raise ValueError("mode must be resume or refresh")
    output_dir, log_path = Path(output_dir), Path(log_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    state = PxState()
    html_catalog = catalog.loc[catalog["source"] == "html_form"].copy()
    rows = []

    for position, row in enumerate(html_catalog.itertuples(index=False), start=1):
        database, table_id = row.database, row.table_id
        safe_name = (f"{database}__{table_id}".replace("/", "_")
                     .replace("\\", "_").replace(":", "_"))
        output_file = output_dir / f"{safe_name}.parquet"
        valid = state.valid(database, table_id, "html_form", output_file)
        status, error, attempts, count = "ERROR", "", 0, None
        candidate = None
        if mode == "resume" and valid:
            status = "SKIPPED"
            count = state.record(database, table_id)["structure"]["row_count"]
        else:
            try:
                data = None
                for attempt in range(1, max_attempts + 1):
                    attempts = attempt
                    try:
                        data = fetch_html_jsonstat_production(row.table_url,
                                                              timeout=timeout, max_retries=1)
                        break
                    except Exception:
                        if attempt == max_attempts:
                            raise
                        time.sleep(2 ** (attempt - 1) + random.uniform(0.5, 1.5))
                frame = decode_nso_jsonstat(data)
                dims = data["dataset"]["dimension"]
                if len(dims["id"]) != len(dims["size"]):
                    raise ValueError("Dimension ID / size mismatch")
                expected = 1
                for size in dims["size"]:
                    expected *= int(size)
                structure = facts(frame)
                if len(frame) != expected or structure["dimension_count"] != len(dims["id"]):
                    raise ValueError(f"JSON-stat structure mismatch: {len(frame)} != {expected}")
                with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".pxweb-",
                                                 suffix=".parquet", delete=False) as f:
                    candidate = Path(f.name)
                frame.to_parquet(candidate, index=False)
                if facts(pd.read_parquet(candidate)) != structure:
                    raise ValueError("Candidate Parquet validation failed")
                if valid and frame.equals(pd.read_parquet(output_file)):
                    state.checked(database, table_id)
                    status = "UNCHANGED"
                else:
                    state.publish(database, table_id, "html_form", row.table_url,
                                  output_file, candidate, structure)
                    status = "SUCCESS"
                count = len(frame)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                if candidate is not None:
                    candidate.unlink(missing_ok=True)
        rows.append({"table_key": f"{database}__{table_id}", "database": database,
                     "table_id": table_id, "table_title": row.table_title, "status": status,
                     "rows": count, "missing_values": "", "dimensions": "",
                     "expected_rows": "", "attempts": attempts, "error": error,
                     "output_file": str(output_file), "timestamp": datetime.now().isoformat(timespec="seconds")})
        print(f"[{position}/{len(html_catalog)}] {status} | {database} | {table_id}"
              + (f" | {error}" if error else ""))

    # Operational log is append-only and is never used to decide whether to skip.
    log = pd.DataFrame(rows, columns=LOG_COLUMNS)
    log.to_csv(log_path, mode="a", header=not log_path.exists(), index=False,
               encoding="utf-8-sig" if not log_path.exists() else "utf-8")
    return log


def run_html_download(timeout=90, max_attempts=3, mode="resume",
                      database=None, table_id=None):
    """Run the HTML branch against the bootstrapped state registry."""
    catalog = pd.read_csv(PXWEB_CATALOG_PATH, encoding="utf-8-sig")
    if (database is None) != (table_id is None):
        raise ValueError("Specify both database and table_id, or neither")
    if database is not None:
        catalog = catalog.loc[
            catalog["database"].eq(database) & catalog["table_id"].eq(table_id)
            & catalog["source"].eq("html_form")
        ]
        if len(catalog) != 1:
            raise ValueError(f"Expected one HTML table: {database} | {table_id}; found {len(catalog)}")
    return download_html_catalog(catalog, PXWEB_HTML_ROOT, PXWEB_HTML_LOG_PATH,
                                 timeout=timeout, max_attempts=max_attempts, mode=mode)

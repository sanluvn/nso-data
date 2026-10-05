"""Local PX-Web state, validation, and recoverable per-table publication.

Run one downloader process at a time. Logs are never consulted for state.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .paths import PROJECT_ROOT, PXWEB_ACQUISITION_STATE_PATH

TECHNICAL = {"value", "value_raw"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()


def facts(frame):
    dims = [c for c in frame.columns if c not in TECHNICAL]
    if (not len(frame) or not dims or not TECHNICAL.issubset(frame.columns)
            or frame.columns.has_duplicates or frame[dims].isna().any().any()
            or frame.duplicated(subset=dims).any()):
        raise ValueError("Invalid PX coordinates or missing required columns")
    expected = math.prod(int(frame[c].nunique(dropna=False)) for c in dims)
    if expected != len(frame):
        raise ValueError(f"Incomplete coordinate grid: {len(frame)} != {expected}")
    return {"row_count": len(frame), "dimension_count": len(dims),
            "expected_coordinate_count": expected}


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".pxweb-", suffix=".tmp", delete=False) as f:
            name = Path(f.name)
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


class PxState:
    def __init__(self, path=PXWEB_ACQUISITION_STATE_PATH):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError("Bootstrap pxweb_acquisition_state.json first")
        with self.path.open(encoding="utf-8") as f:
            self.data = json.load(f)
        if self.data.get("schema_version") != 1 or not isinstance(self.data.get("tables"), dict):
            raise ValueError("Unsupported PX-Web state schema")
        self.recover()

    @staticmethod
    def key(database, table_id):
        return f"vi|{database}|{table_id}"

    def record(self, database, table_id):
        return self.data["tables"].get(self.key(database, table_id))

    def valid(self, database, table_id, source, parquet, metadata=None):
        record = self.record(database, table_id)
        if not record or record.get("mechanism") != source or record.get("status") != "CURRENT":
            return False
        a = record["artifact"]
        if (a["parquet_path"] != relative(parquet)
                or a["metadata_path"] != (relative(metadata) if metadata else None)):
            return False
        try:
            if digest(parquet) != a["parquet_sha256"]:
                return False
            if metadata and digest(metadata) != a["metadata_sha256"]:
                return False
            if metadata:
                with Path(metadata).open(encoding="utf-8") as f:
                    meta = json.load(f)
                if (meta.get("database") != database or meta.get("table_id") != table_id
                        or meta.get("n_rows") != record["structure"]["row_count"]):
                    return False
            return facts(pd.read_parquet(parquet)) == record["structure"]
        except Exception:
            # Any unreadable or structurally invalid local artifact needs reacquisition.
            return False

    def save_record(self, database, table_id, record):
        key = self.key(database, table_id)
        previous = self.data["tables"].get(key)
        self.data["tables"][key] = record
        try:
            write_json_atomic(self.path, self.data)
        except Exception:
            if previous is None:
                self.data["tables"].pop(key, None)
            else:
                self.data["tables"][key] = previous
            raise

    def checked(self, database, table_id):
        record = dict(self.record(database, table_id))
        record["last_checked_at_utc"] = utc_now()
        self.save_record(database, table_id, record)

    @property
    def journal(self):
        return self.path.with_name(".pxweb_publish_journal.json")

    def recover(self):
        if not self.journal.exists():
            return
        with self.journal.open(encoding="utf-8") as f:
            j = json.load(f)
        record = self.data["tables"].get(j["key"])
        committed = record is not None and record.get("publication_id") == j["publication_id"]
        if not committed:
            for item in j["files"]:
                target, backup = Path(item["target"]), Path(item["backup"])
                if item["existed"]:
                    if not backup.is_file():
                        raise RuntimeError(f"Missing recovery backup: {backup}")
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
        for item in j["files"]:
            Path(item["backup"]).unlink(missing_ok=True)
        self.journal.unlink()

    def publish(self, database, table_id, source, url, parquet, candidate,
                structure, metadata=None, metadata_candidate=None):
        if self.journal.exists():
            raise RuntimeError("Pending PX publication journal; recover first")
        now = utc_now()
        publication_id = uuid.uuid4().hex
        old = self.record(database, table_id) or {}
        artifact = {"parquet_path": relative(parquet), "parquet_sha256": digest(candidate),
                    "metadata_path": relative(metadata) if metadata else None,
                    "metadata_sha256": digest(metadata_candidate) if metadata else None}
        record = {"identity": {"language": "vi", "database": database, "table_id": table_id},
                  "mechanism": source, "source_url": url, "status": "CURRENT",
                  "artifact": artifact, "structure": structure,
                  "last_success_at_utc": now, "last_checked_at_utc": now,
                  "bootstrapped_at_utc": old.get("bootstrapped_at_utc"),
                  "publication_id": publication_id}
        pairs = [(Path(parquet), Path(candidate))]
        if metadata:
            pairs.append((Path(metadata), Path(metadata_candidate)))
        files = []
        for target, _ in pairs:
            backup = target.with_name("." + target.name + ".pxweb-backup")
            if backup.exists():
                raise FileExistsError(f"Recovery backup already exists: {backup}")
            files.append({"target": str(target), "backup": str(backup), "existed": target.exists()})
        for item in files:
            if item["existed"]:
                shutil.copy2(item["target"], item["backup"])
        try:
            write_json_atomic(self.journal, {"key": self.key(database, table_id),
                                             "publication_id": publication_id, "files": files})
            for target, new in pairs:
                os.replace(new, target)
            self.save_record(database, table_id, record)
        finally:
            if self.journal.exists():
                self.recover()
            else:
                for item in files:
                    Path(item["backup"]).unlink(missing_ok=True)

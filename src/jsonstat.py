"""Decode NSO JSON-stat responses into long-format pandas DataFrames."""

import pandas as pd


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

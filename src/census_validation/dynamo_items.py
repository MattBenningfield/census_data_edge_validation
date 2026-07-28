"""
DynamoDB-shaped item builders (plain dicts, ready for boto3.put_item).

Two shapes matching dynamo_schema.md:
  * Validation Errors (Table 4) — one item per (rejected row, reason note).
  * Files (Table 3) — one file-level META item with a validation_checks summary.
"""

from __future__ import annotations

import hashlib
import os

import pandas as pd

from .timestamp import timestamp_series

# Sentinel timestamp for errors that can't be tied to a valid hour (per
# dynamo_schema.md's note on structural / timestamp-less errors).
SENTINEL_TIMESTAMP = "0000-00-00T00:00:00Z"

# Ordered file-level checks reported in the Files item's validation_checks[].
CHECK_ORDER = [
    ("SCHEMA", "Schema / required columns"),
    ("REQUIRED_VALUE", "Required fields present"),
    ("CELL_CLEANLINESS", "Cell cleanliness"),
    ("VALUE_FORMAT", "Type & range formatting"),
    ("UNKNOWN_UNIT", "Unit in the known-units list"),
    ("DUPLICATE_RECORD", "Duplicate records"),
]


def classify_note(note: str) -> tuple[str, str]:
    """
    Map a per-row rejection note to a (check_id, column) pair.

    Notes have the form "Column: issue" (e.g. "VolHour: outside 0-23"), plus the
    special cases "duplicate value" and the file-level "Schema: ..." reason.
    """
    if note == "duplicate value":
        return "DUPLICATE_RECORD", ""
    if note.startswith("Schema:"):
        return "SCHEMA", ""
    column, sep, issue = note.partition(": ")
    if not sep:
        return "VALUE_FORMAT", ""
    issue = issue.lower()
    if "missing/empty" in issue:
        return "REQUIRED_VALUE", column
    if "whitespace" in issue or "quote" in issue or "control" in issue:
        return "CELL_CLEANLINESS", column
    if "not in valid list" in issue:
        return "UNKNOWN_UNIT", column
    return "VALUE_FORMAT", column


def json_safe(value):
    """Convert a DataFrame cell to a JSON-serializable native value (or None)."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):  # numpy scalar -> native Python scalar
        value = value.item()
    return value


def build_error_items(failed_df, file_id, created_at, reason_column) -> list[dict]:
    """
    Build Validation Errors (Table 4) items — one per (row, reason note).

    Returns:
        list[dict]: Items keyed by PK=FILE#<file_id> and a timestamped SK.
    """
    items: list[dict] = []
    if failed_df.empty:
        return items

    ts = timestamp_series(failed_df)
    for idx, reason in failed_df[reason_column].items():
        try:
            record_no = int(idx) + 1
        except (TypeError, ValueError):
            record_no = None
        row_ts = ts.loc[idx] or SENTINEL_TIMESTAMP
        facility = json_safe(failed_df.at[idx, "Facility"]) \
            if "Facility" in failed_df.columns else None
        unit = json_safe(failed_df.at[idx, "Unit"]) \
            if "Unit" in failed_df.columns else None

        for note in str(reason).split("; "):
            if not note:
                continue
            check_id, column = classify_note(note)
            invalid_value = (
                json_safe(failed_df.at[idx, column])
                if column and column in failed_df.columns else None
            )
            error_id = hashlib.sha1(
                f"{file_id}|{record_no}|{check_id}|{column}".encode()
            ).hexdigest()[:8]
            items.append({
                "PK": f"FILE#{file_id}",
                "SK": f"TS#{row_ts}#CHECK#{check_id}#FIELD#{column}#ERROR#{error_id}",
                "row_number": record_no,
                "timestamp": row_ts,
                "check_id": check_id,
                "column": column or None,
                "severity": "ERROR",
                "message": note,
                "invalid_value": invalid_value,
                "facility_id": facility,
                "unit_id": unit,
                "item_id": None,
                "created_at": created_at,
            })
    return items


def build_file_item(csv_path, file_id, df, error_items, created_at,
                    org_id, file_type, schema_invalid=False, schema_reason=None) -> dict:
    """
    Build the file-level Files (Table 3) META item.

    Returns:
        dict: An item keyed by PK=FILE#<file_id> / SK=META with the file-level
        attributes and a validation_checks[] summary.
    """
    def _unique(col):
        if col not in df.columns:
            return []
        return sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()})

    valid_ts = sorted(t for t in timestamp_series(df) if t)

    if schema_invalid:
        checks = [{
            "check_id": "SCHEMA",
            "display_order": 1,
            "result": "ERROR",
            "summary": schema_reason or "Schema invalid",
            "affected_row_count": int(len(df)),
        }]
    else:
        affected = {cid: set() for cid, _ in CHECK_ORDER}
        for e in error_items:
            if e["check_id"] in affected and e["row_number"] is not None:
                affected[e["check_id"]].add(e["row_number"])
        checks = []
        for order, (cid, label) in enumerate(CHECK_ORDER, start=1):
            n = len(affected[cid])
            checks.append({
                "check_id": cid,
                "display_order": order,
                "result": "ERROR" if n else "PASS",
                "summary": f"{n} record(s) affected" if n else f"{label}: passed",
                "affected_row_count": n,
            })

    error_count = sum(1 for c in checks if c["result"] == "ERROR")
    return {
        "PK": f"FILE#{file_id}",
        "SK": "META",
        "org_id": org_id,
        "file_name": os.path.basename(csv_path),
        "file_type": file_type,
        "row_count": int(len(df)),
        "facility_ids": _unique("Facility"),
        "unit_ids": _unique("Unit"),
        "census_start": valid_ts[0] if valid_ts else None,
        "census_end": valid_ts[-1] if valid_ts else None,
        "workflow_status": "COMPLETED",
        "validation_result": "BLOCKED" if error_count else "CLEAN",
        "error_count": error_count,
        "warning_count": 0,
        "validation_checks": checks,
    }

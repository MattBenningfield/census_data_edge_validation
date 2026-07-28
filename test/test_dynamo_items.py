"""Tests for census_validation.dynamo_items (DynamoDB item builders)."""

import pandas as pd

from census_validation import dynamo_items as di

REASON = "RejectionReason"


def test_classify_note():
    assert di.classify_note("duplicate value") == ("DUPLICATE_RECORD", "")
    assert di.classify_note("Schema: missing columns ['x']") == ("SCHEMA", "")
    assert di.classify_note("Facility: missing/empty") == ("REQUIRED_VALUE", "Facility")
    assert di.classify_note("Unit: quote character") == ("CELL_CLEANLINESS", "Unit")
    assert di.classify_note("Unit: not in valid list") == ("UNKNOWN_UNIT", "Unit")
    assert di.classify_note("Volume: below minimum (0)") == ("VALUE_FORMAT", "Volume")


def test_json_safe():
    assert di.json_safe(None) is None
    assert di.json_safe(pd.NA) is None
    assert di.json_safe(pd.Series([5], dtype="int64").iloc[0]) == 5
    assert di.json_safe("text") == "text"


def _failed():
    return pd.DataFrame({
        "Type": ["Census", "Census"],
        "Facility": ["F", "F"],
        "Unit": ["UnitA", "9999-Unknown"],
        "VolDate": ["1/13/2026", "1/13/2026"],
        "VolHour": [5, 6],
        "Volume": [-1, 5],
        REASON: ["Volume: below minimum (0)", "Unit: not in valid list"],
    })


def test_build_error_items_maps_reasons():
    items = di.build_error_items(_failed(), "file-abc", "2026-07-28T00:00:00Z", REASON)
    assert len(items) == 2

    vol = next(i for i in items if i["check_id"] == "VALUE_FORMAT")
    assert vol["PK"] == "FILE#file-abc"
    assert vol["SK"].startswith(
        "TS#2026-01-13T05:00:00Z#CHECK#VALUE_FORMAT#FIELD#Volume#ERROR#")
    assert vol["column"] == "Volume" and vol["severity"] == "ERROR"
    assert vol["invalid_value"] == -1
    assert vol["timestamp"] == "2026-01-13T05:00:00Z"

    unit = next(i for i in items if i["check_id"] == "UNKNOWN_UNIT")
    assert unit["row_number"] == 2 and unit["message"] == "Unit: not in valid list"


def test_build_error_items_sentinel_timestamp():
    failed = pd.DataFrame({
        "Type": ["Census"], "Facility": ["F"], "Unit": ["UnitA"],
        "VolDate": ["not-a-date"], "VolHour": [5], "Volume": [5],
        REASON: ["VolDate: not a valid date"],
    })
    items = di.build_error_items(failed, "f1", "2026-07-28T00:00:00Z", REASON)
    assert items[0]["timestamp"] == di.SENTINEL_TIMESTAMP
    assert f"TS#{di.SENTINEL_TIMESTAMP}#" in items[0]["SK"]


def test_build_file_item_summary():
    df = pd.DataFrame([
        ["Census", "F1", "UnitA", "1/13/2026", 0, 10],
        ["Census", "F1", "UnitB", "1/14/2026", 1, 20],
    ], columns=["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"])
    errors = [{"check_id": "VALUE_FORMAT", "row_number": 2}]
    item = di.build_file_item("batch.csv", "file-xyz", df, errors,
                              "2026-07-28T00:00:00Z", "acme", "CENSUS")
    assert item["PK"] == "FILE#file-xyz" and item["SK"] == "META"
    assert item["org_id"] == "acme" and item["file_name"] == "batch.csv"
    assert item["row_count"] == 2
    assert item["facility_ids"] == ["F1"]
    assert item["unit_ids"] == ["UnitA", "UnitB"]
    assert item["census_start"] == "2026-01-13T00:00:00Z"
    assert item["census_end"] == "2026-01-14T01:00:00Z"
    assert item["validation_result"] == "BLOCKED" and item["error_count"] == 1
    fmt = next(c for c in item["validation_checks"] if c["check_id"] == "VALUE_FORMAT")
    assert fmt["result"] == "ERROR" and fmt["affected_row_count"] == 1


def test_build_file_item_schema_invalid():
    df = pd.DataFrame([["Census", "F", "U", "1/13/2026", 0]],
                      columns=["Type", "Facility", "Unit", "VolDate", "VolHour"])
    item = di.build_file_item("batch.csv", "f", df, [], "2026-07-28T00:00:00Z",
                              "acme", "CENSUS", schema_invalid=True,
                              schema_reason="Schema: missing columns ['Volume']")
    assert item["validation_result"] == "BLOCKED"
    assert item["validation_checks"] == [{
        "check_id": "SCHEMA", "display_order": 1, "result": "ERROR",
        "summary": "Schema: missing columns ['Volume']", "affected_row_count": 1,
    }]

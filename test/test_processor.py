"""End-to-end tests for census_validation.processor.CensusFileProcessor."""

import json
import os

import pandas as pd

from census_validation.processor import CensusFileProcessor

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


def _make_processor(tmp_path, write_config, valid_units, **cfg_kwargs):
    units = tmp_path / "valid_units.txt"
    units.write_text("\n".join(valid_units) + "\n", encoding="utf-8")
    cfg = write_config(tmp_path, units, **cfg_kwargs)
    return CensusFileProcessor.from_config_file(str(cfg))


def _write_csv(tmp_path, rows, name="batch.csv"):
    path = tmp_path / name
    pd.DataFrame(rows, columns=COLS).to_csv(path, index=False)
    return path


# ── validate_records integration ─────────────────────────────────────────────

def test_validate_records_all_valid(processor, make_df):
    unit = sorted(processor.valid_units)[0]
    df = make_df([["Census", "F", unit, "1/13/2026", 0, 5]])
    assert (processor.validate_records(df) == "").all()


def test_validate_records_multiple_reasons(processor, make_df):
    df = make_df([["Census", "", "9999-Unknown Test Unit", "bad", 99, -1]])
    reason = processor.validate_records(df).iloc[0]
    for fragment in ("Facility: missing/empty", "not in valid list",
                     "not a valid date", "outside 0-23", "below minimum"):
        assert fragment in reason


# ── process_file routing ──────────────────────────────────────────────────────

def test_splits_pass_and_fail(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = _write_csv(tmp_path, [
        ["Census", "F", "UnitA", "1/13/2026", 0, 5],
        ["Census", "F", "UnitA", "1/13/2026", 1, 6],
        ["Census", "F", "UnitA", "1/13/2026", 99, 7],   # bad hour
        ["Census", "F", "BadUnit", "1/13/2026", 3, 8],  # unknown unit
    ])
    assert proc.process_file(str(csv)) == 1
    ok = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert len(ok) == 2 and len(bad) == 2
    assert "RejectionReason" in bad.columns and "RejectionReason" not in ok.columns


def test_all_valid_exits_zero(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = _write_csv(tmp_path, [["Census", "F", "UnitA", "1/13/2026", h, 5]
                                for h in range(3)])
    assert proc.process_file(str(csv)) == 0
    assert not os.path.isdir(tmp_path / "bad")


def test_schema_gate_rejects_whole_file(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = tmp_path / "batch.csv"
    pd.DataFrame([["Census", "F", "UnitA", "1/13/2026", 0]],
                 columns=["Type", "Facility", "Unit", "VolDate", "VolHour"]).to_csv(
        csv, index=False)
    assert proc.process_file(str(csv)) == 1
    assert not os.path.isdir(tmp_path / "ok")
    bad = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert bad["RejectionReason"].str.contains("Volume").any()


def test_missing_input_returns_two(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    assert proc.process_file(str(tmp_path / "nope.csv")) == 2


def test_reusable_across_files(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    good = _write_csv(tmp_path, [["Census", "F", "UnitA", "1/13/2026", 0, 5]], "good.csv")
    bad = _write_csv(tmp_path, [["Census", "F", "BadUnit", "1/13/2026", 0, 5]], "bad_in.csv")
    assert proc.process_file(str(good)) == 0
    assert proc.process_file(str(bad)) == 1


# ── output shape / summary / dynamo ───────────────────────────────────────────

def test_output_uses_timestamp_column(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = _write_csv(tmp_path, [
        ["Census", "F", "UnitA", "1/13/2026", 5, 17],
        ["Census", "F", "BadUnit", "1/13/2026", 6, 8],
    ])
    assert proc.process_file(str(csv)) == 1
    ok = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    assert "VolTimestamp" in ok.columns
    assert "VolDate" not in ok.columns and "VolHour" not in ok.columns
    assert ok["VolTimestamp"].iloc[0] == "2026-01-13T05:00:00Z"


def test_duplicates_routed(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = _write_csv(tmp_path, [
        ["Census", "F", "UnitA", "1/13/2026", 0, 10],   # dup lower -> rejected
        ["Census", "F", "UnitA", "1/13/2026", 0, 25],   # dup higher -> successful
        ["Census", "F", "UnitA", "1/13/2026", 1, 7],    # unique
    ])
    assert proc.process_file(str(csv)) == 1
    ok = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert sorted(ok["Volume"]) == [7, 25]
    assert len(bad) == 1 and bad["Volume"].iloc[0] == 10
    assert bad["RejectionReason"].str.contains("duplicate value").all()


def test_summary_written(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = _write_csv(tmp_path, [["Census", "F", "BadUnit", "1/13/2026", 0, 5]])
    assert proc.process_file(str(csv)) == 1
    assert len(os.listdir(tmp_path / "summary")) == 1


def test_summary_disabled(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"],
                           write_rejection_summary=False)
    csv = _write_csv(tmp_path, [["Census", "F", "BadUnit", "1/13/2026", 0, 5]])
    assert proc.process_file(str(csv)) == 1
    assert not os.path.isdir(tmp_path / "summary")


def test_dynamo_json_written(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"])
    csv = _write_csv(tmp_path, [
        ["Census", "F", "UnitA", "1/13/2026", 0, 5],
        ["Census", "F", "BadUnit", "1/13/2026", 1, 6],
    ])
    assert proc.process_file(str(csv)) == 1
    dynamo = tmp_path / "dynamo"
    file_json = next(p for p in os.listdir(dynamo) if "_file_" in p)
    errors_json = next(p for p in os.listdir(dynamo) if "_errors_" in p)
    file_item = json.loads((dynamo / file_json).read_text())
    assert file_item["SK"] == "META" and file_item["org_id"] == "test-org"
    assert file_item["validation_result"] == "BLOCKED"
    error_items = json.loads((dynamo / errors_json).read_text())
    assert len(error_items) == 1 and error_items[0]["check_id"] == "UNKNOWN_UNIT"


def test_dynamo_disabled(tmp_path, write_config):
    proc = _make_processor(tmp_path, write_config, ["UnitA"],
                           write_dynamo_output=False)
    csv = _write_csv(tmp_path, [["Census", "F", "BadUnit", "1/13/2026", 0, 5]])
    assert proc.process_file(str(csv)) == 1
    assert not os.path.isdir(tmp_path / "dynamo")


def test_record_mix_fixture(tmp_path, write_config):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(repo, "data", "test", "test_record_mix.csv")
    real_units = os.path.join(repo, "data", "valid_units.txt")
    if not os.path.isfile(src):
        import pytest
        pytest.skip("run test/generate_test_data.py to create the fixtures")

    cfg = write_config(tmp_path, real_units)
    proc = CensusFileProcessor.from_config_file(str(cfg))
    input_csv = tmp_path / "mix.csv"
    input_csv.write_bytes(open(src, "rb").read())

    assert proc.process_file(str(input_csv)) == 1
    ok = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert len(ok) > 0 and len(bad) > 0 and "RejectionReason" in bad.columns

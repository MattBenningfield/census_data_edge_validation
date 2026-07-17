"""
Tests for record_validation.py — the per-record (row-level) validator.

Covers each individual check via validate_records(), plus end-to-end routing
through main() (successful/rejected split, reason column, schema gate) and the
generated fixtures under data/test/.

Shared fixtures (make_df, write_config) come from conftest.py.
"""
import os

import pandas as pd
import pytest

import record_validation as v

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


def _first_unit():
    """A real unit name from the configured valid-units list."""
    return sorted(v.VALID_UNITS)[0]


# ─────────────────────────── per-record checks ──────────────────────────────

def test_all_valid_row_has_no_reason(make_df):
    df = make_df([["Census", "1100-York Hospital", _first_unit(), "1/13/2026", 0, 5]])
    assert (v.validate_records(df) == "").all()


def test_required_field_missing(make_df):
    df = make_df([
        ["Census", "F", _first_unit(), "1/13/2026", 0, 5],   # ok
        ["Census", "", _first_unit(), "1/13/2026", 1, 5],    # blank Facility
        ["Census", "F", _first_unit(), "1/13/2026", 2, None],  # missing Volume
    ])
    reasons = v.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "Facility: missing/empty" in reasons.iloc[1]
    assert "Volume: missing/empty" in reasons.iloc[2]


def test_cleanliness_flags_whitespace_quote_control(make_df):
    df = make_df([
        ["Census", "F", _first_unit(), "1/13/2026", 0, 5],       # clean
        ["Census", " F ", _first_unit(), "1/13/2026", 1, 5],     # whitespace
        ["Census", "F", 'Quote"Unit', "1/13/2026", 2, 5],        # quote char
        ["Census", "F", "Tab\tUnit", "1/13/2026", 3, 5],         # control char
    ])
    reasons = v.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "whitespace" in reasons.iloc[1] and "Facility" in reasons.iloc[1]
    assert "quote" in reasons.iloc[2].lower()
    assert "control" in reasons.iloc[3].lower()


def test_volhour_range_and_type(make_df):
    df = make_df([
        ["Census", "F", _first_unit(), "1/13/2026", 0, 5],     # ok
        ["Census", "F", _first_unit(), "1/13/2026", 5.5, 5],   # fractional
        ["Census", "F", _first_unit(), "1/13/2026", 25, 5],    # out of range
        ["Census", "F", _first_unit(), "1/13/2026", "x", 5],   # non-numeric
    ])
    reasons = v.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "not an integer" in reasons.iloc[1]
    assert "outside 0-23" in reasons.iloc[2]
    assert "not numeric" in reasons.iloc[3]


def test_volume_range_and_type(make_df):
    df = make_df([
        ["Census", "F", _first_unit(), "1/13/2026", 0, 5],     # ok
        ["Census", "F", _first_unit(), "1/13/2026", 1, -7],    # below minimum
        ["Census", "F", _first_unit(), "1/13/2026", 2, 3.5],   # fractional
        ["Census", "F", _first_unit(), "1/13/2026", 3, "abc"], # non-numeric
    ])
    reasons = v.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "below minimum" in reasons.iloc[1]
    assert "not an integer" in reasons.iloc[2]
    assert "not numeric" in reasons.iloc[3]


def test_voldate_parse(make_df):
    df = make_df([
        ["Census", "F", _first_unit(), "1/13/2026", 0, 5],       # ok
        ["Census", "F", _first_unit(), "13/45/2026", 1, 5],      # impossible date
        ["Census", "F", _first_unit(), "not-a-date", 2, 5],      # garbage
    ])
    reasons = v.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "not a valid date" in reasons.iloc[1]
    assert "not a valid date" in reasons.iloc[2]


def test_unknown_unit(make_df):
    df = make_df([
        ["Census", "F", _first_unit(), "1/13/2026", 0, 5],           # known
        ["Census", "F", "9999-Unknown Test Unit", "1/13/2026", 1, 5],  # unknown
    ])
    reasons = v.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "not in valid list" in reasons.iloc[1]


def test_multiple_reasons_join(make_df):
    """A single row failing several checks accumulates all of them."""
    df = make_df([["Census", "", "9999-Unknown Test Unit", "bad", 99, -1]])
    reason = v.validate_records(df).iloc[0]
    assert "Facility: missing/empty" in reason
    assert "not in valid list" in reason
    assert "not a valid date" in reason
    assert "outside 0-23" in reason
    assert "below minimum" in reason


# ─────────────────────────── end-to-end routing ─────────────────────────────

def test_main_splits_pass_and_fail(tmp_path, monkeypatch, write_config):
    units = tmp_path / "valid_units.txt"
    units.write_text("UnitA\n", encoding="utf-8")
    cfg_path = write_config(tmp_path, units)

    df = pd.DataFrame([
        ["Census", "F", "UnitA", "1/13/2026", 0, 5],   # passes
        ["Census", "F", "UnitA", "1/13/2026", 1, 6],   # passes
        ["Census", "F", "UnitA", "1/13/2026", 99, 7],  # bad hour -> rejected
        ["Census", "F", "BadUnit", "1/13/2026", 3, 8], # unknown unit -> rejected
    ], columns=COLS)
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    monkeypatch.setattr(v, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(v.sys, "argv", ["prog", str(input_csv)])
    with pytest.raises(SystemExit) as exc:
        v.main()
    assert exc.value.code == 1   # some records were rejected

    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert len(ok_df) == 2
    assert len(bad_df) == 2
    assert v.REJECTION_REASON_COLUMN in bad_df.columns
    assert v.REJECTION_REASON_COLUMN not in ok_df.columns
    reasons = bad_df[v.REJECTION_REASON_COLUMN].tolist()
    assert any("outside 0-23" in r for r in reasons)
    assert any("not in valid list" in r for r in reasons)


def test_main_all_valid_exits_zero(tmp_path, monkeypatch, write_config):
    units = tmp_path / "valid_units.txt"
    units.write_text("UnitA\n", encoding="utf-8")
    cfg_path = write_config(tmp_path, units)

    df = pd.DataFrame(
        [["Census", "F", "UnitA", "1/13/2026", h, 5] for h in range(3)],
        columns=COLS,
    )
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    monkeypatch.setattr(v, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(v.sys, "argv", ["prog", str(input_csv)])
    with pytest.raises(SystemExit) as exc:
        v.main()
    assert exc.value.code == 0
    assert not os.path.isdir(tmp_path / "bad")   # nothing rejected
    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    assert len(ok_df) == 3


def test_main_schema_gate_rejects_whole_file(tmp_path, monkeypatch, write_config):
    """A file missing an expected column is rejected wholesale."""
    units = tmp_path / "valid_units.txt"
    units.write_text("UnitA\n", encoding="utf-8")
    cfg_path = write_config(tmp_path, units)

    df = pd.DataFrame([["Census", "F", "UnitA", "1/13/2026", 0]],
                      columns=["Type", "Facility", "Unit", "VolDate", "VolHour"])
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    monkeypatch.setattr(v, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(v.sys, "argv", ["prog", str(input_csv)])
    with pytest.raises(SystemExit) as exc:
        v.main()
    assert exc.value.code == 1

    assert not os.path.isdir(tmp_path / "ok")
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert bad_df[v.REJECTION_REASON_COLUMN].str.contains("Volume").any()


# ─────────────────────────── rejection summary ──────────────────────────────

def test_format_ranges():
    assert v._format_ranges([3, 4, 5, 7, 10, 11]) == "3-5, 7, 10-11"
    assert v._format_ranges([1]) == "1"
    assert v._format_ranges([]) == ""
    assert v._format_ranges([5, 5, 4]) == "4-5"   # dedupes + sorts


def test_build_rejection_summary_groups_by_unit_and_error():
    u = _first_unit()
    failed = pd.DataFrame({
        "Unit":    [u, u, "BadUnit"],
        "VolDate": ["1/13/2026", "1/15/2026", "1/14/2026"],
        v.REJECTION_REASON_COLUMN: [
            "Volume: below minimum (0)",
            "Volume: below minimum (0)",
            "Unit: not in valid list",
        ],
    })
    summary = v.build_rejection_summary(failed)

    vol = summary[(summary["Unit"] == u) &
                  (summary["ErrorType"] == "Volume: below minimum (0)")].iloc[0]
    assert vol["Count"] == 2
    # index 0 and 1 here -> records 1 and 2, collapsed to a range
    assert vol["RecordNumbers"] == "1-2"
    assert vol["DateRange"] == "01/13/2026 - 01/15/2026"

    bad = summary[summary["Unit"] == "BadUnit"].iloc[0]
    assert bad["ErrorType"] == "Unit: not in valid list"
    assert bad["Count"] == 1
    assert bad["DateRange"] == "01/14/2026"


def test_build_rejection_summary_splits_multi_reason_row():
    """A single row with several reasons contributes to several summary rows."""
    failed = pd.DataFrame({
        "Unit": ["(missing)"],
        "VolDate": ["bad"],
        v.REJECTION_REASON_COLUMN: [
            "Unit: missing/empty; VolDate: not a valid date"
        ],
    })
    summary = v.build_rejection_summary(failed)
    assert set(summary["ErrorType"]) == {
        "Unit: missing/empty", "VolDate: not a valid date"
    }
    assert (summary["Count"] == 1).all()


def test_main_writes_rejection_summary(tmp_path, monkeypatch, write_config):
    units = tmp_path / "valid_units.txt"
    units.write_text("UnitA\n", encoding="utf-8")
    cfg_path = write_config(tmp_path, units)

    df = pd.DataFrame([
        ["Census", "F", "UnitA", "1/13/2026", 0, 5],    # passes
        ["Census", "F", "UnitA", "1/13/2026", 99, 7],   # bad hour
        ["Census", "F", "UnitA", "1/14/2026", 99, 7],   # bad hour (same unit)
        ["Census", "F", "BadUnit", "1/15/2026", 3, 8],  # unknown unit
    ], columns=COLS)
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    monkeypatch.setattr(v, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(v.sys, "argv", ["prog", str(input_csv)])
    with pytest.raises(SystemExit):
        v.main()

    summary_files = os.listdir(tmp_path / "summary")
    assert len(summary_files) == 1 and "rejected_summary" in summary_files[0]
    summary = pd.read_csv(tmp_path / "summary" / summary_files[0])

    hour_row = summary[summary["ErrorType"].str.contains("outside 0-23")].iloc[0]
    assert hour_row["Unit"] == "UnitA"
    assert hour_row["Count"] == 2
    assert hour_row["RecordNumbers"] == "2-3"   # 2nd and 3rd data rows
    assert hour_row["DateRange"] == "01/13/2026 - 01/14/2026"


def test_summary_disabled_by_config(tmp_path, monkeypatch, write_config):
    units = tmp_path / "valid_units.txt"
    units.write_text("UnitA\n", encoding="utf-8")
    cfg_path = write_config(tmp_path, units, write_rejection_summary=False)

    df = pd.DataFrame([["Census", "F", "BadUnit", "1/13/2026", 0, 5]], columns=COLS)
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    monkeypatch.setattr(v, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(v.sys, "argv", ["prog", str(input_csv)])
    with pytest.raises(SystemExit):
        v.main()
    assert not os.path.isdir(tmp_path / "summary")   # no summary written


# ─────────────────────────── generated fixtures ─────────────────────────────

def test_record_mix_fixture(tmp_path, monkeypatch, write_config):
    """The generated mixed fixture splits across successful and rejected."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(repo, "data", "test", "test_record_mix.csv")
    real_units = os.path.join(repo, "data", "valid_units.txt")
    if not os.path.isfile(src):
        pytest.skip("run test/generate_test_data.py to create the fixtures")

    cfg_path = write_config(tmp_path, real_units)
    input_csv = tmp_path / "mix.csv"
    input_csv.write_bytes(open(src, "rb").read())

    monkeypatch.setattr(v, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(v.sys, "argv", ["prog", str(input_csv)])
    with pytest.raises(SystemExit) as exc:
        v.main()
    assert exc.value.code == 1

    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert len(ok_df) > 0 and len(bad_df) > 0
    assert v.REJECTION_REASON_COLUMN in bad_df.columns

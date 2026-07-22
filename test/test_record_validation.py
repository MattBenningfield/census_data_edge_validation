"""
Tests for record_validation.py — the CensusFileProcessor class.

Covers the per-record checks, end-to-end routing, the schema gate, the rejection
summary, and the generated fixtures. process_file() returns an exit code, so
these tests assert on the return value rather than catching SystemExit.

Shared fixtures (make_df, write_config) come from conftest.py.
"""
import os

import pandas as pd
import pytest

from record_validation import CensusFileProcessor

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


@pytest.fixture
def processor():
    """A processor built from the real project config (realistic valid_units)."""
    return CensusFileProcessor.from_config_file()


def _first_unit(proc):
    """A real unit name from the processor's valid-units list."""
    return sorted(proc.valid_units)[0]


def _make_processor(tmp_path, monkeypatch, write_config, valid_units,
                    **cfg_kwargs):
    """Build a processor from a temp config with the given valid units."""
    units = tmp_path / "valid_units.txt"
    units.write_text("\n".join(valid_units) + "\n", encoding="utf-8")
    cfg_path = write_config(tmp_path, units, **cfg_kwargs)
    return CensusFileProcessor.from_config_file(str(cfg_path))


# ─────────────────────────── per-record checks ──────────────────────────────

def test_all_valid_row_has_no_reason(processor, make_df):
    df = make_df([["Census", "1100-York Hospital", _first_unit(processor),
                   "1/13/2026", 0, 5]])
    assert (processor.validate_records(df) == "").all()


def test_required_field_missing(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 5],       # ok
        ["Census", "", u, "1/13/2026", 1, 5],        # blank Facility
        ["Census", "F", u, "1/13/2026", 2, None],    # missing Volume
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "Facility: missing/empty" in reasons.iloc[1]
    assert "Volume: missing/empty" in reasons.iloc[2]


def test_cleanliness_flags_whitespace_quote_control(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 5],       # clean
        ["Census", " F ", u, "1/13/2026", 1, 5],     # whitespace
        ["Census", "F", 'Quote"Unit', "1/13/2026", 2, 5],   # quote char
        ["Census", "F", "Tab\tUnit", "1/13/2026", 3, 5],    # control char
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "whitespace" in reasons.iloc[1] and "Facility" in reasons.iloc[1]
    assert "quote" in reasons.iloc[2].lower()
    assert "control" in reasons.iloc[3].lower()


def test_volhour_range_and_type(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 5],       # ok
        ["Census", "F", u, "1/13/2026", 5.5, 5],     # fractional
        ["Census", "F", u, "1/13/2026", 25, 5],      # out of range
        ["Census", "F", u, "1/13/2026", "x", 5],     # non-numeric
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "not an integer" in reasons.iloc[1]
    assert "outside 0-23" in reasons.iloc[2]
    assert "not numeric" in reasons.iloc[3]


def test_volume_range_and_type(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 5],       # ok
        ["Census", "F", u, "1/13/2026", 1, -7],      # below minimum
        ["Census", "F", u, "1/13/2026", 2, 3.5],     # fractional
        ["Census", "F", u, "1/13/2026", 3, "abc"],   # non-numeric
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "below minimum" in reasons.iloc[1]
    assert "not an integer" in reasons.iloc[2]
    assert "not numeric" in reasons.iloc[3]


def test_voldate_parse(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 5],       # ok
        ["Census", "F", u, "13/45/2026", 1, 5],      # impossible date
        ["Census", "F", u, "not-a-date", 2, 5],      # garbage
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "not a valid date" in reasons.iloc[1]
    assert "not a valid date" in reasons.iloc[2]


def test_unknown_unit(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 5],                       # known
        ["Census", "F", "9999-Unknown Test Unit", "1/13/2026", 1, 5],  # unknown
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "not in valid list" in reasons.iloc[1]


def test_multiple_reasons_join(processor, make_df):
    df = make_df([["Census", "", "9999-Unknown Test Unit", "bad", 99, -1]])
    reason = processor.validate_records(df).iloc[0]
    assert "Facility: missing/empty" in reason
    assert "not in valid list" in reason
    assert "not a valid date" in reason
    assert "outside 0-23" in reason
    assert "below minimum" in reason


# ─────────────────────────── duplicate resolution ───────────────────────────

def test_duplicate_keeps_higher_volume(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 10],   # dup key, lower vol -> rejected
        ["Census", "F", u, "1/13/2026", 0, 25],   # dup key, higher vol -> kept
        ["Census", "F", u, "1/13/2026", 1, 5],    # unique key -> kept
    ])
    reasons = processor.validate_records(df)
    assert "duplicate value" in reasons.iloc[0]
    assert reasons.iloc[1] == ""
    assert reasons.iloc[2] == ""


def test_duplicate_three_rows_keeps_max(processor, make_df):
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 10],
        ["Census", "F", u, "1/13/2026", 0, 30],   # max -> kept
        ["Census", "F", u, "1/13/2026", 0, 20],
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[1] == ""
    assert "duplicate value" in reasons.iloc[0]
    assert "duplicate value" in reasons.iloc[2]


def test_exact_duplicate_rejected(processor, make_df):
    """Same key AND same Volume: keep the first, reject the rest."""
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 15],   # first -> kept
        ["Census", "F", u, "1/13/2026", 0, 15],   # exact dup -> rejected
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""
    assert "duplicate value" in reasons.iloc[1]


def test_duplicate_ignores_invalid_higher_volume(processor, make_df):
    """A clean lower-Volume row is kept over an invalid higher-Volume duplicate."""
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 10],     # clean -> kept
        ["Census", "F", u, "1/13/2026", 0, 20.5],   # fractional Volume -> rejected
    ])
    reasons = processor.validate_records(df)
    assert reasons.iloc[0] == ""                     # kept, not flagged duplicate
    assert "duplicate value" not in reasons.iloc[0]
    assert "not an integer" in reasons.iloc[1]


def test_no_false_duplicate_when_keys_differ(processor, make_df):
    """Same values but a different VolHour is not a duplicate."""
    u = _first_unit(processor)
    df = make_df([
        ["Census", "F", u, "1/13/2026", 0, 10],
        ["Census", "F", u, "1/13/2026", 1, 20],   # different hour -> not a dup
    ])
    assert (processor.validate_records(df) == "").all()


def test_process_file_routes_duplicates(tmp_path, monkeypatch, write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])
    df = pd.DataFrame([
        ["Census", "F", "UnitA", "1/13/2026", 0, 10],   # dup lower -> rejected
        ["Census", "F", "UnitA", "1/13/2026", 0, 25],   # dup higher -> successful
        ["Census", "F", "UnitA", "1/13/2026", 1, 7],    # unique -> successful
    ], columns=COLS)
    input_csv = tmp_path / "dupes.csv"
    df.to_csv(input_csv, index=False)

    assert proc.process_file(str(input_csv)) == 1

    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert sorted(ok_df["Volume"]) == [7, 25]
    assert len(bad_df) == 1
    assert bad_df["Volume"].iloc[0] == 10
    assert bad_df[proc.rejection_reason_column].str.contains("duplicate value").all()


def test_duplicate_check_can_be_disabled(tmp_path, monkeypatch, write_config,
                                         make_df):
    """With check_duplicates=false, duplicates are left untouched."""
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"],
                           check_duplicates=False)
    df = make_df([
        ["Census", "F", "UnitA", "1/13/2026", 0, 10],   # would be the dup loser
        ["Census", "F", "UnitA", "1/13/2026", 0, 25],   # would be the dup winner
    ])
    reasons = proc.validate_records(df)
    assert (reasons == "").all()   # neither flagged; the check is off


# ─────────────────────────── rejection summary ──────────────────────────────

def test_format_ranges():
    assert CensusFileProcessor._format_ranges([3, 4, 5, 7, 10, 11]) == "3-5, 7, 10-11"
    assert CensusFileProcessor._format_ranges([1]) == "1"
    assert CensusFileProcessor._format_ranges([]) == ""
    assert CensusFileProcessor._format_ranges([5, 5, 4]) == "4-5"


def test_build_rejection_summary_groups_by_unit_and_error(processor):
    u = _first_unit(processor)
    failed = pd.DataFrame({
        "Unit":    [u, u, "BadUnit"],
        "VolDate": ["1/13/2026", "1/15/2026", "1/14/2026"],
        processor.rejection_reason_column: [
            "Volume: below minimum (0)",
            "Volume: below minimum (0)",
            "Unit: not in valid list",
        ],
    })
    summary = processor.build_rejection_summary(failed)

    vol = summary[(summary["Unit"] == u) &
                  (summary["ErrorType"] == "Volume: below minimum (0)")].iloc[0]
    assert vol["Count"] == 2
    assert vol["RecordNumbers"] == "1-2"
    assert vol["DateRange"] == "01/13/2026 - 01/15/2026"

    bad = summary[summary["Unit"] == "BadUnit"].iloc[0]
    assert bad["ErrorType"] == "Unit: not in valid list"
    assert bad["Count"] == 1
    assert bad["DateRange"] == "01/14/2026"


def test_build_rejection_summary_splits_multi_reason_row(processor):
    failed = pd.DataFrame({
        "Unit": ["(missing)"],
        "VolDate": ["bad"],
        processor.rejection_reason_column: [
            "Unit: missing/empty; VolDate: not a valid date"
        ],
    })
    summary = processor.build_rejection_summary(failed)
    assert set(summary["ErrorType"]) == {
        "Unit: missing/empty", "VolDate: not a valid date"
    }
    assert (summary["Count"] == 1).all()


# ─────────────────────────── end-to-end routing ─────────────────────────────

def test_process_file_splits_pass_and_fail(tmp_path, monkeypatch, write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])
    df = pd.DataFrame([
        ["Census", "F", "UnitA", "1/13/2026", 0, 5],   # passes
        ["Census", "F", "UnitA", "1/13/2026", 1, 6],   # passes
        ["Census", "F", "UnitA", "1/13/2026", 99, 7],  # bad hour -> rejected
        ["Census", "F", "BadUnit", "1/13/2026", 3, 8], # unknown unit -> rejected
    ], columns=COLS)
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    assert proc.process_file(str(input_csv)) == 1

    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert len(ok_df) == 2 and len(bad_df) == 2
    assert proc.rejection_reason_column in bad_df.columns
    assert proc.rejection_reason_column not in ok_df.columns


def test_process_file_all_valid_exits_zero(tmp_path, monkeypatch, write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])
    df = pd.DataFrame(
        [["Census", "F", "UnitA", "1/13/2026", h, 5] for h in range(3)],
        columns=COLS,
    )
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    assert proc.process_file(str(input_csv)) == 0
    assert not os.path.isdir(tmp_path / "bad")
    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    assert len(ok_df) == 3


def test_process_file_schema_gate_rejects_whole_file(tmp_path, monkeypatch,
                                                     write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])
    df = pd.DataFrame([["Census", "F", "UnitA", "1/13/2026", 0]],
                      columns=["Type", "Facility", "Unit", "VolDate", "VolHour"])
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    assert proc.process_file(str(input_csv)) == 1
    assert not os.path.isdir(tmp_path / "ok")
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert bad_df[proc.rejection_reason_column].str.contains("Volume").any()


def test_process_file_missing_input_returns_two(tmp_path, monkeypatch,
                                                write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])
    assert proc.process_file(str(tmp_path / "does_not_exist.csv")) == 2


def test_process_file_writes_rejection_summary(tmp_path, monkeypatch,
                                               write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])
    df = pd.DataFrame([
        ["Census", "F", "UnitA", "1/13/2026", 0, 5],    # passes
        ["Census", "F", "UnitA", "1/13/2026", 99, 7],   # bad hour
        ["Census", "F", "UnitA", "1/14/2026", 99, 7],   # bad hour (same unit)
        ["Census", "F", "BadUnit", "1/15/2026", 3, 8],  # unknown unit
    ], columns=COLS)
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    assert proc.process_file(str(input_csv)) == 1

    summary_files = os.listdir(tmp_path / "summary")
    assert len(summary_files) == 1 and "rejected_summary" in summary_files[0]
    summary = pd.read_csv(tmp_path / "summary" / summary_files[0])
    hour_row = summary[summary["ErrorType"].str.contains("outside 0-23")].iloc[0]
    assert hour_row["Unit"] == "UnitA"
    assert hour_row["Count"] == 2
    assert hour_row["RecordNumbers"] == "2-3"
    assert hour_row["DateRange"] == "01/13/2026 - 01/14/2026"


def test_summary_disabled_by_config(tmp_path, monkeypatch, write_config):
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"],
                           write_rejection_summary=False)
    df = pd.DataFrame([["Census", "F", "BadUnit", "1/13/2026", 0, 5]], columns=COLS)
    input_csv = tmp_path / "batch.csv"
    df.to_csv(input_csv, index=False)

    assert proc.process_file(str(input_csv)) == 1
    assert not os.path.isdir(tmp_path / "summary")


def test_processor_reusable_across_files(tmp_path, monkeypatch, write_config):
    """One processor instance can validate multiple files in sequence."""
    proc = _make_processor(tmp_path, monkeypatch, write_config, ["UnitA"])

    good = pd.DataFrame([["Census", "F", "UnitA", "1/13/2026", 0, 5]], columns=COLS)
    bad = pd.DataFrame([["Census", "F", "BadUnit", "1/13/2026", 0, 5]], columns=COLS)
    good_csv = tmp_path / "good.csv"
    bad_csv = tmp_path / "bad_in.csv"
    good.to_csv(good_csv, index=False)
    bad.to_csv(bad_csv, index=False)

    assert proc.process_file(str(good_csv)) == 0
    assert proc.process_file(str(bad_csv)) == 1


# ─────────────────────────── generated fixtures ─────────────────────────────

def test_record_mix_fixture(tmp_path, monkeypatch, write_config):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(repo, "data", "test", "test_record_mix.csv")
    real_units = os.path.join(repo, "data", "valid_units.txt")
    if not os.path.isfile(src):
        pytest.skip("run test/generate_test_data.py to create the fixtures")

    cfg_path = write_config(tmp_path, real_units)
    proc = CensusFileProcessor.from_config_file(str(cfg_path))
    input_csv = tmp_path / "mix.csv"
    input_csv.write_bytes(open(src, "rb").read())

    assert proc.process_file(str(input_csv)) == 1
    ok_df = pd.read_csv(tmp_path / "ok" / os.listdir(tmp_path / "ok")[0])
    bad_df = pd.read_csv(tmp_path / "bad" / os.listdir(tmp_path / "bad")[0])
    assert len(ok_df) > 0 and len(bad_df) > 0
    assert proc.rejection_reason_column in bad_df.columns

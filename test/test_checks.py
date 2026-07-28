"""Tests for census_validation.checks (per-record checks)."""

import pandas as pd

from census_validation import checks

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


def _reasons(df):
    return pd.Series("", index=df.index, dtype="object")


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def test_append_reason_counts_and_joins():
    r = pd.Series(["", "note1"], dtype="object")
    n = checks.append_reason(r, pd.Series([True, True]), "note2")
    assert n == 2
    assert r.iloc[0] == "note2"
    assert r.iloc[1] == "note1; note2"


def test_required_present_flags_missing_and_empty():
    df = _df([
        ["Census", "F", "U", "1/1/2026", 0, 5],
        ["Census", "", "U", "1/1/2026", 1, None],
    ])
    r = _reasons(df)
    checks.check_required_present(df, r, COLS)
    assert r.iloc[0] == ""
    assert "Facility: missing/empty" in r.iloc[1]
    assert "Volume: missing/empty" in r.iloc[1]


def test_cleanliness_flags_whitespace_quote_control():
    df = _df([
        ["Census", "F", "U", "1/1/2026", 0, 5],
        ["Census", " F ", 'Q"U', "1/1/2026", 1, 5],
        ["Census", "F", "Tab\tU", "1/1/2026", 2, 5],
    ])
    r = _reasons(df)
    checks.check_cleanliness(df, r, COLS)
    assert r.iloc[0] == ""
    assert "whitespace" in r.iloc[1] and "quote" in r.iloc[1].lower()
    assert "control" in r.iloc[2].lower()


def test_volhour_range_and_type():
    df = _df([
        ["Census", "F", "U", "1/1/2026", 0, 5],
        ["Census", "F", "U", "1/1/2026", 5.5, 5],
        ["Census", "F", "U", "1/1/2026", 25, 5],
        ["Census", "F", "U", "1/1/2026", "x", 5],
    ])
    r = _reasons(df)
    checks.check_volhour(df, r, 0, 23)
    assert r.iloc[0] == ""
    assert "not an integer" in r.iloc[1]
    assert "outside 0-23" in r.iloc[2]
    assert "not numeric" in r.iloc[3]


def test_volume_range_and_type():
    df = _df([
        ["Census", "F", "U", "1/1/2026", 0, 5],
        ["Census", "F", "U", "1/1/2026", 1, -7],
        ["Census", "F", "U", "1/1/2026", 2, 3.5],
        ["Census", "F", "U", "1/1/2026", 3, "abc"],
    ])
    r = _reasons(df)
    checks.check_volume(df, r, 0)
    assert "below minimum" in r.iloc[1]
    assert "not an integer" in r.iloc[2]
    assert "not numeric" in r.iloc[3]


def test_voldate_parse():
    df = _df([
        ["Census", "F", "U", "1/1/2026", 0, 5],
        ["Census", "F", "U", "13/45/2026", 1, 5],
        ["Census", "F", "U", "not-a-date", 2, 5],
    ])
    r = _reasons(df)
    checks.check_voldate(df, r)
    assert r.iloc[0] == ""
    assert "not a valid date" in r.iloc[1]
    assert "not a valid date" in r.iloc[2]


def test_unit_in_list():
    df = _df([
        ["Census", "F", "UnitA", "1/1/2026", 0, 5],
        ["Census", "F", "BadUnit", "1/1/2026", 1, 5],
    ])
    r = _reasons(df)
    checks.check_unit_in_list(df, r, {"UnitA"}, True)
    assert r.iloc[0] == ""
    assert "not in valid list" in r.iloc[1]


def test_run_record_checks_accumulates_all(settings):
    df = _df([["Census", "", "BadUnit", "bad", 99, -1]])
    r = _reasons(df)
    checks.run_record_checks(df, r, settings, {"UnitA"})
    reason = r.iloc[0]
    assert "Facility: missing/empty" in reason
    assert "not in valid list" in reason
    assert "not a valid date" in reason
    assert "outside 0-23" in reason
    assert "below minimum" in reason

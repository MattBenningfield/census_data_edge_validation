"""Tests for census_validation.duplicates (CHECK 5)."""

import dataclasses

import pandas as pd

from census_validation import duplicates

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


def _reasons(df):
    return pd.Series("", index=df.index, dtype="object")


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def test_keeps_higher_volume(settings):
    df = _df([
        ["Census", "F", "U", "1/13/2026", 0, 10],   # dup, lower -> rejected
        ["Census", "F", "U", "1/13/2026", 0, 25],   # dup, higher -> kept
        ["Census", "F", "U", "1/13/2026", 1, 5],    # unique -> kept
    ])
    r = _reasons(df)
    duplicates.resolve_duplicates(df, r, settings)
    assert "duplicate value" in r.iloc[0]
    assert r.iloc[1] == "" and r.iloc[2] == ""


def test_three_rows_keep_max(settings):
    df = _df([
        ["Census", "F", "U", "1/13/2026", 0, 10],
        ["Census", "F", "U", "1/13/2026", 0, 30],   # max -> kept
        ["Census", "F", "U", "1/13/2026", 0, 20],
    ])
    r = _reasons(df)
    duplicates.resolve_duplicates(df, r, settings)
    assert r.iloc[1] == ""
    assert "duplicate value" in r.iloc[0] and "duplicate value" in r.iloc[2]


def test_exact_duplicate_rejected(settings):
    df = _df([
        ["Census", "F", "U", "1/13/2026", 0, 15],   # first kept
        ["Census", "F", "U", "1/13/2026", 0, 15],   # exact dup -> rejected
    ])
    r = _reasons(df)
    duplicates.resolve_duplicates(df, r, settings)
    assert r.iloc[0] == ""
    assert "duplicate value" in r.iloc[1]


def test_ineligible_rows_do_not_win(settings):
    """A row already failing another check can't be the kept duplicate."""
    df = _df([
        ["Census", "F", "U", "1/13/2026", 0, 10],   # clean, lower volume
        ["Census", "F", "U", "1/13/2026", 0, 25],   # higher, but already failed
    ])
    r = _reasons(df)
    r.iloc[1] = "Volume: not an integer"   # row 2 ineligible
    duplicates.resolve_duplicates(df, r, settings)
    assert r.iloc[0] == ""                 # clean row kept, not flagged duplicate
    assert "duplicate value" not in r.iloc[0]


def test_no_false_duplicate_when_keys_differ(settings):
    df = _df([
        ["Census", "F", "U", "1/13/2026", 0, 10],
        ["Census", "F", "U", "1/13/2026", 1, 20],   # different hour
    ])
    r = _reasons(df)
    duplicates.resolve_duplicates(df, r, settings)
    assert (r == "").all()


def test_can_be_disabled(settings):
    disabled = dataclasses.replace(settings, check_duplicates=False)
    df = _df([
        ["Census", "F", "U", "1/13/2026", 0, 10],
        ["Census", "F", "U", "1/13/2026", 0, 25],
    ])
    r = _reasons(df)
    duplicates.resolve_duplicates(df, r, disabled)
    assert (r == "").all()

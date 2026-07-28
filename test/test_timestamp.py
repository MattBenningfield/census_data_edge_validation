"""Tests for census_validation.timestamp (VolTimestamp shaping)."""

import pandas as pd

from census_validation import timestamp

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


def _df(rows):
    return pd.DataFrame(rows, columns=COLS)


def test_timestamp_series_valid_and_invalid():
    df = _df([
        ["Census", "F", "U", "1/13/2026", 5, 10],    # valid
        ["Census", "F", "U", "not-a-date", 6, 10],   # bad date -> blank
        ["Census", "F", "U", "1/13/2026", 25, 10],   # bad hour -> blank
    ])
    ts = timestamp.timestamp_series(df)
    assert ts.iloc[0] == "2026-01-13T05:00:00Z"
    assert ts.iloc[1] == ""
    assert ts.iloc[2] == ""


def test_to_output_frame_combines_and_drops():
    df = _df([["Census", "F", "U", "1/13/2026", 5, 17]])
    out = timestamp.to_output_frame(df, "VolTimestamp")
    assert list(out.columns) == ["Type", "Facility", "Unit", "VolTimestamp", "Volume"]
    assert out["VolTimestamp"].iloc[0] == "2026-01-13T05:00:00Z"


def test_to_output_frame_passthrough_when_columns_absent():
    df = pd.DataFrame([{"Type": "Census", "Volume": 1}])
    assert timestamp.to_output_frame(df, "VolTimestamp") is df

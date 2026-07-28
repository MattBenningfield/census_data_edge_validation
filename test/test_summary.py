"""Tests for census_validation.summary (aggregated rejection report)."""

import pandas as pd

from census_validation import summary

REASON = "RejectionReason"


def test_format_ranges():
    assert summary.format_ranges([3, 4, 5, 7, 10, 11]) == "3-5, 7, 10-11"
    assert summary.format_ranges([1]) == "1"
    assert summary.format_ranges([]) == ""
    assert summary.format_ranges([5, 5, 4]) == "4-5"


def test_build_rejection_summary_groups_by_unit_and_error():
    failed = pd.DataFrame({
        "Unit": ["UnitA", "UnitA", "BadUnit"],
        "VolDate": ["1/13/2026", "1/15/2026", "1/14/2026"],
        REASON: [
            "Volume: below minimum (0)",
            "Volume: below minimum (0)",
            "Unit: not in valid list",
        ],
    })
    out = summary.build_rejection_summary(failed, REASON)

    vol = out[(out["Unit"] == "UnitA") &
              (out["ErrorType"] == "Volume: below minimum (0)")].iloc[0]
    assert vol["Count"] == 2
    assert vol["RecordNumbers"] == "1-2"
    assert vol["DateRange"] == "01/13/2026 - 01/15/2026"

    bad = out[out["Unit"] == "BadUnit"].iloc[0]
    assert bad["Count"] == 1
    assert bad["DateRange"] == "01/14/2026"


def test_build_rejection_summary_splits_multi_reason_row():
    failed = pd.DataFrame({
        "Unit": ["(missing)"],
        "VolDate": ["bad"],
        REASON: ["Unit: missing/empty; VolDate: not a valid date"],
    })
    out = summary.build_rejection_summary(failed, REASON)
    assert set(out["ErrorType"]) == {
        "Unit: missing/empty", "VolDate: not a valid date"
    }
    assert (out["Count"] == 1).all()


def test_empty_input_returns_empty_frame():
    out = summary.build_rejection_summary(pd.DataFrame(), REASON)
    assert list(out.columns) == summary.SUMMARY_COLUMNS
    assert out.empty

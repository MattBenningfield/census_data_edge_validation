"""Aggregated rejection report: one row per (Unit, error type)."""

from __future__ import annotations

import pandas as pd

SUMMARY_COLUMNS = ["Unit", "ErrorType", "Count", "RecordNumbers", "DateRange"]


def format_ranges(numbers) -> str:
    """
    Collapse a set of integers into a compact, sorted range string.

    e.g. [3, 4, 5, 7, 10, 11] -> "3-5, 7, 10-11".
    """
    nums = sorted({int(n) for n in numbers})
    if not nums:
        return ""
    parts = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def build_rejection_summary(failed_df: pd.DataFrame, reason_column: str) -> pd.DataFrame:
    """
    Aggregate rejected records into one row per (Unit, error type).

    Returns:
        pd.DataFrame: Columns Unit, ErrorType, Count, RecordNumbers, DateRange —
        sorted by Unit then descending Count. Empty if there are no rejections.
    """
    if failed_df.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    has_unit = "Unit" in failed_df.columns
    has_date = "VolDate" in failed_df.columns

    # The source file is read with a default RangeIndex, so each row's index
    # label is its 0-based position; +1 gives a 1-based record number. Fall back
    # to enumeration order if the index is ever non-integer.
    positions = {label: i for i, label in enumerate(failed_df.index)}

    long_rows = []
    for idx, reason in failed_df[reason_column].items():
        unit = str(failed_df.at[idx, "Unit"]).strip() if has_unit else ""
        unit = unit if unit and unit.lower() != "nan" else "(missing)"
        date_raw = failed_df.at[idx, "VolDate"] if has_date else None
        try:
            record_no = int(idx) + 1
        except (TypeError, ValueError):
            record_no = positions[idx] + 1
        for note in str(reason).split("; "):
            if note:
                long_rows.append((unit, note, record_no, date_raw))

    long = pd.DataFrame(
        long_rows, columns=["Unit", "ErrorType", "RecordNumber", "VolDate"]
    )

    rows = []
    for (unit, err), grp in long.groupby(["Unit", "ErrorType"], sort=True):
        dates = pd.to_datetime(
            grp["VolDate"], format="mixed", errors="coerce"
        ).dropna()
        if len(dates):
            lo, hi = dates.min(), dates.max()
            date_range = (
                lo.strftime("%m/%d/%Y") if lo == hi
                else f"{lo.strftime('%m/%d/%Y')} - {hi.strftime('%m/%d/%Y')}"
            )
        else:
            date_range = ""
        rows.append({
            "Unit": unit,
            "ErrorType": err,
            "Count": int(len(grp)),
            "RecordNumbers": format_ranges(grp["RecordNumber"]),
            "DateRange": date_range,
        })

    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    return summary.sort_values(
        ["Unit", "Count", "ErrorType"], ascending=[True, False, True]
    ).reset_index(drop=True)

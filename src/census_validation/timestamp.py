"""
Timestamp shaping for the routed output.

VolDate + VolHour are combined into a single UTC timestamp column formatted
``YYYY-MM-DDTHH:00:00Z`` (matching the DynamoDB validation-errors schema). These
functions are pure — no config or logging — so they are trivially testable.
"""

from __future__ import annotations

import pandas as pd

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:00:00Z"


def timestamp_series(df: pd.DataFrame) -> pd.Series:
    """
    Build a per-row UTC timestamp Series from VolDate + VolHour.

    Rows whose date/hour can't form a valid timestamp (unparseable date,
    non-integer or out-of-range hour) get an empty string.

    Returns:
        pd.Series: One string per row, aligned to df.index.
    """
    ts = pd.Series("", index=df.index, dtype="object")
    if "VolDate" not in df.columns or "VolHour" not in df.columns:
        return ts
    parsed = pd.to_datetime(df["VolDate"], format="mixed", errors="coerce")
    hour = pd.to_numeric(df["VolHour"], errors="coerce")
    valid = (
        parsed.notna() & hour.notna()
        & (hour % 1 == 0) & (hour >= 0) & (hour <= 23)
    )
    full = parsed.dt.normalize() + pd.to_timedelta(hour.where(valid, 0), unit="h")
    ts.loc[valid] = full.dt.strftime(TIMESTAMP_FORMAT).loc[valid]
    return ts


def to_output_frame(df: pd.DataFrame, timestamp_column: str) -> pd.DataFrame:
    """
    Return a copy of ``df`` with VolDate + VolHour replaced by a single
    ``timestamp_column`` (in VolDate's position). Other columns keep their order.

    If either source column is absent (e.g. a schema-invalid file), the frame is
    returned unchanged.
    """
    if "VolDate" not in df.columns or "VolHour" not in df.columns:
        return df
    out = df.copy()
    out.insert(out.columns.get_loc("VolDate"), timestamp_column, timestamp_series(df))
    return out.drop(columns=["VolDate", "VolHour"])

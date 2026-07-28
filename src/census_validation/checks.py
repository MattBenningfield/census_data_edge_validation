"""
Per-record validation checks.

Each check takes the DataFrame plus a ``reasons`` Series (one string per row,
aligned to df.index) and appends a short note to the rows at fault. A row with an
empty reason after every check passed. ``run_record_checks`` runs the full set in
order.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("census_validation")


def append_reason(reasons: pd.Series, mask: pd.Series, note: str) -> int:
    """
    Append ``note`` to every row flagged by ``mask``. NA mask entries count as
    False; multiple notes on one row are joined with "; ".

    Returns:
        int: The number of rows the note was appended to.
    """
    mask = mask.fillna(False).astype(bool)
    if mask.any():
        reasons.loc[mask] = reasons.loc[mask].map(
            lambda existing: f"{existing}; {note}" if existing else note
        )
    return int(mask.sum())


def check_required_present(df, reasons, required_columns) -> None:
    """Flag rows where a required column is missing (null) or empty."""
    log.info("CHECK 1: required fields present ...")
    n = 0
    for col in required_columns:
        if col not in df.columns:
            continue  # handled by the file-level schema gate
        s = df[col].astype("string")
        missing = s.isna() | (s.str.strip() == "")
        n += append_reason(reasons, missing, f"{col}: missing/empty")
    log.error("  %d required-field issue(s).", n) if n else \
        log.info("  SUCCESS: all required fields present.")


def check_cleanliness(df, reasons, expected_columns) -> None:
    """Flag rows with whitespace, quote, or control characters in any cell."""
    log.info("CHECK 2: cell cleanliness ...")
    tests = (
        ("leading/trailing whitespace", lambda s: s != s.str.strip()),
        ("quote character", lambda s: s.str.contains(r"[\"']", regex=True)),
        ("control character",
         lambda s: s.str.contains(r"[\x00-\x1f\x7f]", regex=True)),
    )
    n = 0
    for col in expected_columns:
        if col not in df.columns:
            continue
        s = df[col].astype("string")
        for label, test in tests:
            n += append_reason(reasons, test(s), f"{col}: {label}")
    log.error("  %d cell-cleanliness issue(s).", n) if n else \
        log.info("  SUCCESS: no cell-level issues found.")


def check_volhour(df, reasons, min_hour, max_hour) -> None:
    """Flag non-numeric, fractional, or out-of-range VolHour values."""
    log.info("CHECK 3a: VolHour is an integer in [%d, %d] ...", min_hour, max_hour)
    if "VolHour" not in df.columns:
        return
    present = df["VolHour"].notna()
    numeric = pd.to_numeric(df["VolHour"], errors="coerce")
    n = append_reason(reasons, present & numeric.isna(), "VolHour: not numeric")
    n += append_reason(reasons, numeric.notna() & (numeric % 1 != 0),
                       "VolHour: not an integer")
    n += append_reason(
        reasons,
        numeric.notna() & ((numeric < min_hour) | (numeric > max_hour)),
        f"VolHour: outside {min_hour}-{max_hour}",
    )
    log.error("  %d VolHour issue(s).", n) if n else log.info("  SUCCESS: VolHour valid.")


def check_volume(df, reasons, min_volume) -> None:
    """Flag non-numeric, fractional, or below-minimum Volume values."""
    log.info("CHECK 3b: Volume is an integer >= %d ...", min_volume)
    if "Volume" not in df.columns:
        return
    present = df["Volume"].notna()
    numeric = pd.to_numeric(df["Volume"], errors="coerce")
    n = append_reason(reasons, present & numeric.isna(), "Volume: not numeric")
    n += append_reason(reasons, numeric.notna() & (numeric % 1 != 0),
                       "Volume: not an integer")
    n += append_reason(reasons, numeric.notna() & (numeric < min_volume),
                       f"Volume: below minimum ({min_volume})")
    log.error("  %d Volume issue(s).", n) if n else log.info("  SUCCESS: Volume valid.")


def check_voldate(df, reasons) -> None:
    """Flag rows whose VolDate is present but does not parse as a date."""
    log.info("CHECK 3c: VolDate parses as a valid date ...")
    if "VolDate" not in df.columns:
        return
    s = df["VolDate"].astype("string").str.strip()
    present = s.notna() & (s != "")
    parsed = pd.to_datetime(s, format="mixed", errors="coerce")
    n = append_reason(reasons, present & parsed.isna(), "VolDate: not a valid date")
    log.error("  %d VolDate issue(s).", n) if n else \
        log.info("  SUCCESS: all VolDate values parse.")


def check_unit_in_list(df, reasons, valid_units_cmp, case_sensitive) -> None:
    """Flag rows whose Unit (when present) is not in the valid-units list."""
    log.info("CHECK 4: Unit is in the known-units list ...")
    if "Unit" not in df.columns:
        return
    s = df["Unit"].astype("string").str.strip()
    present = s.notna() & (s != "")
    cmp = s if case_sensitive else s.str.lower()
    n = append_reason(reasons, present & ~cmp.isin(valid_units_cmp),
                      "Unit: not in valid list")
    log.error("  %d row(s) with an unknown Unit.", n) if n else \
        log.info("  SUCCESS: all units present in the valid list.")


def run_record_checks(df, reasons, settings, valid_units_cmp) -> None:
    """Run every per-record check in order, mutating ``reasons`` in place."""
    check_required_present(df, reasons, settings.required_columns)
    check_cleanliness(df, reasons, settings.expected_columns)
    check_volhour(df, reasons, settings.min_hour, settings.max_hour)
    check_volume(df, reasons, settings.min_volume)
    check_voldate(df, reasons)
    check_unit_in_list(df, reasons, valid_units_cmp, settings.case_sensitive_units)

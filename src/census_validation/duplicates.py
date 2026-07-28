"""
Cross-record duplicate resolution.

Rows sharing the same key columns (settings.dup_keys) are duplicates. Among the
rows that otherwise pass every check, the one with the highest Volume is kept and
the rest are rejected with the reason "duplicate value" (ties keep the first
occurrence). Only otherwise-valid rows compete, so a clean record is never
dropped in favour of a higher-Volume duplicate that fails another check.
"""

from __future__ import annotations

import logging

import pandas as pd

from .checks import append_reason

log = logging.getLogger("census_validation")

DUPLICATE_NOTE = "duplicate value"


def resolve_duplicates(df, reasons, settings) -> None:
    """Flag lower-Volume duplicates in ``reasons`` (CHECK 5); mutates in place."""
    if not settings.check_duplicates:
        log.info("CHECK 5: duplicate check disabled (check_duplicates=false).")
        return

    log.info("CHECK 5: duplicate keys (same %s, keep higher Volume) ...",
             settings.dup_keys)
    if not all(c in df.columns for c in settings.dup_keys + ["Volume"]):
        log.error("  Cannot check duplicates: required columns missing.")
        return

    eligible = reasons == ""
    sub = df[eligible]
    if len(sub) < 2:
        log.info("  SUCCESS: no duplicates among valid records.")
        return

    volume = pd.to_numeric(sub["Volume"], errors="coerce")
    # Highest Volume first (stable, so ties keep original order); the first row
    # per key is the keeper, every later row with that key is a loser.
    order = volume.sort_values(ascending=False, kind="stable").index
    dup_mask = sub.loc[order].duplicated(subset=settings.dup_keys, keep="first")
    losers = dup_mask.index[dup_mask]

    if len(losers):
        mask = pd.Series(df.index.isin(losers), index=df.index)
        n = append_reason(reasons, mask, DUPLICATE_NOTE)
        log.error("  %d duplicate record(s) rejected (kept higher Volume).", int(n))
    else:
        log.info("  SUCCESS: no duplicate keys among valid records.")

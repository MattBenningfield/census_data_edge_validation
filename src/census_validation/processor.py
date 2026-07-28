"""
CensusFileProcessor — orchestrates the validation pipeline for one file.

One instance holds the settings and the valid-units reference, so the same
processor can validate many files (construct once, call ``process_file()`` per
file). That suits a warm-start AWS Lambda handler and gives the planned
in-memory valid-units cache a natural home.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime

import pandas as pd

from . import routing
from .checks import run_record_checks
from .config import CONFIG_PATH, Settings, load_settings
from .duplicates import resolve_duplicates
from .valid_units import comparison_set, load_valid_units

log = logging.getLogger("census_validation")


class CensusFileProcessor:
    """Validate a census CSV at the record level and route the results."""

    def __init__(self, settings: Settings, valid_units=None):
        self.settings = settings
        self.valid_units = (
            valid_units if valid_units is not None
            else load_valid_units(settings.valid_units_file)
        )
        self.valid_units_cmp = comparison_set(
            self.valid_units, settings.case_sensitive_units)

        self._file_handler = None
        if sys.platform == "win32":
            sys.stdout.reconfigure(encoding="utf-8")
        logging.basicConfig(level=logging.INFO, format=settings.log_format,
                            datefmt=settings.log_datefmt)

    @classmethod
    def from_config_file(cls, path=None) -> "CensusFileProcessor":
        """Build a processor from a TOML config file (defaults to CONFIG_PATH)."""
        return cls(load_settings(path if path is not None else CONFIG_PATH))

    # ── logging ──────────────────────────────────────────────────────────────

    def _attach_file_logger(self, csv_path) -> str:
        """
        Route this run's log into logs/<csv-stem>/ as a timestamped .log file,
        removing any handler from a previous run so a reused processor does not
        accumulate handlers.
        """
        if self._file_handler is not None:
            log.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

        stem = os.path.splitext(os.path.basename(csv_path))[0]
        run_dir = os.path.join(self.settings.log_root, stem)
        os.makedirs(run_dir, exist_ok=True)
        log_path = os.path.join(
            run_dir, f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            fmt=self.settings.log_format, datefmt=self.settings.log_datefmt))
        log.addHandler(handler)
        self._file_handler = handler
        return log_path

    # ── validation ─────────────────────────────────────────────────────────────

    def validate_records(self, df) -> pd.Series:
        """
        Run every check and build a per-row reason string.

        Returns:
            pd.Series: One string per row (aligned to df.index). Empty means the
            row passed every check; otherwise it names each problem, joined by
            "; ".
        """
        reasons = pd.Series("", index=df.index, dtype="object")
        run_record_checks(df, reasons, self.settings, self.valid_units_cmp)
        resolve_duplicates(df, reasons, self.settings)
        return reasons

    # ── entry point ─────────────────────────────────────────────────────────────

    def process_file(self, path) -> int:
        """
        Validate one CSV file end to end and route the results.

        Returns:
            int: A shell-style exit code — 0 (all passed), 1 (some rejected or
            schema invalid), 2 (file missing or unreadable).
        """
        s = self.settings
        log_path = self._attach_file_logger(path)
        log.info("=" * 70)
        log.info("WFAI RECORD VALIDATION (v2)")
        log.info("File: %s", path)
        log.info("Log : %s", log_path)
        log.info("=" * 70)

        if not os.path.isfile(path):
            log.error("Input file not found: %s", path)
            return 2
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001 - surface any read failure clearly
            log.error("Failed to read CSV: %s", exc)
            return 2

        log.info("Loaded %d rows x %d columns.", len(df), len(df.columns))
        file_id = uuid.uuid4().hex

        # File-level schema gate: per-record checks need the expected columns.
        missing = [c for c in s.expected_columns if c not in df.columns]
        extra = [c for c in df.columns if c not in s.expected_columns]
        if extra:
            log.warning("Unexpected extra column(s) present: %s", extra)
        if missing:
            log.error("=" * 70)
            log.error("SCHEMA INVALID: missing expected column(s): %s", missing)
            log.error("Cannot validate per record. Rejecting whole file.")
            reason = f"Schema: missing columns {missing}"
            rejected = df.assign(**{s.rejection_reason_column: reason})
            _, rejected_path = routing.route_records(path, df.iloc[0:0], rejected, s)
            if rejected_path:
                log.info("Rejected records --> %s (%d rows)", rejected_path, len(df))
            summary_path = routing.write_rejection_summary(path, rejected, s)
            if summary_path:
                log.info("Rejection summary --> %s", summary_path)
            file_item_path, _ = routing.write_dynamo_output(
                path, file_id, df, rejected, s,
                schema_invalid=True, schema_reason=reason)
            if file_item_path:
                log.info("DynamoDB file item --> %s", file_item_path)
            log.error("=" * 70)
            return 1

        reasons = self.validate_records(df)
        passed_mask = reasons == ""
        passed_df = df[passed_mask]
        failed_df = df[~passed_mask].assign(
            **{s.rejection_reason_column: reasons[~passed_mask]})
        n_pass = int(passed_mask.sum())
        n_fail = int((~passed_mask).sum())

        successful_path, rejected_path = routing.route_records(
            path, passed_df, failed_df, s)
        summary_path = routing.write_rejection_summary(path, failed_df, s)
        file_item_path, error_items_path = routing.write_dynamo_output(
            path, file_id, df, failed_df, s)

        log.info("=" * 70)
        log.info("SUMMARY: %d record(s) passed, %d record(s) rejected.",
                 n_pass, n_fail)
        if successful_path:
            log.info("Successful records --> %s (%d rows)", successful_path, n_pass)
        if rejected_path:
            log.info("Rejected records   --> %s (%d rows)", rejected_path, n_fail)
        if summary_path:
            log.info("Rejection summary  --> %s", summary_path)
        if file_item_path:
            log.info("DynamoDB file item --> %s", file_item_path)
        if error_items_path:
            log.info("DynamoDB errors    --> %s", error_items_path)
        log.info("=" * 70)

        return 0 if n_fail == 0 else 1

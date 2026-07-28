"""
WFAI Record Validator (DataValidation v2)
=========================================
A per-record gate for census time-series data. Each row of a dropped-off CSV is
checked on its own to confirm it is complete, well-formatted, clean, and tied to
a known unit before it proceeds further in the validation pipeline.

Configuration and the valid-units reference are held on a `CensusFileProcessor`
instance. That shape lets one processor validate many files (construct once,
call `process_file()` per file) and suits a warm-start AWS Lambda handler — build
the processor outside the handler, then invoke it per event. It also gives the
planned in-memory valid-units cache and DynamoDB routing a natural home as
instance state/methods.

Each record is checked for:
  1. Required fields present  — no required column is missing/empty.
  2. Type & range formatting  — VolHour is an integer in [min_hour, max_hour];
                                Volume is an integer >= min_volume; VolDate
                                parses as a valid date.
  3. Cell cleanliness         — no leading/trailing whitespace, quote characters,
                                or control/non-printable characters in any field.
  4. Unit in known list       — the row's Unit exists in the valid-units list.
  5. No duplicate records      — rows sharing (Type, Facility, Unit, VolDate,
                                VolHour) are duplicates; among otherwise-valid
                                duplicates the highest-Volume row is kept and the
                                rest are rejected as "duplicate value".

Rows that pass every check are routed to accepted_dir; rows that fail are routed
to rejected_dir with a RejectionReason column, and an aggregated report is
written to rejected_summary_dir. process_file() returns a shell-style exit code
(0 = all passed, 1 = some rejected / schema invalid, 2 = file/read error).

In the routed output, VolDate and VolHour are combined into a single UTC
timestamp column (see timestamp_column) formatted "YYYY-MM-DDTHH:00:00Z", and the
original VolDate/VolHour columns are dropped.

Usage:
    python record_validation.py
    python record_validation.py path/to/file.csv
"""

import logging
import os
import sys
import tomllib
from datetime import datetime

import pandas as pd

# ─── Configuration helpers (module-level utilities) ──────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "WFAI_RECORD_VALIDATION_CONFIG", os.path.join(BASE_DIR, "config.toml")
)

log = logging.getLogger("record_validator")


def _resolve(path):
    """
    Resolve a config path against BASE_DIR unless it is already absolute.

    Returns:
        str: The resolved path (absolute, or joined onto BASE_DIR).
    """
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def load_config(path=CONFIG_PATH):
    """
    Load and validate the TOML config into a flat settings namespace.

    Returns:
        dict: The validated configuration settings, keyed by setting name.
    """
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except FileNotFoundError:
        sys.exit(f"[ERROR] Config file not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        sys.exit(f"[ERROR] Could not parse config file {path}: {exc}")

    try:
        return {
            "expected_columns": cfg["schema"]["expected_columns"],
            "required_columns": cfg["schema"]["required_columns"],
            "dup_keys": cfg["schema"]["dup_keys"],
            "min_hour": cfg["rules"]["min_hour"],
            "max_hour": cfg["rules"]["max_hour"],
            "min_volume": cfg["rules"]["min_volume"],
            "case_sensitive_units": cfg["rules"]["case_sensitive_units"],
            "default_file": _resolve(cfg["paths"]["default_file"]),
            "log_root": _resolve(cfg["paths"]["log_root"]),
            "accepted_dir": _resolve(cfg["paths"]["accepted_dir"]),
            "rejected_dir": _resolve(cfg["paths"]["rejected_dir"]),
            "rejected_summary_dir": _resolve(cfg["paths"]["rejected_summary_dir"]),
            "valid_units_file": _resolve(cfg["paths"]["valid_units_file"]),
            "route_files": cfg["behavior"]["route_validated_files"],
            "rejection_reason_column": cfg["behavior"]["rejection_reason_column"],
            "write_rejection_summary": cfg["behavior"]["write_rejection_summary"],
            "check_duplicates": cfg["behavior"]["check_duplicates"],
            "timestamp_column": cfg["behavior"]["timestamp_column"],
            "log_format": cfg["logging"]["format"],
            "log_datefmt": cfg["logging"]["datefmt"],
        }
    except KeyError as exc:
        sys.exit(f"[ERROR] Missing required config key: {exc} in {path}")


def load_valid_units(path):
    """
    Load the set of valid/known unit names from the reference file.

    Accepts a .csv (with a "Unit" column) or any other text file treated as
    one unit per line. Exits if the file is missing or malformed.

    Returns:
        set[str]: The distinct valid/known unit names.
    """
    if not os.path.isfile(path):
        sys.exit(f"[ERROR] Valid-units file not found: {path}")

    if path.lower().endswith(".csv"):
        vu = pd.read_csv(path, encoding="utf-8-sig")
        if "Unit" not in vu.columns:
            sys.exit(f"[ERROR] Valid-units CSV missing 'Unit' column: {path}")
        units = vu["Unit"].dropna().astype(str).str.strip()
    else:
        with open(path, encoding="utf-8-sig") as fh:
            units = [line.strip() for line in fh if line.strip()]

    valid = {u for u in units if u}
    if not valid:
        sys.exit(f"[ERROR] Valid-units file is empty: {path}")
    return valid


# ─── Processor ───────────────────────────────────────────────────────────────


class CensusFileProcessor:
    """
    Validates a census CSV at the record level and routes the results.

    One instance holds the configuration and the valid-units reference, so the
    same processor can validate many files: construct once, then call
    `process_file()` per file. Build with `from_config_file()` for the normal
    case, or pass a config dict directly (handy for tests).
    """

    def __init__(self, config, valid_units=None):
        self.expected_columns = config["expected_columns"]
        self.required_columns = config["required_columns"]
        self.dup_keys = config["dup_keys"]
        self.min_hour = config["min_hour"]
        self.max_hour = config["max_hour"]
        self.min_volume = config["min_volume"]
        self.case_sensitive_units = config["case_sensitive_units"]
        self.default_file = config["default_file"]
        self.log_root = config["log_root"]
        self.accepted_dir = config["accepted_dir"]
        self.rejected_dir = config["rejected_dir"]
        self.rejected_summary_dir = config["rejected_summary_dir"]
        self.valid_units_file = config["valid_units_file"]
        self.route_files = config["route_files"]
        self.rejection_reason_column = config["rejection_reason_column"]
        self.write_summary = config["write_rejection_summary"]
        self.check_duplicates = config["check_duplicates"]
        self.timestamp_column = config["timestamp_column"]
        self.log_format = config["log_format"]
        self.log_datefmt = config["log_datefmt"]

        # The valid-units reference is loaded once and held on the instance.
        # (This is where the planned stale-cache DB refresh would live.)
        self.valid_units = (
            valid_units if valid_units is not None
            else load_valid_units(self.valid_units_file)
        )
        self.valid_units_cmp = (
            self.valid_units if self.case_sensitive_units
            else {u.lower() for u in self.valid_units}
        )

        self._file_handler = None
        self._configure_logging()

    @classmethod
    def from_config_file(cls, path=None):
        """Build a processor from a TOML config file (defaults to CONFIG_PATH)."""
        return cls(load_config(path if path is not None else CONFIG_PATH))

    # ── logging ──────────────────────────────────────────────────────────────

    def _configure_logging(self):
        if sys.platform == "win32":
            sys.stdout.reconfigure(encoding="utf-8")
        logging.basicConfig(
            level=logging.INFO, format=self.log_format, datefmt=self.log_datefmt
        )

    def attach_file_logger(self, csv_path):
        """
        Route this run's log into logs/<csv-stem>/ as a timestamped .log file.

        Any file handler from a previous process_file() call is removed first, so
        a long-lived (reused) processor does not accumulate handlers.

        Returns:
            str: The path to the .log file that was created.
        """
        if self._file_handler is not None:
            log.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

        stem = os.path.splitext(os.path.basename(csv_path))[0]
        run_dir = os.path.join(self.log_root, stem)
        os.makedirs(run_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(run_dir, f"{stem}_{timestamp}.log")

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(fmt=self.log_format, datefmt=self.log_datefmt)
        )
        log.addHandler(handler)
        self._file_handler = handler
        return log_path

    # ── per-record checks ─────────────────────────────────────────────────────
    # Every check takes the DataFrame plus a `reasons` Series (one string per
    # row, aligned to df.index) and appends a short note to the rows at fault.

    @staticmethod
    def _append(reasons, mask, note):
        """
        Append `note` to every row flagged by `mask`. NA mask entries count as
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

    def _check_required_present(self, df, reasons):
        """Flag rows where a required column is missing (null) or empty."""
        log.info("CHECK 1: required fields present ...")
        n = 0
        for col in self.required_columns:
            if col not in df.columns:
                continue  # handled by the file-level schema gate
            s = df[col].astype("string")
            missing = s.isna() | (s.str.strip() == "")
            n += self._append(reasons, missing, f"{col}: missing/empty")
        if n:
            log.error("  %d required-field issue(s).", n)
        else:
            log.info("  SUCCESS: all required fields present.")

    def _check_cleanliness(self, df, reasons):
        """Flag rows with whitespace, quote, or control characters in any cell."""
        log.info("CHECK 2: cell cleanliness ...")
        checks = (
            ("leading/trailing whitespace", lambda s: s != s.str.strip()),
            ("quote character", lambda s: s.str.contains(r"[\"']", regex=True)),
            ("control character",
             lambda s: s.str.contains(r"[\x00-\x1f\x7f]", regex=True)),
        )
        n = 0
        for col in self.expected_columns:
            if col not in df.columns:
                continue
            s = df[col].astype("string")
            for label, test in checks:
                n += self._append(reasons, test(s), f"{col}: {label}")
        if n:
            log.error("  %d cell-cleanliness issue(s).", n)
        else:
            log.info("  SUCCESS: no cell-level issues found.")

    def _check_volhour(self, df, reasons):
        """Flag non-numeric, fractional, or out-of-range VolHour values."""
        log.info("CHECK 3a: VolHour is an integer in [%d, %d] ...",
                 self.min_hour, self.max_hour)
        if "VolHour" not in df.columns:
            return
        present = df["VolHour"].notna()
        numeric = pd.to_numeric(df["VolHour"], errors="coerce")

        non_numeric = present & numeric.isna()
        fractional = numeric.notna() & (numeric % 1 != 0)
        out_of_range = numeric.notna() & (
            (numeric < self.min_hour) | (numeric > self.max_hour)
        )

        n = self._append(reasons, non_numeric, "VolHour: not numeric")
        n += self._append(reasons, fractional, "VolHour: not an integer")
        n += self._append(reasons, out_of_range,
                          f"VolHour: outside {self.min_hour}-{self.max_hour}")
        if n:
            log.error("  %d VolHour issue(s).", n)
        else:
            log.info("  SUCCESS: VolHour valid.")

    def _check_volume(self, df, reasons):
        """Flag non-numeric, fractional, or below-minimum Volume values."""
        log.info("CHECK 3b: Volume is an integer >= %d ...", self.min_volume)
        if "Volume" not in df.columns:
            return
        present = df["Volume"].notna()
        numeric = pd.to_numeric(df["Volume"], errors="coerce")

        non_numeric = present & numeric.isna()
        fractional = numeric.notna() & (numeric % 1 != 0)
        below_min = numeric.notna() & (numeric < self.min_volume)

        n = self._append(reasons, non_numeric, "Volume: not numeric")
        n += self._append(reasons, fractional, "Volume: not an integer")
        n += self._append(reasons, below_min,
                          f"Volume: below minimum ({self.min_volume})")
        if n:
            log.error("  %d Volume issue(s).", n)
        else:
            log.info("  SUCCESS: Volume valid.")

    def _check_voldate(self, df, reasons):
        """Flag rows whose VolDate is present but does not parse as a date."""
        log.info("CHECK 3c: VolDate parses as a valid date ...")
        if "VolDate" not in df.columns:
            return
        s = df["VolDate"].astype("string").str.strip()
        present = s.notna() & (s != "")
        parsed = pd.to_datetime(s, format="mixed", errors="coerce")
        unparseable = present & parsed.isna()

        n = self._append(reasons, unparseable, "VolDate: not a valid date")
        if n:
            log.error("  %d VolDate issue(s).", n)
        else:
            log.info("  SUCCESS: all VolDate values parse.")

    def _check_unit_in_list(self, df, reasons):
        """Flag rows whose Unit (when present) is not in the valid-units list."""
        log.info("CHECK 4: Unit is in the known-units list ...")
        if "Unit" not in df.columns:
            return
        s = df["Unit"].astype("string").str.strip()
        present = s.notna() & (s != "")
        cmp = s if self.case_sensitive_units else s.str.lower()
        unknown = present & ~cmp.isin(self.valid_units_cmp)

        n = self._append(reasons, unknown, "Unit: not in valid list")
        if n:
            log.error("  %d row(s) with an unknown Unit.", n)
        else:
            log.info("  SUCCESS: all units present in the valid list.")

    def _resolve_duplicates(self, df, reasons):
        """
        Resolve duplicate records (a cross-record check).

        Rows sharing the same key columns (self.dup_keys — Type, Facility, Unit,
        VolDate, VolHour) are duplicates. Among the rows that otherwise pass
        every check, the one with the highest Volume is kept and the rest are
        rejected with the reason "duplicate value" (ties keep the first
        occurrence).

        Only rows that already pass every per-record check are considered, so a
        clean record is never dropped in favour of a higher-Volume duplicate that
        fails another check — that invalid row is simply rejected for its own
        reason and the clean, lower-Volume row is kept.
        """
        if not self.check_duplicates:
            log.info("CHECK 5: duplicate check disabled (check_duplicates=false).")
            return

        log.info("CHECK 5: duplicate keys (same %s, keep higher Volume) ...",
                 self.dup_keys)
        if not all(c in df.columns for c in self.dup_keys + ["Volume"]):
            log.error("  Cannot check duplicates: required columns missing.")
            return

        # Only otherwise-valid rows compete to be kept; their Volume already
        # passed its check, so it is a clean non-negative integer.
        eligible = reasons == ""
        sub = df[eligible]
        if len(sub) < 2:
            log.info("  SUCCESS: no duplicates among valid records.")
            return

        volume = pd.to_numeric(sub["Volume"], errors="coerce")
        # Highest Volume first (stable, so ties keep original order); the first
        # row per key is the keeper, every later row with that key is a loser.
        order = volume.sort_values(ascending=False, kind="stable").index
        dup_mask = sub.loc[order].duplicated(subset=self.dup_keys, keep="first")
        losers = dup_mask.index[dup_mask]

        if len(losers):
            mask = pd.Series(df.index.isin(losers), index=df.index)
            n = self._append(reasons, mask, "duplicate value")
            log.error("  %d duplicate record(s) rejected (kept higher Volume).",
                      int(n))
        else:
            log.info("  SUCCESS: no duplicate keys among valid records.")

    def validate_records(self, df):
        """
        Run every check and build a per-row reason string.

        The per-record checks run first (each row judged on its own), then the
        cross-record duplicate resolution decides which of any otherwise-valid
        duplicates to keep.

        Returns:
            pd.Series: One string per row (aligned to df.index). Empty means the
            row passed every check; otherwise it names each problem, joined by
            "; ".
        """
        reasons = pd.Series("", index=df.index, dtype="object")
        self._check_required_present(df, reasons)
        self._check_cleanliness(df, reasons)
        self._check_volhour(df, reasons)
        self._check_volume(df, reasons)
        self._check_voldate(df, reasons)
        self._check_unit_in_list(df, reasons)
        self._resolve_duplicates(df, reasons)
        return reasons

    # ── rejection summary ─────────────────────────────────────────────────────

    @staticmethod
    def _format_ranges(numbers):
        """
        Collapse a set of integers into a compact, sorted range string.

        e.g. [3, 4, 5, 7, 10, 11] -> "3-5, 7, 10-11".

        Returns:
            str: The compacted range string ("" for an empty input).
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

    def build_rejection_summary(self, failed_df):
        """
        Aggregate rejected records into one row per (Unit, error type).

        Returns:
            pd.DataFrame: Columns Unit, ErrorType, Count, RecordNumbers,
            DateRange — sorted by Unit then descending Count. Empty if there are
            no rejections.
        """
        cols = ["Unit", "ErrorType", "Count", "RecordNumbers", "DateRange"]
        if failed_df.empty:
            return pd.DataFrame(columns=cols)

        has_unit = "Unit" in failed_df.columns
        has_date = "VolDate" in failed_df.columns

        # The source file is read with a default RangeIndex, so each row's index
        # label is its 0-based position; +1 gives a 1-based record number. Fall
        # back to enumeration order if the index is ever non-integer.
        positions = {label: i for i, label in enumerate(failed_df.index)}

        long_rows = []
        for idx, reason in failed_df[self.rejection_reason_column].items():
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

        summary_rows = []
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
            summary_rows.append({
                "Unit": unit,
                "ErrorType": err,
                "Count": int(len(grp)),
                "RecordNumbers": self._format_ranges(grp["RecordNumber"]),
                "DateRange": date_range,
            })

        summary = pd.DataFrame(summary_rows, columns=cols)
        return summary.sort_values(
            ["Unit", "Count", "ErrorType"], ascending=[True, False, True]
        ).reset_index(drop=True)

    # ── output shaping ──────────────────────────────────────────────────────────

    def _to_output_frame(self, df):
        """
        Shape a DataFrame for output: combine VolDate + VolHour into one UTC
        timestamp column (self.timestamp_column), formatted "YYYY-MM-DDTHH:00:00Z"
        to match the DynamoDB validation-errors schema, and drop the original
        VolDate and VolHour columns.

        The new column takes VolDate's position; all other columns (including any
        RejectionReason) keep their order. Rows whose VolDate/VolHour cannot form
        a valid timestamp (e.g. rejected rows with a bad date or hour) get an
        empty string. If either source column is absent (e.g. a schema-invalid
        file), the frame is returned unchanged.

        Returns:
            pd.DataFrame: A new frame with the timestamp column in place of
            VolDate/VolHour.
        """
        if "VolDate" not in df.columns or "VolHour" not in df.columns:
            return df

        parsed = pd.to_datetime(df["VolDate"], format="mixed", errors="coerce")
        hour = pd.to_numeric(df["VolHour"], errors="coerce")
        valid = (
            parsed.notna() & hour.notna()
            & (hour % 1 == 0) & (hour >= 0) & (hour <= 23)
        )
        full = parsed.dt.normalize() + pd.to_timedelta(hour.where(valid, 0), unit="h")
        stamped = full.dt.strftime("%Y-%m-%dT%H:00:00Z")

        ts = pd.Series("", index=df.index, dtype="object")
        ts.loc[valid] = stamped.loc[valid]

        out = df.copy()
        out.insert(out.columns.get_loc("VolDate"), self.timestamp_column, ts)
        return out.drop(columns=["VolDate", "VolHour"])

    # ── routing ────────────────────────────────────────────────────────────────

    def route_records(self, csv_path, passed_df, failed_df):
        """
        Write passing and failing records to the successful/rejected folders.

        Returns:
            tuple[str | None, str | None]: (successful_path, rejected_path); each
            is None when that side has no records or routing is disabled.
        """
        if not self.route_files:
            return None, None

        stem = os.path.splitext(os.path.basename(csv_path))[0]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        successful_path = None
        if not passed_df.empty:
            os.makedirs(self.accepted_dir, exist_ok=True)
            successful_path = os.path.join(
                self.accepted_dir, f"{stem}_successful_{stamp}.csv"
            )
            self._to_output_frame(passed_df).to_csv(successful_path, index=False)

        rejected_path = None
        if not failed_df.empty:
            os.makedirs(self.rejected_dir, exist_ok=True)
            rejected_path = os.path.join(
                self.rejected_dir, f"{stem}_rejected_{stamp}.csv"
            )
            self._to_output_frame(failed_df).to_csv(rejected_path, index=False)

        return successful_path, rejected_path

    def write_rejection_summary(self, csv_path, failed_df):
        """
        Write the aggregated rejection report to rejected_summary_dir.

        Returns:
            str | None: The summary path, or None when disabled or there is
            nothing to summarize.
        """
        if not (self.route_files and self.write_summary) or failed_df.empty:
            return None

        summary = self.build_rejection_summary(failed_df)
        if summary.empty:
            return None

        os.makedirs(self.rejected_summary_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(csv_path))[0]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_path = os.path.join(
            self.rejected_summary_dir, f"{stem}_rejected_summary_{stamp}.csv"
        )
        summary.to_csv(summary_path, index=False)
        return summary_path

    # ── entry point ─────────────────────────────────────────────────────────────

    def process_file(self, path):
        """
        Validate one CSV file end to end and route the results.

        Returns:
            int: A shell-style exit code — 0 (all records passed), 1 (some
            records rejected, or the file's schema is invalid), 2 (file missing
            or unreadable).
        """
        log_path = self.attach_file_logger(path)
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

        # File-level schema gate: per-record checks need the expected columns.
        missing = [c for c in self.expected_columns if c not in df.columns]
        extra = [c for c in df.columns if c not in self.expected_columns]
        if extra:
            log.warning("Unexpected extra column(s) present: %s", extra)
        if missing:
            log.error("=" * 70)
            log.error("SCHEMA INVALID: missing expected column(s): %s", missing)
            log.error("Cannot validate per record. Rejecting whole file.")
            reason = f"Schema: missing columns {missing}"
            rejected = df.assign(**{self.rejection_reason_column: reason})
            _, rejected_path = self.route_records(path, df.iloc[0:0], rejected)
            if rejected_path:
                log.info("Rejected records --> %s (%d rows)", rejected_path, len(df))
            summary_path = self.write_rejection_summary(path, rejected)
            if summary_path:
                log.info("Rejection summary --> %s", summary_path)
            log.error("=" * 70)
            return 1

        reasons = self.validate_records(df)
        passed_mask = reasons == ""
        passed_df = df[passed_mask]
        failed_df = df[~passed_mask].assign(
            **{self.rejection_reason_column: reasons[~passed_mask]}
        )
        n_pass = int(passed_mask.sum())
        n_fail = int((~passed_mask).sum())

        successful_path, rejected_path = self.route_records(path, passed_df, failed_df)
        summary_path = self.write_rejection_summary(path, failed_df)

        log.info("=" * 70)
        log.info("SUMMARY: %d record(s) passed, %d record(s) rejected.",
                 n_pass, n_fail)
        if successful_path:
            log.info("Successful records --> %s (%d rows)",
                     successful_path, len(passed_df))
        if rejected_path:
            log.info("Rejected records   --> %s (%d rows)",
                     rejected_path, len(failed_df))
        if summary_path:
            log.info("Rejection summary  --> %s", summary_path)
        log.info("=" * 70)

        return 0 if n_fail == 0 else 1


def main():
    proc = CensusFileProcessor.from_config_file()
    path = sys.argv[1] if len(sys.argv) > 1 else proc.default_file
    sys.exit(proc.process_file(path))


if __name__ == "__main__":
    main()

"""
WFAI Record Validator (DataValidation v2)
=========================================
A simpler, per-record gate for census time-series data. Where the v1 validator
reasons about whole (Type, Facility, Unit) series — date gaps, 24-hour coverage,
duplicate keys — this version checks every ROW on its own to confirm it is clean,
well-formatted, and carries the minimum information needed to proceed further in
the validation pipeline.

Each record is checked for:
  1. Required fields present  — no required column is missing/empty.
  2. Type & range formatting  — VolHour is an integer in [min_hour, max_hour];
                                Volume is an integer >= min_volume; VolDate
                                parses as a valid date.
  3. Cell cleanliness         — no leading/trailing whitespace, quote characters,
                                or control/non-printable characters in any field.
  4. Unit in known list       — the row's Unit exists in the valid-units list.

Rows that pass every check are routed to accepted_dir; rows that fail one or more
checks are routed to rejected_dir with a RejectionReason column describing every
problem found. The process exits non-zero if any record was rejected.

Usage:
    python record_validation.py
    python record_validation.py path/to/file.csv
"""

import logging
import os
import re
import sys
import tomllib
from datetime import datetime

import pandas as pd

# ─── Configuration ───────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "WFAI_RECORD_VALIDATION_CONFIG", os.path.join(BASE_DIR, "config.toml")
)


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


# Settings populated by configure(). They start as None so the module can be
# imported without the config files present (e.g. under unit tests); configure()
# fills them in for a real run and may be called explicitly by tests.
CFG = None
EXPECTED_COLUMNS = REQUIRED_COLUMNS = None
MIN_HOUR = MAX_HOUR = MIN_VOLUME = None
CASE_SENSITIVE_UNITS = VALID_UNITS_FILE = VALID_UNITS = VALID_UNITS_CMP = None
DEFAULT_FILE = LOG_ROOT = ACCEPTED_DIR = REJECTED_DIR = REJECTED_SUMMARY_DIR = None
ROUTE_FILES = REJECTION_REASON_COLUMN = WRITE_REJECTION_SUMMARY = None
LOG_FORMAT = LOG_DATEFMT = None

log = logging.getLogger("record_validator")


def configure(config_path=None):
    """
    Load the config and valid-units list, populate module-level settings, and
    set up logging. Kept out of import so the module can be imported without the
    config files present; main() calls it, and tests may call it explicitly.

    Returns:
        dict: The loaded configuration (also stored in the module global CFG).
    """
    global CFG, EXPECTED_COLUMNS, REQUIRED_COLUMNS
    global MIN_HOUR, MAX_HOUR, MIN_VOLUME
    global CASE_SENSITIVE_UNITS, VALID_UNITS_FILE, VALID_UNITS, VALID_UNITS_CMP
    global DEFAULT_FILE, LOG_ROOT, ACCEPTED_DIR, REJECTED_DIR, REJECTED_SUMMARY_DIR
    global ROUTE_FILES, REJECTION_REASON_COLUMN, WRITE_REJECTION_SUMMARY
    global LOG_FORMAT, LOG_DATEFMT

    CFG = load_config(config_path if config_path is not None else CONFIG_PATH)

    EXPECTED_COLUMNS = CFG["expected_columns"]
    REQUIRED_COLUMNS = CFG["required_columns"]
    MIN_HOUR = CFG["min_hour"]
    MAX_HOUR = CFG["max_hour"]
    MIN_VOLUME = CFG["min_volume"]
    CASE_SENSITIVE_UNITS = CFG["case_sensitive_units"]
    VALID_UNITS_FILE = CFG["valid_units_file"]
    VALID_UNITS = load_valid_units(VALID_UNITS_FILE)
    # Pre-build the comparison set once, honoring the case-sensitivity flag.
    VALID_UNITS_CMP = (
        VALID_UNITS if CASE_SENSITIVE_UNITS else {u.lower() for u in VALID_UNITS}
    )
    DEFAULT_FILE = CFG["default_file"]
    LOG_ROOT = CFG["log_root"]
    ACCEPTED_DIR = CFG["accepted_dir"]
    REJECTED_DIR = CFG["rejected_dir"]
    REJECTED_SUMMARY_DIR = CFG["rejected_summary_dir"]
    ROUTE_FILES = CFG["route_files"]
    REJECTION_REASON_COLUMN = CFG["rejection_reason_column"]
    WRITE_REJECTION_SUMMARY = CFG["write_rejection_summary"]
    LOG_FORMAT = CFG["log_format"]
    LOG_DATEFMT = CFG["log_datefmt"]

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATEFMT)

    return CFG


def attach_file_logger(csv_path):
    """
    Route this run's log into a folder specific to the validated CSV.

    Creates logs/<csv-stem>/ (one folder per source file) and adds a file
    handler that writes a timestamped .log file inside it, capturing exactly
    what is emitted to the console.

    Returns:
        str: The path to the .log file that was created.
    """
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    run_dir = os.path.join(LOG_ROOT, stem)
    os.makedirs(run_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(run_dir, f"{stem}_{timestamp}.log")

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT))
    log.addHandler(handler)
    return log_path


# ─── Per-record checks ───────────────────────────────────────────────────────
# Every check takes the DataFrame plus a `reasons` Series (one string per row,
# aligned to df.index) and appends a short note to the rows it finds at fault.
# A row with an empty reason at the end passed every check.


def _append(reasons, mask, note):
    """
    Append `note` to every row flagged by `mask` in the `reasons` Series.

    NA entries in the mask are treated as False. Multiple notes on the same row
    are joined with "; ", in the order the checks run.

    Returns:
        int: The number of rows the note was appended to.
    """
    mask = mask.fillna(False).astype(bool)
    if mask.any():
        reasons.loc[mask] = reasons.loc[mask].map(
            lambda existing: f"{existing}; {note}" if existing else note
        )
    return int(mask.sum())


def check_required_present(df, reasons):
    """
    Flag rows where a required column is missing (null) or empty after stripping.
    """
    log.info("CHECK 1: required fields present ...")
    n = 0
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            continue  # handled by the file-level schema gate in main()
        s = df[col].astype("string")
        missing = s.isna() | (s.str.strip() == "")
        n += _append(reasons, missing, f"{col}: missing/empty")
    if n:
        log.error("  %d required-field issue(s).", n)
    else:
        log.info("  SUCCESS: all required fields present.")


def check_cleanliness(df, reasons):
    """
    Flag rows with dirty cell content: leading/trailing whitespace, quote
    characters, or control/non-printable characters in any expected column.
    """
    log.info("CHECK 2: cell cleanliness ...")
    checks = (
        ("leading/trailing whitespace", lambda s: s != s.str.strip()),
        ("quote character", lambda s: s.str.contains(r"[\"']", regex=True)),
        ("control character", lambda s: s.str.contains(r"[\x00-\x1f\x7f]", regex=True)),
    )
    n = 0
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            continue
        # Nullable string so .str ops are NA-safe regardless of source dtype.
        # Numeric columns become clean strings ("5") and never trip the checks.
        s = df[col].astype("string")
        for label, test in checks:
            n += _append(reasons, test(s), f"{col}: {label}")
    if n:
        log.error("  %d cell-cleanliness issue(s).", n)
    else:
        log.info("  SUCCESS: no cell-level issues found.")


def check_volhour(df, reasons):
    """
    Flag rows whose VolHour is non-numeric, fractional, or outside the
    configured [MIN_HOUR, MAX_HOUR] range. Missing values are left to the
    required-fields check.
    """
    log.info("CHECK 3a: VolHour is an integer in [%d, %d] ...", MIN_HOUR, MAX_HOUR)
    if "VolHour" not in df.columns:
        return
    present = df["VolHour"].notna()
    numeric = pd.to_numeric(df["VolHour"], errors="coerce")

    non_numeric = present & numeric.isna()
    fractional = numeric.notna() & (numeric % 1 != 0)
    out_of_range = numeric.notna() & ((numeric < MIN_HOUR) | (numeric > MAX_HOUR))

    n = _append(reasons, non_numeric, "VolHour: not numeric")
    n += _append(reasons, fractional, "VolHour: not an integer")
    n += _append(reasons, out_of_range,
                 f"VolHour: outside {MIN_HOUR}-{MAX_HOUR}")
    if n:
        log.error("  %d VolHour issue(s).", n)
    else:
        log.info("  SUCCESS: VolHour valid.")


def check_volume(df, reasons):
    """
    Flag rows whose Volume is non-numeric, fractional, or below MIN_VOLUME.
    Missing values are left to the required-fields check.
    """
    log.info("CHECK 3b: Volume is an integer >= %d ...", MIN_VOLUME)
    if "Volume" not in df.columns:
        return
    present = df["Volume"].notna()
    numeric = pd.to_numeric(df["Volume"], errors="coerce")

    non_numeric = present & numeric.isna()
    fractional = numeric.notna() & (numeric % 1 != 0)
    below_min = numeric.notna() & (numeric < MIN_VOLUME)

    n = _append(reasons, non_numeric, "Volume: not numeric")
    n += _append(reasons, fractional, "Volume: not an integer")
    n += _append(reasons, below_min, f"Volume: below minimum ({MIN_VOLUME})")
    if n:
        log.error("  %d Volume issue(s).", n)
    else:
        log.info("  SUCCESS: Volume valid.")


def check_voldate(df, reasons):
    """
    Flag rows whose VolDate is present but does not parse as a valid date.
    Missing values are left to the required-fields check.
    """
    log.info("CHECK 3c: VolDate parses as a valid date ...")
    if "VolDate" not in df.columns:
        return
    # Strip first so a value flagged for whitespace still gets a fair parse test.
    s = df["VolDate"].astype("string").str.strip()
    present = s.notna() & (s != "")
    parsed = pd.to_datetime(s, format="mixed", errors="coerce")
    unparseable = present & parsed.isna()

    n = _append(reasons, unparseable, "VolDate: not a valid date")
    if n:
        log.error("  %d VolDate issue(s).", n)
    else:
        log.info("  SUCCESS: all VolDate values parse.")


def check_unit_in_list(df, reasons):
    """
    Flag rows whose Unit value (when present) is not in the valid-units list.
    Missing values are left to the required-fields check.
    """
    log.info("CHECK 4: Unit is in the known-units list ...")
    if "Unit" not in df.columns:
        return
    s = df["Unit"].astype("string").str.strip()
    present = s.notna() & (s != "")
    cmp = s if CASE_SENSITIVE_UNITS else s.str.lower()
    unknown = present & ~cmp.isin(VALID_UNITS_CMP)

    n = _append(reasons, unknown, "Unit: not in valid list")
    if n:
        log.error("  %d row(s) with an unknown Unit.", n)
    else:
        log.info("  SUCCESS: all units present in the valid list.")


def validate_records(df):
    """
    Run every per-record check and build a per-row reason string.

    Returns:
        pd.Series: One string per row, aligned to df.index. An empty string
        means the row passed every check; otherwise it names each problem found,
        joined by "; ".
    """
    reasons = pd.Series("", index=df.index, dtype="object")
    check_required_present(df, reasons)
    check_cleanliness(df, reasons)
    check_volhour(df, reasons)
    check_volume(df, reasons)
    check_voldate(df, reasons)
    check_unit_in_list(df, reasons)
    return reasons


# ─── Routing / entry point ───────────────────────────────────────────────────


def resolve_input_path():
    """
    Determine which file to validate: the command-line argument if provided,
    otherwise the configured default file.

    Returns:
        str: The path to the CSV file to validate.
    """
    if len(sys.argv) > 1:
        return sys.argv[1]
    return DEFAULT_FILE


def route_records(csv_path, passed_df, failed_df):
    """
    Write passing and failing records to the successful/rejected folders.

    Returns:
        tuple[str | None, str | None]: (successful_path, rejected_path). Either
        element is None when that side has no records or routing is disabled.
    """
    if not ROUTE_FILES:
        return None, None

    stem = os.path.splitext(os.path.basename(csv_path))[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    successful_path = None
    if not passed_df.empty:
        os.makedirs(ACCEPTED_DIR, exist_ok=True)
        successful_path = os.path.join(
            ACCEPTED_DIR, f"{stem}_successful_{stamp}.csv"
        )
        passed_df.to_csv(successful_path, index=False)

    rejected_path = None
    if not failed_df.empty:
        os.makedirs(REJECTED_DIR, exist_ok=True)
        rejected_path = os.path.join(
            REJECTED_DIR, f"{stem}_rejected_{stamp}.csv"
        )
        failed_df.to_csv(rejected_path, index=False)

    return successful_path, rejected_path


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


def build_rejection_summary(failed_df):
    """
    Aggregate rejected records into one row per (Unit, error type).

    Each rejected row's reason string is split back into its individual error
    notes, then grouped by Unit and error type. For every group the summary
    reports how many records were affected, which record numbers (1-based, where
    1 is the first data row of the source file) and the date range those records
    span.

    Returns:
        pd.DataFrame: Columns Unit, ErrorType, Count, RecordNumbers, DateRange —
        sorted by Unit then descending Count. Empty if there are no rejections.
    """
    cols = ["Unit", "ErrorType", "Count", "RecordNumbers", "DateRange"]
    if failed_df.empty:
        return pd.DataFrame(columns=cols)

    has_unit = "Unit" in failed_df.columns
    has_date = "VolDate" in failed_df.columns

    # The source file is read with a default RangeIndex, so each row's index
    # label is its 0-based position in that file; +1 gives a 1-based record
    # number. Fall back to enumeration order if the index is ever non-integer.
    positions = {label: i for i, label in enumerate(failed_df.index)}

    # Explode each row into (Unit, error note, record number, raw date).
    long_rows = []
    for idx, reason in failed_df[REJECTION_REASON_COLUMN].items():
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
        dates = pd.to_datetime(grp["VolDate"], format="mixed", errors="coerce").dropna()
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
            "RecordNumbers": _format_ranges(grp["RecordNumber"]),
            "DateRange": date_range,
        })

    summary = pd.DataFrame(summary_rows, columns=cols)
    return summary.sort_values(
        ["Unit", "Count", "ErrorType"], ascending=[True, False, True]
    ).reset_index(drop=True)


def write_rejection_summary(csv_path, failed_df):
    """
    Write the aggregated rejection report to rejected_summary_dir.

    Returns:
        str | None: The summary path, or None when disabled or there is nothing
        to summarize.
    """
    if not (ROUTE_FILES and WRITE_REJECTION_SUMMARY) or failed_df.empty:
        return None

    summary = build_rejection_summary(failed_df)
    if summary.empty:
        return None

    os.makedirs(REJECTED_SUMMARY_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = os.path.join(
        REJECTED_SUMMARY_DIR, f"{stem}_rejected_summary_{stamp}.csv"
    )
    summary.to_csv(summary_path, index=False)
    return summary_path


def main():
    configure()
    path = resolve_input_path()
    log_path = attach_file_logger(path)
    log.info("=" * 70)
    log.info("WFAI RECORD VALIDATION (v2)")
    log.info("File: %s", path)
    log.info("Log : %s", log_path)
    log.info("=" * 70)

    if not os.path.isfile(path):
        log.error("Input file not found: %s", path)
        sys.exit(2)

    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001 - surface any read failure clearly
        log.error("Failed to read CSV: %s", exc)
        sys.exit(2)

    log.info("Loaded %d rows x %d columns.", len(df), len(df.columns))

    # ── File-level schema gate: per-record checks need the expected columns to
    #    exist. If any are missing, the whole file is rejected. Extra columns are
    #    only a warning (they are carried through the routed output unchanged).
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in EXPECTED_COLUMNS]
    if extra:
        log.warning("Unexpected extra column(s) present: %s", extra)
    if missing:
        log.error("=" * 70)
        log.error("SCHEMA INVALID: missing expected column(s): %s", missing)
        log.error("Cannot validate per record. Rejecting whole file.")
        reason = f"Schema: missing columns {missing}"
        rejected = df.assign(**{REJECTION_REASON_COLUMN: reason})
        _, rejected_path = route_records(path, df.iloc[0:0], rejected)
        if rejected_path:
            log.info("Rejected records --> %s (%d rows)", rejected_path, len(df))
        summary_path = write_rejection_summary(path, rejected)
        if summary_path:
            log.info("Rejection summary --> %s", summary_path)
        log.error("=" * 70)
        sys.exit(1)

    # ── Per-record validation.
    reasons = validate_records(df)
    passed_mask = reasons == ""
    passed_df = df[passed_mask]
    failed_df = df[~passed_mask].assign(
        **{REJECTION_REASON_COLUMN: reasons[~passed_mask]}
    )
    n_pass = int(passed_mask.sum())
    n_fail = int((~passed_mask).sum())

    successful_path, rejected_path = route_records(path, passed_df, failed_df)
    summary_path = write_rejection_summary(path, failed_df)

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

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()

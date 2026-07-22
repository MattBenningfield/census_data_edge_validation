# DataValidation v2 — Record Validator

A simpler, **per-record** gate for census time-series data. Where the original
DataValidation project reasons about whole `(Type, Facility, Unit)` series —
date gaps, 24-hour coverage, duplicate keys — this version checks every **row**
on its own to confirm it is clean, well-formatted, and carries the minimum
information needed to proceed further in the validation pipeline.

## Expected schema

A valid file must contain these six columns. **Column order does not matter**,
and extra columns are allowed (they are carried through to the output and only
noted with a warning) — the file just needs to include every column below.
Example of a successful file (rows that pass every check):

```
+---------+--------------------+-----------------------------------------------------+------------+---------+--------+
| Type    | Facility           | Unit                                                | VolDate    | VolHour | Volume |
+---------+--------------------+-----------------------------------------------------+------------+---------+--------+
| Census  | 1100-York Hospital | 1100-15001 YH Oper Room - Advanced Procedural Suite | 1/13/2026  | 0       | 10     |
| Census  | 1100-York Hospital | 1100-15001 YH Oper Room - Advanced Procedural Suite | 1/13/2026  | 1       | 11     |
| Census  | 1100-York Hospital | 1100-15001 YH Oper Room - Advanced Procedural Suite | 1/13/2026  | 2       | 12     |
+---------+--------------------+-----------------------------------------------------+------------+---------+--------+
```

| Column     | Type    | Requirement                                                     |
|------------|---------|-----------------------------------------------------------------|
| `Type`     | text    | Present, non-empty, clean (no whitespace/quotes/control chars)  |
| `Facility` | text    | Present, non-empty, clean                                       |
| `Unit`     | text    | Present, non-empty, clean, and found in the valid-units list    |
| `VolDate`  | date    | Present and parses as a valid date                              |
| `VolHour`  | integer | Present, whole number in `0`–`23`                               |
| `Volume`   | integer | Present, whole number `>= 0`                                    |

## What each record is checked for

1. **Required fields present** — no required column is missing or empty.
2. **Type & range formatting** — `VolHour` is an integer in `[min_hour, max_hour]`;
   `Volume` is an integer `>= min_volume`; `VolDate` parses as a valid date.
3. **Cell cleanliness** — no leading/trailing whitespace, quote characters, or
   control/non-printable characters in any field.
4. **Unit in known list** — the row's `Unit` exists in the valid-units list.
5. **No duplicate records** — rows sharing the same `Type`, `Facility`, `Unit`,
   `VolDate`, and `VolHour` are duplicates. Among otherwise-valid duplicates, the
   row with the **highest `Volume`** is kept (routed to successful) and the rest
   are rejected with the reason `duplicate value`. This is the one *cross-record*
   check — it compares rows to each other rather than judging each in isolation.
   A row that fails another check is rejected for that reason, so a clean,
   lower-`Volume` record is never dropped in favour of an invalid higher one.
   This check can be turned off with `check_duplicates = false` in the config.

## Routing

Validation is **row-level**:

- Rows that pass every check are written to `data/successful_data/`.
- Rows that fail one or more checks are written to `data/rejected_data/` with a
  `RejectionReason` column naming every problem found on that row.

> **Production note:** in production, successful records are routed to a
> **DynamoDB database** rather than to the `data/successful_data/` file directory.
> The local file output is the development/testing behavior; the DynamoDB
> destination is not yet built into the v2 project. (Rejected records continue to
> be written to a file directory.)

If the file is missing an expected column entirely, per-record checks can't run,
so the whole file is rejected. The process exits `0` when all records pass and
`1` when any record is rejected.

## Planned functionality (not yet implemented)

In production, `data/valid_units.txt` is the **bootstrap list of valid units** —
pulled from the source database before this validation process is initiated — and
serves as the starting reference from which each run begins.

The unit-in-list check (check 4) currently validates against this static
`data/valid_units.txt` reference file only. A future enhancement will refresh
that reference from the source database on demand:

- When a record's `Unit` is not found in the current reference, the script will
  query the database for the up-to-date list of valid units — **but only if the
  cached reference is older than a configured time threshold** (a stale-cache
  guard). A fresh-enough cache is used as-is without a database call.
- The queried result is held **in memory for the duration of a single file's
  validation**, so at most one database call is made per run — subsequent
  unknown units in the same file are checked against the in-memory copy rather
  than re-querying.

This is documented here for design intent; it is **not built into the v2 project
yet**. Today the validator reads `valid_units.txt` once at startup and does not
contact any database.

## Layout

```
DataValidation_v2/
├── src/
│   ├── record_validation.py   # CensusFileProcessor + CLI entry point
│   └── config.toml            # columns, rules, paths, routing
├── test/
│   ├── conftest.py
│   ├── generate_test_data.py  # writes fixtures into data/test/
│   └── test_record_validation.py
├── data/
│   ├── valid_units.txt        # bootstrap known-units reference (from the DB)
│   ├── test/                  # generated fixtures
│   ├── successful_data/       # passing rows land here
│   ├── rejected_data/         # failing rows land here (+ RejectionReason)
│   └── rejected_summary/      # aggregated per-Unit/error rejection reports
├── logs/                      # per-file run logs
├── pytest.ini
└── requirements-dev.txt
```

## Usage

```bash
# validate the configured default file
py src/record_validation.py

# validate a specific file
py src/record_validation.py data/test/test_record_mix.csv

# (re)generate the test fixtures
py test/generate_test_data.py

# run the test suite
py -m pytest -q
```

Behavior, columns, thresholds, and paths are all configurable in
[`src/config.toml`](src/config.toml). Override the config location with the
`WFAI_RECORD_VALIDATION_CONFIG` environment variable.

## Programmatic use

The validator is implemented as a `CensusFileProcessor` class, so it can be
driven directly instead of via the CLI. One instance holds the configuration and
the valid-units reference and can validate many files in sequence — build it
once, then call `process_file()` per file. This suits a warm-start AWS Lambda
handler (construct the processor outside the handler, invoke it per event).

```python
from record_validation import CensusFileProcessor

processor = CensusFileProcessor.from_config_file()   # or pass a config path
exit_code = processor.process_file("data/test/test_record_mix.csv")
# exit_code: 0 = all records passed, 1 = some rejected / schema invalid,
#            2 = input file missing or unreadable
```

`process_file()` returns the exit code rather than terminating the process; only
the CLI `main()` calls `sys.exit()`. Individual steps are also available on the
instance — `validate_records(df)` returns a per-row reason Series, and
`build_rejection_summary(failed_df)` returns the aggregated report.

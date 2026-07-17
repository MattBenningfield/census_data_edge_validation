"""
Test-data generator for record_validation.py (DataValidation v2)
================================================================
Writes small CSV fixtures under data/test/, each crafted to exercise the
per-record validator. A clean baseline is written first; every other fixture
starts from that valid data and injects a targeted defect into ONE row so that
row is rejected while the rest pass.

The rows use real unit names pulled from data/valid_units.txt so the baseline
genuinely passes the unit-in-list check against the real config.

Run:
    python generate_test_data.py

Then validate any fixture:
    python src/record_validation.py data/test/test_record_mix.csv
"""

import os

import pandas as pd

# This script lives in DataValidation_v2/test/, so the project root is one up.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "test")
VALID_UNITS_FILE = os.path.join(PROJECT_ROOT, "data", "valid_units.txt")

FACILITY = "1100-York Hospital"
DATE = "1/13/2026"
UNKNOWN_UNIT = "9999-Unknown Test Unit"

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


def load_two_valid_units():
    """
    Read the first two unit names from data/valid_units.txt so fixtures pass the
    unit-in-list check against the real config.

    Returns:
        tuple[str, str]: Two distinct valid unit names.
    """
    if not os.path.isfile(VALID_UNITS_FILE):
        raise SystemExit(f"[ERROR] valid-units file not found: {VALID_UNITS_FILE}")
    with open(VALID_UNITS_FILE, encoding="utf-8-sig") as fh:
        units = [line.strip() for line in fh if line.strip()]
    if len(units) < 2:
        raise SystemExit(
            f"[ERROR] need at least 2 units in {VALID_UNITS_FILE}, found {len(units)}"
        )
    return units[0], units[1]


UNIT_A, UNIT_B = load_two_valid_units()


def valid_frame(hours=range(6)):
    """A small, fully valid dataset (one clean row per hour) that passes."""
    rows = [
        {"Type": "Census", "Facility": FACILITY, "Unit": UNIT_A,
         "VolDate": DATE, "VolHour": h, "Volume": 10 + h}
        for h in hours
    ]
    return pd.DataFrame(rows, columns=COLS)


def write(df, name):
    path = os.path.join(OUT_DIR, name)
    df.to_csv(path, index=False)
    print(f"  wrote {name:<28} ({len(df):>3} rows)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Generating fixtures in {OUT_DIR}")
    print(f"Using units: {UNIT_A!r}, {UNIT_B!r}\n")

    # 0. Baseline — every row passes.
    write(valid_frame(), "test_all_valid.csv")

    # 1. Missing required field — blank Unit in one row.
    df = valid_frame()
    df.loc[1, "Unit"] = ""
    write(df, "test_missing_field.csv")

    # 2. Cell cleanliness — whitespace, quote, and control chars, one per row.
    df = valid_frame()
    df["VolDate"] = df["VolDate"].astype("object")
    df.loc[1, "VolDate"] = " 1/13/2026 "     # leading/trailing whitespace
    df.loc[2, "Unit"] = f'{UNIT_A}"'          # quote character
    df.loc[3, "Facility"] = f"{FACILITY}\t"   # control character (tab)
    write(df, "test_dirty_cells.csv")

    # 3. VolHour formatting — fractional and out-of-range values.
    df = valid_frame()
    df["VolHour"] = df["VolHour"].astype("float64")
    df.loc[1, "VolHour"] = 5.5    # not an integer
    df.loc[2, "VolHour"] = 25.0   # outside 0-23
    write(df, "test_bad_volhour.csv")

    # 4. Volume formatting — negative, fractional, and non-numeric values.
    df = valid_frame()
    df["Volume"] = df["Volume"].astype("object")
    df.loc[1, "Volume"] = -7       # below minimum
    df.loc[2, "Volume"] = 3.5      # not an integer
    df.loc[3, "Volume"] = "abc"    # not numeric
    write(df, "test_bad_volume.csv")

    # 5. VolDate formatting — an unparseable date.
    df = valid_frame()
    df["VolDate"] = df["VolDate"].astype("object")
    df.loc[1, "VolDate"] = "13/45/2026"   # not a valid date
    write(df, "test_bad_voldate.csv")

    # 6. Unknown unit — one row uses a unit absent from valid_units.txt.
    df = valid_frame()
    df.loc[1, "Unit"] = UNKNOWN_UNIT
    write(df, "test_unknown_unit.csv")

    # 7. Mixed — a realistic batch where several rows fail different checks and
    #    the rest pass, so routing splits it across successful + rejected.
    df = valid_frame(hours=range(8))
    df["Volume"] = df["Volume"].astype("object")
    df["VolDate"] = df["VolDate"].astype("object")
    df.loc[1, "Unit"] = ""                # missing field
    df.loc[2, "Unit"] = f'{UNIT_A}"'      # dirty cell (quote)
    df.loc[3, "Volume"] = -1              # bad volume
    df.loc[4, "VolDate"] = "not-a-date"   # bad date
    df.loc[5, "Unit"] = UNKNOWN_UNIT      # unknown unit
    write(df, "test_record_mix.csv")

    # 8. Date-range demo — like the mix above, but with CONSECUTIVE rows that
    #    share ONE error across several days. This makes the summary's DateRange
    #    (and the collapsed RecordNumbers) span more than a single day:
    #      * records 2-5: UNIT_A Volume below minimum, 1/13/2026 - 1/16/2026
    #      * records 7-9: unknown unit, 1/13/2026 - 1/15/2026
    #    plus a few single-row errors (missing/quote/bad-date) for variety.
    rows = [
        ["Census", FACILITY, UNIT_A, "1/13/2026", 0, 10],      # pass
        ["Census", FACILITY, UNIT_A, "1/13/2026", 1, -1],      # Volume < min ┐
        ["Census", FACILITY, UNIT_A, "1/14/2026", 2, -5],      # Volume < min │ records
        ["Census", FACILITY, UNIT_A, "1/15/2026", 3, -3],      # Volume < min │ 2-5
        ["Census", FACILITY, UNIT_A, "1/16/2026", 4, -2],      # Volume < min ┘
        ["Census", FACILITY, UNIT_A, "1/17/2026", 5, 15],      # pass
        ["Census", FACILITY, UNKNOWN_UNIT, "1/13/2026", 6, 20],  # unknown ┐ records
        ["Census", FACILITY, UNKNOWN_UNIT, "1/14/2026", 7, 21],  # unknown │ 7-9
        ["Census", FACILITY, UNKNOWN_UNIT, "1/15/2026", 8, 22],  # unknown ┘
        ["Census", FACILITY, "", "1/13/2026", 9, 11],            # missing Unit
        ["Census", FACILITY, f'{UNIT_A}"', "1/13/2026", 10, 12], # quote + unknown
        ["Census", FACILITY, UNIT_A, "not-a-date", 11, 14],      # bad date (no range)
    ]
    write(pd.DataFrame(rows, columns=COLS), "test_record_mix_daterange.csv")

    print("\nDone. test_all_valid.csv passes entirely; each other fixture has "
          "one or more rows that fail the matching check; test_record_mix.csv "
          "splits across successful and rejected output; "
          "test_record_mix_daterange.csv adds consecutive multi-day error runs "
          "to demonstrate the summary's DateRange field.")


if __name__ == "__main__":
    main()

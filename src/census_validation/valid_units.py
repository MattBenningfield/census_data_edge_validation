"""Loading and comparison of the valid/known units reference list."""

from __future__ import annotations

import os
import sys

import pandas as pd


def load_valid_units(path: str) -> set[str]:
    """
    Load the set of valid/known unit names from the reference file.

    Accepts a .csv (with a "Unit" column) or any other text file treated as one
    unit per line. Exits the process if the file is missing or malformed.

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


def comparison_set(units: set[str], case_sensitive: bool) -> set[str]:
    """Pre-build the set used for membership tests, honoring case sensitivity."""
    return set(units) if case_sensitive else {u.lower() for u in units}

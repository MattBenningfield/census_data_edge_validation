"""
Shared pytest fixtures/config for the record-validator test suite.

Puts src/ on sys.path so record_validation can be imported, configures the
module from the real config before each test, and provides helpers for building
DataFrames and temporary config files.
"""
import os
import sys
import textwrap

import pandas as pd
import pytest

SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import record_validation as v  # noqa: E402

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


@pytest.fixture(autouse=True)
def _configured():
    """
    Populate the module's settings from the real config before every test so
    globals like EXPECTED_COLUMNS / MIN_VOLUME / VALID_UNITS_CMP are realistic.
    Individual tests may still monkeypatch specific globals.
    """
    v.configure()
    yield


@pytest.fixture
def make_df():
    """Return a helper that builds a DataFrame with the expected columns."""
    def _make(rows):
        return pd.DataFrame(rows, columns=COLS)
    return _make


@pytest.fixture
def write_config():
    """
    Return a helper that writes a temp config.toml (absolute tmp paths) and
    returns its path. Flags/values are overridable via keyword arguments.
    """
    def _write(tmp_path, valid_units_path, route_validated_files=True,
               write_rejection_summary=True):
        d = str(tmp_path).replace("\\", "/")
        vu = str(valid_units_path).replace("\\", "/")
        cfg = textwrap.dedent(f'''
            [schema]
            expected_columns = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]
            required_columns = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]
            [rules]
            min_hour = 0
            max_hour = 23
            min_volume = 0
            case_sensitive_units = true
            [paths]
            default_file = "{d}/in.csv"
            log_root = "{d}/logs"
            accepted_dir = "{d}/ok"
            rejected_dir = "{d}/bad"
            rejected_summary_dir = "{d}/summary"
            valid_units_file = "{vu}"
            [behavior]
            route_validated_files = {str(route_validated_files).lower()}
            rejection_reason_column = "RejectionReason"
            write_rejection_summary = {str(write_rejection_summary).lower()}
            [logging]
            format = "%(asctime)s  %(levelname)-8s  %(message)s"
            datefmt = "%Y-%m-%d %H:%M:%S"
        ''')
        p = tmp_path / "config.toml"
        p.write_text(cfg, encoding="utf-8")
        return p
    return _write

"""
Shared pytest fixtures for the census_validation test suite.

Puts src/ on sys.path so the ``census_validation`` package imports, and provides
a real-config ``settings``/``processor``, a DataFrame builder, and a temp-config
writer. Tests build their own processors/settings, so there is no module-level
configuration step.
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

from census_validation.config import load_settings           # noqa: E402
from census_validation.processor import CensusFileProcessor   # noqa: E402

COLS = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]


@pytest.fixture
def settings():
    """Settings loaded from the real project config."""
    return load_settings()


@pytest.fixture
def processor():
    """A processor built from the real project config (realistic valid_units)."""
    return CensusFileProcessor.from_config_file()


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
               write_rejection_summary=True, check_duplicates=True,
               write_dynamo_output=True):
        d = str(tmp_path).replace("\\", "/")
        vu = str(valid_units_path).replace("\\", "/")
        cfg = textwrap.dedent(f'''
            [schema]
            expected_columns = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]
            required_columns = ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]
            dup_keys = ["Type", "Facility", "Unit", "VolDate", "VolHour"]
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
            check_duplicates = {str(check_duplicates).lower()}
            timestamp_column = "VolTimestamp"
            [dynamo]
            write_dynamo_output = {str(write_dynamo_output).lower()}
            dynamo_dir = "{d}/dynamo"
            org_id = "test-org"
            file_type = "CENSUS"
            [logging]
            format = "%(asctime)s  %(levelname)-8s  %(message)s"
            datefmt = "%Y-%m-%d %H:%M:%S"
        ''')
        p = tmp_path / "config.toml"
        p.write_text(cfg, encoding="utf-8")
        return p
    return _write

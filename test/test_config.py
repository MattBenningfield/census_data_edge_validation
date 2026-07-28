"""Tests for census_validation.config (settings loading)."""

import pytest

from census_validation.config import Settings, load_settings


def test_load_settings_returns_populated_settings(tmp_path, write_config):
    units = tmp_path / "u.txt"
    units.write_text("UnitA\n", encoding="utf-8")
    cfg = write_config(tmp_path, units)

    s = load_settings(str(cfg))
    assert isinstance(s, Settings)
    assert s.expected_columns == ["Type", "Facility", "Unit", "VolDate", "VolHour", "Volume"]
    assert s.dup_keys == ["Type", "Facility", "Unit", "VolDate", "VolHour"]
    assert (s.min_hour, s.max_hour, s.min_volume) == (0, 23, 0)
    assert s.timestamp_column == "VolTimestamp"
    assert s.org_id == "test-org" and s.file_type == "CENSUS"
    assert s.check_duplicates is True and s.write_dynamo is True


def test_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        load_settings(str(tmp_path / "nope.toml"))


def test_missing_required_key_exits(tmp_path):
    bad = tmp_path / "config.toml"
    bad.write_text("[schema]\nexpected_columns = []\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_settings(str(bad))

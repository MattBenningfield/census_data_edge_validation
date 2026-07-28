"""
Configuration loading for the census validator.

The TOML config is parsed once into an immutable ``Settings`` object that the
rest of the package reads from, replacing the previous bag of module globals.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "WFAI_RECORD_VALIDATION_CONFIG", os.path.join(BASE_DIR, "config.toml")
)


def _resolve(path: str) -> str:
    """Resolve a relative config path against BASE_DIR; absolute paths pass through."""
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


@dataclass(frozen=True)
class Settings:
    """Every validator setting, loaded from config.toml."""

    # [schema]
    expected_columns: list[str]
    required_columns: list[str]
    dup_keys: list[str]
    # [rules]
    min_hour: int
    max_hour: int
    min_volume: int
    case_sensitive_units: bool
    # [paths]
    default_file: str
    log_root: str
    accepted_dir: str
    rejected_dir: str
    rejected_summary_dir: str
    valid_units_file: str
    # [behavior]
    route_files: bool
    rejection_reason_column: str
    write_summary: bool
    check_duplicates: bool
    timestamp_column: str
    # [dynamo]
    write_dynamo: bool
    dynamo_dir: str
    org_id: str
    file_type: str
    # [logging]
    log_format: str
    log_datefmt: str


def load_settings(path: str = CONFIG_PATH) -> Settings:
    """
    Load and validate the TOML config.

    Returns:
        Settings: The validated configuration. Exits the process with a clear
        message if the file is missing, unparseable, or missing a required key.
    """
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except FileNotFoundError:
        sys.exit(f"[ERROR] Config file not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        sys.exit(f"[ERROR] Could not parse config file {path}: {exc}")

    try:
        return Settings(
            expected_columns=cfg["schema"]["expected_columns"],
            required_columns=cfg["schema"]["required_columns"],
            dup_keys=cfg["schema"]["dup_keys"],
            min_hour=cfg["rules"]["min_hour"],
            max_hour=cfg["rules"]["max_hour"],
            min_volume=cfg["rules"]["min_volume"],
            case_sensitive_units=cfg["rules"]["case_sensitive_units"],
            default_file=_resolve(cfg["paths"]["default_file"]),
            log_root=_resolve(cfg["paths"]["log_root"]),
            accepted_dir=_resolve(cfg["paths"]["accepted_dir"]),
            rejected_dir=_resolve(cfg["paths"]["rejected_dir"]),
            rejected_summary_dir=_resolve(cfg["paths"]["rejected_summary_dir"]),
            valid_units_file=_resolve(cfg["paths"]["valid_units_file"]),
            route_files=cfg["behavior"]["route_validated_files"],
            rejection_reason_column=cfg["behavior"]["rejection_reason_column"],
            write_summary=cfg["behavior"]["write_rejection_summary"],
            check_duplicates=cfg["behavior"]["check_duplicates"],
            timestamp_column=cfg["behavior"]["timestamp_column"],
            write_dynamo=cfg["dynamo"]["write_dynamo_output"],
            dynamo_dir=_resolve(cfg["dynamo"]["dynamo_dir"]),
            org_id=cfg["dynamo"]["org_id"],
            file_type=cfg["dynamo"]["file_type"],
            log_format=cfg["logging"]["format"],
            log_datefmt=cfg["logging"]["datefmt"],
        )
    except KeyError as exc:
        sys.exit(f"[ERROR] Missing required config key: {exc} in {path}")

"""
WFAI Record Validator (DataValidation v2) — census_validation package.

A per-record "edge" gate for census time-series CSVs. Each row is checked on its
own to confirm it is complete, well-formatted, clean, and tied to a known unit,
plus a cross-record duplicate check that keeps the highest-Volume duplicate.

Modules:
  config        settings loading (Settings dataclass)
  valid_units   the known-units reference
  checks        the six per-record checks
  duplicates    duplicate resolution (CHECK 5)
  timestamp     VolDate + VolHour -> VolTimestamp output column
  summary       aggregated rejection report
  dynamo_items  DynamoDB-shaped item builders (Files + Validation Errors)
  routing       write records / summary / JSON to disk
  processor     CensusFileProcessor orchestration + CLI entry (__main__)
"""

from .config import Settings, load_settings
from .processor import CensusFileProcessor

__all__ = ["CensusFileProcessor", "Settings", "load_settings"]

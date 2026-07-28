"""
Output routing: write validated records and reports to disk.

Three outputs, all gated by settings:
  * accepted/rejected record CSVs (VolDate+VolHour collapsed to VolTimestamp),
  * an aggregated rejection-summary CSV,
  * DynamoDB-shaped JSON (Files item + Validation Errors items).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .dynamo_items import build_error_items, build_file_item
from .summary import build_rejection_summary
from .timestamp import to_output_frame

log = logging.getLogger("census_validation")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def route_records(csv_path, passed_df, failed_df, settings):
    """
    Write passing and failing records to the successful/rejected folders.

    Returns:
        tuple[str | None, str | None]: (successful_path, rejected_path); each is
        None when that side has no records or routing is disabled.
    """
    if not settings.route_files:
        return None, None

    stem = os.path.splitext(os.path.basename(csv_path))[0]
    stamp = _stamp()

    successful_path = None
    if not passed_df.empty:
        os.makedirs(settings.accepted_dir, exist_ok=True)
        successful_path = os.path.join(settings.accepted_dir,
                                       f"{stem}_successful_{stamp}.csv")
        to_output_frame(passed_df, settings.timestamp_column).to_csv(
            successful_path, index=False)

    rejected_path = None
    if not failed_df.empty:
        os.makedirs(settings.rejected_dir, exist_ok=True)
        rejected_path = os.path.join(settings.rejected_dir,
                                     f"{stem}_rejected_{stamp}.csv")
        to_output_frame(failed_df, settings.timestamp_column).to_csv(
            rejected_path, index=False)

    return successful_path, rejected_path


def write_rejection_summary(csv_path, failed_df, settings):
    """
    Write the aggregated rejection report to rejected_summary_dir.

    Returns:
        str | None: The summary path, or None when disabled or nothing to report.
    """
    if not (settings.route_files and settings.write_summary) or failed_df.empty:
        return None

    summary = build_rejection_summary(failed_df, settings.rejection_reason_column)
    if summary.empty:
        return None

    os.makedirs(settings.rejected_summary_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    summary_path = os.path.join(settings.rejected_summary_dir,
                                f"{stem}_rejected_summary_{_stamp()}.csv")
    summary.to_csv(summary_path, index=False)
    return summary_path


def write_dynamo_output(csv_path, file_id, df, failed_df, settings,
                        schema_invalid=False, schema_reason=None):
    """
    Write the DynamoDB-shaped JSON files (Files META item + Validation Errors
    items) to dynamo_dir.

    Returns:
        tuple[str | None, str | None]: (file_item_path, error_items_path), or
        (None, None) when disabled.
    """
    if not settings.write_dynamo:
        return None, None

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Structural (schema) failures are recorded on the file item only, not as
    # row-level error items (per dynamo_schema.md).
    error_items = ([] if schema_invalid else build_error_items(
        failed_df, file_id, created_at, settings.rejection_reason_column))
    file_item = build_file_item(
        csv_path, file_id, df, error_items, created_at,
        settings.org_id, settings.file_type,
        schema_invalid=schema_invalid, schema_reason=schema_reason,
    )

    os.makedirs(settings.dynamo_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    stamp = _stamp()
    file_path = os.path.join(settings.dynamo_dir, f"{stem}_file_{stamp}.json")
    errors_path = os.path.join(settings.dynamo_dir, f"{stem}_errors_{stamp}.json")
    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump(file_item, fh, indent=2)
    with open(errors_path, "w", encoding="utf-8") as fh:
        json.dump(error_items, fh, indent=2)
    return file_path, errors_path

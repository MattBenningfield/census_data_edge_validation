# DynamoDB Schema — Census Ingestion & Validation Pipeline

Four tables: canonical time-series data, time-series header/pointers, file metadata, and validation errors.

---

## 1. Time-series Data

Canonical vetted hourly values. One item = one validated hour for one Unit/Type combination.
Monthly bucketing in PK keeps partitions bounded and supports efficient time-range queries.

> **Proposal (not yet in POC):** The current POC code uses `FACILITY#<f>#UNIT#<u>#TYPE#<t>`
> without `ORG#` or `MONTH#` segments. We advocate for adding both:
> - `ORG#` — scopes data per tenant; prevents cross-org leakage and supports future multi-tenant isolation.
> - `MONTH#` — bounds partition size; without it a unit with years of hourly data accumulates in a single partition.
>
> The POC reader's `partition_key` property would change from
> `f"FACILITY#{f}#UNIT#{u}#TYPE#{t}"` to
> `f"ORG#{org}#FACILITY#{f}#UNIT#{u}#TYPE#{t}#MONTH#{month}"`.

### Keys

| Key | Pattern |
|-----|---------|
| PK  | `ORG#<org_id>#FACILITY#<facility_id>#UNIT#<unit_id>#TYPE#<type>#MONTH#<yyyy-mm>` |
| SK  | `TS#<yyyy-mm-ddThh:00:00Z>` |

### Example

```
PK = ORG#mercy#FACILITY#facility_1#UNIT#MH-MED-N#TYPE#CENSUS#MONTH#2026-07
SK = TS#2026-07-22T00:00:00Z
```

### Attributes

Denormalized row attributes matching the census CSV structure (aligned with POC):

```json
{
  "Facility": "facility_1",
  "Unit": "MH-MED-N",
  "Type": "CENSUS",
  "VolDate": "2026-07-22T00:00:00",
  "VolHour": 0,
  "Volume": 42,
  "source_file_id": "file_123",
  "validation_execution_id": "execution_456",
  "validated_at": "2026-07-27T18:22:00Z"
}
```

### Access Patterns

| Pattern | Operation |
|---------|-----------|
| All hours for a unit/type in a month | `Query PK = ORG#...#TYPE#...#MONTH#2026-07` |
| Specific hour | `GetItem PK + SK` |
| Range within a month | `Query PK, SK between TS#...start and TS#...end` |
| Range spanning months | One query per monthly bucket, merge client-side |
| All rows from incoming date onward | `Query PK, SK >= TS#<incoming_date>` |

### Notes

- A later vetted correction for the same hour overwrites the existing item.
- All timestamps normalized to UTC ISO-8601 without timezone offsets or fractional seconds.
- Original upload artifacts retained in S3 for audit/revision history.
- `Facility`, `Unit`, `Type` are denormalized onto each row for query projection convenience (matches POC).

---

## 2. Time-series Headers / Pointers

One item per Unit/Type combination. The **pointer** is the latest contiguous vetted hour — it must not
advance through gaps (e.g., if July 22 passes but July 21 has a gap, pointer stays before the gap).

> **Proposal (not yet in POC):** The current POC code uses `FACILITY#<f>#UNIT#<u>` / `TYPE#<t>`
> without `ORG#`. We advocate for adding `ORG#` for multi-tenant isolation.

### Keys

| Key | Pattern |
|-----|---------|
| PK  | `ORG#<org_id>#FACILITY#<facility_id>#UNIT#<unit_id>` |
| SK  | `TYPE#<type>` |

### Example

```
PK = ORG#mercy#FACILITY#facility_1#UNIT#MH-MED-N
SK = TYPE#CENSUS
```

### Attributes

Denormalized fields matching the POC's `write_time_series_header`:

```json
{
  "Facility": "facility_1",
  "Unit": "MH-MED-N",
  "Type": "CENSUS",
  "pointer": "2026-07-22T00:00:00"
}
```

Future additions (not yet in POC):

```json
{
  "last_validated_file_id": "file_123",
  "last_validation_execution_id": "execution_456",
  "updated_at": "2026-07-27T18:22:00Z",
  "version": 17
}
```

### Access Patterns

| Pattern | Operation |
|---------|-----------|
| Current pointer for a unit/type | `GetItem PK + SK` |
| All types for a unit | `Query PK` |
| Batch lookup for a file's known unit/types | `BatchGetItem` |

### Notes

- POC uses `update_item` with an upsert-style `SET` expression. For production, add a conditional update on `version` or existing `pointer` to prevent concurrent file validations from moving the pointer incorrectly.
- No GSI required at MVP.

---

## 3. Files

File metadata and file-list indexes. One **metadata item** per file, plus lightweight **unit membership items** for the unit-filter UI.

### Keys — Metadata Item

| Key | Pattern |
|-----|---------|
| PK  | `FILE#<file_id>` |
| SK  | `META` |

### Example

```
PK = FILE#file_123
SK = META
```

### Attributes

```json
{
  "org_id": "mercy",
  "file_name": "census_mercy_main_v3.xlsx",
  "file_type": "CENSUS",
  "uploaded_by_id": "user_123",
  "uploaded_by_display": "Jamie Torres",
  "uploaded_at": "2026-07-27T17:15:00Z",
  "row_count": 2841,
  "facility_ids": ["facility_1"],
  "unit_ids": ["MH-MED-S", "MH-MED-N", "MH-ICU-E"],
  "census_start": "2024-01-15T00:00:00Z",
  "census_end": "2025-06-09T23:00:00Z",
  "pointer_snapshot": "2024-03-01T00:00:00Z",
  "pointer_limiting_unit_id": "MH-MED-N",
  "workflow_status": "COMPLETED",
  "validation_result": "WARNING",
  "error_count": 1,
  "warning_count": 3,
  "validation_checks": [
    {
      "check_id": "FILE_FORMAT",
      "display_order": 1,
      "result": "PASS",
      "summary": ".xlsx — valid",
      "affected_row_count": 0
    },
    {
      "check_id": "REQUIRED_COLUMNS",
      "display_order": 2,
      "result": "PASS",
      "summary": "All 14 present",
      "affected_row_count": 0
    },
    {
      "check_id": "DUPLICATE_ROWS",
      "display_order": 3,
      "result": "WARNING",
      "summary": "3 duplicate employee IDs",
      "affected_row_count": 3
    },
    {
      "check_id": "BLANK_FIELDS",
      "display_order": 4,
      "result": "ERROR",
      "summary": "47 rows missing unit_code",
      "affected_row_count": 47
    }
  ]
}
```

### Status Enums

| Field | Values |
|-------|--------|
| `workflow_status` | `UPLOADED`, `VALIDATING`, `COMPLETED`, `FAILED` |
| `validation_result` | `CLEAN`, `WARNING`, `BLOCKED` |

### Keys — Unit Membership Items (for unit-filter GSI)

| Key | Pattern |
|-----|---------|
| PK  | `FILE#<file_id>` |
| SK  | `UNIT#<facility_id>#<unit_id>` |

### Example

```
PK = FILE#file_123
SK = UNIT#facility_1#MH-MED-N
```

### GSIs

#### GSI1 — Upload History (all files for an org, chronological)

| Key | Pattern |
|-----|---------|
| GSI1PK | `ORG#<org_id>` |
| GSI1SK | `UPLOADED#<uploaded_at>#FILE#<file_id>` |

```
GSI1PK = ORG#mercy
GSI1SK = UPLOADED#2026-07-27T17:15:00Z#FILE#file_123
```

#### GSI2 — Files with Problems (sparse index)

Only populated when `validation_result` is `WARNING` or `BLOCKED`. DynamoDB sparse indexes
automatically exclude items that lack the GSI key attributes.

| Key | Pattern |
|-----|---------|
| GSI2PK | `ORG#<org_id>#PROBLEMS` |
| GSI2SK | `UPLOADED#<uploaded_at>#FILE#<file_id>` |

```
GSI2PK = ORG#mercy#PROBLEMS
GSI2SK = UPLOADED#2026-07-27T17:15:00Z#FILE#file_123
```

#### GSI3 — Files by Unit (for "Filter by unit" dropdown)

Populated from the unit membership items.

| Key | Pattern |
|-----|---------|
| GSI3PK | `ORG#<org_id>#UNIT#<unit_id>` |
| GSI3SK | `UPLOADED#<uploaded_at>#FILE#<file_id>` |

```
GSI3PK = ORG#mercy#UNIT#MH-MED-N
GSI3SK = UPLOADED#2026-07-27T17:15:00Z#FILE#file_123
```

Query GSI3 → get file IDs containing that unit → `BatchGetItem` their META items.

### Access Patterns

| Pattern | Operation |
|---------|-----------|
| File metadata | `GetItem FILE#<id> / META` |
| Org upload history | `Query GSI1PK = ORG#<org_id>` |
| Files with problems | `Query GSI2PK = ORG#<org_id>#PROBLEMS` |
| Files containing a unit | `Query GSI3PK = ORG#<org_id>#UNIT#<unit_id>` |

### Notes

- Project list-UI columns into GSI1 and GSI2 to avoid base-table fetches for rendering the list.
- File-level validation check summaries live here (on the META item), not in the errors table.
- File-level failures (unreadable workbook, missing columns) are recorded in `validation_checks` and do not produce row-level error items.
- `pointer_snapshot` and `pointer_limiting_unit_id` capture the state at validation time so old results don't shift as newer files advance the pointer.

---

## 4. Validation Errors

One item per validation error. Each error corresponds to a problem found at a specific timestamp
in the incoming CSV. Using `TS#<timestamp>` instead of `ROW#<row_number>` gives chronological
ordering, which is more meaningful for time-series data. Row number is preserved as a regular
attribute for CSV traceability.

### Keys

| Key | Pattern |
|-----|---------|
| PK  | `FILE#<file_id>` |
| SK  | `TS#<yyyy-mm-ddThh:00:00Z>#CHECK#<check_id>#FIELD#<column>#ERROR#<error_id>` |

### Example

```
PK = FILE#file_123
SK = TS#2024-03-01T00:00:00Z#CHECK#REQUIRED_VALUE#FIELD#unit_code#ERROR#a7f21
```

### Attributes

```json
{
  "row_number": 47,
  "timestamp": "2024-03-01T00:00:00Z",
  "check_id": "REQUIRED_VALUE",
  "column": "unit_code",
  "severity": "ERROR",
  "message": "unit_code is required",
  "invalid_value": "",
  "facility_id": "facility_1",
  "unit_id": null,
  "item_id": null,
  "created_at": "2026-07-27T18:20:00Z"
}
```

### Access Patterns

| Pattern | Operation |
|---------|-----------|
| All errors for a file (chronological) | `Query PK = FILE#<file_id>` |
| Errors in a time range | `Query PK, SK between TS#<start> and TS#<end>~` |
| Errors for a specific check | `Query PK, SK begins_with TS#... then filter on check_id` |

### Optional GSI — Errors by Unit (if UI needs unit-sorted error view)

| Key | Pattern |
|-----|---------|
| GSI1PK | `FILE#<file_id>` |
| GSI1SK | `UNIT#<unit_id_or_UNKNOWN>#TS#<timestamp>#CHECK#<check_id>#ERROR#<error_id>` |

Not required if row/chronological order is sufficient for MVP.

### Notes

- `error_id` should be **deterministic** (e.g. hash of file_id + row_number + check_id + column) so validation retries are idempotent and don't create duplicate errors.
- Multiple errors on the same timestamp are disambiguated by the CHECK#FIELD#ERROR# suffix.
- Errors that don't correspond to a specific timestamp (e.g., structural file issues) should use a sentinel like `TS#0000-00-00T00:00:00Z` or be recorded only in the file's `validation_checks` summary.
- Zero-pad row numbers in the `row_number` attribute if they'll be used for display sorting.
- Consider DynamoDB TTL for error retention.

---

## Design Decisions & Tradeoffs

### Timestamp vs Row Number in Errors SK

Using `TS#<timestamp>` was chosen because:
- Census CSV rows represent hourly time-series values — the timestamp is the natural identity.
- Chronological ordering is more useful than CSV row ordering for the UI.
- `begins_with(SK, "TS#2024-03")` retrieves all March 2024 errors efficiently.

`row_number` is kept as a regular attribute so error reports can still reference the original CSV line.

### Validation Check Summaries

Stored on the **Files table META item** (not as individual error-table records) because:
- DynamoDB has no `GROUP BY` — aggregating error records to produce "47 rows missing unit_code" would require reading every error.
- The UI's "Validation Checks" panel needs pre-computed summaries.
- Summaries are written once at validation completion.

### Pointer Semantics

The pointer means "latest **contiguous** vetted hour." It must not advance through gaps.
Pointer updates must use conditional writes and only occur after all canonical writes for the
validated range succeed.

### GSI Consistency

GSIs are eventually consistent. A newly completed file may take a short time to appear in
upload/problem lists even though its base metadata is immediately readable via `GetItem`.

### Hot Partition Risk

- **Files GSI2 (problems):** Partition key is `ORG#<org_id>#PROBLEMS`. Acceptable if per-org traffic is moderate.
- **Errors table:** Partition key is `FILE#<file_id>`. Very bad files could produce thousands of items in one partition. Acceptable for batch MVP processing with batched writes and retry-with-backoff.
- **Time-series data:** Monthly bucketing prevents unbounded partition growth.

---

## TODO / Open Questions

- [ ] Confirm whether the errors-by-unit GSI is needed at MVP or can be deferred.
- [ ] Define error retention policy / TTL values.
- [ ] Determine if `facility_id` values are globally unique or need an org prefix.
- [ ] Decide scope of the "files with problems" list — per org is assumed; per facility would need a different GSI2PK.
- [ ] Confirm the sentinel approach for timestamp-less errors (structural file issues).
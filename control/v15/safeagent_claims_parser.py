"""
safeagent_claims_parser.py

Parses SafeAgent claim/audit records (JSON, JSONL, or CSV) into
InternalClaim objects, per the actual schema SafeAgent's dedup guard uses
in production (see rv_qqq_v23_no_churn.py's place_order_with_retry() and
the safeagent_orders.db `orders` table: request_id keyed on
symbol+side+qty+stage+bar timestamp, status PENDING/COMMITTED, a
serialized broker result once committed).

CORE IS FROZEN. This module only ever imports InternalClaim,
Interpretation, and FailureType from reconcile_v12 and constructs them
through their existing public constructor -- no core file is touched.

DESIGN PRINCIPLES
------------------
- Missing OPTIONAL fields are left None. Never invented, never defaulted
  to a guessed value.
- Missing or unparseable REQUIRED fields reject the record with its
  1-based record number and a specific reason -- the record is skipped,
  not silently coerced.
- Duplicate request_id rows are NEVER collapsed here. Every row that
  parses successfully becomes its own InternalClaim object, in input
  order. assign_claim_stable_ids() (called inside reconcile()) is what
  gives genuinely distinct claims sharing a request_id distinct
  identities -- see ACCEPTANCE_REPORT_V12_1.md item 1. This parser's job
  is only to not throw that guarantee away before the engine gets a
  chance to apply it (e.g. by building a dict keyed on request_id, which
  would silently keep only the last row for each key).
- Every accepted row keeps a PROVENANCE record (source file, format,
  1-based record number, raw fields as given) so the CLI can trace any
  finding back to the exact input row. This is tracked in parallel to the
  claims list, indexed by position, NOT by mutating InternalClaim, which
  is a frozen v12.1 core dataclass with no such field.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from reconcile_v12 import InternalClaim, Interpretation, FailureType


REQUIRED_FIELDS = ("request_id", "symbol", "side", "qty", "stage", "claimed_at", "outcome")
# caller_interpretation is required too, but validated separately (see below)
# because it is the field that actually distinguishes a correctly-handled
# SKIP from the "SKIP mis-logged as success" incident pattern -- the whole
# reason this reconciliation core exists. Silently defaulting it to
# something plausible would erase exactly the signal SafeAgent is for.

_INTERPRETATION_VALUES = {m.value for m in Interpretation}
_FAILURE_TYPE_VALUES = {m.value for m in FailureType}
_VALID_SIDES = {"buy", "sell"}


@dataclass
class RejectedRecord:
    record_number: int  # 1-based: JSON array index+1, JSONL line number, or CSV data-row number (header excluded)
    reason: str
    raw: dict


@dataclass
class ClaimProvenance:
    source_file: str
    source_format: str  # 'json' | 'jsonl' | 'csv'
    record_number: int
    raw: dict


def _parse_timestamp(value) -> Optional[datetime]:
    """Accepts an ISO-8601 string (with or without a trailing 'Z', with or
    without fractional seconds of any length) or a datetime already.
    Returns None if unparseable -- caller decides whether that's fatal."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Truncate over-long fractional seconds (Python's fromisoformat wants
    # at most 6 digits; some real logs/exports emit more).
    if "." in s:
        head, _, rest = s.partition(".")
        frac = ""
        tz = ""
        for i, ch in enumerate(rest):
            if ch in "+-" and i > 0:
                frac, tz = rest[:i], rest[i:]
                break
        else:
            frac, tz = rest, ""
        frac = frac[:6]
        s = f"{head}.{frac}{tz}" if frac else head + tz
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_qty(value) -> Optional[float]:
    """Accepts a native number or a numeric string. Returns None if it
    isn't a valid positive number."""
    if value is None or value == "":
        return None
    try:
        q = float(value)
    except (TypeError, ValueError):
        return None
    if q <= 0:
        return None
    return q


def _clean_optional_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return str(value)


def _row_to_claim(row: dict, record_number: int) -> tuple[Optional[InternalClaim], Optional[RejectedRecord]]:
    missing = [f for f in REQUIRED_FIELDS if row.get(f) in (None, "")]
    if missing:
        return None, RejectedRecord(record_number, f"missing required field(s): {missing}", row)

    request_id = str(row["request_id"])
    symbol = str(row["symbol"])
    side = str(row["side"]).strip().lower()
    if side not in _VALID_SIDES:
        return None, RejectedRecord(record_number, f"side must be 'buy' or 'sell', got {row['side']!r}", row)

    qty = _parse_qty(row["qty"])
    if qty is None:
        return None, RejectedRecord(record_number, f"qty is not a valid positive number: {row['qty']!r}", row)

    stage = str(row["stage"])

    claimed_at = _parse_timestamp(row["claimed_at"])
    if claimed_at is None:
        return None, RejectedRecord(record_number, f"claimed_at is not a valid ISO-8601 timestamp: {row['claimed_at']!r}", row)

    outcome = str(row["outcome"])

    interp_raw = row.get("caller_interpretation")
    if interp_raw in (None, ""):
        return None, RejectedRecord(record_number, "missing required field(s): ['caller_interpretation']", row)
    interp_str = str(interp_raw).strip().upper()
    if interp_str not in _INTERPRETATION_VALUES:
        return None, RejectedRecord(
            record_number,
            f"caller_interpretation must be one of {sorted(_INTERPRETATION_VALUES)}, got {interp_raw!r}",
            row,
        )
    caller_interpretation = Interpretation(interp_str)

    failure_type_raw = row.get("failure_type")
    if failure_type_raw in (None, ""):
        failure_type = FailureType.NONE
    else:
        ft_str = str(failure_type_raw).strip().upper()
        if ft_str not in _FAILURE_TYPE_VALUES:
            return None, RejectedRecord(
                record_number,
                f"failure_type must be one of {sorted(_FAILURE_TYPE_VALUES)}, got {failure_type_raw!r}",
                row,
            )
        failure_type = FailureType(ft_str)

    broker_order_id = _clean_optional_str(row.get("broker_order_id"))
    client_order_id = _clean_optional_str(row.get("client_order_id"))
    source_system = _clean_optional_str(row.get("source_system"))
    source_account = _clean_optional_str(row.get("source_account"))
    event_id = _clean_optional_str(row.get("event_id"))

    claim = InternalClaim(
        request_id=request_id, symbol=symbol, side=side, qty=qty, stage=stage,
        claimed_at=claimed_at, outcome=outcome, caller_interpretation=caller_interpretation,
        broker_order_id=broker_order_id, client_order_id=client_order_id,
        failure_type=failure_type,
        source_system=source_system, source_account=source_account,
        immutable_source_event_id=event_id,
    )
    return claim, None


def _iter_json_records(text: str):
    data = json.loads(text)
    if isinstance(data, dict):
        # Common envelope shapes: {"claims": [...]}  or {"records": [...]}
        for key in ("claims", "records", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            data = [data]  # a single claim object
    if not isinstance(data, list):
        raise ValueError("JSON claims input must be a list of objects, or an object wrapping one")
    for i, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            yield i, {"_non_object_row": row}
        else:
            yield i, row


def _iter_jsonl_records(text: str):
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            yield i, {"_unparseable_line": line, "_error": str(e)}
            continue
        if not isinstance(row, dict):
            yield i, {"_non_object_row": row}
        else:
            yield i, row


def _iter_csv_records(text: str):
    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader, start=1):
        yield i, dict(row)


def parse_claims(path: str, fmt: Optional[str] = None) -> tuple[list[InternalClaim], list[RejectedRecord], list[ClaimProvenance]]:
    """
    Parses a SafeAgent claims file. `fmt` is inferred from the extension
    (.json / .jsonl / .csv) if not given explicitly.

    Returns (claims, rejections, provenance). `claims` and `provenance`
    are parallel lists -- claims[i] was built from provenance[i].raw.
    Rejected rows never appear in either.
    """
    if fmt is None:
        lower = path.lower()
        if lower.endswith(".jsonl") or lower.endswith(".ndjson"):
            fmt = "jsonl"
        elif lower.endswith(".json"):
            fmt = "json"
        elif lower.endswith(".csv"):
            fmt = "csv"
        else:
            raise ValueError(f"Cannot infer claims file format from path {path!r}; pass fmt explicitly")

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    if fmt == "json":
        records = list(_iter_json_records(text))
    elif fmt == "jsonl":
        records = list(_iter_jsonl_records(text))
    elif fmt == "csv":
        records = list(_iter_csv_records(text))
    else:
        raise ValueError(f"Unknown claims format: {fmt!r}")

    claims: list[InternalClaim] = []
    rejections: list[RejectedRecord] = []
    provenance: list[ClaimProvenance] = []

    for record_number, row in records:
        if "_non_object_row" in row or "_unparseable_line" in row:
            reason = row.get("_error", "row is not a JSON object")
            rejections.append(RejectedRecord(record_number, reason, row))
            continue
        claim, rejection = _row_to_claim(row, record_number)
        if rejection is not None:
            rejections.append(rejection)
            continue
        claims.append(claim)
        provenance.append(ClaimProvenance(source_file=path, source_format=fmt, record_number=record_number, raw=row))

    return claims, rejections, provenance
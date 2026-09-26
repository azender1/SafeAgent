"""
alpaca_orders_parser.py

Parses Alpaca order records (JSON or CSV) into BrokerOrder objects, using
the real, documented Alpaca order object shape (id, client_order_id,
symbol, side, qty, status, submitted_at, filled_at, ...), and feeds them
through the EXISTING order-ingestion normalization contract
(ingestion_normalization.normalize_ingested_orders) -- no new
consolidation logic is invented here.

CORE IS FROZEN. Only imports BrokerOrder from reconcile_v12 and
normalize_ingested_orders from ingestion_normalization, both used through
their existing public interfaces.

REAL ALPACA QUIRKS THIS HANDLES
--------------------------------
- Alpaca's JSON API returns qty, filled_qty, and price fields as STRINGS
  (e.g. "qty": "10"), not native JSON numbers, to preserve decimal
  precision. Both string and native-number forms are accepted.
- Timestamps are ISO-8601 with a trailing 'Z' and sometimes
  sub-microsecond fractional seconds (nanoseconds) that Python's
  datetime.fromisoformat cannot parse directly -- both are normalized.
- A single order object, a bare list of orders, or an envelope like
  {"orders": [...]} are all accepted.
- Notional-only orders (Alpaca supports buying by dollar amount instead
  of share count) have no `qty` at all. BrokerOrder.qty is required, and
  this parser does NOT invent a share count from notional/price -- such
  rows are rejected with an explicit reason, not silently guessed.

STATUS IS NEVER REINTERPRETED
------------------------------
Whatever string Alpaca reports in `status` is preserved verbatim
(case-normalized to lowercase, since Alpaca's own API is already
lowercase). This parser does not map, rename, or guess a precedence for
any status value, known or unknown -- that is
ingestion_normalization.STATUS_PRECEDENCE's job, and its documented
behavior for anything it doesn't recognize is to flag it
(UNMAPPED_STATUS_UNRESOLVED) rather than silently order it. Preserving
unknown statuses here, unchanged, is what makes that guarantee possible
downstream.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from reconcile_v12 import BrokerOrder
from ingestion_normalization import normalize_ingested_orders, IngestionFinding


REQUIRED_FIELDS_JSON = ("symbol", "side", "status", "submitted_at")
_VALID_SIDES = {"buy", "sell"}

# Column-name aliases seen across different real Alpaca CSV export tools
# (dashboard export vs. get_orders().df.to_csv() vs. hand-built exports).
_CSV_ALIASES = {
    "order_id": "id", "alpaca_order_id": "id", "broker_order_id": "id",
    "client_id": "client_order_id",
    "qty": "qty", "quantity": "qty",
    "submitted_at": "submitted_at", "created_at": "submitted_at",
    "filled_at": "filled_at",
    "account": "source_account", "account_id": "source_account",
}


@dataclass
class RejectedRecord:
    record_number: int
    reason: str
    raw: dict


@dataclass
class OrderProvenance:
    source_file: str
    source_format: str  # 'json' | 'csv'
    record_number: int
    raw: dict


def _parse_timestamp(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, _, rest = s.partition(".")
        frac, tz = rest, ""
        for i, ch in enumerate(rest):
            if ch in "+-" and i > 0:
                frac, tz = rest[:i], rest[i:]
                break
        frac = frac[:6]
        s = f"{head}.{frac}{tz}" if frac else head + tz
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_qty(value) -> Optional[float]:
    """Accepts Alpaca's native number OR string-encoded number form.
    Returns None (never a guess) if absent or unparseable -- notional-only
    orders with no qty field fall here deliberately."""
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


def _row_to_order(
    row: dict, record_number: int, default_source_system: str, default_source_account: Optional[str],
) -> tuple[Optional[BrokerOrder], Optional[RejectedRecord]]:
    missing = [f for f in REQUIRED_FIELDS_JSON if row.get(f) in (None, "")]
    if missing:
        return None, RejectedRecord(record_number, f"missing required field(s): {missing}", row)

    symbol = str(row["symbol"])
    side = str(row["side"]).strip().lower()
    if side not in _VALID_SIDES:
        return None, RejectedRecord(record_number, f"side must be 'buy' or 'sell', got {row['side']!r}", row)

    # status is preserved EXACTLY as given (lowercased only) -- never
    # remapped, never validated against a known set. An Alpaca status this
    # parser has never seen is exactly as valid an input as one it has.
    status = str(row["status"]).strip().lower()

    submitted_at = _parse_timestamp(row["submitted_at"])
    if submitted_at is None:
        return None, RejectedRecord(
            record_number, f"submitted_at is not a valid ISO-8601 timestamp: {row['submitted_at']!r}", row
        )

    qty = _parse_qty(row.get("qty"))
    if qty is None:
        reason = ("qty is missing (notional-only orders are not supported -- "
                   "a share quantity cannot be safely invented from notional/price)"
                   if row.get("qty") in (None, "") else
                   f"qty is not a valid positive number: {row.get('qty')!r}")
        return None, RejectedRecord(record_number, reason, row)

    filled_at = _parse_timestamp(row.get("filled_at"))
    broker_order_id = _clean_optional_str(row.get("id"))
    client_order_id = _clean_optional_str(row.get("client_order_id"))
    source_system = _clean_optional_str(row.get("source_system")) or default_source_system
    source_account = _clean_optional_str(row.get("source_account")) or default_source_account

    order = BrokerOrder(
        symbol=symbol, side=side, qty=qty, status=status,
        submitted_at=submitted_at, filled_at=filled_at,
        broker_order_id=broker_order_id, client_order_id=client_order_id,
        source_system=source_system, source_account=source_account,
        immutable_source_record_id=None,  # Alpaca has no second identity layer beyond id/client_order_id
    )
    return order, None


def _iter_json_records(text: str):
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("orders", "records", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            data = [data]  # a single order object -- nested envelope of one
    if not isinstance(data, list):
        raise ValueError("JSON orders input must be a list of objects, or an object wrapping one")
    for i, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            yield i, {"_non_object_row": row}
        else:
            yield i, row


def _iter_csv_records(text: str):
    reader = csv.DictReader(io.StringIO(text))
    for i, raw_row in enumerate(reader, start=1):
        row = dict(raw_row)
        for alias, canonical in _CSV_ALIASES.items():
            if alias in row and canonical not in row:
                row[canonical] = row[alias]
        yield i, row


def parse_orders(
    path: str, fmt: Optional[str] = None,
    default_source_system: str = "ALPACA", default_source_account: Optional[str] = None,
) -> tuple[list[BrokerOrder], list[RejectedRecord], list[OrderProvenance]]:
    """Parses a raw Alpaca orders file. Does NOT normalize -- see
    parse_and_normalize_orders() for the full pipeline including the
    ingestion contract. Exposed separately so parsing itself is testable
    without dragging in ingestion behavior."""
    if fmt is None:
        lower = path.lower()
        if lower.endswith(".json"):
            fmt = "json"
        elif lower.endswith(".csv"):
            fmt = "csv"
        else:
            raise ValueError(f"Cannot infer orders file format from path {path!r}; pass fmt explicitly")

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    if fmt == "json":
        records = list(_iter_json_records(text))
    elif fmt == "csv":
        records = list(_iter_csv_records(text))
    else:
        raise ValueError(f"Unknown orders format: {fmt!r}")

    orders: list[BrokerOrder] = []
    rejections: list[RejectedRecord] = []
    provenance: list[OrderProvenance] = []

    for record_number, row in records:
        if "_non_object_row" in row:
            rejections.append(RejectedRecord(record_number, "row is not a JSON object", row))
            continue
        order, rejection = _row_to_order(row, record_number, default_source_system, default_source_account)
        if rejection is not None:
            rejections.append(rejection)
            continue
        orders.append(order)
        provenance.append(OrderProvenance(source_file=path, source_format=fmt, record_number=record_number, raw=row))

    return orders, rejections, provenance


def parse_and_normalize_orders(
    path: str, fmt: Optional[str] = None,
    default_source_system: str = "ALPACA", default_source_account: Optional[str] = None,
) -> tuple[list[BrokerOrder], list[IngestionFinding], list[RejectedRecord], dict]:
    """
    The full pipeline: parse raw Alpaca records, then feed them through
    the existing normalize_ingested_orders() contract unchanged.

    Returns (clean_orders, ingestion_findings, rejections, provenance_by_id)
    where provenance_by_id maps id(order_object) -> OrderProvenance for
    every RAW parsed order (including ones later consolidated away).
    normalize_ingested_orders() always returns actual objects from its
    input list (never newly-constructed ones), so looking up a surviving
    representative's id() always finds its original raw row.
    """
    raw_orders, rejections, provenance = parse_orders(path, fmt, default_source_system, default_source_account)
    provenance_by_id = {id(o): p for o, p in zip(raw_orders, provenance)}
    clean_orders, ingestion_findings = normalize_ingested_orders(raw_orders)
    return clean_orders, ingestion_findings, rejections, provenance_by_id
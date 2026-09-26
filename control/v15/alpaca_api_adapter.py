"""
alpaca_api_adapter.py

STRICTLY READ-ONLY. Retrieves historical Alpaca orders (every status, not
just open ones) via the official Trading API SDK (alpaca-py) and converts
them into the existing BrokerOrder contract, feeding through the existing
ingestion_normalization contract unchanged -- no new consolidation logic,
no core changes.

SECURITY BOUNDARY, enforced structurally, not just by convention.
ReadOnlyOrdersClient below wraps alpaca.trading.client.TradingClient and
exposes exactly ONE method: get_orders(). TradingClient itself also has
submit_order, cancel_order_by_id, cancel_orders, replace_order_by_id,
close_position, close_all_positions, set_account_configurations,
update_watchlist_by_id, patch, post, put, delete, and more -- none of
those names exist on this wrapper's interface, so no code path in this
module, or anything that imports it, can reach them by construction, not
merely by promise. test_alpaca_api_adapter.py additionally proves this
with a fake client whose write methods raise if ever called, driven
through the full adapter and CLI flow.

CREDENTIALS: read ONLY from the APCA_API_KEY_ID / APCA_API_SECRET_KEY
environment variables (the same ones the trading bots already use).
Never logged, never interpolated into an exception message, never
written to any report or file this module produces --
_sanitize_error_message() is a hard backstop that redacts them from any
exception text even though Alpaca's own error bodies don't normally echo
credentials back. test_alpaca_api_adapter.py proves no report or stdout
output can contain them.

PAPER BY DEFAULT: build_client(paper=True) unless the caller explicitly
passes paper=False (wired to control_cli.py's --live flag, which the
person must pass deliberately -- see that file). Never inferred from
credentials, environment, or any other signal.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.common.enums import Sort
from alpaca.common.exceptions import APIError

from reconcile_v12 import BrokerOrder
from ingestion_normalization import normalize_ingested_orders, IngestionFinding
from alpaca_orders_parser import _parse_qty, _clean_optional_str, RejectedRecord, OrderProvenance


class AlpacaRetrievalError(Exception):
    """
    Raised for any failure retrieving orders: authentication, rate
    limiting, network, or a malformed response. Text is passed through
    _sanitize_error_message() before ever reaching this exception, so it
    is safe to print to stderr or include in a report.
    """


class ReadOnlyOrdersClient:
    """
    The ENTIRE read surface this module uses or can use. Wraps a real
    (or, in tests, fake) TradingClient and exposes exactly one method.
    """
    def __init__(self, trading_client):
        self._client = trading_client

    def get_orders(self, filter):
        return self._client.get_orders(filter=filter)


def _sanitize_error_message(exc: Exception) -> str:
    """Strips anything matching the current session's own credential
    values out of an exception's text before it's ever surfaced. A hard
    backstop, not a claim that Alpaca's error bodies normally leak them."""
    text = str(exc)
    api_key = os.environ.get("APCA_API_KEY_ID", "")
    api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if api_key and api_key in text:
        text = text.replace(api_key, "***REDACTED***")
    if api_secret and api_secret in text:
        text = text.replace(api_secret, "***REDACTED***")
    return text


def build_client(paper: bool = True) -> ReadOnlyOrdersClient:
    """
    Constructs the read-only client from APCA_API_KEY_ID/APCA_API_SECRET_KEY.
    Raises AlpacaRetrievalError (never lets a raw exception that might
    embed credential text propagate) if they aren't set.
    """
    api_key = os.environ.get("APCA_API_KEY_ID")
    api_secret = os.environ.get("APCA_API_SECRET_KEY")
    if not api_key or not api_secret:
        raise AlpacaRetrievalError(
            "APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set in this environment. "
            "Set them the same way the trading bots already do, then retry. "
            "This adapter never falls back to any other data source."
        )
    try:
        trading_client = TradingClient(api_key, api_secret, paper=paper)
    except Exception as e:
        raise AlpacaRetrievalError(
            f"Failed to construct the Alpaca trading client ({type(e).__name__}): "
            f"{_sanitize_error_message(e)}"
        ) from None
    return ReadOnlyOrdersClient(trading_client)


MAX_PAGE_SIZE = 500   # Alpaca's documented per-request maximum for get_orders
MAX_PAGES = 200        # hard cap: 200 x 500 = 100,000 orders. Exists so a
                        # pagination bug fails LOUDLY (complete=False) rather
                        # than looping forever or silently truncating.


def fetch_all_orders(
    client: ReadOnlyOrdersClient, after: datetime, until: datetime,
    symbols: Optional[list[str]] = None,
) -> tuple[list, bool]:
    """
    Retrieves EVERY order (QueryOrderStatus.ALL -- not just open ones) in
    [after, until], paginating completely. Alpaca's get_orders has no
    separate page-token; the standard idiom for this endpoint is to walk
    `until` backward to just before the earliest submitted_at seen in the
    previous (descending-sorted) page, and stop once a page comes back
    shorter than the page size. Deduplicates by order id in case of any
    boundary overlap between pages.

    Returns (orders, complete). complete is False ONLY if MAX_PAGES was
    exhausted without a natural stop -- callers MUST treat that as a real
    retrieval failure (see control_cli.py: never presents a partial
    result as complete), not silently truncate and proceed.
    """
    seen_ids = set()
    all_orders = []
    page_until = until
    complete = True

    for _page in range(MAX_PAGES):
        req = GetOrdersRequest(
            status=QueryOrderStatus.ALL, after=after, until=page_until,
            limit=MAX_PAGE_SIZE, direction=Sort.DESC, symbols=symbols,
        )
        try:
            batch = client.get_orders(filter=req)
        except APIError as e:
            raise AlpacaRetrievalError(
                f"Alpaca API error retrieving orders (status_code={getattr(e, 'status_code', None)}): "
                f"{_sanitize_error_message(e)}"
            ) from None
        except Exception as e:
            raise AlpacaRetrievalError(
                f"Failed to retrieve orders from Alpaca ({type(e).__name__}): {_sanitize_error_message(e)}"
            ) from None

        if not batch:
            break

        new_in_batch = 0
        earliest_in_batch = None
        for o in batch:
            if o.id in seen_ids:
                continue
            seen_ids.add(o.id)
            all_orders.append(o)
            new_in_batch += 1
            if earliest_in_batch is None or o.submitted_at < earliest_in_batch:
                earliest_in_batch = o.submitted_at

        if len(batch) < MAX_PAGE_SIZE:
            break  # short page: this was the last one
        if earliest_in_batch is None or new_in_batch == 0:
            break  # nothing new came in -- stop rather than loop on a pathological response
        page_until = earliest_in_batch
    else:
        complete = False

    return all_orders, complete


def _order_status_value(order) -> str:
    """OrderStatus is normally a real enum member (status is a required,
    non-Optional field on Order) -- but this is defensive against any
    SDK/response variation where it might already be a plain string, so
    an unrecognized future status value is preserved verbatim either way
    rather than raising."""
    status = order.status
    return (status.value if hasattr(status, "value") else str(status)).lower()


def _summarize_order(order) -> dict:
    """A small, credential-free summary of a raw Order for provenance --
    never the full pydantic object (which could in principle be logged
    or serialized somewhere downstream), and never anything from the
    request/response envelope that could carry auth headers."""
    return {
        "id": str(order.id), "client_order_id": order.client_order_id,
        "symbol": order.symbol, "side": order.side.value if order.side else None,
        "status": _order_status_value(order),
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
        "filled_at": order.filled_at.isoformat() if order.filled_at else None,
    }


def _order_to_broker_order(
    order, record_number: int, default_source_system: str, default_source_account: Optional[str],
) -> tuple[Optional[BrokerOrder], Optional[RejectedRecord]]:
    """
    Converts one real alpaca-py Order into BrokerOrder through its
    existing public constructor. Returns (None, rejection_dict) -- same
    "never invent a required field" rule as the file-based parsers --
    if qty or side is missing/unusable; both are required by BrokerOrder
    and this adapter does not guess either.
    """
    qty = _parse_qty(order.qty)
    side = order.side.value.lower() if order.side else None
    reasons = []
    if qty is None:
        reasons.append(f"qty is missing or unusable: {order.qty!r}")
    if side not in ("buy", "sell"):
        reasons.append(f"side is missing or unrecognized: {order.side!r}")
    if order.submitted_at is None:
        reasons.append("submitted_at is missing")
    if reasons:
        return None, RejectedRecord(record_number=record_number, reason="; ".join(reasons), raw=_summarize_order(order))

    bo = BrokerOrder(
        symbol=order.symbol, side=side, qty=qty, status=_order_status_value(order),
        submitted_at=order.submitted_at, filled_at=order.filled_at,
        broker_order_id=str(order.id), client_order_id=_clean_optional_str(order.client_order_id),
        source_system=default_source_system, source_account=default_source_account,
        immutable_source_record_id=None,
    )
    return bo, None


def fetch_and_normalize_orders(
    client: ReadOnlyOrdersClient, after: datetime, until: datetime,
    default_source_system: str = "ALPACA", default_source_account: Optional[str] = None,
    symbols: Optional[list[str]] = None,
) -> tuple[list[BrokerOrder], list[IngestionFinding], list, dict, dict, bool]:
    """
    The full pipeline: retrieve every real order in the window, convert
    each to BrokerOrder (tracking any dropped rows as rejections in the
    same shape the file-based parsers use), feed through
    normalize_ingested_orders() unchanged, and return retrieval metadata
    (never credentials) for the CLI to include in its report.

    Returns (clean_orders, ingestion_findings, rejections, provenance_by_id,
    retrieval_metadata, complete) -- deliberately the same shape
    alpaca_orders_parser.parse_and_normalize_orders() returns (plus the
    two extra API-specific fields), so control_cli.py's report-building
    code works unchanged for either order source.
    """
    raw_orders, complete = fetch_all_orders(client, after, until, symbols=symbols)

    broker_orders = []
    rejections = []
    provenance_list = []
    for i, order in enumerate(raw_orders, start=1):
        bo, rejection = _order_to_broker_order(order, i, default_source_system, default_source_account)
        if bo is None:
            rejections.append(rejection)
            continue
        broker_orders.append(bo)
        provenance_list.append(OrderProvenance(
            source_file="alpaca-api", source_format="alpaca_api", record_number=i, raw=_summarize_order(order),
        ))

    provenance_by_id = {id(o): p for o, p in zip(broker_orders, provenance_list)}
    clean_orders, ingestion_findings = normalize_ingested_orders(broker_orders)

    status_distribution: dict = {}
    for order in raw_orders:
        s = _order_status_value(order)
        status_distribution[s] = status_distribution.get(s, 0) + 1

    metadata = {
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "after": after.isoformat(), "until": until.isoformat(),
        "raw_orders_retrieved": len(raw_orders),
        "orders_dropped_missing_required_fields": len(rejections),
        "orders_after_normalization": len(clean_orders),
        "status_distribution": status_distribution,
        "pagination_complete": complete,
    }
    return clean_orders, ingestion_findings, rejections, provenance_by_id, metadata, complete
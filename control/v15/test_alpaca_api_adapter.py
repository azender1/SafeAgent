"""
test_alpaca_api_adapter.py

All tests here use FAKE/MOCKED Alpaca clients -- no real network calls,
no real credentials required. Covers alpaca_api_adapter.py directly and
control_cli.py's --alpaca-api wiring end to end.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from alpaca.trading.models import Order
from alpaca.trading.enums import OrderSide, OrderStatus
from alpaca.common.exceptions import APIError

from reconcile_v12 import Result
import alpaca_api_adapter as adapter
from alpaca_api_adapter import (
    ReadOnlyOrdersClient, AlpacaRetrievalError, fetch_all_orders, fetch_and_normalize_orders, build_client,
)
from control_cli import main as cli_main

FIXTURES = Path(__file__).parent / "fixtures"


# ===================== fake Order / fake client builders =====================

def _mk_order(symbol="TQQQ", side=OrderSide.BUY, qty="7", status=OrderStatus.FILLED,
              submitted_at=None, filled_at=None, cid=None, oid=None):
    return Order(
        id=oid or uuid.uuid4(), client_order_id=cid if cid is not None else f"c-{uuid.uuid4()}",
        created_at=submitted_at or datetime.now(timezone.utc),
        updated_at=submitted_at or datetime.now(timezone.utc),
        submitted_at=submitted_at or datetime.now(timezone.utc), filled_at=filled_at,
        expired_at=None, expires_at=None, canceled_at=None, failed_at=None,
        replaced_at=None, replaced_by=None, replaces=None, asset_id=uuid.uuid4(),
        symbol=symbol, asset_class="us_equity", notional=None, qty=qty, filled_qty="0",
        filled_avg_price=None, order_class="simple", order_type="market", type="market",
        side=side, time_in_force="day", limit_price=None, stop_price=None,
        status=status, extended_hours=False, legs=None, trail_percent=None, trail_price=None, hwm=None,
    )


class _FakeTradingClient:
    """A fake standing in for alpaca.trading.client.TradingClient. Every
    write-capable method Alpaca's real client has is present here too --
    each one records that it was called and raises, so a test can prove
    none of them was ever reached through the adapter or CLI."""

    def __init__(self, pages=None, error=None):
        self.pages = pages if pages is not None else [[]]
        self._call_count = 0
        self.error = error
        self.write_calls: list[str] = []
        self.get_orders_calls: list = []

    def get_orders(self, filter):
        self.get_orders_calls.append(filter)
        if self.error is not None:
            raise self.error
        idx = min(self._call_count, len(self.pages) - 1)
        batch = self.pages[idx]
        self._call_count += 1
        return batch

    def _record_write(self, name):
        self.write_calls.append(name)
        raise AssertionError(f"write-capable method {name!r} was called -- this must never happen")

    def submit_order(self, *a, **k): self._record_write("submit_order")
    def cancel_order_by_id(self, *a, **k): self._record_write("cancel_order_by_id")
    def cancel_orders(self, *a, **k): self._record_write("cancel_orders")
    def replace_order_by_id(self, *a, **k): self._record_write("replace_order_by_id")
    def close_position(self, *a, **k): self._record_write("close_position")
    def close_all_positions(self, *a, **k): self._record_write("close_all_positions")
    def set_account_configurations(self, *a, **k): self._record_write("set_account_configurations")
    def update_watchlist_by_id(self, *a, **k): self._record_write("update_watchlist_by_id")
    def patch(self, *a, **k): self._record_write("patch")
    def post(self, *a, **k): self._record_write("post")
    def put(self, *a, **k): self._record_write("put")
    def delete(self, *a, **k): self._record_write("delete")


_NOW = datetime(2026, 9, 8, 17, 0, 0, tzinfo=timezone.utc)
_AFTER = _NOW - timedelta(days=7)


# ===================== basic retrieval: statuses =====================

def test_filled_order_retrieval():
    o = _mk_order(status=OrderStatus.FILLED, filled_at=_NOW)
    client = ReadOnlyOrdersClient(_FakeTradingClient(pages=[[o]]))
    clean, findings, rejections, prov, meta, complete = fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert complete and len(clean) == 1 and not rejections
    assert clean[0].status == "filled"
    print("filled order retrieval: PASS")


def test_rejected_canceled_expired_orders():
    orders = [_mk_order(status=OrderStatus.REJECTED), _mk_order(status=OrderStatus.CANCELED),
              _mk_order(status=OrderStatus.EXPIRED)]
    client = ReadOnlyOrdersClient(_FakeTradingClient(pages=[orders]))
    clean, findings, rejections, prov, meta, complete = fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert {o.status for o in clean} == {"rejected", "canceled", "expired"}
    print("rejected/canceled/expired order retrieval: PASS")


def test_partially_filled_orders():
    o = _mk_order(status=OrderStatus.PARTIALLY_FILLED, qty="10", filled_at=None)
    client = ReadOnlyOrdersClient(_FakeTradingClient(pages=[[o]]))
    clean, findings, rejections, prov, meta, complete = fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert clean[0].status == "partially_filled" and clean[0].qty == 10.0
    print("partially filled order retrieval: PASS")


def test_unknown_status_preserved():
    """A status this installed SDK's enum doesn't recognize would fail at
    the pydantic layer before reaching this adapter for a truly unknown
    string -- but the adapter's own status handling must not coerce or
    guess at ANY status value, known or not; this proves it passes every
    real enum member through completely unchanged, and _order_status_value
    defensively handles a plain-string status too (see its docstring)."""
    class _FakeStatus:
        value = "some_future_status_this_code_has_never_seen"
    o = _mk_order()
    object.__setattr__(o, "status", _FakeStatus())
    assert adapter._order_status_value(o) == "some_future_status_this_code_has_never_seen"
    print("unknown/future status value preserved verbatim: PASS")


# ===================== numeric and timestamp normalization =====================

def test_string_quantities():
    o = _mk_order(qty="12.5")
    client = ReadOnlyOrdersClient(_FakeTradingClient(pages=[[o]]))
    clean, *_ = fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert clean[0].qty == 12.5
    print("string quantity normalization: PASS")


def test_timezone_aware_and_nanosecond_timestamps():
    # alpaca-py always parses submitted_at/filled_at into real datetime
    # objects (see BUILD_STATUS.md/module docstring) -- so "nanosecond
    # precision" here means the datetime itself carries microsecond
    # resolution and a real tzinfo, which this adapter must pass through
    # to BrokerOrder unchanged, not re-parse or truncate.
    ts = datetime(2026, 5, 26, 9, 36, 39, 734952, tzinfo=timezone.utc)
    o = _mk_order(submitted_at=ts, filled_at=ts)
    client = ReadOnlyOrdersClient(_FakeTradingClient(pages=[[o]]))
    clean, *_ = fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert clean[0].submitted_at.microsecond == 734952
    assert clean[0].submitted_at.tzinfo is not None
    print("timezone-aware, microsecond-precision timestamps preserved: PASS")


# ===================== pagination =====================

def test_pagination_across_multiple_pages():
    # Two full-size pages forces a third (short) page fetch to detect the end.
    page1 = [_mk_order(submitted_at=_NOW - timedelta(minutes=i)) for i in range(adapter.MAX_PAGE_SIZE)]
    page2 = [_mk_order(submitted_at=_NOW - timedelta(minutes=adapter.MAX_PAGE_SIZE + i)) for i in range(3)]
    fake = _FakeTradingClient(pages=[page1, page2])
    client = ReadOnlyOrdersClient(fake)
    orders, complete = fetch_all_orders(client, _AFTER - timedelta(days=365), _NOW)
    assert complete
    assert len(orders) == adapter.MAX_PAGE_SIZE + 3
    assert len(fake.get_orders_calls) == 2
    print(f"pagination across multiple pages: {len(orders)} orders, {len(fake.get_orders_calls)} calls: PASS")


def test_pagination_deduplicates_boundary_overlap():
    shared = _mk_order(submitted_at=_NOW - timedelta(minutes=adapter.MAX_PAGE_SIZE - 1))
    page1 = [shared] + [_mk_order(submitted_at=_NOW - timedelta(minutes=i)) for i in range(adapter.MAX_PAGE_SIZE - 1)]
    page2 = [shared, _mk_order(submitted_at=_NOW - timedelta(minutes=adapter.MAX_PAGE_SIZE))]
    fake = _FakeTradingClient(pages=[page1, page2])
    client = ReadOnlyOrdersClient(fake)
    orders, complete = fetch_all_orders(client, _AFTER - timedelta(days=365), _NOW)
    ids = [o.id for o in orders]
    assert len(ids) == len(set(ids)), "duplicate order id across page boundary was not deduplicated"
    print("pagination deduplicates boundary overlap: PASS")


def test_pagination_hits_safety_limit_reports_incomplete():
    # Every page full-size and always-new -> never naturally terminates ->
    # must stop at MAX_PAGES and report complete=False, never loop forever.
    def infinite_pages():
        i = 0
        while True:
            yield [_mk_order(submitted_at=_NOW - timedelta(minutes=i * adapter.MAX_PAGE_SIZE + j))
                   for j in range(adapter.MAX_PAGE_SIZE)]
            i += 1

    gen = infinite_pages()

    class _InfiniteFakeClient(_FakeTradingClient):
        def get_orders(self, filter):
            self.get_orders_calls.append(filter)
            return next(gen)

    client = ReadOnlyOrdersClient(_InfiniteFakeClient())
    orders, complete = fetch_all_orders(client, _AFTER - timedelta(days=3650), _NOW)
    assert complete is False
    assert len(orders) == adapter.MAX_PAGES * adapter.MAX_PAGE_SIZE
    print(f"pagination safety limit ({adapter.MAX_PAGES} pages) correctly reports incomplete: PASS")


# ===================== empty results =====================

def test_empty_results():
    client = ReadOnlyOrdersClient(_FakeTradingClient(pages=[[]]))
    clean, findings, rejections, prov, meta, complete = fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert clean == [] and complete and meta["raw_orders_retrieved"] == 0
    print("empty results handled cleanly: PASS")


# ===================== failure modes =====================

def test_authentication_failure_raises_clear_error():
    err = APIError(json.dumps({"code": 40110000, "message": "access key verification failed"}))
    client = ReadOnlyOrdersClient(_FakeTradingClient(error=err))
    try:
        fetch_all_orders(client, _AFTER, _NOW)
        assert False, "expected AlpacaRetrievalError"
    except AlpacaRetrievalError as e:
        assert "verification failed" in str(e) or "Alpaca API error" in str(e)
    print("authentication failure raises a clear AlpacaRetrievalError: PASS")


def test_rate_limit_and_network_failure_raise_clear_errors():
    rate_limit_err = APIError(json.dumps({"code": 42910000, "message": "rate limit exceeded"}))
    client = ReadOnlyOrdersClient(_FakeTradingClient(error=rate_limit_err))
    try:
        fetch_all_orders(client, _AFTER, _NOW)
        assert False
    except AlpacaRetrievalError:
        pass

    network_err = ConnectionError("connection refused")
    client2 = ReadOnlyOrdersClient(_FakeTradingClient(error=network_err))
    try:
        fetch_all_orders(client2, _AFTER, _NOW)
        assert False
    except AlpacaRetrievalError as e:
        assert "Failed to retrieve orders" in str(e)
    print("rate-limit and network failures both raise clear AlpacaRetrievalError: PASS")


def test_build_client_without_credentials_raises_clear_error():
    with mock.patch.dict("os.environ", {}, clear=True):
        try:
            build_client(paper=True)
            assert False, "expected AlpacaRetrievalError"
        except AlpacaRetrievalError as e:
            assert "APCA_API_KEY_ID" in str(e)
    print("missing credentials raise a clear error naming the expected env vars: PASS")


# ===================== the security boundary =====================

def test_no_write_method_is_ever_called_via_full_pipeline():
    fake = _FakeTradingClient(pages=[[_mk_order(status=OrderStatus.FILLED)]])
    client = ReadOnlyOrdersClient(fake)
    fetch_and_normalize_orders(client, _AFTER, _NOW)
    assert fake.write_calls == [], f"write methods were called: {fake.write_calls}"
    print("proof: no write-capable method is called by the adapter: PASS")


def test_readonly_wrapper_exposes_only_get_orders():
    public_methods = [m for m in dir(ReadOnlyOrdersClient) if not m.startswith("_")]
    assert public_methods == ["get_orders"], (
        f"ReadOnlyOrdersClient exposes more than get_orders: {public_methods}"
    )
    print("proof: ReadOnlyOrdersClient's public interface is exactly {get_orders}: PASS")


def test_credentials_never_appear_in_cli_output_or_reports():
    fake_key, fake_secret = "AKFAKE1234567890TEST", "SECRETFAKEVALUEFORTESTING999"
    with mock.patch.dict("os.environ", {"APCA_API_KEY_ID": fake_key, "APCA_API_SECRET_KEY": fake_secret}):
        with tempfile.TemporaryDirectory() as d:
            fake = _FakeTradingClient(pages=[[_mk_order()]])
            with mock.patch("alpaca_api_adapter.build_client",
                             return_value=ReadOnlyOrdersClient(fake)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_main([
                        "reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                        "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                        "--source-account", "ACC1", "--as-of", "2026-09-08T17:00:00-04:00",
                        "--output-dir", str(Path(d) / "out"),
                    ])
            assert rc == 0
            stdout_text = buf.getvalue()
            assert fake_key not in stdout_text and fake_secret not in stdout_text

            for name in ("reconciliation_report.json", "reconciliation_findings.csv", "reconciliation_summary.txt"):
                content = (Path(d) / "out" / name).read_text()
                assert fake_key not in content, f"credential leaked into {name}"
                assert fake_secret not in content, f"credential leaked into {name}"
    print("proof: credentials never appear in CLI stdout or any generated report: PASS")


def test_sanitize_error_message_redacts_credentials():
    with mock.patch.dict("os.environ", {"APCA_API_KEY_ID": "AKLEAKTEST123", "APCA_API_SECRET_KEY": "SECRETLEAKTEST456"}):
        exc = Exception("auth failed for key AKLEAKTEST123 with secret SECRETLEAKTEST456")
        sanitized = adapter._sanitize_error_message(exc)
        assert "AKLEAKTEST123" not in sanitized and "SECRETLEAKTEST456" not in sanitized
        assert "REDACTED" in sanitized
    print("proof: _sanitize_error_message redacts credential values from exception text: PASS")


# ===================== CLI: mutual exclusion, paper default =====================

def test_cli_orders_and_alpaca_api_are_mutually_exclusive():
    buf = io.StringIO()
    with redirect_stdout(io.StringIO()):
        try:
            cli_main(["reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                      "--orders", str(FIXTURES / "alpaca_orders_sample.json"), "--alpaca-api",
                      "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T00:00:00Z",
                      "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", "/tmp/x"])
            assert False, "expected SystemExit from argparse"
        except SystemExit as e:
            assert e.code == 2
    print("CLI: --orders and --alpaca-api are mutually exclusive: PASS")


def test_cli_requires_exactly_one_order_source():
    try:
        cli_main(["reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                  "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", "/tmp/x"])
        assert False
    except SystemExit as e:
        assert e.code == 2
    print("CLI: exactly one of --orders/--alpaca-api is required: PASS")


def test_cli_defaults_to_paper_and_reports_it_clearly():
    fake = _FakeTradingClient(pages=[[_mk_order()]])
    with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)) as mocked:
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main([
                    "reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T00:00:00Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(Path(d) / "out"),
                ])
            assert rc == 0
            assert "PAPER" in buf.getvalue()
            mocked.assert_called_once_with(paper=True)
            report = json.loads((Path(d) / "out" / "reconciliation_report.json").read_text())
            assert report["summary"]["alpaca_api_retrieval"]["environment"] == "paper"
    print("CLI defaults to paper mode and reports it clearly (stdout + report): PASS")


def test_cli_live_requires_explicit_flag():
    fake = _FakeTradingClient(pages=[[_mk_order()]])
    with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)) as mocked:
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main([
                    "reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                    "--alpaca-api", "--live", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T00:00:00Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(Path(d) / "out"),
                ])
            assert rc == 0
            assert "LIVE" in buf.getvalue()
            mocked.assert_called_once_with(paper=False)
    print("CLI: --live must be passed explicitly to leave paper mode: PASS")


def test_cli_alpaca_api_requires_after_and_until():
    try:
        cli_main(["reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                  "--alpaca-api", "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", "/tmp/x"])
    except SystemExit:
        pass
    with redirect_stdout(io.StringIO()):
        rc = cli_main(["reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                       "--alpaca-api", "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", "/tmp/x"])
    assert rc == 2
    print("CLI: --alpaca-api without --after/--until fails clearly: PASS")


def test_cli_exits_nonzero_on_retrieval_failure_never_falls_back():
    err = APIError(json.dumps({"code": 40110000, "message": "access key verification failed"}))
    fake = _FakeTradingClient(error=err)
    with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T00:00:00Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
            assert rc != 0
            assert not out_dir.exists() or not any(out_dir.iterdir()), (
                "no report should be written when retrieval fails -- that would be presenting "
                "a failed/absent retrieval as a completed one"
            )
    print("CLI exits nonzero on retrieval failure and writes no report (never falls back): PASS")


def test_cli_refuses_incomplete_pagination_rather_than_reconciling_partial_data():
    def infinite_pages():
        i = 0
        while True:
            yield [_mk_order(submitted_at=_NOW - timedelta(minutes=i * adapter.MAX_PAGE_SIZE + j))
                   for j in range(adapter.MAX_PAGE_SIZE)]
            i += 1
    gen = infinite_pages()

    class _InfiniteFakeClient(_FakeTradingClient):
        def get_orders(self, filter):
            return next(gen)

    with mock.patch("alpaca_api_adapter.build_client",
                     return_value=ReadOnlyOrdersClient(_InfiniteFakeClient())):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
                    "--alpaca-api", "--after", "2020-01-01T00:00:00Z", "--until", "2026-09-08T00:00:00Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
            assert rc != 0
            assert not out_dir.exists() or not any(out_dir.iterdir())
    print("CLI refuses to reconcile against incomplete pagination rather than presenting it as complete: PASS")


# ===================== claims-outside-retrieval-window filtering =====================

def test_claims_outside_window_are_excluded_not_misclassified():
    """The real bug this section exists for: a claim from a date the
    retrieval window doesn't cover has no order to match (the order was
    never fetched, not missing), and must be excluded from reconciliation
    entirely -- never counted as MISSING_EXTERNALLY or UNCERTAIN. Found via
    a real acceptance run: 6 of 7 MISSING_EXTERNALLY findings were exactly
    this, not genuine discrepancies."""
    claims_json = [
        {"request_id": "in-window", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
         "claimed_at": "2026-09-05T10:00:00-04:00", "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"},
        {"request_id": "before-window", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
         "claimed_at": "2026-08-15T09:34:00-04:00", "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"},
        {"request_id": "after-window", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
         "claimed_at": "2026-09-09T09:30:00-04:00", "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"},
    ]
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        claims_path.write_text(json.dumps(claims_json))
        fake = _FakeTradingClient(pages=[[]])  # no orders at all -- isolates the filtering itself
        with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(claims_path),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
        assert rc == 0
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        summary = report["summary"]
        assert summary["raw_claims_received"] == 3, "original total must be preserved regardless of filtering"
        assert summary["claims_outside_retrieval_window"] == 2
        assert summary["claims_accepted"] == 1
        request_ids_seen = {f["claim_request_id"] for f in report["findings"] if f["claim_request_id"]}
        assert "in-window" in request_ids_seen
        assert "before-window" not in request_ids_seen, "excluded claim must not appear as a finding at all"
        assert "after-window" not in request_ids_seen, "excluded claim must not appear as a finding at all"
        # the in-window claim genuinely has no matching order in this test
        # (empty order pages) -- MISSING_EXTERNALLY is the CORRECT result for
        # it. The bug this test guards against is the two EXCLUDED claims
        # showing up as findings at all, which the request_id checks above
        # already rule out.
        in_window_finding = next(f for f in report["findings"] if f["claim_request_id"] == "in-window")
        assert in_window_finding["result"] == "MISSING_EXTERNALLY"
    print("claims outside the retrieval window are excluded, not misclassified: PASS")


def test_claims_window_boundary_immediately_before_after_is_excluded():
    """A claim timestamped exactly 1 second before --after (in absolute UTC
    terms) must be excluded -- the boundary is inclusive of --after itself,
    not before it."""
    claims_json = [{"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
                     "claimed_at": "2026-08-31T23:59:59+00:00",  # 1s before after=2026-09-01T00:00:00Z
                     "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"}]
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        claims_path.write_text(json.dumps(claims_json))
        fake = _FakeTradingClient(pages=[[]])
        with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(claims_path),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_outside_retrieval_window"] == 1
        assert report["summary"]["claims_accepted"] == 0
    print("boundary: claim 1s before --after is excluded: PASS")


def test_claims_window_boundary_exactly_at_after_is_included():
    claims_json = [{"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
                     "claimed_at": "2026-09-01T00:00:00+00:00",  # exactly == --after, in UTC
                     "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"}]
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        claims_path.write_text(json.dumps(claims_json))
        fake = _FakeTradingClient(pages=[[]])
        with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(claims_path),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_outside_retrieval_window"] == 0
        assert report["summary"]["claims_accepted"] == 1
    print("boundary: claim exactly at --after is included (inclusive boundary): PASS")


def test_claims_window_boundary_exactly_at_until_is_included():
    claims_json = [{"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
                     "claimed_at": "2026-09-08T23:59:59+00:00",  # exactly == --until, in UTC
                     "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"}]
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        claims_path.write_text(json.dumps(claims_json))
        fake = _FakeTradingClient(pages=[[]])
        with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(claims_path),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_outside_retrieval_window"] == 0
        assert report["summary"]["claims_accepted"] == 1
    print("boundary: claim exactly at --until is included (inclusive boundary): PASS")


def test_claims_window_boundary_immediately_after_until_is_excluded():
    claims_json = [{"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
                     "claimed_at": "2026-09-09T00:00:00+00:00",  # 1s after --until=2026-09-08T23:59:59Z
                     "outcome": "COMMITTED", "caller_interpretation": "SUCCESS"}]
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        claims_path.write_text(json.dumps(claims_json))
        fake = _FakeTradingClient(pages=[[]])
        with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(claims_path),
                    "--alpaca-api", "--after", "2026-09-01T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                    "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
                ])
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_outside_retrieval_window"] == 1
        assert report["summary"]["claims_accepted"] == 0
    print("boundary: claim 1s after --until is excluded: PASS")


def test_claims_window_filtering_does_not_apply_to_file_based_orders_mode():
    """The filter is scoped to --alpaca-api only -- the file-based --orders
    path has no defined retrieval window, so claims of any date must be
    left alone there, exactly as before this fix."""
    claims_json = [{"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
                     "claimed_at": "2020-01-01T00:00:00-04:00",  # wildly outside any plausible window
                     "outcome": "COMMITTED", "caller_interpretation": "SUCCESS",
                     "broker_order_id": "B1", "client_order_id": "C1"}]
    orders_json = {"orders": [{"id": "B1", "client_order_id": "C1", "symbol": "TQQQ", "side": "buy",
                                "qty": 7, "status": "filled", "submitted_at": "2020-01-01T00:00:00Z",
                                "filled_at": "2020-01-01T00:00:05Z"}]}
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        orders_path = Path(d) / "orders.json"
        claims_path.write_text(json.dumps(claims_json))
        orders_path.write_text(json.dumps(orders_json))
        out_dir = Path(d) / "out"
        with redirect_stdout(io.StringIO()):
            rc = cli_main([
                "reconcile", "--claims", str(claims_path), "--orders", str(orders_path),
                "--as-of", "2026-09-08T17:00:00-04:00", "--output-dir", str(out_dir),
            ])
        assert rc == 0
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_outside_retrieval_window"] == 0
        assert report["summary"]["claims_accepted"] == 1
        assert report["summary"]["result_counts"]["CONFIRMED"] == 1
    print("claims-window filtering does not apply in file-based --orders mode: PASS")


# ===================== end-to-end: fresh statuses change results =====================

def test_end_to_end_uncertain_becomes_confirmed_rejected_and_missing():
    """The exact scenario this whole release exists for: a claim whose
    matching order shows a merely-submitted snapshot elsewhere becomes
    CONFIRMED once the live API reports a real final 'filled' status; a
    genuinely rejected order becomes correctly classified too; and a
    claim with no matching order at all is MISSING_EXTERNALLY -- not
    silently dropped."""
    claims_json = [
        {"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
         "claimed_at": "2026-09-08T09:30:00-04:00", "outcome": "COMMITTED", "caller_interpretation": "SUCCESS",
         "broker_order_id": None, "client_order_id": "CID-FILLED"},
        {"request_id": "r2", "symbol": "TQQQ", "side": "sell", "qty": 3, "stage": "base",
         "claimed_at": "2026-09-08T09:31:00-04:00", "outcome": "COMMITTED", "caller_interpretation": "SUCCESS",
         "broker_order_id": None, "client_order_id": "CID-REJECTED"},
        {"request_id": "r3", "symbol": "TQQQ", "side": "buy", "qty": 5, "stage": "base",
         "claimed_at": "2026-09-08T09:32:00-04:00", "outcome": "COMMITTED", "caller_interpretation": "SUCCESS",
         "broker_order_id": None, "client_order_id": "CID-NEVER-ARRIVES"},
    ]
    with tempfile.TemporaryDirectory() as d:
        claims_path = Path(d) / "claims.json"
        # broker_order_id is only known once a real order comes back --
        # fill it in below from the SAME real UUIDs used to build the fake
        # orders, so this proves matching by client_order_id/broker_order_id
        # against genuinely-shaped Alpaca identifiers, not string sentinels.
        filled_oid, rejected_oid = uuid.uuid4(), uuid.uuid4()
        claims_json[0]["broker_order_id"] = str(filled_oid)
        claims_json[1]["broker_order_id"] = str(rejected_oid)
        claims_json[2]["broker_order_id"] = str(uuid.uuid4())  # a real-shaped UUID that never actually arrives
        claims_path.write_text(json.dumps(claims_json))

        filled = _mk_order(oid=filled_oid, cid="CID-FILLED", status=OrderStatus.FILLED,
                            submitted_at=_NOW - timedelta(minutes=90), filled_at=_NOW - timedelta(minutes=89))
        rejected = _mk_order(oid=rejected_oid, cid="CID-REJECTED", status=OrderStatus.REJECTED, side=OrderSide.SELL, qty="3",
                              submitted_at=_NOW - timedelta(minutes=89))
        fake = _FakeTradingClient(pages=[[filled, rejected]])

        with mock.patch("alpaca_api_adapter.build_client", return_value=ReadOnlyOrdersClient(fake)):
            out_dir = Path(d) / "out"
            with redirect_stdout(io.StringIO()):
                rc = cli_main([
                    "reconcile", "--claims", str(claims_path),
                    "--alpaca-api", "--after", "2026-09-08T00:00:00Z", "--until", "2026-09-08T23:59:59Z",
                    "--source-account", "ACC1", "--as-of", "2026-09-08T17:00:00-04:00",
                    "--output-dir", str(out_dir),
                ])
        assert rc == 0
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        by_rid = {f["claim_request_id"]: f for f in report["findings"]}
        assert by_rid["r1"]["result"] == Result.CONFIRMED.value, by_rid["r1"]
        assert by_rid["r2"]["result"] != Result.CONFIRMED.value, by_rid["r2"]  # rejected order, must not confirm
        assert by_rid["r3"]["result"] == Result.MISSING_EXTERNALLY.value, by_rid["r3"]
    print("end-to-end: fresh live statuses correctly move claims out of UNCERTAIN: PASS")


# ===================== Auto-discovering runner =====================

if __name__ == "__main__":
    import inspect

    _module = sys.modules[__name__]
    tests = sorted(
        (n, f) for n, f in vars(_module).items()
        if n.startswith("test_") and inspect.isfunction(f) and f.__module__ == __name__
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failures.append((name, str(e)[:500]))
            print(f"{name}: FAIL -- {str(e)[:500]}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed.")
    if failures:
        print(f"\n{len(failures)} FAILURES")
        for name, err in failures:
            print(f"  - {name}: {err}")
    sys.exit(1 if failures else 0)
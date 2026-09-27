"""
test_safeagent_db_adapter.py

Tests for safeagent_db_adapter.py against every real request_id shape
confirmed by inspecting the actual production safeagent_orders.db during
development (see BUILD_STATUS.md). This distributable package ships only
fixtures/safeagent_orders_synthetic_sample.db -- a SYNTHETIC dataset
(fabricated identifiers, fictional dates) generated to match those same
real shapes structurally, not real production data. Plus the CLI's .db
wiring.
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from reconcile_v12 import Interpretation
from safeagent_db_adapter import decode_request_id, _parse_result_column, parse_safeagent_db
from control_cli import main as cli_main

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_LOG = FIXTURES / "synthetic_bot_sample.log"


def _make_db(rows: list[tuple], path: str) -> None:
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE orders (
            request_id TEXT PRIMARY KEY, result TEXT,
            status TEXT DEFAULT 'PENDING', created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.executemany("INSERT INTO orders (request_id, result, status, created_at) VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


# ===================== decode_request_id: all real shapes =====================

def test_decode_old_plain_shape():
    d = decode_request_id("order:TQQQ:buy:6:2026-05-19T12:31:00-04:00")
    assert d["strategy"] is None and d["symbol"] == "TQQQ" and d["side"] == "buy"
    assert d["qty"] == 6.0 and d["stage"] is None
    assert d["bar_ts"] is not None
    print("decode: old plain shape (no strategy, no stage): PASS")


def test_decode_old_with_suffix_shape():
    d = decode_request_id("order:TQQQ:sell:18:2026-05-19T13:25:00-04:00:TRAIL_EXIT_BULL")
    assert d["symbol"] == "TQQQ" and d["side"] == "sell" and d["qty"] == 18.0
    assert d["stage"] == "TRAIL_EXIT_BULL"
    print("decode: old shape with free-form exit-reason suffix: PASS")


def test_decode_messy_real_suffix_with_spaces_and_equals():
    """A real suffix observed in production: spaces and '=' but the
    decoder must not choke on it or mis-split the timestamp."""
    rid = ("order:TQQQ:sell:6:2026-05-19T13:46:00-04:00:V20_FAST_FLIP pos=BULL opp=BEAR "
           "held=15.9 pnl=-0.0059 score=-4 bull=2 bear=6_BULL_TO_BEAR")
    d = decode_request_id(rid)
    assert d["symbol"] == "TQQQ" and d["qty"] == 6.0
    assert d["stage"].startswith("V20_FAST_FLIP")
    print("decode: messy real suffix with spaces and '=': PASS")


def test_decode_new_no_stage_shape():
    d = decode_request_id("order:SAMPLE_STRATEGY:SQQQ:buy:12:2026-09-01T13:10:00-04:00")
    assert d["strategy"] == "SAMPLE_STRATEGY" and d["symbol"] == "SQQQ"
    assert d["stage"] is None
    print("decode: current strategy-keyed shape, no stage: PASS")


def test_decode_new_with_stage_shape():
    d = decode_request_id("order:SAMPLE_STRATEGY:SQQQ:buy:12:add1:2026-09-08T09:35:00-04:00")
    assert d["strategy"] == "SAMPLE_STRATEGY" and d["stage"] == "add1"
    print("decode: current strategy-keyed shape, with stage: PASS")


def test_decode_microsecond_timestamp():
    d = decode_request_id("order:SQQQ:sell:8:2026-05-26T09:42:00.734952-04:00:LOSS_STOP_BEAR")
    assert d["bar_ts"] is not None and d["bar_ts"].microsecond == 734952
    print("decode: microsecond-precision real timestamp: PASS")


def test_decode_unrecognized_format_returns_none():
    assert decode_request_id("something:completely:different") is None
    assert decode_request_id("order:no-timestamp-anywhere-here") is None
    print("decode: unrecognized format returns None (caller must reject): PASS")


# ===================== result column parsing =====================

def test_parse_result_column_real_double_encoding():
    """The real column format: json.dumps(str(dict)) -- a JSON string
    containing a Python repr, not a JSON object directly."""
    inner_dict_repr = str({"id": "abc-123", "client_order_id": "xyz-789", "status": "pending_new"})
    raw = json.dumps(inner_dict_repr)
    fields = _parse_result_column(raw)
    assert fields["broker_order_id"] == "abc-123"
    assert fields["client_order_id"] == "xyz-789"
    print("parse_result_column: real double-JSON-string-wrapped Python repr: PASS")


def test_parse_result_column_null_is_empty_not_error():
    assert _parse_result_column(None) == {}
    assert _parse_result_column("") == {}
    print("parse_result_column: NULL/empty result (pending claim) -> {}, no error: PASS")


def test_parse_result_column_garbage_is_empty_not_error():
    assert _parse_result_column("not json at all {{{") == {}
    print("parse_result_column: unparseable garbage -> {}, never raises: PASS")


# ===================== exit: family explicit rejection =====================

def test_exit_prefixed_rows_explicitly_rejected():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "s.db")
        _make_db([("exit:SQQQ:8:2026-05-26T09:36:39.734952-04:00", None, "PENDING", "2026-05-26 13:43:30")], path)
        claims, rejections, prov = parse_safeagent_db(path)
        assert not claims and len(rejections) == 1
        assert "no side field" in rejections[0].reason
    print("exit: prefixed rows explicitly rejected (no side field, never guessed): PASS")


# ===================== caller_interpretation defaults to UNKNOWN, never rejects =====================

def test_missing_caller_interpretation_defaults_unknown_never_rejects():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "s.db")
        _make_db([("order:TQQQ:buy:6:2026-05-19T12:31:00-04:00", None, "PENDING", "2026-05-19 16:32:11")], path)
        claims, rejections, prov = parse_safeagent_db(path)
        assert len(claims) == 1 and not rejections
        assert claims[0].caller_interpretation == Interpretation.UNKNOWN
        assert prov[0].interpretation_source == "default_unknown"
    print("missing caller_interpretation -> UNKNOWN, claim accepted, never rejected: PASS")


# ===================== end-to-end against the real sanitized sample =====================

def test_parse_synthetic_sample_db():
    """fixtures/safeagent_orders_synthetic_sample.db is a SYNTHETIC dataset
    (fabricated identifiers, fictional dates) generated to match every
    real request_id shape confirmed against the actual production
    database during development -- see BUILD_STATUS.md. No real
    production data ships in this distributable package; see
    BUILD_STATUS.md for where the real-scale verification results are
    summarized instead."""
    claims, rejections, prov = parse_safeagent_db(
        str(FIXTURES / "safeagent_orders_synthetic_sample.db"), source_account="ACC1",
    )
    assert len(claims) == 39, f"expected 39 decodable claims, got {len(claims)}"
    assert len(rejections) == 6, f"expected 6 rejected exit:-family rows, got {len(rejections)}"
    assert all(c.source_account == "ACC1" for c in claims)
    assert all(c.caller_interpretation == Interpretation.UNKNOWN for c in claims)
    # every shape family should be represented
    strategies = {c.source_system for c in claims}
    assert None in strategies, "expected at least one older, non-strategy-keyed row"
    assert "SYNTH_STRAT_A" in strategies or "SYNTH_STRAT_B" in strategies, \
        "expected at least one current strategy-keyed row"
    print("parse synthetic sample DB (all real request_id shapes, no real data): PASS")


def test_bot_log_join_against_real_log_produces_real_correlations():
    """Two of the synthetic sample's rows were deliberately generated to
    have a genuine correlation in fixtures/synthetic_bot_sample.log --
    fabricated content, but a real, independently verifiable match (same
    side/symbol/qty/bar_ts) between the two files, exercising the SAME
    join mechanism that was proven against real data during development
    (see BUILD_STATUS.md). No real production data is used here."""
    if not SAMPLE_LOG.is_file():
        print("bot-log join correlation test: SKIPPED (sample log file not present)")
        return
    claims, rejections, prov = parse_safeagent_db(
        str(FIXTURES / "safeagent_orders_synthetic_sample.db"), source_account="ACC1", bot_log_path=str(SAMPLE_LOG),
    )
    by_rid = {c.request_id: (c, p) for c, p in zip(claims, prov)}

    skip_claim, skip_prov = by_rid["order:DDM:buy:6:2026-03-05T10:10:00-04:00"]
    assert skip_claim.caller_interpretation == Interpretation.SKIPPED
    assert skip_prov.interpretation_source == "log_observed"

    enter_claim, enter_prov = by_rid["order:DDM:sell:5:2026-03-05T10:20:00-04:00"]
    assert enter_claim.caller_interpretation == Interpretation.SUCCESS
    assert enter_prov.interpretation_source == "log_inferred"

    # some other row with no log coverage at all in this file must stay UNKNOWN
    other_rids = [rid for rid in by_rid if rid not in
                  ("order:DDM:buy:6:2026-03-05T10:10:00-04:00", "order:DDM:sell:5:2026-03-05T10:20:00-04:00")]
    assert other_rids, "expected at least one uncorrelated row in the sample"
    unrelated_claim, unrelated_prov = by_rid[other_rids[0]]
    assert unrelated_claim.caller_interpretation == Interpretation.UNKNOWN
    assert unrelated_prov.interpretation_source == "default_unknown"
    print("bot-log join against the sample log produces real, verified correlations: PASS")


# ===================== CLI wiring: .db input, --source-account, --source-system =====================

def test_cli_accepts_db_input_with_source_flags():
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "out"
        rc = cli_main([
            "reconcile",
            "--claims", str(FIXTURES / "safeagent_orders_synthetic_sample.db"),
            "--orders", str(FIXTURES / "alpaca_orders_synthetic_sample.json"),
            "--as-of", "2026-09-08T17:00:00-04:00",
            "--source-account", "ACC1",
            "--source-system", "SAMPLE_STRATEGY",
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_accepted"] == 39
        assert report["summary"]["claims_rejected"] == 6
        req_ids_seen = {f["claim_request_id"] for f in report["findings"] if f["claim_request_id"]}
        assert "order:DDM:buy:6:2026-03-05T10:10:00-04:00" in req_ids_seen
    print("CLI accepts .db input with --source-account/--source-system: PASS")


def test_cli_source_system_backfill_does_not_override_decoded_value():
    claims, rejections, prov = parse_safeagent_db(str(FIXTURES / "safeagent_orders_synthetic_sample.db"))
    from reconcile_v12 import InternalClaim
    backfilled = [
        c if c.source_system else InternalClaim(
            request_id=c.request_id, symbol=c.symbol, side=c.side, qty=c.qty, stage=c.stage,
            claimed_at=c.claimed_at, outcome=c.outcome, caller_interpretation=c.caller_interpretation,
            broker_order_id=c.broker_order_id, client_order_id=c.client_order_id,
            failure_type=c.failure_type, source_system="DEFAULT_SYS", source_account=c.source_account,
        )
        for c in claims
    ]
    by_rid = {c.request_id: c for c in backfilled}
    # this row (old_plain shape) has NO strategy embedded -> should be backfilled
    assert by_rid["order:DDM:buy:6:2026-03-05T10:10:00-04:00"].source_system == "DEFAULT_SYS"
    # this row (current strategy-keyed shape) already has one -> must NOT be overwritten
    strategy_keyed = next(c for c in backfilled if c.source_system == "SYNTH_STRAT_A")
    assert strategy_keyed.source_system == "SYNTH_STRAT_A"
    print("source_system backfill only fills gaps, never overwrites a decoded value: PASS")


def test_cli_completes_quickly_on_the_synthetic_sample():
    """Documents that this dataset completes quickly end-to-end thanks to
    the v14 scalability correction (see BUILD_STATUS.md). The real
    643-row production database was verified at full scale during
    development (also documented in BUILD_STATUS.md, ~0.5s end to end)
    but does not ship in this distributable package -- see the privacy
    section there."""
    import time
    with tempfile.TemporaryDirectory() as d:
        t0 = time.time()
        rc = cli_main([
            "reconcile",
            "--claims", str(FIXTURES / "safeagent_orders_synthetic_sample.db"),
            "--orders", str(FIXTURES / "alpaca_orders_synthetic_sample.json"),
            "--as-of", "2026-09-08T17:00:00-04:00",
            "--source-account", "ACC1",
            "--output-dir", str(Path(d) / "out"),
        ])
        elapsed = time.time() - t0
        assert rc == 0
        assert elapsed < 10, f"took {elapsed:.1f}s -- see BUILD_STATUS.md's scalability correction"
    print(f"CLI completes quickly on the synthetic sample ({elapsed:.2f}s < 10s): PASS")


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
            failures.append((name, str(e)[:400]))
            print(f"{name}: FAIL -- {str(e)[:400]}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed.")
    if failures:
        print(f"\n{len(failures)} FAILURES")
        for name, err in failures:
            print(f"  - {name}: {err}")
    sys.exit(1 if failures else 0)
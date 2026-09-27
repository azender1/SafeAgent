"""
test_parsers_and_cli.py

Deterministic tests for safeagent_claims_parser.py, alpaca_orders_parser.py,
and control_cli.py. Does not touch or re-test the v12.1 core itself --
test_v12_full_suite.py still owns that, unchanged, run separately.

Run directly (auto-discovering, like test_v12_full_suite.py) or via pytest:
    python3 test_parsers_and_cli.py
    python3 -m pytest test_parsers_and_cli.py -q
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from reconcile_v12 import Result, OrderDisposition, ReasonCode
from safeagent_claims_parser import parse_claims
from alpaca_orders_parser import parse_orders, parse_and_normalize_orders
from control_cli import main as cli_main

FIXTURES = Path(__file__).parent / "fixtures"


def _write(tmp_dir: Path, name: str, content: str) -> str:
    p = tmp_dir / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ===================== SafeAgent claims: format parsing =====================

def test_safeagent_json_parsing():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "c.json", json.dumps([
            {"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
             "claimed_at": "2026-09-08T09:30:00-04:00", "outcome": "COMMITTED",
             "caller_interpretation": "SUCCESS", "broker_order_id": "B1"},
        ]))
        claims, rejections, prov = parse_claims(path)
        assert len(claims) == 1 and not rejections
        assert claims[0].request_id == "r1" and claims[0].broker_order_id == "B1"
        assert prov[0].source_format == "json" and prov[0].record_number == 1
    print("safeagent JSON parsing: PASS")


def test_safeagent_jsonl_parsing():
    lines = [
        json.dumps({"request_id": "r1", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
                    "claimed_at": "2026-09-08T09:30:00-04:00", "outcome": "COMMITTED",
                    "caller_interpretation": "SUCCESS"}),
        json.dumps({"request_id": "r2", "symbol": "TQQQ", "side": "sell", "qty": 3, "stage": "exit",
                    "claimed_at": "2026-09-08T09:31:00-04:00", "outcome": "SKIP",
                    "caller_interpretation": "SKIPPED"}),
    ]
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "c.jsonl", "\n".join(lines) + "\n")
        claims, rejections, prov = parse_claims(path)
        assert len(claims) == 2 and not rejections
        assert [c.request_id for c in claims] == ["r1", "r2"]
        assert prov[1].record_number == 2  # 1-based line number
    print("safeagent JSONL parsing: PASS")


def test_safeagent_csv_parsing():
    csv_text = (
        "request_id,symbol,side,qty,stage,claimed_at,outcome,caller_interpretation,broker_order_id\n"
        "r1,TQQQ,buy,7,base,2026-09-08T09:30:00-04:00,COMMITTED,SUCCESS,B1\n"
        "r2,TQQQ,sell,3,exit,2026-09-08T09:31:00-04:00,SKIP,SKIPPED,\n"
    )
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "c.csv", csv_text)
        claims, rejections, prov = parse_claims(path)
        assert len(claims) == 2 and not rejections
        assert claims[0].broker_order_id == "B1"
        assert claims[1].broker_order_id is None  # empty CSV cell -> None, not ""
    print("safeagent CSV parsing: PASS")


# ===================== Alpaca orders: format parsing =====================

def test_alpaca_json_parsing():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "o.json", json.dumps({"orders": [
            {"id": "O1", "client_order_id": "C1", "symbol": "TQQQ", "side": "buy", "qty": "7",
             "status": "filled", "submitted_at": "2026-09-08T13:30:00Z", "filled_at": "2026-09-08T13:30:05Z"},
        ]}))
        orders, rejections, prov = parse_orders(path)
        assert len(orders) == 1 and not rejections
        assert orders[0].broker_order_id == "O1" and orders[0].qty == 7.0
        assert prov[0].source_format == "json"
    print("alpaca JSON parsing: PASS")


def test_alpaca_csv_parsing():
    csv_text = (
        "order_id,client_order_id,symbol,side,qty,status,submitted_at,filled_at\n"
        "O1,C1,TQQQ,buy,7,filled,2026-09-08T13:30:00Z,2026-09-08T13:30:05Z\n"
    )
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "o.csv", csv_text)
        orders, rejections, prov = parse_orders(path)
        assert len(orders) == 1 and not rejections
        assert orders[0].broker_order_id == "O1"  # aliased from order_id
        assert orders[0].status == "filled"
    print("alpaca CSV parsing: PASS")


# ===================== Numeric and timestamp normalization =====================

def test_numeric_and_timestamp_normalization():
    with tempfile.TemporaryDirectory() as d:
        # string qty, nanosecond timestamp, trailing Z, native-number qty
        path = _write(Path(d), "o.json", json.dumps([
            {"id": "O1", "symbol": "TQQQ", "side": "buy", "qty": "7.5", "status": "filled",
             "submitted_at": "2026-09-08T13:30:00.123456789Z", "filled_at": None},
            {"id": "O2", "symbol": "TQQQ", "side": "buy", "qty": 3, "status": "new",
             "submitted_at": "2026-09-08T13:31:00+00:00"},
        ]))
        orders, rejections, prov = parse_orders(path)
        assert not rejections
        assert orders[0].qty == 7.5
        assert orders[0].submitted_at.microsecond == 123456
        assert orders[1].qty == 3.0
    print("numeric and timestamp normalization: PASS")


# ===================== Missing required fields =====================

def test_claims_missing_required_fields_rejected_with_reason():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "c.json", json.dumps([
            {"symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
             "claimed_at": "2026-09-08T09:30:00-04:00", "outcome": "COMMITTED",
             "caller_interpretation": "SUCCESS"},  # missing request_id
        ]))
        claims, rejections, prov = parse_claims(path)
        assert not claims and len(rejections) == 1
        assert rejections[0].record_number == 1
        assert "request_id" in rejections[0].reason
    print("claims missing required fields rejected with reason: PASS")


def test_orders_missing_required_fields_rejected_with_reason():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "o.json", json.dumps([
            {"id": "O1", "side": "buy", "qty": 7, "status": "filled",
             "submitted_at": "2026-09-08T13:30:00Z"},  # missing symbol
        ]))
        orders, rejections, prov = parse_orders(path)
        assert not orders and len(rejections) == 1
        assert "symbol" in rejections[0].reason
    print("orders missing required fields rejected with reason: PASS")


def test_orders_notional_only_rejected_not_guessed():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "o.json", json.dumps([
            {"id": "O1", "symbol": "TQQQ", "side": "buy", "notional": "500.00",
             "status": "new", "submitted_at": "2026-09-08T13:30:00Z"},
        ]))
        orders, rejections, prov = parse_orders(path)
        assert not orders and len(rejections) == 1
        assert "notional" in rejections[0].reason
    print("orders notional-only (no qty) rejected, not guessed: PASS")


# ===================== Unknown status preservation =====================

def test_unknown_alpaca_status_preserved_and_flagged_unresolved():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "o.json", json.dumps([
            {"id": "O1", "symbol": "TQQQ", "side": "buy", "qty": 7, "status": "filled",
             "submitted_at": "2026-09-08T13:30:00Z"},
            {"id": "O1", "symbol": "TQQQ", "side": "buy", "qty": 7, "status": "pending_broker_migration",
             "submitted_at": "2026-09-08T13:30:00Z"},
        ]))
        clean, findings, rejections, prov_by_id = parse_and_normalize_orders(path)
        assert not rejections
        # The unknown status must survive somewhere in the surviving record OR
        # produce an explicit UNMAPPED_STATUS_UNRESOLVED finding -- never be
        # silently discarded/reordered away with no trace.
        assert len(findings) == 1
        assert findings[0].finding_type == "UNMAPPED_STATUS_UNRESOLVED"
        assert "pending_broker_migration" in findings[0].detail
    print("unknown Alpaca status preserved and flagged unresolved: PASS")


# ===================== Duplicate claim rows =====================

def test_duplicate_claim_request_id_not_collapsed():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "c.json", json.dumps([
            {"request_id": "same-id", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
             "claimed_at": "2026-09-08T09:30:00-04:00", "outcome": "COMMITTED",
             "caller_interpretation": "SUCCESS", "broker_order_id": "P"},
            {"request_id": "same-id", "symbol": "TQQQ", "side": "buy", "qty": 7, "stage": "base",
             "claimed_at": "2026-09-08T09:30:01-04:00", "outcome": "COMMITTED",
             "caller_interpretation": "SUCCESS", "broker_order_id": "Q"},
        ]))
        claims, rejections, prov = parse_claims(path)
        assert len(claims) == 2, "Both rows must parse as separate claims -- the parser must not dedupe by request_id"
        assert claims[0].request_id == claims[1].request_id == "same-id"
        assert claims[0].broker_order_id != claims[1].broker_order_id
    print("duplicate claim request_id rows are not collapsed by the parser: PASS")


# ===================== Duplicate/lifecycle-updated order rows =====================

def test_duplicate_order_rows_consolidate_through_ingestion_contract():
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "o.json", json.dumps([
            {"id": "O1", "symbol": "TQQQ", "side": "buy", "qty": 7, "status": "pending_new",
             "submitted_at": "2026-09-08T13:30:00Z"},
            {"id": "O1", "symbol": "TQQQ", "side": "buy", "qty": 7, "status": "filled",
             "submitted_at": "2026-09-08T13:30:00Z", "filled_at": "2026-09-08T13:30:05Z"},
        ]))
        clean, findings, rejections, prov_by_id = parse_and_normalize_orders(path)
        assert not rejections and not findings
        assert len(clean) == 1
        assert clean[0].status == "filled"
        assert clean[0].ingestion_count == 2
        # provenance for the surviving representative must trace back to a real raw row
        assert id(clean[0]) in prov_by_id
        assert prov_by_id[id(clean[0])].record_number in (1, 2)
    print("duplicate/lifecycle-updated order rows consolidate via the ingestion contract: PASS")


# ===================== Full CLI end-to-end execution =====================

def test_cli_end_to_end_execution():
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "out"
        rc = cli_main([
            "reconcile",
            "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
            "--orders", str(FIXTURES / "alpaca_orders_sample.json"),
            "--as-of", "2026-09-08T17:00:00-04:00",
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        for name in ("reconciliation_report.json", "reconciliation_findings.csv", "reconciliation_summary.txt"):
            assert (out_dir / name).is_file(), f"missing {name}"
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert report["summary"]["claims_rejected"] == 2
        assert report["summary"]["orders_rejected"] == 1
        assert len(report["findings"]) > 0
        with open(out_dir / "reconciliation_findings.csv") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(report["findings"])
    print("CLI end-to-end execution: PASS")


def test_cli_exits_nonzero_on_missing_file():
    with tempfile.TemporaryDirectory() as d:
        rc = cli_main([
            "reconcile",
            "--claims", str(FIXTURES / "does_not_exist.json"),
            "--orders", str(FIXTURES / "alpaca_orders_sample.json"),
            "--as-of", "2026-09-08T17:00:00-04:00",
            "--output-dir", str(Path(d) / "out"),
        ])
        assert rc == 2
    print("CLI exits nonzero on missing input file: PASS")


def test_cli_exits_zero_even_with_discrepancies():
    """Discrepancies are the whole point of the tool -- exit 0 means
    'processing succeeded', not 'no findings'. The fixture is full of
    intentional discrepancies and must still exit 0."""
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "out"
        rc = cli_main([
            "reconcile",
            "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
            "--orders", str(FIXTURES / "alpaca_orders_sample.json"),
            "--as-of", "2026-09-08T17:00:00-04:00",
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        report = json.loads((out_dir / "reconciliation_report.json").read_text())
        assert sum(report["summary"]["result_counts"].values()) > 0
        assert any(rc_ != 0 for k, rc_ in report["summary"]["order_disposition_counts"].items() if k != "ASSIGNED")
    print("CLI exits 0 even with real discrepancies present: PASS")


def test_cli_as_a_real_subprocess():
    """One real subprocess invocation, exactly as documented in the
    README, to catch anything an in-process import wouldn't (argv
    parsing, __main__ guard, actual process exit code)."""
    with tempfile.TemporaryDirectory() as d:
        out_dir = Path(d) / "out"
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "control_cli.py"), "reconcile",
             "--claims", str(FIXTURES / "safeagent_claims_sample.json"),
             "--orders", str(FIXTURES / "alpaca_orders_sample.json"),
             "--as-of", "2026-09-08T17:00:00-04:00",
             "--output-dir", str(out_dir)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "API" not in result.stdout and "SECRET" not in result.stdout.upper()
        assert (out_dir / "reconciliation_summary.txt").is_file()
    print("CLI as a real subprocess: PASS")


# ===================== The sanitized real-incident fixture, checked directly =====================

def test_real_incident_fixture_covers_all_required_patterns():
    """Verifies each of the six required incident patterns individually
    against the shared fixture files, not just that the CLI ran."""
    from reconcile_v12_classify import reconcile_with_disposition

    claims, claim_rejections, _ = parse_claims(str(FIXTURES / "safeagent_claims_sample.json"))
    clean_orders, ingestion_findings, order_rejections, _ = \
        parse_and_normalize_orders(str(FIXTURES / "alpaca_orders_sample.json"))
    results, disposition = reconcile_with_disposition(
        claims, clean_orders, now=__import__("datetime").datetime.fromisoformat("2026-09-08T17:00:00-04:00")
    )
    by_request_id = {}
    for r in results:
        if r.claim is not None:
            by_request_id.setdefault(r.claim.request_id, []).append(r)

    # 1. Normal confirmed execution
    conf = by_request_id["order:SAMPLE_STRATEGY:SQQQ:buy:13:base:2026-09-08T09:33:00"]
    assert any(r.result == Result.CONFIRMED for r in conf if r.claim.broker_order_id == "8f3b6b2e-alpaca-001")

    # 2. A SafeAgent SKIP correctly handled -- must not be an executed duplicate
    skip = by_request_id["order:SAMPLE_STRATEGY:SQQQ:sell:37:exit:2026-09-08T09:50:00"]
    assert len(skip) == 1 and skip[0].result == Result.DUPLICATE_BLOCKED

    # 3. The September SKIP-mislogged-as-success pattern
    incident = by_request_id["order:SAMPLE_STRATEGY:SQQQ:buy:12:add2:2026-09-08T09:39:00"]
    assert len(incident) == 1
    assert incident[0].result == Result.MISSING_EXTERNALLY
    assert ReasonCode.CALLER_IGNORED_SKIP in incident[0].reason_codes

    # 4. Identifier conflict
    conflict = by_request_id["order:SAMPLE_STRATEGY:SQQQ:buy:20:base:2026-09-08T11:00:00"]
    assert len(conflict) == 1
    assert ReasonCode.IDENTIFIER_CONFLICT in conflict[0].reason_codes

    # 5. Unmatched external order (no claim references it)
    unmatched = [r for r in results if r.claim is None and r.result == Result.UNRECORDED_INTERNALLY]
    assert any(r.matched_broker_order.broker_order_id == "alpaca-manual-005" for r in unmatched)

    # 6. Duplicate/lifecycle-updated Alpaca input row, consolidated then confirmed
    lifecycle_order = next(o for o in clean_orders if o.broker_order_id == "alpaca-order-006")
    assert lifecycle_order.ingestion_count == 2
    assert lifecycle_order.status == "filled"
    lifecycle_claim = by_request_id["order:SAMPLE_STRATEGY:SQQQ:buy:8:base:2026-09-08T11:20:00"]
    assert lifecycle_claim[0].result == Result.CONFIRMED

    # Duplicate-request-id claim proof: two claims share a request_id, neither collapsed
    dup_rid = "order:SAMPLE_STRATEGY:SQQQ:buy:13:base:2026-09-08T09:33:00"
    assert len(by_request_id[dup_rid]) == 2
    assert by_request_id[dup_rid][0].claim.stable_id != by_request_id[dup_rid][1].claim.stable_id

    # Malformed rows really were rejected, not silently dropped without a trace
    assert len(claim_rejections) == 2
    assert len(order_rejections) == 1

    print("real-incident fixture covers all six required patterns: PASS")


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
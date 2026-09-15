#!/usr/bin/env python3
"""test_v3.py -- automated tests for the v3 harness itself (not the 180
evidence trials, which are a separate run -- see evidence.json).

Run: python -m pytest test_v3.py -v
(or: python test_v3.py, which runs everything without pytest)
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from parent import validate_trial, EXPECTED  # noqa: E402
from worker import install_network_lockdown  # noqa: E402


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ledger_call(base_url, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _start_ledger(port, record_token, control_token):
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "ledger_server.py"), "--port", str(port),
         "--record-token", record_token, "--control-token", control_token],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line.strip() == "LEDGER_READY":
            return proc
    proc.terminate()
    raise TimeoutError("ledger did not become ready")


# ===================== 1. acceptance-gate guard-status enforcement =====================

def test_wrong_guard_status_fails_even_with_correct_effect_count():
    """The v1 review's core finding: effect_count matching alone must NOT
    be enough to pass. A trial with the RIGHT effect_count but the WRONG
    guard_status must be rejected."""
    events = [
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},
        {"event": "claim", "attempt_id": "x:attempt-1", "decision": "PROCEED"},
        {"event": "effect_committed", "attempt_id": "x:attempt-1", "ledger_seq": 1},
        {"event": "settled", "attempt_id": "x:attempt-1"},
        {"event": "runtime_result", "status": "completed", "tool_attempts": 1,
         "llm_calls": 2, "agent_retries": 0},
    ]
    ledger_read = {"sealed": True, "entries": [{"seq": 1, "attempt_id": "x:attempt-1"}]}
    # Correct effect_count (1) but WRONG guard_status (should be COMMITTED, we give PENDING)
    v = validate_trial("safeagent", "clean", 0, events, ledger_read, None, "PENDING", None, False)
    assert v["status"] == "FAIL", f"expected FAIL, got {v}"
    assert any("wrong_guard_state" in r for r in v["reasons"]), v["reasons"]
    print("test_wrong_guard_status_fails_even_with_correct_effect_count: PASS")


def test_correct_guard_status_and_effect_count_passes():
    events = [
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},
        {"event": "claim", "attempt_id": "x:attempt-1", "decision": "PROCEED"},
        {"event": "effect_committed", "attempt_id": "x:attempt-1", "ledger_seq": 1},
        {"event": "settled", "attempt_id": "x:attempt-1"},
        {"event": "runtime_result", "status": "completed", "tool_attempts": 1,
         "llm_calls": 2, "agent_retries": 0},
    ]
    ledger_read = {"sealed": True, "entries": [{"seq": 1, "attempt_id": "x:attempt-1"}]}
    v = validate_trial("safeagent", "clean", 0, events, ledger_read, None, "COMMITTED", None, False)
    assert v["status"] == "PASS", v
    print("test_correct_guard_status_and_effect_count_passes: PASS")


# ===================== 2. fail-closed incomplete observation =====================

def test_timeout_produces_void_not_zero_effects():
    """A timed-out trial must be VOID, with effect_count=None -- never
    silently reported as effect_count=0."""
    v = validate_trial("safeagent", "pre_effect", -1, [], None, None, None, None, timed_out=True)
    assert v["status"] == "VOID", v
    assert v["effect_count"] is None, "timeout must never be reported as effect_count=0"
    print("test_timeout_produces_void_not_zero_effects: PASS")


def test_missing_runtime_result_is_void():
    events = [{"event": "tool_enter", "attempt_id": "x:attempt-1"}]  # no runtime_result at all
    ledger_read = {"sealed": True, "entries": []}
    v = validate_trial("safeagent", "clean", 0, events, ledger_read, None, "COMMITTED", None, False)
    assert v["status"] == "VOID", v
    assert v["effect_count"] is None
    print("test_missing_runtime_result_is_void: PASS")


def test_unsealed_ledger_at_read_time_is_void():
    """If the ledger was somehow read before being sealed, that's a real
    protocol violation of the required sequence and must void the trial,
    not be silently accepted."""
    events = [{"event": "runtime_result", "status": "completed", "tool_attempts": 1,
               "llm_calls": 2, "agent_retries": 0}]
    ledger_read = {"sealed": False, "entries": []}  # never sealed
    v = validate_trial("safeagent", "clean", 0, events, ledger_read, None, "COMMITTED", None, False)
    assert v["status"] == "VOID", v
    assert "ledger_not_sealed_at_read_time" in v["reasons"]
    print("test_unsealed_ledger_at_read_time_is_void: PASS")


def test_ledger_read_error_is_void():
    v = validate_trial("safeagent", "clean", 0, [], None, "connection refused", None, None, False)
    assert v["status"] == "VOID", v
    assert v["effect_count"] is None
    print("test_ledger_read_error_is_void: PASS")


# ===================== 3. duplicate attempt-ID rejection =====================

def test_duplicate_attempt_ids_in_events_fails():
    events = [
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},  # duplicate, should be attempt-2
        {"event": "runtime_result", "status": "completed", "tool_attempts": 2,
         "llm_calls": 2, "agent_retries": 0},
        {"event": "injected_failure", "attempt_id": "x:attempt-1", "point": "before_effect"},
    ]
    ledger_read = {"sealed": True, "entries": []}
    v = validate_trial("safeagent", "pre_effect", 0, events, ledger_read, None, "PENDING", None, False)
    assert v["status"] == "FAIL", v
    assert any("duplicate_attempt_ids" in r for r in v["reasons"]), v["reasons"]
    print("test_duplicate_attempt_ids_in_events_fails: PASS")


def test_duplicate_ledger_entries_fails():
    events = [{"event": "runtime_result", "status": "completed", "tool_attempts": 1,
               "llm_calls": 2, "agent_retries": 0}]
    ledger_read = {"sealed": True, "entries": [
        {"seq": 1, "attempt_id": "x:attempt-1"}, {"seq": 2, "attempt_id": "x:attempt-1"},
    ]}
    v = validate_trial("safeagent", "clean", 0, events, ledger_read, None, "COMMITTED", None, False)
    assert v["status"] == "FAIL", v
    assert any("duplicate_ledger_entries" in r for r in v["reasons"]), v["reasons"]
    print("test_duplicate_ledger_entries_fails: PASS")


# ===================== 4. incorrect injection ordering =====================

def test_wrong_injection_point_fails():
    events = [
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},
        {"event": "injected_failure", "attempt_id": "x:attempt-1", "point": "after_effect_before_confirmation"},
        {"event": "tool_enter", "attempt_id": "x:attempt-2"},
        {"event": "runtime_result", "status": "completed", "tool_attempts": 2,
         "llm_calls": 2, "agent_retries": 0},
    ]
    ledger_read = {"sealed": True, "entries": []}
    # case=pre_effect expects point="before_effect"; we gave the post_effect point instead
    v = validate_trial("safeagent", "pre_effect", 0, events, ledger_read, None, "PENDING", None, False)
    assert v["status"] == "FAIL", v
    assert any("wrong_injection_point" in r for r in v["reasons"]), v["reasons"]
    print("test_wrong_injection_point_fails: PASS")


def test_post_effect_injection_before_commit_ack_fails():
    """The failure must occur strictly AFTER the ledger acknowledged
    attempt 1's effect -- if the injected_failure timestamp precedes the
    effect_committed timestamp for the same attempt, that's a real
    ordering violation, not just a formality."""
    events = [
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},
        {"event": "effect_committed", "attempt_id": "x:attempt-1", "ledger_seq": 1, "ts": 100.0},
        {"event": "injected_failure", "attempt_id": "x:attempt-1",
         "point": "after_effect_before_confirmation", "ts": 50.0},  # BEFORE the commit -- wrong
        {"event": "tool_enter", "attempt_id": "x:attempt-2"},
        {"event": "runtime_result", "status": "completed", "tool_attempts": 2,
         "llm_calls": 2, "agent_retries": 0},
    ]
    ledger_read = {"sealed": True, "entries": [{"seq": 1, "attempt_id": "x:attempt-1"}]}
    v = validate_trial("safeagent", "post_effect", 0, events, ledger_read, None, "PENDING", None, False)
    assert v["status"] == "FAIL", v
    assert any("occurred_before_effect_committed_ts" in r for r in v["reasons"]), v["reasons"]
    print("test_post_effect_injection_before_commit_ack_fails: PASS")


def test_second_attempt_not_attributable_to_use_retry_fails():
    """If llm_calls != 2 for a failure case, the second tool attempt can't
    be confidently attributed to ToolUsage._use's internal retry (it could
    have come from the LLM re-issuing the action) -- must fail."""
    events = [
        {"event": "tool_enter", "attempt_id": "x:attempt-1"},
        {"event": "injected_failure", "attempt_id": "x:attempt-1", "point": "before_effect"},
        {"event": "tool_enter", "attempt_id": "x:attempt-2"},
        {"event": "runtime_result", "status": "completed", "tool_attempts": 2,
         "llm_calls": 3, "agent_retries": 0},  # 3, not 2 -- suspicious extra LLM call
    ]
    ledger_read = {"sealed": True, "entries": []}
    v = validate_trial("safeagent", "pre_effect", 0, events, ledger_read, None, "PENDING", None, False)
    assert v["status"] == "FAIL", v
    assert any("unexpected_llm_calls" in r for r in v["reasons"]), v["reasons"]
    print("test_second_attempt_not_attributable_to_use_retry_fails: PASS")


# ===================== 5. worker inability to use the control channel =====================

def test_worker_record_token_rejected_on_every_control_endpoint():
    port = _free_port()
    record_token, control_token = "rec-test-token", "ctrl-test-token"
    proc = _start_ledger(port, record_token, control_token)
    base = f"http://127.0.0.1:{port}"
    try:
        for method, path in [("POST", "/control/reset"), ("POST", "/control/seal"), ("GET", "/control/read")]:
            status, body = _ledger_call(base, record_token, method, path)
            assert status == 403, f"{method} {path} with record_token: expected 403, got {status} {body}"
        # And with no token at all
        status, body = _ledger_call(base, None, "GET", "/control/read")
        assert status == 403, f"no-token read: expected 403, got {status}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    print("test_worker_record_token_rejected_on_every_control_endpoint: PASS")


def test_control_token_rejected_on_record_endpoint():
    """The separation is symmetric: the control_token must not work for
    recording either -- it is a genuinely disjoint capability, not just a
    higher-privilege superset."""
    port = _free_port()
    record_token, control_token = "rec-test-token2", "ctrl-test-token2"
    proc = _start_ledger(port, record_token, control_token)
    base = f"http://127.0.0.1:{port}"
    try:
        status, body = _ledger_call(base, control_token, "POST", "/record",
                                     {"logical_id": "x", "attempt_id": "x:1"})
        assert status == 403, f"expected 403, got {status} {body}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    print("test_control_token_rejected_on_record_endpoint: PASS")


def test_record_rejected_after_seal():
    port = _free_port()
    record_token, control_token = "rec-test-token3", "ctrl-test-token3"
    proc = _start_ledger(port, record_token, control_token)
    base = f"http://127.0.0.1:{port}"
    try:
        status, _ = _ledger_call(base, control_token, "POST", "/control/seal")
        assert status == 200
        status, body = _ledger_call(base, record_token, "POST", "/record",
                                     {"logical_id": "x", "attempt_id": "x:1"})
        assert status == 409, f"expected 409 after seal, got {status} {body}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    print("test_record_rejected_after_seal: PASS")


# ===================== 6. telemetry/network isolation =====================

def test_network_lockdown_blocks_unexpected_destination():
    """Runs in a fresh subprocess -- install_network_lockdown patches
    socket.socket.connect process-wide and permanently, exactly as it does
    in the real worker (which is always a short-lived, single-purpose
    process). Testing it in-process here would leak the patch into every
    other test that runs afterward in this same test file's process."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from worker import install_network_lockdown\n"
        "import socket\n"
        "install_network_lockdown('127.0.0.1', 19999)\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(1)\n"
        "try:\n"
        "    s.connect(('127.0.0.5', 8080))\n"
        "    print('NOT_BLOCKED'); sys.exit(1)\n"
        "except PermissionError:\n"
        "    print('BLOCKED_OK'); sys.exit(0)\n"
    ) % str(HERE)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0 and "BLOCKED_OK" in proc.stdout, (proc.returncode, proc.stdout, proc.stderr)
    print("test_network_lockdown_blocks_unexpected_destination: PASS")


def test_network_lockdown_allows_configured_destination():
    """Same subprocess-isolation reasoning as above."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from worker import install_network_lockdown\n"
        "import socket\n"
        "install_network_lockdown('127.0.0.1', 19998)\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(1)\n"
        "try:\n"
        "    s.connect(('127.0.0.1', 19998))\n"
        "    print('UNEXPECTEDLY_CONNECTED'); sys.exit(1)\n"
        "except PermissionError:\n"
        "    print('WRONGLY_BLOCKED'); sys.exit(1)\n"
        "except ConnectionRefusedError:\n"
        "    print('ALLOWED_OK'); sys.exit(0)\n"
    ) % str(HERE)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0 and "ALLOWED_OK" in proc.stdout, (proc.returncode, proc.stdout, proc.stderr)
    print("test_network_lockdown_allows_configured_destination: PASS")


def test_worker_env_disables_telemetry():
    """Static check that the worker sets the documented telemetry-disable
    environment variables before importing crewai."""
    src = (HERE / "worker.py").read_text()
    idx_env = src.index("CREWAI_DISABLE_TELEMETRY")
    idx_import = src.index("from crewai import")
    assert idx_env < idx_import, "telemetry env vars must be set BEFORE crewai is imported"
    print("test_worker_env_disables_telemetry: PASS")


# ===================== 7. receipt schema validation =====================

REQUIRED_TRIAL_FIELDS = {
    "mode", "case", "logical_action_id", "worker_exit_status", "observation_complete",
    "tool_attempts", "effect_count", "effect_attempt_ids", "guard_status", "expected",
    "trial_status", "fail_reasons", "events",
}
REQUIRED_RECORD_FIELDS = {
    "schema", "crewai_version", "safeagent_version", "crewai_retry_entry_point",
    "k_per_case", "total_trials", "claim", "isolation_model", "limitations",
    "summary", "trials",
}


def test_evidence_record_has_required_top_level_fields():
    evidence_path = HERE / "evidence.json"
    if not evidence_path.exists():
        print("test_evidence_record_has_required_top_level_fields: SKIPPED (no evidence.json present)")
        return
    record = json.loads(evidence_path.read_text())
    missing = REQUIRED_RECORD_FIELDS - set(record.keys())
    assert not missing, f"missing top-level fields: {missing}"
    print("test_evidence_record_has_required_top_level_fields: PASS")


def test_evidence_every_trial_has_required_fields():
    evidence_path = HERE / "evidence.json"
    if not evidence_path.exists():
        print("test_evidence_every_trial_has_required_fields: SKIPPED (no evidence.json present)")
        return
    record = json.loads(evidence_path.read_text())
    for t in record["trials"]:
        missing = REQUIRED_TRIAL_FIELDS - set(t.keys())
        assert not missing, f"trial {t.get('logical_action_id')} missing fields: {missing}"
        assert t["trial_status"] in ("PASS", "FAIL", "VOID")
        if t["trial_status"] == "VOID":
            assert t["effect_count"] is None, (
                f"VOID trial {t['logical_action_id']} must not report a numeric effect_count"
            )
    print(f"test_evidence_every_trial_has_required_fields: PASS ({len(record['trials'])} trials checked)")


def test_evidence_all_180_trials_pass():
    evidence_path = HERE / "evidence.json"
    if not evidence_path.exists():
        print("test_evidence_all_180_trials_pass: SKIPPED (no evidence.json present)")
        return
    record = json.loads(evidence_path.read_text())
    assert record["total_trials"] == 180, record["total_trials"]
    failing = [t for t in record["trials"] if t["trial_status"] != "PASS"]
    assert not failing, f"{len(failing)} trials did not pass: {[t['logical_action_id'] for t in failing]}"
    print("test_evidence_all_180_trials_pass: PASS")


if __name__ == "__main__":
    import inspect
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and inspect.isfunction(f))
    failures = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failures.append((name, str(e)))
            print(f"{name}: FAIL -- {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed.")
    if failures:
        for name, err in failures:
            print(f"  - {name}: {err}")
    sys.exit(1 if failures else 0)

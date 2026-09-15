#!/usr/bin/env python3
"""parent.py -- the only process that ever holds the ledger's control_token.

Per trial, in order:
  1. Generate fresh record_token/control_token (secrets.token_hex -- never
     reused across trials, so no trial's tokens could leak into another).
  2. Start a NEW ledger_server.py subprocess (genuinely out-of-process),
     wait for its LEDGER_READY line.
  3. Call /control/reset (belt-and-suspenders on a freshly-started, already-
     empty ledger -- makes the "start and reset" sequence explicit rather
     than relying on the process being fresh).
  4. Launch worker.py with ONLY the record_token, ledger URL/host/port, and
     the guard_db path -- the control_token is never constructed into the
     worker's argv or environment anywhere in this file.
  5. subprocess.run(...) blocks until the worker fully exits. Only after
     that do we touch the ledger again.
  6. Call /control/seal, then /control/read -- both authenticated with the
     control_token the worker never had.
  7. Independently open guard_db (SafeAgent's own store) and read the
     claim's final status.
  8. Terminate the ledger subprocess.
  9. Validate every invariant in ITEM 5 / fail-closed per ITEM 8. Any
     violation marks the trial VOID with a specific reason -- never
     silently treated as effect_count=0.

Fail-closed policy (ITEM 8): timeout, a missing/malformed runtime_result
event, an incomplete or unreadable ledger response, a broken seq/attempt
chain, wrong attempt ordering, or a guard_status mismatch all produce
status="VOID" with a recorded reason, and VOID trials fail the overall
acceptance gate regardless of what effect_count happened to look like.
"""
from __future__ import annotations

import argparse
import json
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

CASES = ("clean", "pre_effect", "post_effect")
MODES = ("unguarded", "safeagent")
PREFIX = "SA_AB "
HERE = Path(__file__).parent
WORKER_TIMEOUT_S = 60
LEDGER_READY_TIMEOUT_S = 10

EXPECTED = {
    ("unguarded", "clean"): (1, None),
    ("unguarded", "pre_effect"): (1, None),
    ("unguarded", "post_effect"): (2, None),
    ("safeagent", "clean"): (1, "COMMITTED"),
    ("safeagent", "pre_effect"): (0, "PENDING"),
    ("safeagent", "post_effect"): (1, "PENDING"),
}
EXPECTED_TOOL_ATTEMPTS = {"clean": 1, "pre_effect": 2, "post_effect": 2}
EXPECTED_INJECTION_POINT = {"pre_effect": "before_effect", "post_effect": "after_effect_before_confirmation"}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def parse_events(stdout: str) -> list[dict[str, Any]]:
    out = []
    for line in stdout.splitlines():
        if not line.startswith(PREFIX):
            continue
        try:
            out.append(json.loads(line[len(PREFIX):]))
        except json.JSONDecodeError:
            out.append({"event": "__malformed__", "raw": line})
    return out


class LedgerControlClient:
    """The ONLY class in this file that ever holds control_token. Mirrors
    LedgerRecordClient's shape on the worker side but with the opposite,
    disjoint capability set -- reset/seal/read, no record method exists
    here at all."""

    def __init__(self, base_url: str, control_token: str):
        self._base_url = base_url
        self._token = control_token

    def _call(self, method: str, path: str) -> tuple[int, dict]:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(self._base_url + path, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def reset(self) -> None:
        status, body = self._call("POST", "/control/reset")
        if status != 200:
            raise RuntimeError(f"ledger reset failed: {status} {body}")

    def seal(self) -> None:
        status, body = self._call("POST", "/control/seal")
        if status != 200:
            raise RuntimeError(f"ledger seal failed: {status} {body}")

    def read(self) -> dict:
        status, body = self._call("GET", "/control/read")
        if status != 200:
            raise RuntimeError(f"ledger read failed: {status} {body}")
        return body


def start_ledger(port: int, record_token: str, control_token: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "ledger_server.py"), "--port", str(port),
         "--record-token", record_token, "--control-token", control_token],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + LEDGER_READY_TIMEOUT_S
    line = ""
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line.strip() == "LEDGER_READY":
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"ledger process exited early: {proc.stderr.read()}")
    proc.terminate()
    raise TimeoutError(f"ledger did not signal ready within {LEDGER_READY_TIMEOUT_S}s (last line: {line!r})")


def validate_trial(mode: str, case: str, worker_exit: int, events: list[dict],
                    ledger_read: dict | None, ledger_read_error: str | None,
                    guard_status: str | None, guard_read_error: str | None,
                    timed_out: bool) -> dict:
    """Enforces every invariant from ITEM 5 plus the fail-closed policy from
    ITEM 8. Returns {"status": "PASS"|"FAIL"|"VOID", "reasons": [...],
    "effect_count": int|None, "effect_attempt_ids": [...]}."""
    reasons: list[str] = []

    if timed_out:
        return {"status": "VOID", "reasons": ["worker_timeout"], "effect_count": None, "effect_attempt_ids": []}
    if ledger_read_error:
        return {"status": "VOID", "reasons": [f"ledger_read_failed: {ledger_read_error}"],
                "effect_count": None, "effect_attempt_ids": []}
    if guard_read_error and mode == "safeagent":
        return {"status": "VOID", "reasons": [f"guard_read_failed: {guard_read_error}"],
                "effect_count": None, "effect_attempt_ids": []}
    if ledger_read is None or not ledger_read.get("sealed"):
        return {"status": "VOID", "reasons": ["ledger_not_sealed_at_read_time"],
                "effect_count": None, "effect_attempt_ids": []}

    malformed = [e for e in events if e.get("event") == "__malformed__"]
    if malformed:
        return {"status": "VOID", "reasons": [f"malformed_event: {malformed[0]}"],
                "effect_count": None, "effect_attempt_ids": []}

    runtime_results = [e for e in events if e["event"] == "runtime_result"]
    if worker_exit != 0 or len(runtime_results) != 1:
        return {"status": "VOID",
                "reasons": [f"incomplete_observation: exit={worker_exit} runtime_results={len(runtime_results)}"],
                "effect_count": None, "effect_attempt_ids": []}
    rr = runtime_results[0]

    tool_enters = [e for e in events if e["event"] == "tool_enter"]
    attempt_ids_seen = [e["attempt_id"] for e in tool_enters]
    expected_attempts = EXPECTED_TOOL_ATTEMPTS[case]

    if len(tool_enters) != expected_attempts:
        reasons.append(f"wrong_tool_entry_count: expected={expected_attempts} got={len(tool_enters)}")
    if len(set(attempt_ids_seen)) != len(attempt_ids_seen):
        reasons.append(f"duplicate_attempt_ids: {attempt_ids_seen}")
    if rr.get("agent_retries", -1) != 0:
        reasons.append(f"nonzero_agent_retries: {rr.get('agent_retries')}")

    if case in ("pre_effect", "post_effect"):
        # Second tool entry must be attributable to CrewAI's OWN internal
        # ToolUsage._use retry, not the LLM re-authorizing the action: the
        # ScriptedLLM only ever emits "Action:" on its first call, so if a
        # second tool attempt occurred with llm_calls==2 (one Action, one
        # Final Answer) and agent_retries==0, the second attempt could only
        # have come from _use's internal recursive retry.
        if rr.get("llm_calls") != 2:
            reasons.append(f"unexpected_llm_calls: expected=2 got={rr.get('llm_calls')} "
                            f"(second attempt not attributable to _use's internal retry)")

        injected = [e for e in events if e["event"] == "injected_failure"]
        if len(injected) != 1:
            reasons.append(f"wrong_injected_failure_count: expected=1 got={len(injected)}")
        else:
            inj = injected[0]
            if inj.get("point") != EXPECTED_INJECTION_POINT[case]:
                reasons.append(f"wrong_injection_point: expected={EXPECTED_INJECTION_POINT[case]} got={inj.get('point')}")
            if not inj.get("attempt_id", "").endswith(":attempt-1"):
                reasons.append(f"injection_not_on_first_attempt: {inj.get('attempt_id')}")

            if case == "post_effect":
                committed = [e for e in events if e["event"] == "effect_committed"
                             and e["attempt_id"] == inj["attempt_id"]]
                if not committed:
                    reasons.append("post_effect_injection_with_no_prior_effect_committed_event")
                elif inj.get("ts", 0) < committed[0].get("ts", 1e18):
                    reasons.append("post_effect_injection_occurred_before_effect_committed_ts")
                elif committed[0].get("ledger_seq") is None:
                    reasons.append("effect_committed_event_missing_ledger_ack")

    ledger_entries = ledger_read.get("entries", [])
    ledger_attempt_ids = [e["attempt_id"] for e in ledger_entries]
    expected_effect_count, expected_guard = EXPECTED[(mode, case)]

    if mode == "safeagent" and guard_status != expected_guard:
        reasons.append(f"wrong_guard_state: expected={expected_guard} got={guard_status}")
    if len(ledger_entries) != expected_effect_count:
        reasons.append(f"wrong_ledger_effect_count: expected={expected_effect_count} got={len(ledger_entries)}")
    if len(set(ledger_attempt_ids)) != len(ledger_attempt_ids):
        reasons.append(f"duplicate_ledger_entries: {ledger_attempt_ids}")

    seqs = [e["seq"] for e in ledger_entries]
    if seqs != sorted(seqs):
        reasons.append(f"ledger_seq_not_monotonic: {seqs}")

    status = "PASS" if not reasons else "FAIL"
    return {"status": status, "reasons": reasons, "effect_count": len(ledger_entries),
            "effect_attempt_ids": ledger_attempt_ids}


def run_trial(mode: str, case: str, index: int, guard_db: Path) -> dict:
    logical_id = f"{mode}:{case}:{index}"
    port = _free_port()
    record_token = secrets.token_hex(16)
    control_token = secrets.token_hex(16)  # never passed to the worker anywhere below

    ledger_proc = start_ledger(port, record_token, control_token)
    control = LedgerControlClient(f"http://127.0.0.1:{port}", control_token)
    try:
        control.reset()

        cmd = [sys.executable, str(HERE / "worker.py"),
               "--mode", mode, "--case", case, "--logical-id", logical_id,
               "--ledger-url", f"http://127.0.0.1:{port}",
               "--ledger-host", "127.0.0.1", "--ledger-port", str(port),
               "--record-token", record_token,
               "--guard-db", str(guard_db)]
        timed_out = False
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=WORKER_TIMEOUT_S)
            worker_exit, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            worker_exit, stdout, stderr = -1, e.stdout or "", e.stderr or ""

        control.seal()

        ledger_read, ledger_read_error = None, None
        try:
            ledger_read = control.read()
        except Exception as e:
            ledger_read_error = str(e)

        guard_status, guard_read_error = None, None
        if mode == "safeagent":
            try:
                con = sqlite3.connect(str(guard_db))
                row = con.execute(
                    "SELECT status FROM execution_requests WHERE request_id=?", (logical_id,)
                ).fetchone()
                con.close()
                guard_status = row[0] if row else None
            except Exception as e:
                guard_read_error = str(e)

        events = parse_events(stdout)
        verdict = validate_trial(mode, case, worker_exit, events, ledger_read, ledger_read_error,
                                  guard_status, guard_read_error, timed_out)

        return {
            "mode": mode, "case": case, "logical_action_id": logical_id,
            "worker_exit_status": worker_exit,
            "observation_complete": verdict["status"] != "VOID",
            "tool_attempts": len([e for e in events if e.get("event") == "tool_enter"]),
            "effect_count": verdict["effect_count"],
            "effect_attempt_ids": verdict["effect_attempt_ids"],
            "guard_status": guard_status,
            "expected": {"effect_count": EXPECTED[(mode, case)][0], "guard_status": EXPECTED[(mode, case)][1]},
            "trial_status": verdict["status"],
            "fail_reasons": verdict["reasons"],
            "worker_stderr_tail": stderr[-2000:] if stderr else "",
            "events": events,
        }
    finally:
        ledger_proc.terminate()
        try:
            ledger_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ledger_proc.kill()


def run_parent(k: int, output: Path, only_mode: str | None = None, only_case: str | None = None) -> int:
    import tempfile
    trials: list[dict[str, Any]] = []
    modes = [only_mode] if only_mode else list(MODES)
    cases = [only_case] if only_case else list(CASES)
    with tempfile.TemporaryDirectory() as td:
        for mode in modes:
            for case in cases:
                for index in range(k):
                    guard_db = Path(td) / f"guard-{mode}-{case}-{index}.db"
                    t0 = time.time()
                    trial = run_trial(mode, case, index, guard_db)
                    trial["wall_time_s"] = round(time.time() - t0, 3)
                    trials.append(trial)
                    print(f"{mode}:{case}:{index} -> {trial['trial_status']}"
                          + (f" {trial['fail_reasons']}" if trial["fail_reasons"] else ""))

    summary: dict[str, Any] = {}
    for mode in modes:
        summary[mode] = {}
        for case in cases:
            selected = [t for t in trials if t["mode"] == mode and t["case"] == case]
            summary[mode][case] = {
                "k": len(selected),
                "pass": sum(1 for t in selected if t["trial_status"] == "PASS"),
                "fail": sum(1 for t in selected if t["trial_status"] == "FAIL"),
                "void": sum(1 for t in selected if t["trial_status"] == "VOID"),
                "effect_counts": dict(Counter(str(t["effect_count"]) for t in selected)),
                "guard_statuses": dict(Counter(str(t["guard_status"]) for t in selected)),
            }

    record = {
        "schema": "safeagent.crewai_retry_ab.v3",
        "crewai_version": "1.15.21",
        "safeagent_version": "0.1.23",
        "crewai_retry_entry_point": "crewai.tools.tool_usage.ToolUsage._use",
        "k_per_case": k,
        "total_trials": len(trials),
        "claim": (
            "This validates SafeAgent 0.1.23 against CrewAI 1.15.21's same-process synchronous "
            "ToolUsage._use retry, using a scripted deterministic LLM and a harmless effect recorded "
            "through an out-of-process, capability-separated ledger. It does not validate process "
            "death, fresh-worker recovery, a real provider, or SafeAgent Control reconciliation."
        ),
        "isolation_model": (
            "Effects are recorded by the worker through a record-only bearer token against a "
            "separate ledger_server.py OS process. The worker holds no token or endpoint capable "
            "of querying, resetting, modifying, or sealing the ledger; those operations require a "
            "disjoint control_token that is generated by and never leaves the parent process. The "
            "parent seals the ledger and reads it only after the worker subprocess has fully exited."
        ),
        "limitations": (
            "Same-process CrewAI ToolUsage retry (the retry itself occurs within one worker process's "
            "call stack, not across a process crash/restart boundary); scripted local LLM; the recorded "
            "effect, while now out-of-process, is still a local-machine ledger, not a real external "
            "side effect with independent network/latency semantics; no real provider API; no hostile-"
            "subject isolation beyond the capability/token boundary described above."
        ),
        "summary": summary,
        "trials": trials,
    }
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"wrote {output}")

    all_pass = all(t["trial_status"] == "PASS" for t in trials)
    print(f"\nACCEPTANCE GATE: {'PASS' if all_pass else 'FAIL'} "
          f"({sum(1 for t in trials if t['trial_status']=='PASS')}/{len(trials)} trials PASS)")
    return 0 if all_pass else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=30)
    ap.add_argument("--output", default="evidence.json")
    ap.add_argument("--only-mode", choices=MODES, default=None,
                     help="restrict to one mode (for chunked execution under a time-limited runner)")
    ap.add_argument("--only-case", choices=CASES, default=None,
                     help="restrict to one case (for chunked execution under a time-limited runner)")
    args = ap.parse_args()
    return run_parent(args.k, Path(args.output), args.only_mode, args.only_case)


if __name__ == "__main__":
    raise SystemExit(main())

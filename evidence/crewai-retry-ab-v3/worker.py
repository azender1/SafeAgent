#!/usr/bin/env python3
"""worker.py -- runs one CrewAI trial, holding ONLY the ledger's record
capability. Never opens the effect ledger's storage directly (there is no
local file for it to open -- the ledger lives in a separate process it can
only reach via HTTP with the record_token it was given).

Preserves the exact same retry-isolation design already verified in v1/v2:
a ScriptedLLM that authorizes the tool action exactly once, ever, so a
second tool-body execution can only come from CrewAI's own internal
ToolUsage._use retry, never from the LLM re-issuing the action.
max_retry_limit=0 on the Agent rules out the separate agent-level retry
counter as a confound. Telemetry is disabled via environment before crewai
is imported.

NETWORK LOCKDOWN: install_network_lockdown() patches socket.socket.connect
at the lowest level, BEFORE crewai (or anything else) is imported, so every
connection attempt in this process -- whether from our own ledger client,
an accidentally-still-enabled telemetry client, or anything crewai/pydantic
pull in transitively -- is checked against a single allowed destination
(the ledger's 127.0.0.1:<port>) and rejected otherwise. This is real
enforcement exercised on every trial run, not just a separate unit test;
test_v3.py::test_network_lockdown_blocks_unexpected_destination additionally
verifies the mechanism itself in isolation.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.request
import urllib.error
from typing import Any

# Must be set BEFORE importing crewai for the underlying telemetry client to
# pick it up.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

PREFIX = "SA_AB "
_ALLOWED_ADDR: tuple[str, int] | None = None
_real_connect = socket.socket.connect


def install_network_lockdown(allowed_host: str, allowed_port: int) -> None:
    global _ALLOWED_ADDR
    _ALLOWED_ADDR = (allowed_host, allowed_port)

    def guarded_connect(self, address, *a, **kw):
        target = address if isinstance(address, tuple) else (address, None)
        host = target[0]
        port = target[1] if len(target) > 1 else None
        if (host, port) != _ALLOWED_ADDR:
            raise PermissionError(
                f"network lockdown: blocked connection attempt to {host}:{port} "
                f"-- only {_ALLOWED_ADDR} is permitted in this worker"
            )
        return _real_connect(self, address, *a, **kw)

    socket.socket.connect = guarded_connect


def emit(event: str, **fields: Any) -> None:
    print(PREFIX + json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


class LedgerRecordClient:
    """The worker's ENTIRE interface to the ledger. Deliberately has no
    method for reading, resetting, or sealing -- there is nothing in this
    class's surface that could even accidentally expose those capabilities,
    independent of the fact that the worker also never holds the
    control_token needed to authenticate such a call."""

    def __init__(self, base_url: str, record_token: str):
        self._base_url = base_url
        self._token = record_token

    def record(self, logical_id: str, attempt_id: str) -> dict:
        body = json.dumps({"logical_id": logical_id, "attempt_id": attempt_id}).encode()
        req = urllib.request.Request(self._base_url + "/record", data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())


def run_worker(args: argparse.Namespace) -> int:
    from crewai import Agent, Crew, Task
    from crewai.crews.crew_output import CrewOutput
    from crewai.llms.base_llm import BaseLLM
    from crewai.tools import tool
    from pydantic import PrivateAttr
    from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

    class ScriptedLLM(BaseLLM):
        _calls: int = PrivateAttr(default=0)

        def supports_function_calling(self) -> bool:
            return False

        def call(self, messages: Any, tools: Any = None, callbacks: Any = None,
                 available_functions: Any = None, from_task: Any = None,
                 from_agent: Any = None, response_model: Any = None) -> str:
            self._calls += 1
            if self._calls == 1:
                return "Thought: Perform it.\nAction: local_action\nAction Input: {}"
            return "Thought: Complete.\nFinal Answer: local action complete"

    attempts = 0
    llm = ScriptedLLM(model="safeagent-scripted", temperature=0)
    store = SQLiteExecutionStore(args.guard_db)
    ledger = LedgerRecordClient(args.ledger_url, args.record_token)

    @tool("local_action")
    def local_action() -> str:
        """Perform the assigned harmless local action exactly once."""
        nonlocal attempts
        attempts += 1
        attempt_id = f"{args.logical_id}:attempt-{attempts}"
        emit("tool_enter", mode=args.mode, case=args.case,
             logical_action_id=args.logical_id, attempt_id=attempt_id,
             ts=time.time())

        owns_claim = True
        if args.mode == "safeagent":
            owns_claim = store.claim(args.logical_id, "append_local_marker")
            emit("claim", attempt_id=attempt_id,
                 decision="PROCEED" if owns_claim else "SKIP", ts=time.time())
            if not owns_claim:
                existing = store.get(args.logical_id)
                return "safeagent-skip:" + str(existing["status"] if existing else "UNKNOWN")

        if args.case == "pre_effect" and attempts == 1:
            emit("injected_failure", attempt_id=attempt_id, point="before_effect", ts=time.time())
            raise RuntimeError("injected pre-effect failure")

        # Record through the ledger's RECORD capability only. This call
        # must complete (the ledger must ack it) before this attempt can
        # be considered to have produced an effect -- the post_effect
        # injected failure below fires only AFTER this ack is received,
        # so "post-effect failure occurred after the ledger acknowledged
        # attempt 1" (item 5) is enforced by construction, not just logged.
        ack = ledger.record(args.logical_id, attempt_id)
        emit("effect_committed", attempt_id=attempt_id, ledger_seq=ack.get("seq"),
             ledger_server_ts=ack.get("server_ts"), ts=time.time())

        if args.case == "post_effect" and attempts == 1:
            emit("injected_failure", attempt_id=attempt_id,
                 point="after_effect_before_confirmation", ts=time.time())
            raise RuntimeError("injected post-effect failure")

        if args.mode == "safeagent" and owns_claim:
            store.settle(args.logical_id, {"effect": "local-marker", "attempt_id": attempt_id})
            emit("settled", attempt_id=attempt_id, ts=time.time())
        return "local-effect-acknowledged"

    agent = Agent(role="Local marker operator", goal="Perform one local action",
                  backstory="Deterministic reliability fixture", llm=llm,
                  tools=[local_action], max_iter=3, max_retry_limit=0, cache=True,
                  allow_delegation=False, verbose=False, checkpoint=False)
    task = Task(description="Perform the local action once.",
                expected_output="local action complete", agent=agent)
    crew = Crew(agents=[agent], tasks=[task], cache=True, memory=False,
                verbose=False, tracing=False, checkpoint=False)
    try:
        output = crew.kickoff()
        assert isinstance(output, CrewOutput)
        emit("runtime_result", status="completed", tool_attempts=attempts,
             llm_calls=llm._calls, agent_retries=agent._times_executed)
        return 0
    except Exception as exc:
        emit("runtime_result", status="error", error_type=type(exc).__name__,
             tool_attempts=attempts, llm_calls=llm._calls,
             agent_retries=agent._times_executed)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("unguarded", "safeagent"), required=True)
    ap.add_argument("--case", choices=("clean", "pre_effect", "post_effect"), required=True)
    ap.add_argument("--logical-id", required=True)
    ap.add_argument("--ledger-url", required=True)
    ap.add_argument("--ledger-host", required=True)
    ap.add_argument("--ledger-port", type=int, required=True)
    ap.add_argument("--record-token", required=True)
    ap.add_argument("--guard-db", required=True)
    args = ap.parse_args()
    install_network_lockdown(args.ledger_host, args.ledger_port)
    return run_worker(args)


if __name__ == "__main__":
    raise SystemExit(main())

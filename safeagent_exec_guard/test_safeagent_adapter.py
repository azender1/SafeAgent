"""Unit tests for WisePick → SafeAgent adapter (deterministic request_id + start_time_ms)."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from adapters.safeagent_adapter import (  # noqa: E402
    SafeAgentAdapter,
    StubSafeAgentRuntime,
    wisepick_to_safeagent_request_id,
)

# Fixed orchestrator turn anchor (epoch ms) — must not depend on local clock in tests.
FIXED_START_TIME_MS = 1716780000000.0

# May 21 live-session replay anchor (SQQQ exit cascade scenario).
SQQQ_EXIT_TURN_MS = 1_716_300_000_000.0
SQQQ_SESSION = "live-2024-05-21-sqqq"
SQQQ_SYMBOL = "SQQQ"


class FakeWisePick:
    def __init__(self, ecu: dict) -> None:
        self._ecu = ecu
        self.feedback_calls: list[dict] = []

    def decide(self, task: str) -> dict:
        return dict(self._ecu)

    def feedback(self, decision_id: str, success: bool, latency_ms: int, **kwargs) -> dict:
        self.feedback_calls.append(
            {"decision_id": decision_id, "success": success, "latency_ms": latency_ms, **kwargs}
        )
        return {"ok": True}


def _id_kwargs(**extra: object) -> dict:
    base = {
        "session_id": "sess-abc",
        "turn_id": 3,
        "task": "Summarize the quarterly report",
        "capability_id": "general_content",
        "provider": "chatgpt",
        "start_time_ms": FIXED_START_TIME_MS,
        "constraints": {"max_cost": 1.0},
    }
    base.update(extra)
    return base


def test_wisepick_to_safeagent_request_id_deterministic_with_fixed_start_time():
    first = wisepick_to_safeagent_request_id(**_id_kwargs())
    second = wisepick_to_safeagent_request_id(**_id_kwargs())
    assert first == second
    assert len(first) == 36


def test_wisepick_to_safeagent_request_id_stable_ignores_local_clock():
    """Same fixed start_time → same request_id even if default clock would differ."""
    with patch("adapters.safeagent_adapter.time.time", return_value=9999999999.0):
        a = wisepick_to_safeagent_request_id(**_id_kwargs())
    with patch("adapters.safeagent_adapter.time.time", return_value=1.0):
        b = wisepick_to_safeagent_request_id(**_id_kwargs())
    assert a == b


def test_wisepick_to_safeagent_request_id_changes_with_turn():
    a = wisepick_to_safeagent_request_id(**_id_kwargs(turn_id=1))
    b = wisepick_to_safeagent_request_id(**_id_kwargs(turn_id=2))
    assert a != b


def test_wisepick_to_safeagent_request_id_changes_with_start_time():
    a = wisepick_to_safeagent_request_id(**_id_kwargs(start_time_ms=FIXED_START_TIME_MS))
    b = wisepick_to_safeagent_request_id(**_id_kwargs(start_time_ms=FIXED_START_TIME_MS + 1))
    assert a != b


def test_wisepick_to_safeagent_request_id_ignores_decision_id_not_in_preimage():
    ecu_a = {
        "decision_id": "dec_first",
        "capability_id": "search_files",
        "provider": "github_copilot",
        "callable": True,
        "confidence": 0.8,
        "reason": "match",
        "execution_type": "api",
    }
    ecu_b = {**ecu_a, "decision_id": "dec_second", "confidence": 0.9}
    adapter = SafeAgentAdapter(wisepick=FakeWisePick(ecu_a), runtime=StubSafeAgentRuntime())  # type: ignore[arg-type]
    req_a = adapter.ecu_to_dispatch_request(
        ecu_a,
        task="find TODOs",
        session_id="s1",
        turn_id=1,
        start_time=FIXED_START_TIME_MS,
    )
    req_b = adapter.ecu_to_dispatch_request(
        ecu_b,
        task="find TODOs",
        session_id="s1",
        turn_id=1,
        start_time=FIXED_START_TIME_MS,
    )
    assert req_a["request_id"] == req_b["request_id"]
    assert req_a["decision_id"] != req_b["decision_id"]
    assert req_a["startTime_ms"] == int(FIXED_START_TIME_MS)


def test_ecu_to_dispatch_request_includes_deterministic_request_id():
    ecu = {
        "decision_id": "dec_xyz",
        "capability_id": "audio_transcription",
        "provider": "feishu_minutes",
        "execution_type": "api",
        "callable": True,
        "confidence": 0.91,
        "reason": "capability_match",
    }
    adapter = SafeAgentAdapter(
        wisepick=FakeWisePick(ecu),  # type: ignore[arg-type]
        runtime=StubSafeAgentRuntime(),
    )
    dispatch = adapter.ecu_to_dispatch_request(
        ecu,
        task="Transcribe meeting",
        session_id="session-1",
        turn_id="turn-0",
        start_time=FIXED_START_TIME_MS,
    )
    expected_id = wisepick_to_safeagent_request_id(
        session_id="session-1",
        turn_id="turn-0",
        task="Transcribe meeting",
        capability_id="audio_transcription",
        provider="feishu_minutes",
        start_time_ms=FIXED_START_TIME_MS,
    )
    assert dispatch["request_id"] == expected_id
    assert dispatch["startTime_ms"] == int(FIXED_START_TIME_MS)
    assert dispatch["decision_id"] == "dec_xyz"


def test_select_and_execute_passes_request_id_and_start_time_to_runtime():
    ecu = {
        "decision_id": "dec_run",
        "capability_id": "general_content",
        "provider": "chatgpt",
        "execution_type": "api",
        "callable": True,
        "confidence": 0.75,
        "reason": "general",
    }
    runtime = StubSafeAgentRuntime()
    wp = FakeWisePick(ecu)
    adapter = SafeAgentAdapter(wisepick=wp, runtime=runtime)  # type: ignore[arg-type]

    out = adapter.select_and_execute(
        "Write a one-line summary",
        session_id="sess-99",
        turn_id=7,
        start_time=FIXED_START_TIME_MS,
    )

    assert out["request_id"]
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["request_id"] == out["request_id"]
    assert runtime.calls[0]["startTime_ms"] == int(FIXED_START_TIME_MS)
    assert runtime.calls[0]["decision_id"] == "dec_run"
    assert len(wp.feedback_calls) == 1


def test_select_and_execute_non_callable_skips_runtime():
    ecu = {
        "decision_id": "dec_nc",
        "capability_id": "none",
        "provider": "none",
        "callable": False,
        "confidence": 0.0,
        "reason": "no match",
        "execution_type": "api",
    }
    runtime = StubSafeAgentRuntime()
    wp = FakeWisePick(ecu)
    adapter = SafeAgentAdapter(wisepick=wp, runtime=runtime)  # type: ignore[arg-type]

    out = adapter.select_and_execute(
        "unknown task",
        session_id="s",
        turn_id=1,
        start_time=FIXED_START_TIME_MS,
    )

    assert runtime.calls == []
    assert out["trace"]["error"] == "ECU callable=false"


# --- Fault-injection: Exit-side 422 cascade & phantom position (May 21 replay) ---


@dataclass
class PhantomPositionLedger:
    """Quarantined broker-side ghost state after failed exit; must not gate entry."""

    quarantined: Dict[str, str] = field(default_factory=dict)

    def record_phantom(self, symbol: str, reason: str) -> None:
        self.quarantined[symbol] = reason

    def entry_blocked(self, symbol: str) -> bool:
        """Phantom state is isolated — entry must never be blocked by quarantine."""
        return False


class Broker422ExitFaultSimulator:
    """
    Mock broker API (fault-injection).

    Exit path: attempt 1 → transient network jitter (503); retries → 422 Unprocessable Entity.
    Entry path: succeeds when not explicitly blocked by a separate guard bug.
    """

    HTTP_JITTER = 503
    HTTP_UNPROCESSABLE = 422

    def __init__(self, ledger: PhantomPositionLedger) -> None:
        self._ledger = ledger
        self.exit_attempts: Dict[str, int] = {}
        self.entry_executed: Set[str] = set()

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        ctx = request.get("context") if isinstance(request.get("context"), dict) else {}
        side = str(ctx.get("side") or "").upper()
        symbol = str(ctx.get("symbol") or SQQQ_SYMBOL)
        request_id = str(request.get("request_id") or "")

        if side == "EXIT":
            return self._exit_side(request_id, symbol)
        if side == "ENTRY":
            return self._entry_side(request_id, symbol)
        return {"success": False, "http_status": 400, "error": "unknown_side"}

    def _exit_side(self, request_id: str, symbol: str) -> Dict[str, Any]:
        n = self.exit_attempts.get(request_id, 0) + 1
        self.exit_attempts[request_id] = n
        if n == 1:
            return {
                "success": False,
                "disposition": "RUN",
                "http_status": self.HTTP_JITTER,
                "error": "network_jitter",
                "request_id": request_id,
            }
        self._ledger.record_phantom(symbol, "422_cascade_after_jitter")
        return {
            "success": False,
            "disposition": "RUN",
            "http_status": self.HTTP_UNPROCESSABLE,
            "error": "Unprocessable Entity",
            "request_id": request_id,
            "phantom_position": True,
        }

    def _entry_side(self, request_id: str, symbol: str) -> Dict[str, Any]:
        if self._ledger.entry_blocked(symbol):
            return {
                "success": False,
                "disposition": "BLOCKED",
                "http_status": 409,
                "error": "entry_blocked_by_phantom",
                "request_id": request_id,
            }
        self.entry_executed.add(request_id)
        return {
            "success": True,
            "disposition": "RUN",
            "http_status": 200,
            "request_id": request_id,
            "output": {"symbol": symbol, "side": "ENTRY"},
        }


class ExitDuplicateGuardRuntime:
    """
    Adapter guard: after terminal 422 on an exit request_id, intercept duplicate exit dispatches.

    Mirrors production SafeAgent idempotency — duplicate logical exit must not re-hit the broker.
    """

    def __init__(self, inner: Broker422ExitFaultSimulator) -> None:
        self._inner = inner
        self._terminal_exit_422: Set[str] = set()
        self.intercepted_duplicate_exits: list[str] = []

    @property
    def broker(self) -> Broker422ExitFaultSimulator:
        return self._inner

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        ctx = request.get("context") if isinstance(request.get("context"), dict) else {}
        side = str(ctx.get("side") or "").upper()
        request_id = str(request.get("request_id") or "")

        if side == "EXIT" and request_id in self._terminal_exit_422:
            self.intercepted_duplicate_exits.append(request_id)
            return {
                "success": False,
                "disposition": "BLOCKED",
                "http_status": 422,
                "error": "duplicate_exit_intercepted",
                "request_id": request_id,
                "guard": "exit_duplicate_after_422",
            }

        result = self._inner.execute(request)
        if (
            side == "EXIT"
            and not result.get("success")
            and int(result.get("http_status") or 0) == Broker422ExitFaultSimulator.HTTP_UNPROCESSABLE
        ):
            self._terminal_exit_422.add(request_id)
        return result


class ScenarioWisePick:
    """Route exit vs entry intents to distinct ECU shapes (new decision_id each call)."""

    def __init__(self) -> None:
        self._seq = 0
        self.feedback_calls: list[dict] = []

    def decide(self, task: str) -> dict:
        self._seq += 1
        task_l = (task or "").lower()
        is_exit = any(k in task_l for k in ("close", "exit", "flat", "liquidate"))
        cap = "position_exit" if is_exit else "position_entry"
        return {
            "decision_id": f"dec_{'exit' if is_exit else 'entry'}_{self._seq:03d}",
            "capability_id": cap,
            "provider": "alpaca_broker",
            "execution_type": "api",
            "callable": True,
            "confidence": 0.92 if is_exit else 0.88,
            "reason": "capability_match",
        }

    def feedback(self, decision_id: str, success: bool, latency_ms: int, **kwargs: Any) -> dict:
        self.feedback_calls.append(
            {"decision_id": decision_id, "success": success, "latency_ms": latency_ms, **kwargs}
        )
        return {"ok": True}


def test_exit_side_422_cascade_and_phantom_position_isolation():
    """
    Fault-injection replay: May 21 SQQQ exit 422 cascade must not block later entry.

    Assert 1: duplicate exit after terminal 422 is intercepted by ExitDuplicateGuardRuntime.
    Assert 2: quarantined phantom position does not block a new-turn entry dispatch.
    """
    ledger = PhantomPositionLedger()
    broker = Broker422ExitFaultSimulator(ledger)
    guard = ExitDuplicateGuardRuntime(broker)
    wisepick = ScenarioWisePick()
    adapter = SafeAgentAdapter(wisepick=wisepick, runtime=guard)  # type: ignore[arg-type]

    exit_task = "Close / flatten SQQQ position (exit side)"
    exit_ctx = {"side": "EXIT", "symbol": SQQQ_SYMBOL}
    exit_turn = "turn-exit-sqqq"

    # Attempt 1: network jitter (503) — orchestrator will retry with same turn anchor.
    out_jitter = adapter.select_and_execute(
        exit_task,
        session_id=SQQQ_SESSION,
        turn_id=exit_turn,
        context=exit_ctx,
        start_time=SQQQ_EXIT_TURN_MS,
    )
    assert out_jitter["execution"]["http_status"] == 503
    assert broker.exit_attempts[out_jitter["request_id"]] == 1

    # Attempt 2: broker 422 cascade → phantom position recorded (quarantined, not entry gate).
    out_422 = adapter.select_and_execute(
        exit_task,
        session_id=SQQQ_SESSION,
        turn_id=exit_turn,
        context=exit_ctx,
        start_time=SQQQ_EXIT_TURN_MS,
    )
    assert out_422["request_id"] == out_jitter["request_id"]
    assert out_422["execution"]["http_status"] == 422
    assert out_422["execution"].get("phantom_position") is True
    assert SQQQ_SYMBOL in ledger.quarantined

    # Attempt 3: duplicate exit — guard must intercept (Assert 1).
    out_dup = adapter.select_and_execute(
        exit_task,
        session_id=SQQQ_SESSION,
        turn_id=exit_turn,
        context=exit_ctx,
        start_time=SQQQ_EXIT_TURN_MS,
    )
    assert out_dup["request_id"] == out_jitter["request_id"]
    assert guard.intercepted_duplicate_exits == [out_jitter["request_id"]]
    assert out_dup["execution"]["disposition"] == "BLOCKED"
    assert out_dup["execution"].get("guard") == "exit_duplicate_after_422"
    assert broker.exit_attempts[out_jitter["request_id"]] == 2  # no third broker call

    # New turn: legitimate entry must proceed despite phantom quarantine (Assert 2).
    entry_task = "Open long SQQQ (entry side)"
    entry_ctx = {"side": "ENTRY", "symbol": SQQQ_SYMBOL}
    out_entry = adapter.select_and_execute(
        entry_task,
        session_id=SQQQ_SESSION,
        turn_id="turn-entry-sqqq",
        context=entry_ctx,
        start_time=SQQQ_EXIT_TURN_MS + 60_000,
    )
    assert out_entry["request_id"] != out_jitter["request_id"]
    assert ledger.entry_blocked(SQQQ_SYMBOL) is False
    assert out_entry["execution"]["success"] is True
    assert out_entry["execution"]["http_status"] == 200
    assert out_entry["execution"].get("error") != "entry_blocked_by_phantom"
    assert out_entry["request_id"] in broker.entry_executed

    # Feedback closed for each adapter attempt (learning path stays open for entry).
    assert len(wisepick.feedback_calls) >= 4
    entry_feedback = [f for f in wisepick.feedback_calls if f["success"] is True]
    assert any(f["decision_id"].startswith("dec_entry_") for f in entry_feedback)

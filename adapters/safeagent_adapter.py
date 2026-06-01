"""
SafeAgent adapter: bridges WisePick ECU decisions to SafeAgent runtime dispatch.

Key guarantee: request_id is deterministic from (session_id, turn_id, task,
capability_id, provider, start_time_ms) — never from the local clock or
decision_id, so retries within the same orchestrator turn produce the same id.
"""

import time
import uuid
from typing import Any, Dict, List, Optional


def wisepick_to_safeagent_request_id(
    session_id: str,
    turn_id: Any,
    task: str,
    capability_id: str,
    provider: str,
    start_time_ms: float,
    constraints: Optional[Dict] = None,
) -> str:
    """Return a stable UUID-format request_id for a (session, turn, capability) triple."""
    preimage = f"{session_id}:{turn_id}:{task}:{capability_id}:{provider}:{int(start_time_ms)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, preimage))


class StubSafeAgentRuntime:
    """In-memory stub for unit tests — records every execute() call."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(request)
        return {"success": True, "request_id": request.get("request_id"), "output": "stub"}


class SafeAgentAdapter:
    """
    Wraps a WisePick selector and a SafeAgent runtime into a single call surface.

    select_and_execute():
      1. Ask WisePick to pick a capability (ECU).
      2. Build a deterministic dispatch request.
      3. Hand it to the runtime (which enforces exactly-once).
      4. Feed the outcome back to WisePick.
    """

    def __init__(self, wisepick: Any, runtime: Any) -> None:
        self._wisepick = wisepick
        self._runtime = runtime

    def ecu_to_dispatch_request(
        self,
        ecu: Dict[str, Any],
        task: str,
        session_id: str,
        turn_id: Any,
        start_time: float,
        context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        request_id = wisepick_to_safeagent_request_id(
            session_id=session_id,
            turn_id=turn_id,
            task=task,
            capability_id=ecu.get("capability_id", ""),
            provider=ecu.get("provider", ""),
            start_time_ms=start_time,
        )
        req: Dict[str, Any] = {
            "request_id": request_id,
            "startTime_ms": int(start_time),
            "decision_id": ecu.get("decision_id"),
            "capability_id": ecu.get("capability_id"),
            "provider": ecu.get("provider"),
            "task": task,
            "session_id": session_id,
            "turn_id": turn_id,
        }
        if context is not None:
            req["context"] = context
        return req

    def select_and_execute(
        self,
        task: str,
        session_id: str,
        turn_id: Any,
        start_time: float,
        context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        t0 = time.time()
        ecu = self._wisepick.decide(task)

        if not ecu.get("callable"):
            return {
                "request_id": None,
                "trace": {"error": "ECU callable=false", "ecu": ecu},
            }

        dispatch = self.ecu_to_dispatch_request(
            ecu=ecu,
            task=task,
            session_id=session_id,
            turn_id=turn_id,
            start_time=start_time,
            context=context,
        )

        execution = self._runtime.execute(dispatch)
        latency_ms = int((time.time() - t0) * 1000)

        self._wisepick.feedback(
            ecu["decision_id"],
            success=bool(execution.get("success")),
            latency_ms=latency_ms,
        )

        return {
            "request_id": dispatch["request_id"],
            "execution": execution,
            "trace": {"ecu": ecu, "dispatch": dispatch},
        }

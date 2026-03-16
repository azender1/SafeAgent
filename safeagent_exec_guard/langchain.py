from __future__ import annotations

from typing import Any, Callable, Dict

from safeagent_exec_guard.decorators import safeagent_guard


class SafeAgentTool:
    """
    Minimal LangChain-style adapter.

    Wrap a side-effecting function so execution is guarded by SafeAgent.

    Example:
        tool = SafeAgentTool(
            name="send_email",
            func=send_email,
            registry=registry,
            request_id_fn=lambda payload: f"email:{payload['to']}",
        )

        receipt = tool.run({"to": "user@example.com"})
    """

    def __init__(
        self,
        *,
        name: str,
        func: Callable[[Dict[str, Any]], Any],
        registry: Any,
        request_id_fn: Callable[[Dict[str, Any]], str],
    ) -> None:
        self.name = name
        self.func = func
        self.registry = registry
        self.request_id_fn = request_id_fn

        @safeagent_guard(
            registry=self.registry,
            action=self.name,
            request_id_fn=lambda payload: self.request_id_fn(payload),
        )
        def guarded(payload: Dict[str, Any]) -> Any:
            return self.func(payload)

        self._guarded = guarded

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._guarded(payload)
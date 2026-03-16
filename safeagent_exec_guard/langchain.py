from __future__ import annotations

from typing import Any, Callable, Dict


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

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = self.request_id_fn(payload)
        return self.registry.execute(
            request_id=request_id,
            action=self.name,
            payload=payload,
            execute_fn=self.func,
        )
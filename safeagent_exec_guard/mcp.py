from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, Dict, Optional

from settlement.settlement_requests import SettlementRequestRegistry


def _build_payload(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build a deterministic payload dict from the decorated function's bound arguments."""
    sig = inspect.signature(func)
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def safe_mcp_tool(
    *,
    registry: Optional[Any] = None,
    action: Optional[str] = None,
    request_id_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Dict[str, Any]]]:
    """Wrap an MCP-style tool function with SafeAgent."""
    local_registry = registry or SettlementRequestRegistry()

    def decorator(func: Callable[..., Any]) -> Callable[..., Dict[str, Any]]:
        action_name = action or func.__name__

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            payload = _build_payload(func, *args, **kwargs)

            if request_id_fn is None:
                raise ValueError("safe_mcp_tool requires a deterministic request_id_fn")

            request_id = request_id_fn(payload)

            def execute_fn(p: Dict[str, Any]) -> Any:
                return func(**p)

            return local_registry.execute(
                request_id=request_id,
                action=action_name,
                payload=payload,
                execute_fn=execute_fn,
            )

        return wrapper

    return decorator
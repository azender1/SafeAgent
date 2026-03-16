from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Dict, Optional


def safeagent_guard(
    *,
    registry: Any,
    action: str,
    request_id_fn: Callable[..., str],
) -> Callable[[Callable[..., Any]], Callable[..., Dict[str, Any]]]:
    """
    Decorator that routes a side-effecting function through SafeAgent.

    Required:
    - registry: a SettlementRequestRegistry instance
    - action: action name to store in the execution receipt
    - request_id_fn: function that deterministically derives a request_id
      from the decorated function's arguments

    The decorated function should typically accept a single payload dict.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Dict[str, Any]]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            request_id = request_id_fn(*args, **kwargs)

            payload: Dict[str, Any]
            if "payload" in kwargs and isinstance(kwargs["payload"], dict):
                payload = kwargs["payload"]
            elif len(args) > 0 and isinstance(args[0], dict):
                payload = args[0]
            else:
                payload = {"args": list(args), "kwargs": kwargs}

            def execute_fn(p: Dict[str, Any]) -> Any:
                return func(p)

            return registry.execute(
                request_id=request_id,
                action=action,
                payload=payload,
                execute_fn=execute_fn,
            )

        return wrapper

    return decorator
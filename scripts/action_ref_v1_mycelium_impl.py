"""stdin/stdout adapter for SafeAgent's Mycelium action-ref-v1 implementation."""
from __future__ import annotations

import json
import sys

from safeagent_exec_guard.mycelium_trail import compute_action_ref


def main() -> int:
    try:
        preimage = json.load(sys.stdin)
        print(
            compute_action_ref(
                preimage["agent_id"],
                preimage["action_type"],
                preimage["scope"],
                preimage["timestamp"],
            )
        )
        return 0
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""stdin/stdout adapter for the frozen action-ref-v1 conformance Action."""
from __future__ import annotations

import json
import sys

from safeagent_exec_guard.attestation_gate import compute_action_ref


def main() -> int:
    try:
        preimage = json.load(sys.stdin)
        print(compute_action_ref(preimage))
        return 0
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

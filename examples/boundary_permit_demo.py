"""Minimal SafeAgent Boundary demonstration. No external service required."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from safeagent_exec_guard.boundary import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    PermitDenied,
    SQLitePermitStore,
)


def main() -> None:
    authority = PermitAuthority.generate(issuer="demo-control-plane")
    request = ActionRequest(
        principal="demo-agent",
        run_id="demo-run-001",
        action="payment.send",
        target="customer:123",
        payload={"amount_minor": 2500, "currency": "USD"},
    )
    permit = authority.issue(request, ttl_seconds=120)

    with tempfile.TemporaryDirectory() as tmp:
        store = SQLitePermitStore(Path(tmp) / "boundary.db")
        gateway = BoundaryGateway(authority.public_key_hex(), store)

        first = gateway.dispatch(
            permit,
            request,
            lambda approved: {"provider_id": "demo_payment_001", "accepted": True},
        )
        print(json.dumps(first.__dict__, indent=2, sort_keys=True))

        try:
            gateway.dispatch(permit, request, lambda _: {"duplicate": True})
        except PermitDenied as exc:
            print(json.dumps({"second_attempt": "DENIED", "reason": exc.reason}, indent=2))


if __name__ == "__main__":
    main()

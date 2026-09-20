"""End-to-end SafeAgent + Stripe Test Mode verification.

This creates one USD 1.00 Stripe test PaymentIntent, proves that the signed
permit cannot be replayed, then reads the provider result back authoritatively.
It refuses live Stripe keys.
"""
from __future__ import annotations

import os
import tempfile
import uuid

from safeagent_exec_guard import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    PermitDenied,
    SQLitePermitStore,
    SQLiteStripeStore,
    StripePaymentIntentGateway,
)


def main() -> None:
    api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not api_key.startswith("sk_test_"):
        raise SystemExit("Set STRIPE_SECRET_KEY to a Stripe Test Mode secret key")

    work_dir = tempfile.mkdtemp(prefix="safeagent-stripe-")
    authority = PermitAuthority.generate(issuer="stripe-test")
    boundary = BoundaryGateway(
        authority.public_key_hex(),
        SQLitePermitStore(os.path.join(work_dir, "permits.db")),
    )
    stripe_store = SQLiteStripeStore(os.path.join(work_dir, "stripe.db"))
    gateway = StripePaymentIntentGateway.from_api_key(
        boundary, api_key, stripe_store
    )

    request = ActionRequest(
        principal="payment-agent",
        run_id=f"stripe-confirmed-{uuid.uuid4()}",
        action="stripe.payment_intent.create",
        target="stripe:test-payment",
        payload={
            "amount": 100,
            "currency": "usd",
            "payment_method_types": ["card"],
            "payment_method": "pm_card_visa",
            "confirm": True,
            "description": "SafeAgent confirmed payment test",
        },
    )
    token = authority.issue(request, ttl_seconds=300)
    first = gateway.dispatch(token, request)
    print("FIRST:", first.decision)

    if first.result is None:
        operation = stripe_store.get(first.permit_id)
        error = operation["last_error"] if operation else "operation not found"
        print("PROVIDER ERROR:", error)
        raise SystemExit(1)

    print("PAYMENT INTENT:", first.result["id"])
    print("STRIPE STATUS:", first.result["status"])

    try:
        gateway.dispatch(token, request)
        raise SystemExit("REPLAY ERROR: duplicate dispatch was allowed")
    except PermitDenied as exc:
        print("REPLAY: BLOCKED -", exc.reason)

    observation = gateway.reconcile(first.permit_id)
    print("RECONCILIATION:", observation.state)
    print("SOURCE:", observation.source)
    print("DATABASE:", work_dir)

    if first.result["status"] != "succeeded" or observation.state != "CONFIRMED":
        raise SystemExit("Stripe did not reach the expected confirmed state")


if __name__ == "__main__":
    main()

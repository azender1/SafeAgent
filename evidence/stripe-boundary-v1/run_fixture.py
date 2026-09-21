#!/usr/bin/env python3
"""Deterministic SafeAgent-to-Stripe execution-boundary fixture."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import rfc8785

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from safeagent_exec_guard.boundary import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    PermitDenied,
    SQLitePermitStore,
    verify_permit,
)
from safeagent_exec_guard.stripe_gateway import SQLiteStripeStore, StripePaymentIntentGateway


NOW = 1_800_000_000


class FakeStripePaymentIntents:
    """Minimal deterministic model of Stripe idempotent PaymentIntent create."""

    def __init__(self) -> None:
        self.by_key: dict[str, dict] = {}
        self.create_calls: list[dict] = []
        self.retrieve_calls: list[str] = []
        self.lose_next_response = False

    def create(self, **params):
        self.create_calls.append(copy.deepcopy(params))
        key = params.pop("idempotency_key")
        value = self.by_key.get(key)
        if value is None:
            value = {
                "id": f"pi_fixture_{len(self.by_key) + 1}",
                "object": "payment_intent",
                "status": "requires_payment_method",
                "amount": params["amount"],
                "currency": params["currency"],
                "metadata": params["metadata"],
            }
            self.by_key[key] = value
        if self.lose_next_response:
            self.lose_next_response = False
            raise TimeoutError("response lost")
        return copy.deepcopy(value)

    def retrieve(self, payment_intent_id: str):
        self.retrieve_calls.append(payment_intent_id)
        return copy.deepcopy(
            next(value for value in self.by_key.values() if value["id"] == payment_intent_id)
        )


def digest(value) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def jcs_parity() -> tuple[dict, dict]:
    fixture_root = Path(__file__).resolve().parent
    vectors = json.loads((fixture_root / "jcs_vectors.json").read_text(encoding="utf-8"))
    completed = subprocess.run(
        ["node", str(fixture_root / "jcs_parity.js"), str(fixture_root / "jcs_vectors.json")],
        check=True,
        capture_output=True,
        text=True,
    )
    node_results = json.loads(completed.stdout)
    python_results = {
        name: {
            "canonical": rfc8785.dumps(value).decode("utf-8"),
            "sha256": digest(value),
        }
        for name, value in vectors.items()
    }
    return python_results, node_results


def build(root: Path, name: str):
    authority = PermitAuthority.generate(issuer="stripe-fixture")
    permit_store = SQLitePermitStore(root / f"{name}-permits.db")
    boundary = BoundaryGateway(authority.public_key_hex(), permit_store)
    stripe_store = SQLiteStripeStore(root / f"{name}-stripe.db")
    stripe = FakeStripePaymentIntents()
    gateway = StripePaymentIntentGateway(boundary, stripe, stripe_store)
    request = ActionRequest(
        principal="invoice-agent",
        run_id=f"{name}-run",
        action="stripe.payment_intent.create",
        target="customer:fixture",
        payload={
            "amount": 100,
            "currency": "USD",
            "payment_method_types": ["card"],
            "metadata": {"fixture": "stripe-boundary-v1"},
        },
    )
    token = authority.issue(request, now=NOW, ttl_seconds=300)
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    return permit_store, stripe_store, stripe, gateway, request, token, permit_id


def check(condition: bool, name: str, passed: list[str]) -> None:
    if not condition:
        raise AssertionError(name)
    passed.append(name)


def main() -> int:
    passed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="safeagent-stripe-fixture-") as directory:
        root = Path(directory)

        permit_store, stripe_store, stripe, gateway, request, token, permit_id = build(root, "first")
        receipt = gateway.dispatch(token, request, now=NOW + 1)
        check(receipt.decision == "SETTLED", "first call settles", passed)
        check(
            stripe.create_calls[0]["idempotency_key"] == f"safeagent:{permit_id}",
            "permit ID binds Stripe idempotency key",
            passed,
        )
        try:
            gateway.dispatch(token, request, now=NOW + 2)
            raise AssertionError("replay unexpectedly dispatched")
        except PermitDenied as exc:
            check(str(exc) == "permit_already_consumed", "permit replay blocked", passed)
        check(len(stripe.create_calls) == 1, "blocked replay never reaches provider", passed)

        stripe.by_key[f"safeagent:{permit_id}"]["status"] = "succeeded"
        observation = gateway.reconcile(permit_id, now=NOW + 3)
        check(observation.state == "CONFIRMED", "provider readback confirms outcome", passed)
        check(observation.source == "retrieve", "known provider object is retrieved", passed)

        _, _, stripe2, gateway2, request2, token2, permit_id2 = build(root, "lost")
        stripe2.lose_next_response = True
        lost = gateway2.dispatch(token2, request2, now=NOW + 1)
        check(
            lost.decision == "PENDING_RECONCILIATION",
            "lost response remains pending reconciliation",
            passed,
        )
        replayed = gateway2.reconcile(permit_id2, now=NOW + 2)
        check(
            replayed.source == "idempotent_replay" and len(stripe2.by_key) == 1,
            "reconciliation reuses one provider object",
            passed,
        )

        check(
            digest({"amount": 100, "currency": "usd"})
            == digest({"currency": "usd", "amount": 100.0}),
            "JCS removes key-order and equivalent-number drift",
            passed,
        )
        check(
            digest({"amount": 100}) != digest({"amount": "100"}),
            "material JSON type change receives a new identity",
            passed,
        )

        python_jcs, node_jcs = jcs_parity()
        for vector_name, label in (
            ("ascii_control", "Python/Node ASCII control parity"),
            ("bmp_control", "Python/Node all-BMP control parity"),
            ("supplementary_minimal_pair", "Python/Node supplementary-plane parity"),
            ("payment_metadata_pair", "Python/Node payment metadata parity"),
        ):
            check(
                python_jcs[vector_name] == node_jcs[vector_name],
                label,
                passed,
            )

        minimal = python_jcs["supplementary_minimal_pair"]["canonical"]
        check(
            minimal.index("\U00010000") < minimal.index("\ufffd"),
            "RFC 8785 UTF-16 order puts U+10000 before U+FFFD",
            passed,
        )
        naive = json.dumps(
            {key: {"\ufffd": "replacement", "\U00010000": "supplementary"}[key]
             for key in sorted(("\ufffd", "\U00010000"))},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        check(
            hashlib.sha256(naive.encode("utf-8")).hexdigest()
            != python_jcs["supplementary_minimal_pair"]["sha256"],
            "naive Python code-point ordering is detected",
            passed,
        )

    result = {
        "result": "PASS",
        "invariants_passed": len(passed),
        "invariants": passed,
    }
    print(json.dumps(result, indent=2))
    print(f"RESULT: {len(passed)}/16 invariants passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

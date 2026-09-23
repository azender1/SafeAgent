from __future__ import annotations

import copy
import hashlib
import hmac
import json
import time

import pytest
import stripe

from safeagent_exec_guard.boundary import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    PermitDenied,
    SQLitePermitStore,
    verify_permit,
)
from safeagent_exec_guard.stripe_gateway import (
    SQLiteStripeStore,
    StripePaymentIntentGateway,
    StripeWebhookVerifier,
)


NOW = 1_800_000_000


class FakeStripePaymentIntents:
    def __init__(self):
        self.by_key = {}
        self.create_calls = []
        self.retrieve_calls = []
        self.lose_next_response = False

    def create(self, **params):
        self.create_calls.append(copy.deepcopy(params))
        key = params.pop("idempotency_key")
        prior = self.by_key.get(key)
        if prior is None:
            prior = {
                "id": f"pi_{len(self.by_key) + 1}",
                "object": "payment_intent",
                "status": "requires_payment_method",
                "amount": params["amount"],
                "currency": params["currency"],
                "metadata": params["metadata"],
            }
            self.by_key[key] = prior
        if self.lose_next_response:
            self.lose_next_response = False
            raise TimeoutError("response lost")
        return copy.deepcopy(prior)

    def retrieve(self, payment_intent_id):
        self.retrieve_calls.append(payment_intent_id)
        return copy.deepcopy(
            next(value for value in self.by_key.values() if value["id"] == payment_intent_id)
        )


@pytest.fixture()
def stripe_system(tmp_path):
    authority = PermitAuthority.generate(issuer="payments-control")
    permit_store = SQLitePermitStore(tmp_path / "boundary.db")
    boundary = BoundaryGateway(authority.public_key_hex(), permit_store)
    stripe_store = SQLiteStripeStore(tmp_path / "stripe.db")
    stripe = FakeStripePaymentIntents()
    gateway = StripePaymentIntentGateway(boundary, stripe, stripe_store)
    request = ActionRequest(
        principal="invoice-agent",
        run_id="run-001",
        action="stripe.payment_intent.create",
        target="customer:cus_123",
        payload={
            "amount": 2500,
            "currency": "USD",
            "customer": "cus_123",
            "payment_method_types": ["card"],
            "metadata": {"invoice_id": "inv_456"},
        },
    )
    token = authority.issue(request, now=NOW, ttl_seconds=300)
    return authority, permit_store, stripe_store, stripe, gateway, request, token


def test_permit_gates_payment_intent_and_supplies_native_idempotency(stripe_system):
    authority, _, stripe_store, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id

    receipt = gateway.dispatch(token, request, now=NOW + 1)

    assert receipt.decision == "SETTLED"
    assert receipt.result["id"] == "pi_1"
    assert len(stripe.create_calls) == 1
    call = stripe.create_calls[0]
    assert call["idempotency_key"] == f"safeagent:{permit_id}"
    assert call["metadata"] == {
        "invoice_id": "inv_456",
        "safeagent_permit_id": permit_id,
        "safeagent_run_id": "run-001",
    }
    operation = stripe_store.get(permit_id)
    assert operation["payment_intent_id"] == "pi_1"
    assert operation["reconciliation_state"] == "OPEN"
    assert operation["boundary_decision"] == "SETTLED"


def test_provider_create_error_is_durable_for_operator_diagnosis(stripe_system):
    authority, _, stripe_store, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    stripe.lose_next_response = True

    receipt = gateway.dispatch(token, request, now=NOW + 1)

    assert receipt.decision == "PENDING_RECONCILIATION"
    operation = stripe_store.get(permit_id)
    assert operation["reconciliation_state"] == "UNCERTAIN"
    assert operation["last_source"] == "create"
    assert operation["last_error"] == "response lost"


def test_replay_never_reaches_stripe(stripe_system):
    _, _, _, stripe, gateway, request, token = stripe_system
    gateway.dispatch(token, request, now=NOW + 1)

    with pytest.raises(PermitDenied, match="permit_already_consumed"):
        gateway.dispatch(token, request, now=NOW + 2)

    assert len(stripe.create_calls) == 1


def test_upstream_check_denies_before_permit_consumption(stripe_system):
    authority, permit_store, _, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id

    with pytest.raises(PermitDenied, match="upstream_revoked"):
        gateway.dispatch(token, request, now=NOW + 1,
                         pre_dispatch=lambda: (_ for _ in ()).throw(PermitDenied("upstream_revoked")))

    assert permit_store.get(permit_id) is None
    assert stripe.create_calls == []


def test_upstream_expiry_between_check_and_effect_fails_closed(stripe_system):
    authority, permit_store, stripe_store, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    checks = 0

    def upstream_check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise PermitDenied("upstream_expired")

    receipt = gateway.dispatch(token, request, now=NOW + 1, pre_dispatch=upstream_check)
    assert checks == 2
    assert receipt.decision == "PENDING_RECONCILIATION"
    assert permit_store.get(permit_id)["uses"] == 1
    assert stripe_store.get(permit_id)["reconciliation_state"] == "UNCERTAIN"
    assert stripe.create_calls == []
    with pytest.raises(PermitDenied, match="permit_already_consumed"):
        gateway.dispatch(token, request, now=NOW + 2)


def test_lost_create_response_reconciles_with_same_request_and_key(stripe_system):
    authority, permit_store, stripe_store, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    stripe.lose_next_response = True

    receipt = gateway.dispatch(token, request, now=NOW + 1)
    observation = gateway.reconcile(permit_id, now=NOW + 2)

    assert receipt.decision == "PENDING_RECONCILIATION"
    assert permit_store.get(permit_id)["status"] == "PENDING_RECONCILIATION"
    assert observation.payment_intent_id == "pi_1"
    assert observation.source == "idempotent_replay"
    assert len(stripe.by_key) == 1
    assert len(stripe.create_calls) == 2
    assert stripe.create_calls[0] == stripe.create_calls[1]
    assert stripe_store.get(permit_id)["reconciliation_state"] == "OPEN"


def test_known_payment_intent_is_retrieved_for_reconciliation(stripe_system):
    authority, _, _, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    gateway.dispatch(token, request, now=NOW + 1)
    stripe.by_key[f"safeagent:{permit_id}"]["status"] = "succeeded"

    observation = gateway.reconcile(permit_id, now=NOW + 2)

    assert observation.state == "CONFIRMED"
    assert observation.source == "retrieve"
    assert stripe.retrieve_calls == ["pi_1"]


def test_signed_payload_mutation_is_denied_before_stripe_or_correlation(stripe_system):
    authority, _, stripe_store, stripe, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    mutated = ActionRequest(
        principal=request.principal,
        run_id=request.run_id,
        action=request.action,
        target=request.target,
        payload={**request.payload, "amount": 250000},
    )

    with pytest.raises(PermitDenied, match="payload_sha256_mismatch"):
        gateway.dispatch(token, mutated, now=NOW + 1)

    assert stripe.create_calls == []
    assert stripe_store.get(permit_id) is None


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"amount": 0, "currency": "USD"}, "invalid_stripe_amount"),
        ({"amount": 100, "currency": "US"}, "invalid_stripe_currency"),
        ({"amount": 100, "currency": "USD", "api_key": "stolen"}, "unsupported_stripe_parameter"),
    ],
)
def test_invalid_or_credential_bearing_payload_is_denied(tmp_path, payload, reason):
    authority = PermitAuthority.generate()
    request = ActionRequest("agent", "run", "stripe.payment_intent.create", "customer:x", payload)
    boundary = BoundaryGateway(authority.public_key_hex(), SQLitePermitStore(tmp_path / "p.db"))
    stripe = FakeStripePaymentIntents()
    gateway = StripePaymentIntentGateway(boundary, stripe, SQLiteStripeStore(tmp_path / "s.db"))
    token = authority.issue(request, now=NOW)

    with pytest.raises(PermitDenied, match=reason):
        gateway.dispatch(token, request, now=NOW + 1)
    assert stripe.create_calls == []


def test_verified_webhook_updates_once_and_duplicate_is_ignored(stripe_system):
    authority, _, stripe_store, _, gateway, request, token = stripe_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    gateway.dispatch(token, request, now=NOW + 1)
    event = {
        "id": "evt_1",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_1", "status": "succeeded"}},
    }

    first = stripe_store.ingest_webhook(event, now=NOW + 2)
    second = stripe_store.ingest_webhook(event, now=NOW + 3)

    assert first.state == "CONFIRMED"
    assert first.source == "webhook:payment_intent.succeeded"
    assert second is None
    assert stripe_store.get(permit_id)["reconciliation_state"] == "CONFIRMED"


def test_webhook_verifier_accepts_stripe_signature_and_rejects_forgery(stripe_system):
    _, _, stripe_store, _, gateway, request, token = stripe_system
    gateway.dispatch(token, request, now=NOW + 1)
    secret = "whsec_test_secret"
    payload = json.dumps(
        {
            "id": "evt_signed",
            "object": "event",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_1", "object": "payment_intent", "status": "succeeded"}},
        },
        separators=(",", ":"),
    )
    timestamp = int(time.time())
    signed = f"{timestamp}.{payload}".encode()
    signature = f"t={timestamp},v1={hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()}"
    verifier = StripeWebhookVerifier(secret, stripe_store)

    observation = verifier.process(payload.encode(), signature, now=NOW + 2)

    assert observation.state == "CONFIRMED"
    with pytest.raises(stripe.error.SignatureVerificationError):
        verifier.process(payload.encode(), "t=1,v1=forged", now=NOW + 3)


def test_live_key_requires_explicit_opt_in(tmp_path):
    authority = PermitAuthority.generate()
    boundary = BoundaryGateway(authority.public_key_hex(), SQLitePermitStore(tmp_path / "p.db"))
    store = SQLiteStripeStore(tmp_path / "s.db")
    with pytest.raises(ValueError, match="allow_live"):
        StripePaymentIntentGateway.from_api_key(boundary, "sk_live_secret", store)

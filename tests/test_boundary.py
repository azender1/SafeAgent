from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from safeagent_exec_guard.boundary import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    PermitDenied,
    SQLitePermitStore,
    verify_permit,
)


NOW = 1_800_000_000


@pytest.fixture()
def system(tmp_path):
    authority = PermitAuthority.generate(issuer="test-control-plane")
    store = SQLitePermitStore(tmp_path / "boundary.db")
    gateway = BoundaryGateway(authority.public_key_hex(), store)
    request = ActionRequest(
        principal="agent-17",
        run_id="run-001",
        action="payment.send",
        target="customer:123",
        payload={"amount_minor": 2500, "currency": "USD"},
    )
    return authority, store, gateway, request


def issue(authority, request, **kwargs):
    return authority.issue(request, now=NOW, ttl_seconds=300, **kwargs)


def test_valid_permit_dispatches_once_and_records_receipt(system):
    authority, store, gateway, request = system
    token = issue(authority, request)
    calls = []

    receipt = gateway.dispatch(
        token, request, lambda req: calls.append(req) or {"provider_id": "pay_123"}, now=NOW + 1
    )

    assert receipt.decision == "SETTLED"
    assert receipt.result == {"provider_id": "pay_123"}
    assert len(calls) == 1
    row = store.get(receipt.permit_id)
    assert row["status"] == "SETTLED"
    assert row["uses"] == 1
    assert [event["event_type"] for event in store.events(receipt.permit_id)] == [
        "PERMIT_CONSUMED",
        "SETTLED",
    ]


def test_replay_is_blocked_before_executor(system):
    authority, _, gateway, request = system
    token = issue(authority, request)
    calls = 0

    def execute(_):
        nonlocal calls
        calls += 1
        return {"ok": True}

    gateway.dispatch(token, request, execute, now=NOW + 1)
    with pytest.raises(PermitDenied, match="permit_already_consumed"):
        gateway.dispatch(token, request, execute, now=NOW + 2)
    assert calls == 1


@pytest.mark.parametrize(
    ("field", "changed", "reason"),
    [
        ("principal", "agent-18", "principal_mismatch"),
        ("run_id", "run-002", "run_id_mismatch"),
        ("action", "payment.refund", "action_mismatch"),
        ("target", "customer:attacker", "target_mismatch"),
        ("payload", {"amount_minor": 250000, "currency": "USD"}, "payload_sha256_mismatch"),
    ],
)
def test_exact_binding_blocks_substitution_and_mutation(system, field, changed, reason):
    authority, store, gateway, request = system
    token = issue(authority, request)
    values = {
        "principal": request.principal,
        "run_id": request.run_id,
        "action": request.action,
        "target": request.target,
        "payload": request.payload,
    }
    values[field] = changed
    mutated = ActionRequest(**values)

    with pytest.raises(PermitDenied, match=reason):
        gateway.dispatch(token, mutated, lambda _: pytest.fail("executor must not run"), now=NOW + 1)
    claims = verify_permit(token, authority.public_key_hex())
    assert store.get(claims.permit_id) is None


def test_expired_permit_is_denied_without_registering(system):
    authority, store, gateway, request = system
    token = authority.issue(request, now=NOW, ttl_seconds=1)
    claims = verify_permit(token, authority.public_key_hex())
    with pytest.raises(PermitDenied, match="permit_expired"):
        gateway.dispatch(token, request, lambda _: pytest.fail("must not run"), now=NOW + 1)
    assert store.get(claims.permit_id) is None


def test_tampered_token_is_denied(system):
    authority, _, gateway, request = system
    token = issue(authority, request)
    encoded, signature = token.split(".")
    tampered = ("A" if encoded[0] != "A" else "B") + encoded[1:] + "." + signature
    with pytest.raises(PermitDenied, match="invalid_permit"):
        gateway.dispatch(tampered, request, lambda _: pytest.fail("must not run"), now=NOW + 1)


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "A" * 16_385 + ".x"])
def test_malformed_or_oversized_token_is_denied(system, token):
    _, _, gateway, request = system
    with pytest.raises(PermitDenied, match="invalid_permit"):
        gateway.dispatch(token, request, lambda _: pytest.fail("must not run"), now=NOW + 1)


def test_wrong_authority_is_denied(system):
    authority, store, _, request = system
    attacker = PermitAuthority.generate(issuer="attacker")
    gateway = BoundaryGateway(authority.public_key_hex(), store)
    token = issue(attacker, request)
    with pytest.raises(PermitDenied, match="invalid_permit"):
        gateway.dispatch(token, request, lambda _: pytest.fail("must not run"), now=NOW + 1)


def test_exception_becomes_pending_and_never_reopens(system):
    authority, store, gateway, request = system
    token = issue(authority, request)

    def ambiguous(_):
        raise TimeoutError("response lost after provider may have committed")

    receipt = gateway.dispatch(token, request, ambiguous, now=NOW + 1)
    assert receipt.decision == "PENDING_RECONCILIATION"
    assert store.get(receipt.permit_id)["status"] == "PENDING_RECONCILIATION"
    with pytest.raises(PermitDenied, match="permit_already_consumed"):
        gateway.dispatch(token, request, lambda _: {"duplicate": True}, now=NOW + 2)


def test_unserializable_result_becomes_pending(system):
    authority, store, gateway, request = system
    token = issue(authority, request)
    receipt = gateway.dispatch(token, request, lambda _: object(), now=NOW + 1)
    assert receipt.decision == "PENDING_RECONCILIATION"
    assert receipt.reason == "result_not_serializable"
    assert store.get(receipt.permit_id)["status"] == "PENDING_RECONCILIATION"


def test_concurrent_consumption_allows_exactly_one_executor(system):
    authority, store, gateway, request = system
    token = issue(authority, request)

    def attempt(index):
        try:
            receipt = gateway.dispatch(
                token, request, lambda _: {"winner": index}, now=NOW + 1
            )
            return receipt.decision
        except PermitDenied as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(attempt, range(24)))

    assert outcomes.count("SETTLED") == 1
    assert outcomes.count("permit_already_consumed") == 23
    rows = store.events(verify_permit(token, authority.public_key_hex()).permit_id)
    assert [row["event_type"] for row in rows] == ["PERMIT_CONSUMED", "SETTLED"]


def test_permits_are_one_use_by_contract(system):
    authority, _, _, request = system
    with pytest.raises(ValueError, match="one-use"):
        authority.issue(request, now=NOW, allowed_uses=2)


@pytest.mark.parametrize("field", ["principal", "run_id", "action", "target"])
def test_request_identity_fields_must_be_nonempty(field):
    values = {
        "principal": "agent-17",
        "run_id": "run-001",
        "action": "payment.send",
        "target": "customer:123",
        "payload": {"amount_minor": 2500},
    }
    values[field] = "  "
    with pytest.raises(ValueError, match=field):
        ActionRequest(**values)

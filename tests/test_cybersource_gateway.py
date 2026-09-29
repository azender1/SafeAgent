from __future__ import annotations

import copy

import pytest

from safeagent_exec_guard.boundary import (
    ActionRequest,
    BoundaryGateway,
    PermitAuthority,
    PermitDenied,
    SQLitePermitStore,
    verify_permit,
)
from safeagent_exec_guard.cybersource_gateway import (
    CYBERSOURCE_CONFIRM_TRANSACTION,
    CybersourceConfirmTransactionGateway,
    SQLiteCybersourceStore,
)


NOW = 1_800_100_000


class FakeCybersourceTransport:
    def __init__(self):
        self.calls = []
        self.lose_next_response = False

    def confirm_transaction(self, instruction_id, payload):
        self.calls.append((instruction_id, copy.deepcopy(dict(payload))))
        response = {
            "clientCorrelationId": payload["clientCorrelationId"],
            "transactionId": "txn_001",
            "status": "COMPLETED",
            "signedPayload": "test-jws",
        }
        if self.lose_next_response:
            self.lose_next_response = False
            raise TimeoutError("response lost")
        return response


@pytest.fixture()
def visa_system(tmp_path):
    authority = PermitAuthority.generate(issuer="visa-agentic-control")
    boundary = BoundaryGateway(
        authority.public_key_hex(),
        SQLitePermitStore(tmp_path / "boundary.db"),
    )
    store = SQLiteCybersourceStore(tmp_path / "cybersource.db")
    transport = FakeCybersourceTransport()
    gateway = CybersourceConfirmTransactionGateway(boundary, transport, store)
    request = ActionRequest(
        principal="commerce-agent",
        run_id="run-visa-001",
        action=CYBERSOURCE_CONFIRM_TRANSACTION,
        target="instruction:inst_123",
        payload={
            "instruction_id": "inst_123",
            "clientCorrelationId": "corr_123",
            "transactionData": [
                {
                    "clientReferenceInformation": {"code": "order_123"},
                    "type": "PURCHASE",
                    "orderInformation": {
                        "amountDetail": {"totalAmount": "100.00", "currency": "USD"}
                    },
                    "merchantInformation": {
                        "merchantName": "SafeAgent Sandbox Merchant",
                        "merchantDescriptor": {
                            "country": "US",
                            "url": "https://example.com",
                        },
                    },
                }
            ],
        },
    )
    token = authority.issue(request, now=NOW, ttl_seconds=300)
    return authority, store, transport, gateway, request, token


def test_confirm_transaction_is_permit_gated_and_correlated(visa_system):
    authority, store, transport, gateway, request, token = visa_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id

    receipt = gateway.dispatch(token, request, now=NOW + 1)

    assert receipt.decision == "SETTLED"
    assert receipt.result["status"] == "COMPLETED"
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "inst_123"
    operation = store.get(permit_id)
    assert operation["instruction_id"] == "inst_123"
    assert operation["client_correlation_id"] == "corr_123"
    assert operation["provider_status"] == "COMPLETED"
    assert operation["reconciliation_state"] == "CONFIRMED"
    assert operation["boundary_decision"] == "SETTLED"


def test_replay_never_reaches_cybersource(visa_system):
    _, _, transport, gateway, request, token = visa_system
    gateway.dispatch(token, request, now=NOW + 1)

    with pytest.raises(PermitDenied, match="permit_already_consumed"):
        gateway.dispatch(token, request, now=NOW + 2)

    assert len(transport.calls) == 1


def test_lost_response_becomes_pending_reconciliation(visa_system):
    authority, store, transport, gateway, request, token = visa_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    transport.lose_next_response = True

    receipt = gateway.dispatch(token, request, now=NOW + 1)

    assert receipt.decision == "PENDING_RECONCILIATION"
    operation = store.get(permit_id)
    assert operation["reconciliation_state"] == "UNCERTAIN"
    assert operation["last_source"] == "confirm_transaction"
    assert operation["last_error"] == "response lost"


def test_operator_readback_can_close_uncertain_state_without_redispatch(visa_system):
    authority, store, transport, gateway, request, token = visa_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    transport.lose_next_response = True
    gateway.dispatch(token, request, now=NOW + 1)

    observation = gateway.ingest_provider_observation(
        permit_id,
        {
            "clientCorrelationId": "corr_123",
            "transactionId": "txn_001",
            "status": "COMPLETED",
        },
        source="visa_dashboard",
        now=NOW + 2,
    )

    assert observation.state == "CONFIRMED"
    assert observation.source == "visa_dashboard"
    assert len(transport.calls) == 1
    assert store.get(permit_id)["reconciliation_state"] == "CONFIRMED"


def test_provider_correlation_mismatch_is_rejected(visa_system):
    authority, _, _, gateway, request, token = visa_system
    permit_id = verify_permit(token, authority.public_key_hex()).permit_id
    gateway.dispatch(token, request, now=NOW + 1)

    with pytest.raises(ValueError, match="clientCorrelationId mismatch"):
        gateway.ingest_provider_observation(
            permit_id,
            {
                "clientCorrelationId": "wrong",
                "transactionId": "txn_001",
                "status": "COMPLETED",
            },
            now=NOW + 2,
        )


@pytest.mark.parametrize(
    "payload,reason",
    [
        (
            {"clientCorrelationId": "corr", "transactionData": [{}]},
            "missing_cybersource_instruction_id",
        ),
        (
            {"instruction_id": "inst", "transactionData": [{}]},
            "missing_cybersource_client_correlation_id",
        ),
        (
            {
                "instruction_id": "inst",
                "clientCorrelationId": "corr",
                "transactionData": [],
            },
            "missing_cybersource_transaction_data",
        ),
        (
            {
                "instruction_id": "inst",
                "clientCorrelationId": "corr",
                "transactionData": [{}],
                "shared_secret": "do-not-put-credentials-in-action-payload",
            },
            "credential_material_not_allowed_in_payload",
        ),
    ],
)
def test_invalid_payloads_fail_closed(tmp_path, payload, reason):
    authority = PermitAuthority.generate()
    boundary = BoundaryGateway(
        authority.public_key_hex(),
        SQLitePermitStore(tmp_path / "boundary.db"),
    )
    store = SQLiteCybersourceStore(tmp_path / "cybersource.db")
    transport = FakeCybersourceTransport()
    gateway = CybersourceConfirmTransactionGateway(boundary, transport, store)
    request = ActionRequest(
        "agent",
        "run",
        CYBERSOURCE_CONFIRM_TRANSACTION,
        "instruction:test",
        payload,
    )
    token = authority.issue(request, now=NOW)

    with pytest.raises(PermitDenied, match=reason):
        gateway.dispatch(token, request, now=NOW + 1)

    assert transport.calls == []

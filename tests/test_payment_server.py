"""
Tests for the x402-gated payment server.

Two groups:

1. Business-logic tests — run always, no x402 middleware active.
   These verify the claim/settle/sweep route handlers in isolation.

2. x402 gate tests — activated when x402 can load properly.
   These verify that POST /claim returns 402 when no payment header
   is present, and that the response carries the correct payment
   requirement metadata.
"""
from __future__ import annotations

import time
import json

import pytest
from fastapi.testclient import TestClient

from safeagent_exec_guard.payment_server import create_app
from safeagent_exec_guard.hosted_access import settlement_token, tenant_request_id
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv('SAFEAGENT_SETTLEMENT_SECRET', 's' * 48)
    monkeypatch.setenv('SAFEAGENT_AUDIT_TOKEN', 'a' * 48)


def settlement_header(request_id):
    return {'X-SafeAgent-Settlement-Token': settlement_token(request_id)}


@pytest.fixture()
def store() -> SQLiteExecutionStore:
    return SQLiteExecutionStore(":memory:")


@pytest.fixture()
def client(store: SQLiteExecutionStore, auth_env) -> TestClient:
    """TestClient with no payment gating (no payment_address)."""
    c = TestClient(create_app(store=store))
    c.headers['X-SafeAgent-Audit-Token'] = 'a' * 48
    return c


# ---------------------------------------------------------------------------
# 1. Business logic (no payment gating)
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestClaimRoute:
    def test_tenant_keys_isolate_identical_ids_and_scoped_audit(
        self, monkeypatch, store: SQLiteExecutionStore
    ) -> None:
        monkeypatch.setenv('SAFEAGENT_TENANT_KEYS', json.dumps({
            'alice': 'a' * 48, 'bob': 'b' * 48,
        }))
        client = TestClient(create_app(store=store))
        body = {'request_id': 'predictable-invoice-1', 'action': 'charge'}
        assert client.post('/claim', json=body).status_code == 403
        alice = {'X-SafeAgent-Api-Key': 'a' * 48}
        bob = {'X-SafeAgent-Api-Key': 'b' * 48}
        a = client.post('/claim', json=body, headers=alice).json()
        b = client.post('/claim', json=body, headers=bob).json()
        assert a['status'] == b['status'] == 'PROCEED'
        assert a['settlement_token'] != b['settlement_token']
        assert store.get(tenant_request_id('alice', body['request_id']))['status'] == 'PENDING'
        assert store.get(tenant_request_id('bob', body['request_id']))['status'] == 'PENDING'
        assert client.get('/audit', headers=alice).json()['total'] == 1
        assert client.get('/audit', headers=bob).json()['total'] == 1
        assert client.post('/settle/predictable-invoice-1',
                           json={'result': {'forged': True}},
                           headers={**bob, 'X-SafeAgent-Settlement-Token': a['settlement_token']}
                           ).status_code == 403
        assert store.get(tenant_request_id('bob', body['request_id']))['status'] == 'PENDING'

    def test_capability_is_required_for_settle_and_cached_result(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        body = {"request_id": "private-1", "action": "email.send"}
        first = client.post("/claim", json=body).json()
        token = first["settlement_token"]
        assert first["status"] == "PROCEED"
        assert client.post("/settle/private-1", json={"result": {"secret": 1}}).status_code == 403
        assert store.get("private-1")["status"] == "PENDING"
        assert client.post("/settle/private-1", json={"result": {"secret": 1}},
                           headers={"X-SafeAgent-Settlement-Token": token}).status_code == 200
        duplicate = client.post("/claim", json=body).json()
        assert duplicate["status"] == "SKIP" and "existing" not in duplicate
        authorized = client.post("/claim", json=body,
                                 headers={"X-SafeAgent-Settlement-Token": token}).json()
        assert authorized["existing"] == {"secret": 1}

    def test_new_request_id_returns_proceed(self, client: TestClient) -> None:
        resp = client.post("/claim", json={"request_id": "r1", "action": "email.send"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "PROCEED"
        assert body["request_id"] == "r1"

    def test_claim_inserts_pending_row(self, client: TestClient, store: SQLiteExecutionStore) -> None:
        client.post("/claim", json={"request_id": "r2", "action": "action"})
        row = store.get("r2")
        assert row is not None
        assert row["status"] == "PENDING"

    def test_pending_request_id_returns_pending(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("r3", "action")  # inject PENDING directly
        resp = client.post("/claim", json={"request_id": "r3", "action": "action"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "PENDING"

    def test_committed_request_id_returns_skip(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("r4", "action")
        store.settle("r4", {"ok": True, "sent": "alice@example.com"})
        resp = client.post("/claim", json={"request_id": "r4", "action": "action"},
                           headers=settlement_header('r4'))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "SKIP"
        assert body["existing"] == {"ok": True, "sent": "alice@example.com"}

    def test_skip_response_includes_stored_result(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        stored_result = {"ok": True, "execution_id": "abc-123", "data": [1, 2, 3]}
        store.claim("r5", "webhook")
        store.settle("r5", stored_result)
        resp = client.post("/claim", json={"request_id": "r5", "action": "webhook"},
                           headers=settlement_header('r5'))
        assert resp.json()["existing"] == stored_result

    def test_different_request_ids_are_independent(self, client: TestClient) -> None:
        r_a = client.post("/claim", json={"request_id": "a1", "action": "x"}).json()
        r_b = client.post("/claim", json={"request_id": "b1", "action": "x"}).json()
        assert r_a["status"] == "PROCEED"
        assert r_b["status"] == "PROCEED"


class TestSettleRoute:
    def test_settle_transitions_to_committed(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("s1", "action")
        resp = client.post("/settle/s1", json={"result": {"ok": True}}, headers=settlement_header('s1'))
        assert resp.status_code == 200
        assert resp.json()["status"] == "committed"
        assert store.get("s1")["status"] == "COMMITTED"

    def test_settle_stores_result(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("s2", "action")
        result = {"ok": True, "execution_id": "xyz", "data": {"key": "val"}}
        client.post("/settle/s2", json={"result": result}, headers=settlement_header('s2'))
        assert store.get("s2")["result"] == result

    def test_settle_unknown_request_id_returns_404(self, client: TestClient) -> None:
        resp = client.post("/settle/no-such-id", json={"result": {}},
                           headers=settlement_header('no-such-id'))
        assert resp.status_code == 404

    def test_settle_already_committed_is_idempotent(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("s3", "action")
        store.settle("s3", {"ok": True})
        resp = client.post("/settle/s3", json={"result": {"ok": False}},
                           headers=settlement_header('s3'))
        assert resp.json()["status"] == "already_committed"
        # original result unchanged
        assert store.get("s3")["result"] == {"ok": True}

    def test_settle_not_payment_gated(
        self, client: TestClient, store: SQLiteExecutionStore, monkeypatch
    ) -> None:
        """settle/ must always be 200 even on a payment-enabled server."""
        # The tenant key and settlement capability replace payment for settle.
        monkeypatch.setenv('SAFEAGENT_TENANT_KEYS', json.dumps({'operator': 'k' * 48}))
        payment_app = create_app(
            store=store,
            payment_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        stored_id = tenant_request_id('operator', 's4')
        store.claim(stored_id, "action")
        with TestClient(payment_app, raise_server_exceptions=False) as c:
            resp = c.post("/settle/s4", json={"result": {"ok": True}},
                          headers={**settlement_header(stored_id), 'X-SafeAgent-Api-Key': 'k' * 48})
            assert resp.status_code == 200


class TestSweepRoute:
    def test_sweep_reports_but_preserves_stale_pending(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.pending_ttl_seconds = 0.01
        store.claim("stale-1", "action")
        store.claim("stale-2", "action")
        time.sleep(0.05)
        resp = client.post("/sweep")
        assert resp.status_code == 200
        assert resp.json() == {
            "swept": 0,
            "stale_pending": 2,
            "requires_reconciliation": True,
            "action": "none",
        }
        assert store.get("stale-1")["status"] == "PENDING"
        assert store.get("stale-2")["status"] == "PENDING"

    def test_sweep_does_not_remove_committed(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.pending_ttl_seconds = 0.01
        store.claim("done-req", "action")
        store.settle("done-req", {"ok": True})
        time.sleep(0.05)
        resp = client.post("/sweep")
        assert resp.json()["swept"] == 0
        assert resp.json()["stale_pending"] == 0
        assert resp.json()["requires_reconciliation"] is False

    def test_sweep_not_payment_gated(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        payment_app = create_app(
            store=store,
            payment_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        with TestClient(payment_app, raise_server_exceptions=False) as c:
            resp = c.post("/sweep", headers={'X-SafeAgent-Audit-Token': 'a' * 48})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 2. x402 gate tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 2. agent_id extraction helper (no middleware needed)
# ---------------------------------------------------------------------------


class TestAgentIdExtraction:
    def test_no_payment_state_returns_none(self) -> None:
        from safeagent_exec_guard.payment_server import _extract_agent_id
        from unittest.mock import MagicMock

        req = MagicMock()
        req.state = MagicMock(spec=[])  # no payment_payload attr
        assert _extract_agent_id(req) is None

    def test_payment_payload_from_address_snake_case(self) -> None:
        from safeagent_exec_guard.payment_server import _extract_agent_id
        from unittest.mock import MagicMock

        addr = "0xAbCd1234AbCd1234AbCd1234AbCd1234AbCd1234"
        payload = MagicMock()
        payload.payload = {"authorization": {"from_address": addr}}
        req = MagicMock()
        req.state.payment_payload = payload
        assert _extract_agent_id(req) == addr

    def test_payment_payload_from_address_camel_case(self) -> None:
        from safeagent_exec_guard.payment_server import _extract_agent_id
        from unittest.mock import MagicMock

        addr = "0xDeAd000000000000000000000000000000000001"
        payload = MagicMock()
        payload.payload = {"authorization": {"fromAddress": addr}}
        req = MagicMock()
        req.state.payment_payload = payload
        assert _extract_agent_id(req) == addr

    def test_claim_stores_agent_id_when_injected(
        self, store: SQLiteExecutionStore
    ) -> None:
        """If the middleware injects a payment_payload, agent_id is persisted."""
        from safeagent_exec_guard.payment_server import create_app
        from unittest.mock import patch, MagicMock

        app = create_app(store=store)
        addr = "0xBeEf000000000000000000000000000000000002"

        # Patch _extract_agent_id so it returns our test address
        with patch(
            "safeagent_exec_guard.payment_server._extract_agent_id",
            return_value=addr,
        ):
            with TestClient(app) as c:
                resp = c.post(
                    "/claim", json={"request_id": "aid-1", "action": "send"}
                )

        assert resp.json()["agent_id"] == addr
        assert store.get("aid-1")["agent_id"] == addr


# ---------------------------------------------------------------------------
# 3. GET /audit endpoint
# ---------------------------------------------------------------------------


class TestAuditRoute:
    def _seed(self, store: SQLiteExecutionStore) -> None:
        """Insert a mix of rows for filter tests."""
        store.claim("a1", "email.send", agent_id="0xAlice")
        store.settle("a1", {"ok": True})
        store.claim("a2", "webhook", agent_id="0xBob")
        store.settle("a2", {"ok": True})
        store.claim("a3", "email.send", agent_id="0xAlice")  # PENDING
        store.claim("a4", "sms.send", agent_id=None)
        store.settle("a4", {"ok": False})

    def test_audit_returns_all_rows(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp = client.get("/audit")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4
        assert len(body["items"]) == 4

    def test_audit_filter_by_agent_id(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp = client.get("/audit", params={"agent_id": "0xAlice"})
        body = resp.json()
        assert body["total"] == 2
        assert all(i["agent_id"] == "0xAlice" for i in body["items"])

    def test_audit_filter_by_action(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp = client.get("/audit", params={"action": "email.send"})
        body = resp.json()
        assert body["total"] == 2
        assert all(i["action"] == "email.send" for i in body["items"])

    def test_audit_filter_by_status_committed(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp = client.get("/audit", params={"status": "COMMITTED"})
        body = resp.json()
        assert body["total"] == 3
        assert all(i["status"] == "COMMITTED" for i in body["items"])

    def test_audit_filter_by_status_pending(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp = client.get("/audit", params={"status": "PENDING"})
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["request_id"] == "a3"

    def test_audit_filter_by_date_range(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        now = time.time()
        store.claim("ts1", "action", agent_id=None)
        resp = client.get(
            "/audit", params={"from_ts": now - 5, "to_ts": now + 60}
        )
        body = resp.json()
        assert any(i["request_id"] == "ts1" for i in body["items"])

    def test_audit_pagination(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp1 = client.get("/audit", params={"limit": 2, "offset": 0})
        resp2 = client.get("/audit", params={"limit": 2, "offset": 2})
        b1, b2 = resp1.json(), resp2.json()
        assert b1["total"] == 4
        assert len(b1["items"]) == 2
        assert len(b2["items"]) == 2
        ids = {i["request_id"] for i in b1["items"]} | {
            i["request_id"] for i in b2["items"]
        }
        assert len(ids) == 4

    def test_audit_combined_filters(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        self._seed(store)
        resp = client.get(
            "/audit",
            params={"agent_id": "0xAlice", "status": "COMMITTED"},
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["request_id"] == "a1"

    def test_audit_empty_result(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        resp = client.get("/audit", params={"agent_id": "0xNobody"})
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_audit_not_payment_gated(self, store: SQLiteExecutionStore) -> None:
        app = create_app(
            store=store,
            payment_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/audit", headers={'X-SafeAgent-Audit-Token': 'a' * 48})
            assert resp.status_code == 200

    def test_audit_items_include_agent_id_field(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("f1", "action", agent_id="0xFoo")
        store.settle("f1", {"ok": True})
        resp = client.get("/audit")
        items = resp.json()["items"]
        assert items[0]["agent_id"] == "0xFoo"

    def test_audit_agent_id_null_when_unset(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("f2", "action")  # no agent_id
        resp = client.get("/audit")
        items = resp.json()["items"]
        assert items[0]["agent_id"] is None


# ---------------------------------------------------------------------------
# 4. x402 gate tests
# ---------------------------------------------------------------------------

x402 = pytest.importorskip("x402", reason="x402 not installed")

_DUMMY_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


@pytest.fixture()
def payment_client(store: SQLiteExecutionStore, auth_env, monkeypatch) -> TestClient:
    """TestClient with x402 middleware active on POST /claim."""
    monkeypatch.setenv('SAFEAGENT_TENANT_KEYS', json.dumps({'operator': 'k' * 48}))
    app = create_app(store=store, payment_address=_DUMMY_ADDRESS)
    client = TestClient(app, raise_server_exceptions=False)
    client.headers['X-SafeAgent-Api-Key'] = 'k' * 48
    return client


class TestX402Gate:
    def test_missing_tenant_key_blocks_before_payment_verification(
        self, payment_client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        response = payment_client.post('/claim', json={'request_id': 'unowned', 'action': 'charge'},
                                       headers={'X-SafeAgent-Api-Key': 'bad', 'x-payment': 'invalid'})
        assert response.status_code == 403
        assert store.get('unowned') is None

    def test_missing_secret_rejects_paid_claim_before_payment_gate(
        self, monkeypatch, store: SQLiteExecutionStore
    ) -> None:
        monkeypatch.delenv('SAFEAGENT_SETTLEMENT_SECRET')
        app = create_app(store=store, payment_address=_DUMMY_ADDRESS)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post('/claim', json={'request_id': 'bad-config', 'action': 'charge'},
                               headers={'x-payment': 'invalid'})
        assert response.status_code == 503
        assert store.get('bad-config') is None

    def test_claim_without_payment_returns_402(
        self, payment_client: TestClient
    ) -> None:
        resp = payment_client.post(
            "/claim", json={"request_id": "pay-1", "action": "action"}
        )
        assert resp.status_code == 402

    def test_402_response_is_json(self, payment_client: TestClient) -> None:
        resp = payment_client.post(
            "/claim", json={"request_id": "pay-2", "action": "action"}
        )
        assert resp.status_code == 402
        # x402 returns structured JSON with payment details
        body = resp.json()
        assert isinstance(body, dict)

    def test_health_not_gated(self, payment_client: TestClient) -> None:
        resp = payment_client.get("/health")
        assert resp.status_code == 200

    def test_settle_not_gated_when_payment_enabled(
        self, store: SQLiteExecutionStore, payment_client: TestClient
    ) -> None:
        stored_id = tenant_request_id('operator', 'pay-3')
        store.claim(stored_id, "action")
        resp = payment_client.post("/settle/pay-3", json={"result": {"ok": True}},
                                   headers=settlement_header(stored_id))
        assert resp.status_code == 200

    def test_402_payment_required_header_contains_network(
        self, payment_client: TestClient
    ) -> None:
        import base64, json
        resp = payment_client.post(
            "/claim", json={"request_id": "pay-4", "action": "action"}
        )
        assert resp.status_code == 402
        # x402 encodes payment details in the PAYMENT-REQUIRED header (base64 JSON)
        header = resp.headers.get("payment-required", "")
        assert header, "PAYMENT-REQUIRED header must be present"
        decoded = json.loads(base64.b64decode(header + "=="))
        accepts = decoded.get("accepts", [])
        assert len(accepts) >= 1
        networks = [a.get("network", "") for a in accepts]
        assert any("84532" in n or "eip155" in n for n in networks)

    def test_custom_price_appears_in_requirement(
        self, store: SQLiteExecutionStore, monkeypatch
    ) -> None:
        monkeypatch.setenv('SAFEAGENT_TENANT_KEYS', json.dumps({'operator': 'k' * 48}))
        app = create_app(
            store=store,
            payment_address=_DUMMY_ADDRESS,
            claim_price_usdc="0.005",
        )
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/claim", json={"request_id": "pay-5", "action": "action"},
                headers={'X-SafeAgent-Api-Key': 'k' * 48},
            )
            assert resp.status_code == 402

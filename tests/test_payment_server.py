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

import pytest
from fastapi.testclient import TestClient

from safeagent_exec_guard.payment_server import create_app
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> SQLiteExecutionStore:
    return SQLiteExecutionStore(":memory:")


@pytest.fixture()
def client(store: SQLiteExecutionStore) -> TestClient:
    """TestClient with no payment gating (no payment_address)."""
    return TestClient(create_app(store=store))


# ---------------------------------------------------------------------------
# 1. Business logic (no payment gating)
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestClaimRoute:
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
        resp = client.post("/claim", json={"request_id": "r4", "action": "action"})
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
        resp = client.post("/claim", json={"request_id": "r5", "action": "webhook"})
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
        resp = client.post("/settle/s1", json={"result": {"ok": True}})
        assert resp.status_code == 200
        assert resp.json()["status"] == "committed"
        assert store.get("s1")["status"] == "COMMITTED"

    def test_settle_stores_result(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("s2", "action")
        result = {"ok": True, "execution_id": "xyz", "data": {"key": "val"}}
        client.post("/settle/s2", json={"result": result})
        assert store.get("s2")["result"] == result

    def test_settle_unknown_request_id_returns_404(self, client: TestClient) -> None:
        resp = client.post("/settle/no-such-id", json={"result": {}})
        assert resp.status_code == 404

    def test_settle_already_committed_is_idempotent(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.claim("s3", "action")
        store.settle("s3", {"ok": True})
        resp = client.post("/settle/s3", json={"result": {"ok": False}})
        assert resp.json()["status"] == "already_committed"
        # original result unchanged
        assert store.get("s3")["result"] == {"ok": True}

    def test_settle_not_payment_gated(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        """settle/ must always be 200 even on a payment-enabled server."""
        payment_app = create_app(
            store=store,
            payment_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        store.claim("s4", "action")
        with TestClient(payment_app, raise_server_exceptions=False) as c:
            resp = c.post("/settle/s4", json={"result": {"ok": True}})
            assert resp.status_code == 200


class TestSweepRoute:
    def test_sweep_removes_stale_pending(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.pending_ttl_seconds = 0.01
        store.claim("stale-1", "action")
        store.claim("stale-2", "action")
        time.sleep(0.05)
        resp = client.post("/sweep")
        assert resp.status_code == 200
        assert resp.json()["swept"] == 2

    def test_sweep_does_not_remove_committed(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        store.pending_ttl_seconds = 0.01
        store.claim("done-req", "action")
        store.settle("done-req", {"ok": True})
        time.sleep(0.05)
        resp = client.post("/sweep")
        assert resp.json()["swept"] == 0

    def test_sweep_not_payment_gated(
        self, client: TestClient, store: SQLiteExecutionStore
    ) -> None:
        payment_app = create_app(
            store=store,
            payment_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        with TestClient(payment_app, raise_server_exceptions=False) as c:
            resp = c.post("/sweep")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 2. x402 gate tests
# ---------------------------------------------------------------------------

x402 = pytest.importorskip("x402", reason="x402 not installed")

_DUMMY_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


@pytest.fixture()
def payment_client(store: SQLiteExecutionStore) -> TestClient:
    """TestClient with x402 middleware active on POST /claim."""
    app = create_app(store=store, payment_address=_DUMMY_ADDRESS)
    return TestClient(app, raise_server_exceptions=False)


class TestX402Gate:
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
        store.claim("pay-3", "action")
        resp = payment_client.post("/settle/pay-3", json={"result": {"ok": True}})
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
        self, store: SQLiteExecutionStore
    ) -> None:
        app = create_app(
            store=store,
            payment_address=_DUMMY_ADDRESS,
            claim_price_usdc="0.005",
        )
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/claim", json={"request_id": "pay-5", "action": "action"}
            )
            assert resp.status_code == 402

"""
FastAPI-based SafeAgent claim server with x402 per-claim payment gating.

Every POST /claim requires a micro-payment in USDC on Base before the
two-phase claim is processed.  The x402 middleware handles the full
402 → pay → retry flow transparently; the route handler only sees
requests that have already been verified and settled by the facilitator.

Quickstart
----------
    export SAFEAGENT_PAYMENT_ADDRESS=0xYourBaseAddress
    uvicorn safeagent_exec_guard.payment_server:app --port 8402

Environment variables
---------------------
SAFEAGENT_PAYMENT_ADDRESS   Recipient Base address for USDC (enables x402)
SAFEAGENT_CLAIM_PRICE_USDC  USDC per claim          (default: 0.001)
SAFEAGENT_NETWORK           CAIP-2 chain ID          (default: eip155:84532 = Base Sepolia)
SAFEAGENT_FACILITATOR_URL   x402 facilitator URL     (default: https://x402.org/facilitator)
SAFEAGENT_DB_PATH           SQLite file path         (default: safeagent.db)
SAFEAGENT_PENDING_TTL       Stale-pending TTL secs   (default: 300)
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

# USDC contract address on Base Sepolia (testnet)
_USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
# USDC contract address on Base mainnet
_USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

_USDC_BY_NETWORK: Dict[str, str] = {
    "eip155:84532": _USDC_BASE_SEPOLIA,  # Base Sepolia
    "eip155:8453": _USDC_BASE_MAINNET,   # Base mainnet
}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ClaimRequest(BaseModel):
    request_id: str
    action: str


class SettleRequest(BaseModel):
    result: Dict[str, Any]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    store: Optional[SQLiteExecutionStore] = None,
    payment_address: Optional[str] = None,
    claim_price_usdc: str = "0.001",
    network: str = "eip155:84532",
    facilitator_url: str = "https://x402.org/facilitator",
) -> FastAPI:
    """
    Build and return the FastAPI application.

    When *payment_address* is provided (or ``SAFEAGENT_PAYMENT_ADDRESS``
    is set), ``POST /claim`` is gated by x402: callers must pay
    *claim_price_usdc* USDC on *network* before the claim is processed.

    Omitting *payment_address* disables payment gating — useful for local
    development and unit tests.
    """
    _store = store or SQLiteExecutionStore(
        db_path=os.getenv("SAFEAGENT_DB_PATH", "safeagent.db"),
        pending_ttl_seconds=float(os.getenv("SAFEAGENT_PENDING_TTL", "300")),
    )
    _payment_address = payment_address or os.getenv("SAFEAGENT_PAYMENT_ADDRESS")
    _price = os.getenv("SAFEAGENT_CLAIM_PRICE_USDC", claim_price_usdc)
    _network = os.getenv("SAFEAGENT_NETWORK", network)
    _facilitator = os.getenv("SAFEAGENT_FACILITATOR_URL", facilitator_url)

    app = FastAPI(
        title="SafeAgent Claim Server",
        version="0.1.0",
        description="Two-phase claim endpoint with x402 per-claim payment gating.",
    )
    app.state.store = _store

    # ------------------------------------------------------------------
    # x402 payment middleware — only attached when payment_address is set
    # ------------------------------------------------------------------
    if _payment_address:
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.facilitator_client import HTTPFacilitatorClient
        from x402.http.facilitator_client_base import FacilitatorConfig
        from x402.http.types import RouteConfig, PaymentOption
        from x402.server import x402ResourceServer
        from x402.mechanisms.evm.exact import register_exact_evm_server

        facilitator_client = HTTPFacilitatorClient(
            FacilitatorConfig(url=_facilitator)
        )
        resource_server = x402ResourceServer(facilitator_client)
        register_exact_evm_server(resource_server, networks=_network)

        routes: dict = {
            "POST /claim": RouteConfig(
                accepts=PaymentOption(
                    scheme="exact",
                    price=f"${_price}",
                    network=_network,
                    pay_to=_payment_address,
                ),
                description="SafeAgent two-phase claim — returns PROCEED or SKIP",
            )
        }
        app.add_middleware(
            PaymentMiddlewareASGI,
            routes=routes,
            server=resource_server,
        )

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/claim")
    async def claim(body: ClaimRequest) -> Dict[str, Any]:
        """
        Two-phase claim.

        ``{"status": "PROCEED"}``  — new claim; caller should execute
        their action, then POST /settle/{request_id}.

        ``{"status": "SKIP", "existing": {...}}`` — already COMMITTED;
        caller should reuse the stored result.

        ``{"status": "PENDING"}`` — another caller has this in-flight;
        retry after the pending TTL expires and the sweeper resets it.

        Requires x402 payment when the server is started with
        ``SAFEAGENT_PAYMENT_ADDRESS`` set.
        """
        store: SQLiteExecutionStore = app.state.store
        existing = store.get(body.request_id)
        if existing is not None:
            if existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": body.request_id,
                    "existing": existing.get("result"),
                }
            return {"status": "PENDING", "request_id": body.request_id}

        # Phase 1: atomic INSERT of PENDING row
        if not store.claim(body.request_id, body.action):
            # Lost the concurrent INSERT race
            existing = store.get(body.request_id)
            if existing and existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": body.request_id,
                    "existing": existing.get("result"),
                }
            return {"status": "PENDING", "request_id": body.request_id}

        return {"status": "PROCEED", "request_id": body.request_id}

    @app.post("/settle/{request_id}")
    async def settle(request_id: str, body: SettleRequest) -> Dict[str, Any]:
        """
        Transition PENDING → COMMITTED with the execution result.

        Not payment-gated — settling is always free.
        """
        store: SQLiteExecutionStore = app.state.store
        existing = store.get(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="request_id not found")
        if existing["status"] == "COMMITTED":
            return {"status": "already_committed", "request_id": request_id}
        store.settle(request_id, body.result)
        return {"status": "committed", "request_id": request_id}

    @app.post("/sweep")
    async def sweep() -> Dict[str, Any]:
        """Reset stale PENDING rows to CLAIMABLE.  Not payment-gated."""
        store: SQLiteExecutionStore = app.state.store
        swept = store.sweep_stale_pending()
        return {"swept": swept}

    return app


# ---------------------------------------------------------------------------
# Default application instance (used by uvicorn)
# ---------------------------------------------------------------------------

app = create_app()

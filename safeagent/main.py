"""
SafeAgent — Execution Guard for AI Agents

Endpoints
---------
GET  /                  Landing page
POST /claim             x402-gated exactly-once claim → PROCEED / SKIP / PENDING
POST /claim/test        Free test claim (rate-limited: 10 calls per IP total)
POST /settle/{id}       Commit a PENDING claim with its result (free)
GET  /audit             Filterable claim history (free)
POST /sweep             Reset stale PENDING rows (free)
GET  /.well-known/x402  x402 discovery document
GET  /health            Liveness probe (free)

Environment variables
---------------------
SAFEAGENT_PAYMENT_ADDRESS   Recipient Base address for USDC (enables x402)
SAFEAGENT_CLAIM_PRICE_USDC  USDC per claim          (default: 0.001)
SAFEAGENT_NETWORK           CAIP-2 chain ID          (default: eip155:84532 = Base Sepolia)
SAFEAGENT_FACILITATOR_URL   x402 facilitator URL     (default: https://api.cdp.coinbase.com/platform/v2/x402/facilitator)
SAFEAGENT_RESOURCE_URL      Public URL of /claim     (default: https://safeagent-production.up.railway.app/claim)
SAFEAGENT_DB_PATH           SQLite file path         (default: safeagent_orders.db)
SAFEAGENT_PENDING_TTL       Stale-pending TTL secs   (default: 300)
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

_USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

_USDC_BY_NETWORK: Dict[str, str] = {
    "eip155:84532": _USDC_BASE_SEPOLIA,
    "eip155:8453": _USDC_BASE_MAINNET,
}

_TEST_RATE_LIMIT = 10
# In-memory per-IP call counter for /claim/test (resets on server restart).
_test_ip_counts: Dict[str, int] = defaultdict(int)


def _extract_agent_id(request: Request) -> Optional[str]:
    """Extract the payer's EVM wallet address from the x402-verified payment payload."""
    payment = getattr(getattr(request, "state", None), "payment_payload", None)
    if payment is None:
        return None
    raw: Dict[str, Any] = getattr(payment, "payload", {}) or {}
    auth: Dict[str, Any] = raw.get("authorization") or {}
    return (
        auth.get("from_address")
        or auth.get("fromAddress")
        or raw.get("from")
        or None
    )


class ClaimRequest(BaseModel):
    agent_id: str
    action_type: str
    scope: str


class SettleRequest(BaseModel):
    result: Dict[str, Any]


def _derive_request_id(body: ClaimRequest) -> str:
    return f"{body.agent_id}:{body.action_type}:{body.scope}"


def create_app(
    *,
    store: Optional[SQLiteExecutionStore] = None,
    payment_address: Optional[str] = None,
    claim_price_usdc: str = "0.001",
    network: str = "eip155:84532",
    facilitator_url: str = "https://api.cdp.coinbase.com/platform/v2/x402/facilitator",
    resource_url: str = "https://safeagent-production.up.railway.app/claim",
) -> FastAPI:
    _store = store or SQLiteExecutionStore(
        db_path=os.getenv("SAFEAGENT_DB_PATH", "safeagent_orders.db"),
        pending_ttl_seconds=float(os.getenv("SAFEAGENT_PENDING_TTL", "300")),
    )
    _payment_address = payment_address or os.getenv("SAFEAGENT_PAYMENT_ADDRESS")
    _price = os.getenv("SAFEAGENT_CLAIM_PRICE_USDC", claim_price_usdc)
    _network = os.getenv("SAFEAGENT_NETWORK", network)
    _facilitator = os.getenv("SAFEAGENT_FACILITATOR_URL", facilitator_url)
    _resource_url = os.getenv("SAFEAGENT_RESOURCE_URL", resource_url)

    app = FastAPI(
        title="SafeAgent",
        version="0.1.0",
        description="Execution Guard for AI Agents — exactly-once claim before execute.",
    )
    app.state.store = _store

    # ------------------------------------------------------------------
    # x402 payment middleware — only gates POST /claim exactly.
    # POST /claim/test is intentionally excluded.
    # ------------------------------------------------------------------
    if _payment_address:
        from x402.extensions.bazaar import (
            OutputConfig,
            bazaar_resource_server_extension,
            declare_discovery_extension,
        )
        from x402.http.constants import PAYMENT_REQUIRED_HEADER
        from x402.http.facilitator_client import HTTPFacilitatorClient
        from x402.http.facilitator_client_base import FacilitatorConfig
        from x402.http.middleware.fastapi import payment_middleware
        from x402.http.types import PaymentOption, RouteConfig
        from x402.http.utils import encode_payment_required_header
        from x402.mechanisms.evm.exact import register_exact_evm_server
        from x402.schemas import PaymentRequired, PaymentRequirements, ResourceInfo
        from x402.server import x402ResourceServer

        _usdc_asset = _USDC_BY_NETWORK.get(_network, _USDC_BASE_SEPOLIA)
        _price_atomic = str(int(float(_price) * 1_000_000))

        _bazaar_extension: Dict[str, Any] = declare_discovery_extension(
            input={
                "agent_id": "bot-1",
                "action_type": "order",
                "scope": "TQQQ:buy:6:bar:2026-05-19T13:31:00-04:00",
            },
            body_type="json",
            output=OutputConfig(
                example={
                    "status": "PROCEED",
                    "request_id": "bot-1:order:TQQQ:buy:6:bar:2026-05-19T13:31:00-04:00",
                    "agent_id": "0x2Dc36fb02357aDa6E210Cb4b0EA783EA5153EAa8",
                },
                schema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "request_id": {"type": "string"},
                        "agent_id": {"type": "string"},
                    },
                    "required": ["status", "request_id"],
                },
            ),
        )
        try:
            _bazaar_extension["bazaar"]["info"]["input"]["method"] = "POST"
            _bazaar_extension["bazaar"]["description"] = (
                "Claim-before-execute guard for AI agents. Returns PROCEED on first call, "
                "SKIP with cached result on retry. Crash-safe. Works with CrewAI, LangGraph, n8n, MCP."
            )
        except (KeyError, TypeError):
            pass

        _payment_requirements = PaymentRequirements(
            scheme="exact",
            network=_network,
            asset=_usdc_asset,
            amount=_price_atomic,
            pay_to=_payment_address,
            max_timeout_seconds=300,
            extra={"name": "USDC", "version": "2"},
        )
        _well_known_doc = PaymentRequired(
            x402_version=2,
            resource=ResourceInfo(
                url=_resource_url,
                description=(
                    "Claim-before-execute guard for AI agents. Atomically reserves an "
                    "(agent_id, action_type, scope) triple before execution. Returns PROCEED "
                    "on first call, SKIP with cached result on retry. Crash-safe."
                ),
                mime_type="application/json",
            ),
            accepts=[_payment_requirements],
            extensions=_bazaar_extension,
        )
        app.state.well_known_x402 = _well_known_doc.model_dump(
            by_alias=True, exclude_none=True
        )

        def _make_payment_required_response() -> JSONResponse:
            pr = PaymentRequired(
                x402_version=2,
                error="Payment required",
                resource=ResourceInfo(
                    url=_resource_url,
                    description=(
                        "Claim-before-execute guard for AI agents. Returns PROCEED on first call, "
                        "SKIP with cached result on retry. Crash-safe."
                    ),
                    mime_type="application/json",
                ),
                accepts=[_payment_requirements],
                extensions=_bazaar_extension,
            )
            return JSONResponse(
                content={},
                status_code=402,
                headers={PAYMENT_REQUIRED_HEADER: encode_payment_required_header(pr)},
            )

        facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url=_facilitator))
        resource_server = x402ResourceServer(facilitator_client)
        register_exact_evm_server(resource_server, networks=_network)
        resource_server.register_extension(bazaar_resource_server_extension)

        routes: dict = {
            "POST /claim": RouteConfig(
                accepts=PaymentOption(
                    scheme="exact",
                    price=f"${_price}",
                    network=_network,
                    pay_to=_payment_address,
                ),
                description=(
                    "Claim-before-execute guard for AI agents. Returns PROCEED on first call, "
                    "SKIP with cached result on retry. Crash-safe."
                ),
                extensions=_bazaar_extension,
            )
        }
        _x402 = payment_middleware(
            routes, resource_server, sync_facilitator_on_start=False
        )

        @app.on_event("startup")
        async def _init_x402_server() -> None:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, resource_server.initialize)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "x402 facilitator init failed at startup (%s); "
                    "payment verification may fail until the facilitator is reachable",
                    exc,
                )

        @app.middleware("http")
        async def payment_gate(request: Request, call_next):  # type: ignore[misc]
            if request.method == "POST" and request.url.path == "/claim":
                payment_header = request.headers.get(
                    "x-payment"
                ) or request.headers.get("payment-signature")
                if not payment_header:
                    return _make_payment_required_response()
                return await _x402(request, call_next)
            return await call_next(request)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/")
    async def landing() -> Dict[str, Any]:
        return {
            "api": "SafeAgent",
            "description": (
                "Execution Guard for AI Agents — prevents duplicate actions on "
                "crash-retry, webhook replay, and concurrent execution."
            ),
            "endpoints": {
                "POST /claim": {
                    "description": "x402-gated exactly-once claim",
                    "price": f"${_price} USDC per call",
                    "returns": "PROCEED | SKIP | PENDING",
                },
                "POST /claim/test": {
                    "description": "Free test claim — verify your integration before paying",
                    "limit": f"{_TEST_RATE_LIMIT} calls per IP address total",
                    "returns": "PROCEED | SKIP",
                },
                "POST /settle/{request_id}": {
                    "description": "Commit a PENDING claim with its result",
                    "price": "free",
                },
                "GET /audit": {
                    "description": "Filterable claim history",
                    "price": "free",
                },
                "POST /sweep": {
                    "description": "Reset stale PENDING rows",
                    "price": "free",
                },
                "GET /health": {
                    "description": "Liveness probe",
                    "price": "free",
                },
            },
        }

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/x402")
    async def well_known_x402() -> Dict[str, Any]:
        doc = getattr(app.state, "well_known_x402", None)
        if doc is None:
            raise HTTPException(
                status_code=404, detail="x402 payment gating is not configured"
            )
        return doc

    @app.post("/claim")
    async def claim(
        request: Request, body: Optional[ClaimRequest] = None
    ) -> Dict[str, Any]:
        """
        Two-phase exactly-once claim.

        ``{"status": "PROCEED"}``  — new claim; execute your action, then POST /settle/{request_id}.
        ``{"status": "SKIP", "existing": {...}}`` — already COMMITTED; reuse the stored result.
        ``{"status": "PENDING"}`` — another caller has this in-flight; retry after the TTL.

        Requires x402 payment when SAFEAGENT_PAYMENT_ADDRESS is set.
        """
        if body is None:
            raise HTTPException(
                status_code=422,
                detail="agent_id, action_type, and scope are required",
            )
        agent_id = _extract_agent_id(request)
        store: SQLiteExecutionStore = app.state.store
        request_id = _derive_request_id(body)

        existing = store.get(request_id)
        if existing is not None:
            if existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": request_id,
                    "agent_id": existing.get("agent_id"),
                    "existing": existing.get("result"),
                }
            return {
                "status": "PENDING",
                "request_id": request_id,
                "agent_id": existing.get("agent_id"),
            }

        if not store.claim(request_id, body.action_type, agent_id=agent_id):
            existing = store.get(request_id)
            if existing and existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": request_id,
                    "agent_id": existing.get("agent_id"),
                    "existing": existing.get("result"),
                }
            return {
                "status": "PENDING",
                "request_id": request_id,
                "agent_id": existing.get("agent_id") if existing else None,
            }

        return {
            "status": "PROCEED",
            "request_id": request_id,
            "agent_id": agent_id,
        }

    @app.post("/claim/test")
    async def claim_test(request: Request, body: ClaimRequest) -> Dict[str, Any]:
        """
        Free test claim — same logic as POST /claim but no x402 payment required.

        Rate-limited to 10 calls per IP address total. After the limit is reached,
        use POST /claim with an x402 payment header for production access.
        """
        client_ip = (request.client.host if request.client else "unknown")
        count = _test_ip_counts[client_ip]
        if count >= _TEST_RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "message": (
                        f"POST /claim/test is limited to {_TEST_RATE_LIMIT} calls per IP. "
                        "Use POST /claim with an x402 payment header for unlimited access."
                    ),
                    "paid_endpoint": "POST /claim",
                },
            )
        _test_ip_counts[client_ip] = count + 1
        calls_remaining = _TEST_RATE_LIMIT - _test_ip_counts[client_ip]

        store: SQLiteExecutionStore = app.state.store
        request_id = _derive_request_id(body)

        existing = store.get(request_id)
        if existing is not None:
            if existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": request_id,
                    "existing": existing.get("result"),
                    "test": True,
                    "calls_remaining": calls_remaining,
                }
            return {
                "status": "PENDING",
                "request_id": request_id,
                "test": True,
                "calls_remaining": calls_remaining,
            }

        if not store.claim(request_id, body.action_type):
            existing = store.get(request_id)
            if existing and existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": request_id,
                    "existing": existing.get("result"),
                    "test": True,
                    "calls_remaining": calls_remaining,
                }
            return {
                "status": "PENDING",
                "request_id": request_id,
                "test": True,
                "calls_remaining": calls_remaining,
            }

        return {
            "status": "PROCEED",
            "request_id": request_id,
            "test": True,
            "calls_remaining": calls_remaining,
        }

    @app.post("/settle/{request_id}")
    async def settle(request_id: str, body: SettleRequest) -> Dict[str, Any]:
        """Transition PENDING → COMMITTED with the execution result. Not payment-gated."""
        store: SQLiteExecutionStore = app.state.store
        existing = store.get(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="request_id not found")
        if existing["status"] == "COMMITTED":
            return {"status": "already_committed", "request_id": request_id}
        store.settle(request_id, body.result)
        return {"status": "committed", "request_id": request_id}

    @app.get("/audit")
    async def audit(
        agent_id: Optional[str] = Query(
            default=None, description="Filter by agent EVM wallet address"
        ),
        action: Optional[str] = Query(
            default=None, description="Filter by action name"
        ),
        status: Optional[str] = Query(
            default=None, description="Filter by status: PENDING or COMMITTED"
        ),
        from_ts: Optional[float] = Query(
            default=None,
            description="Include rows with claimed_at >= this Unix timestamp",
        ),
        to_ts: Optional[float] = Query(
            default=None,
            description="Include rows with claimed_at <= this Unix timestamp",
        ),
        limit: int = Query(default=100, ge=1, le=1000, description="Page size"),
        offset: int = Query(default=0, ge=0, description="Pagination offset"),
    ) -> Dict[str, Any]:
        """Claim history, optionally filtered. Results ordered newest-first. Not payment-gated."""
        store: SQLiteExecutionStore = app.state.store
        return store.audit_claims(
            agent_id=agent_id,
            action=action,
            status=status,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
            offset=offset,
        )

    @app.post("/sweep")
    async def sweep() -> Dict[str, Any]:
        """Reset stale PENDING rows to CLAIMABLE. Not payment-gated."""
        store: SQLiteExecutionStore = app.state.store
        swept = store.sweep_stale_pending()
        return {"swept": swept}

    return app


# ---------------------------------------------------------------------------
# Default application instance (used by uvicorn)
# ---------------------------------------------------------------------------

app = create_app()

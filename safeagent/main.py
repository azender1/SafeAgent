"""
SafeAgent — Execution Guard for AI Agents

Endpoints
---------
GET  /                  Landing page
POST /claim             x402-gated exactly-once claim → PROCEED / SKIP / PENDING
POST /claim/test        Free test claim (rate-limited: 10 calls per IP total)
POST /settle/{id}       Commit a PENDING claim with its result (free)
GET  /audit             Filterable claim history ($0.005 USDC via Orbis)
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

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, HTMLResponse
from pydantic import BaseModel

import os
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore
from safeagent_exec_guard import mycelium_trail
try:
    import safeagent_governance as _gov
    _GOV_ENABLED = True
except ImportError:
    _GOV_ENABLED = False
if os.environ.get("DATABASE_URL"):
    from safeagent_exec_guard.pg_store import PgExecutionStore

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
    request_id: str
    action: str


class TestClaimRequest(BaseModel):
    agent_id: str
    action_type: str
    scope: str


class SettleRequest(BaseModel):
    result: Dict[str, Any]


def _derive_test_request_id(body: TestClaimRequest) -> str:
    """SHA256(JCS({agent_id, action_type, scope})) -- content-addressed,
    collision-safe request_id for the free /claim/test endpoint.

    Previously this was plain colon concatenation
    (f"{agent_id}:{action_type}:{scope}"), which has the same collision
    class flagged by chopmob-cloud for "||" concatenation: a colon
    inside agent_id or scope can cause two different input tuples to
    produce the same request_id. JCS gives unambiguous field
    boundaries via canonical JSON encoding.

    No timestamp is included here (unlike the conformance fixture's
    action_ref, which is {agent_id, action_type, scope, timestamp}):
    /claim/test's dedup contract is "same (agent_id, action_type, scope)
    -> same request_id -> SKIP on retry", and adding a per-call
    timestamp would break that by making every call PROCEED.

    NOTE: this changes the request_id format returned by /claim/test
    for *new* calls (was a readable colon string, now a hex digest).
    Existing COMMITTED rows from the old format remain in the DB and
    are unaffected -- they're just not reachable via the new
    derivation, which is fine for a free, no-continuity-guaranteed
    test endpoint.
    """
    preimage = {
        "agent_id": body.agent_id,
        "action_type": body.action_type,
        "scope": body.scope,
    }
    return mycelium_trail.sha256hex(mycelium_trail.jcs(preimage))


def create_app(
    *,
    store: Optional[SQLiteExecutionStore] = None,
    payment_address: Optional[str] = None,
    claim_price_usdc: str = "0.001",
    network: str = "eip155:84532",
    facilitator_url: str = "https://api.cdp.coinbase.com/platform/v2/x402/facilitator",
    resource_url: str = "https://safeagent-production.up.railway.app/claim",
) -> FastAPI:
    if store is not None:
        _store = store
    elif os.environ.get("DATABASE_URL"):
        _store = PgExecutionStore(pending_ttl_seconds=float(os.getenv("SAFEAGENT_PENDING_TTL", "300")))
    else:
        _store = SQLiteExecutionStore(
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

    # ------------------------------------------------------------------
    # Global validation error handler — logs raw body on every 422
    # so failed /claim attempts are visible in Railway logs.
    # ------------------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        _log = logging.getLogger(__name__)
        try:
            raw = await request.body()
            _log.warning(
                "FAILED_ATTEMPT %s %s — errors: %s | raw_body: %s | content_type: %s | user_agent: %s",
                request.method,
                request.url.path,
                exc.errors(),
                raw.decode(errors="replace"),
                request.headers.get("content-type", ""),
                request.headers.get("user-agent", ""),
            )
        except Exception as _e:
            _log.warning("FAILED_ATTEMPT — could not read body: %s", _e)
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
        allow_headers=["*"],
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
            input={"request_id": "evt-abc123", "action": "send_email"},
            body_type="json",
            output=OutputConfig(
                example={
                    "status": "PROCEED",
                    "request_id": "evt-abc123",
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

    @app.get("/.well-known/ai-plugin.json")
    async def ai_plugin() -> Dict[str, Any]:
        return {
            "schema_version": "v1",
            "name_for_human": "SafeAgent Execution Guard",
            "name_for_model": "safeagent",
            "description_for_human": "Prevents duplicate payments, trades, emails and webhooks when AI agents retry. Claim before execute — PROCEED on first call, SKIP on retry.",
            "description_for_model": "SafeAgent is an exactly-once execution guard for AI agents. Before any irreversible action (payment, email, trade, webhook), call POST /claim/test with agent_id, action_type, and scope. Returns PROCEED if this is a new action, or SKIP with the cached result if it already ran. This prevents duplicate side effects when agents retry after crashes or timeouts. Free test endpoint, no payment required. Cited as normative requirement in A2A v0.4 RFC #1920.",
            "auth": {"type": "none"},
            "api": {
                "type": "openapi",
                "url": "https://safeagent-production.up.railway.app/openapi.json",
            },
            "logo_url": "https://safeagent-production.up.railway.app/favicon.ico",
            "contact_email": "azender1@yahoo.com",
            "legal_info_url": "https://github.com/azender1/SafeAgent",
        }

    @app.get("/.well-known/safeagent.json")
    async def safeagent_discovery() -> Dict[str, Any]:
        return {
            "name": "SafeAgent Execution Guard",
            "version": "0.1.21",
            "description": "Exactly-once execution guard for AI agents. Prevents duplicate payments, trades, emails, and webhooks when agents retry after crashes or timeouts.",
            "spec_ref": "a2aproject/A2A#1920 — cited as normative requirement in v0.4 RFC",
            "soma_listing": "https://soma-api.rgiskard.xyz/catalog",
            "endpoints": {
                "claim_test": {
                    "method": "POST",
                    "url": "https://safeagent-production.up.railway.app/claim/test",
                    "description": "Free exactly-once claim — no payment required. Returns PROCEED (new) or SKIP (duplicate).",
                    "payload": {
                        "agent_id": "your-agent-id",
                        "action_type": "payment.send | email.send | trade.execute | webhook.process",
                        "scope": "unique identifier for this specific action"
                    },
                    "returns": {
                        "PROCEED": "New claim — safe to execute your action",
                        "SKIP": "Already ran — return cached result, do not re-execute"
                    }
                },
                "claim_paid": {
                    "method": "POST",
                    "url": "https://safeagent-production.up.railway.app/claim",
                    "description": "x402-gated exactly-once claim. $0.001 USDC per call on Base.",
                    "x402": True
                },
                "settle": {
                    "method": "POST",
                    "url": "https://safeagent-production.up.railway.app/settle/{request_id}",
                    "description": "Mark a PENDING claim as COMMITTED after successful execution. Free.",
                },
                "audit": {
                    "method": "GET",
                    "url": "https://safeagent-production.up.railway.app/audit",
                    "description": "Full claim history. Filter by agent_id, status, timestamp range. $0.005 USDC via Orbis."
                }
            },
            "use_case": "Call /claim/test before any irreversible action. If PROCEED, execute and call /settle. If SKIP, return the cached result. Prevents duplicate charges, emails, trades on agent retry.",
            "github": "https://github.com/azender1/SafeAgent",
            "pypi": "pip install safeagent-exec-guard",
            "audit_service": {
                "url": "https://safeagent-production.up.railway.app/audit-service",
                "description": "Paid duplicate execution audit — $2,500 flat fee. Written report identifying every place your agent system can fire twice.",
                "contact": "azender1@yahoo.com"
            },
            "conformance": {
                "fixture": "https://github.com/azender1/SafeAgent/tree/main/docs/conformance",
                "spec": "argentum-core action-ref-v1 + A2A v0.4 RFC #1920",
                "verified_by": "kenneives (agentgraph-co), evidai (LemonCake)"
            }
        }

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots_txt() -> str:
        return "User-agent: *\nDisallow: /claim\nDisallow: /settle\nDisallow: /sweep\nAllow: /\nAllow: /audit\nAllow: /audit-service\nSitemap: https://safeagent-production.up.railway.app/sitemap.xml\n"

    @app.get("/sitemap.xml")
    async def sitemap_xml():
        from datetime import date
        from fastapi.responses import Response as _Resp
        today = date.today().isoformat()
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://safeagent-production.up.railway.app/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://safeagent-production.up.railway.app/audit-service</loc>
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://safeagent-production.up.railway.app/docs</loc>
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
        return _Resp(content=xml, media_type="application/xml")

    @app.get("/llms.txt", response_class=PlainTextResponse)
    async def llms_txt() -> str:
        with open("llms.txt") as f:
            return f.read()

    @app.get("/.well-known/agent.json")
    async def agent_json():
        import json
        with open(".well-known/agent.json") as f:
            return json.load(f)

    @app.get("/.well-known/mcp.json")
    async def mcp_json():
        import json
        with open(".well-known/mcp.json") as f:
            return json.load(f)

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def landing() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SafeAgent — Execution Guard for AI Agents</title>
<meta name="description" content="Exactly-once execution guard for AI agents. Prevents duplicate payments, emails, trades and webhooks when agents retry. BIP-340 signed receipts. EU AI Act Art. 12 compliant audit trail.">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "SafeAgent",
  "description": "Exactly-once execution guard for AI agents. Prevents duplicate payments, emails, trades and webhooks when agents retry after crashes or timeouts.",
  "applicationCategory": "DeveloperApplication",
  "operatingSystem": "Any",
  "url": "https://safeagent-production.up.railway.app",
  "author": {
    "@type": "Person",
    "name": "Anthony Zender"
  },
  "offers": {
    "@type": "Offer",
    "price": "0.001",
    "priceCurrency": "USD",
    "description": "Per claim via x402 micropayment"
  },
  "codeRepository": "https://github.com/azender1/SafeAgent",
  "keywords": "AI agent, exactly-once execution, idempotency, duplicate prevention, EU AI Act, agent governance"
}
</script>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 720px; margin: 60px auto; padding: 0 24px; color: #1a1a1a; line-height: 1.6; }
  h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 4px; }
  .tagline { color: #555; margin-bottom: 32px; font-size: 1.05rem; }
  .badge { display: inline-block; background: #e8f5e9; color: #2e7d32; border-radius: 4px; padding: 2px 8px; font-size: 0.8rem; font-weight: 600; margin-bottom: 24px; }
  h2 { font-size: 1.1rem; font-weight: 600; margin-top: 32px; margin-bottom: 8px; }
  code { background: #f4f4f4; border-radius: 4px; padding: 2px 6px; font-size: 0.9rem; }
  pre { background: #f4f4f4; border-radius: 6px; padding: 16px; overflow-x: auto; font-size: 0.85rem; }
  table { width: 100%; border-collapse: collapse; margin: 16px 0; }
  th { text-align: left; border-bottom: 2px solid #eee; padding: 8px 0; font-size: 0.85rem; color: #555; }
  td { border-bottom: 1px solid #eee; padding: 8px 0; font-size: 0.9rem; }
  .endpoint { font-family: monospace; }
  a { color: #1a73e8; text-decoration: none; }
  a:hover { text-decoration: underline; }
  hr { border: none; border-top: 1px solid #eee; margin: 32px 0; }
</style>
</head>
<body>
<h1>SafeAgent</h1>
<div class="tagline">Exactly-once execution guard for AI agents and SaaS applications.</div>
<span class="badge">&#10003; Verified on Soma &mdash; First Integrator</span>
<p style="font-size: 0.9rem; color: #555; margin: 8px 0 24px;">496 installs this month &middot; 520 GitHub clones &middot; Cited in Stripe, CrewAI, A2A, AutoGen threads &middot; Live audit trail on Postgres</p>
<p>Prevents duplicate payments, emails, trades, and webhook processing when agents retry after a crash or timeout. Claim before you execute. Commit after. Every retry returns the same receipt.</p>
<h2>State machine</h2>
<p><code>PENDING &rarr; COMMITTED | SKIP</code></p>
<h2>Endpoints</h2>
<table>
  <tr><th>Method</th><th>Path</th><th>Description</th><th>Cost</th></tr>
  <tr><td>POST</td><td class="endpoint">/claim</td><td>Gate an action &mdash; returns PROCEED or SKIP</td><td>$0.001 USDC</td></tr>
  <tr><td>POST</td><td class="endpoint">/claim/test</td><td>Free test endpoint (10 calls/IP)</td><td>Free</td></tr>
  <tr><td>POST</td><td class="endpoint">/settle/{id}</td><td>Commit a PENDING claim</td><td>Free</td></tr>
  <tr><td>GET</td><td class="endpoint">/audit</td><td>Full claim history with filters</td><td>$0.005 USDC</td></tr>
  <tr><td>GET</td><td class="endpoint">/health</td><td>Liveness probe</td><td>Free</td></tr>
</table>
<h2>Quick start</h2>
<pre>curl -s -X POST https://safeagent-production.up.railway.app/claim/test \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"my-agent","action_type":"send_payment","scope":"customer:123"}'
# First call: {"status":"PROCEED","test":true,"calls_remaining":9}
# Retry:      {"status":"SKIP","test":true}</pre>
<hr>
<h2>On-chain audit trail</h2>
<p>Every production execution is anchored on <a href="https://soma-api.rgiskard.xyz/catalog" target="_blank">Soma</a> via Mycelium Trails on Arbitrum.</p>
<p><a href="https://argentum-api.rgiskard.xyz/dashboard/trails?client=safeagent-prod" target="_blank">View live trails &rarr;</a></p>
<hr>
<p>
  <a href="https://github.com/azender1/SafeAgent" target="_blank">GitHub</a> &middot;
  <a href="/docs">API Docs</a> &middot;
  <a href="/audit-service">Audit Service</a> &middot;
  <a href="https://pypi.org/project/safeagent-exec-guard/" target="_blank">PyPI</a>
</p>
</body>
</html>"""

    @app.api_route("/audit-service", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def audit_service() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SafeAgent — Duplicate Execution Audit Service | EU AI Act Compliance</title>
<meta name="description" content="AI agent duplicate execution audit. Written report identifying every place your agent can fire twice. EU AI Act Art. 12 readiness. $2,500 flat fee, 5 business days.">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "ProfessionalService",
  "name": "SafeAgent Duplicate Execution Audit",
  "description": "AI agent duplicate execution audit service. Identifies every place your agent system can fire twice on crash, timeout, or duplicate signal. Includes EU AI Act Art. 12 audit readiness assessment.",
  "provider": {
    "@type": "Person",
    "name": "Anthony Zender",
    "jobTitle": "AI Systems Auditor",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Dayton",
      "addressRegion": "OH",
      "addressCountry": "US"
    }
  },
  "offers": {
    "@type": "Offer",
    "price": "2500",
    "priceCurrency": "USD",
    "description": "Flat fee. Written report. 5 business days."
  },
  "serviceType": "AI Compliance Audit",
  "areaServed": "Worldwide",
  "url": "https://safeagent-production.up.railway.app/audit-service",
  "email": "azender1@yahoo.com",
  "keywords": "EU AI Act compliance, AI agent audit, duplicate execution, Art. 12, agentic AI governance"
}
</script>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 720px; margin: 60px auto; padding: 0 24px; color: #1a1a1a; line-height: 1.6; }
  h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 4px; }
  .tagline { color: #555; margin-bottom: 32px; font-size: 1.05rem; }
  h2 { font-size: 1.1rem; font-weight: 600; margin-top: 32px; margin-bottom: 8px; }
  ul { padding-left: 20px; }
  li { margin-bottom: 8px; }
  .price { font-size: 2rem; font-weight: 700; color: #1a73e8; margin: 16px 0; }
  .price span { font-size: 1rem; font-weight: 400; color: #555; }
  .proof { background: #f4f4f4; border-radius: 6px; padding: 16px; margin: 16px 0; font-size: 0.9rem; }
  a { color: #1a73e8; text-decoration: none; }
  .cta { background: #1a73e8; color: white; display: inline-block; padding: 12px 24px; border-radius: 6px; margin-top: 16px; font-weight: 600; font-size: 1rem; }
  hr { border: none; border-top: 1px solid #eee; margin: 32px 0; }
</style>
</head>
<body>
<h1>Duplicate Execution Audit</h1>
<div class="tagline">Find every place your AI agent can fire twice before it costs you money.</div>
<div class="price">$2,500 <span>flat fee &middot; written report &middot; 5 business days</span></div>
<h2>What you get</h2>
<ul>
  <li>Full review of your agent's retry paths and side-effect boundaries</li>
  <li>Every action that can execute twice on crash, timeout, or duplicate signal &mdash; identified and documented</li>
  <li>Risk classification by severity and dollar exposure</li>
  <li>SafeAgent integration recommendations with code examples</li>
  <li>EU AI Act Art. 12 audit readiness assessment (deadline: August 2026)</li>
  <li>Written report delivered via email</li>
</ul>
<h2>Who this is for</h2>
<ul>
  <li>Companies running AI agents that touch payments, orders, emails, or webhooks</li>
  <li>Teams preparing for EU AI Act compliance (August 2026)</li>
  <li>Anyone who has seen a duplicate charge or phantom position and doesn't know why</li>
</ul>
<h2>Production proof</h2>
<div class="proof">
  Six duplicate execution attempts blocked in a single live trading session on May 21, 2026. Total exposure: <strong>$3,653</strong>. Every block is on-chain and independently verifiable.<br><br>
  <a href="https://gist.github.com/azender1/b9112b6519c935df4a75cb05cd250e26" target="_blank">View session data &rarr;</a> &middot;
  <a href="https://argentum-api.rgiskard.xyz/dashboard/trails?client=safeagent-prod" target="_blank">View live trails &rarr;</a>
</div>
<h2>About</h2>
<p>Built by Anthony Zender &mdash; tax accountant, Dayton OH. I found this problem running a live trading bot and a patented wagering system. Both hit the same failure mode. I audit agent systems the same way I audit financials: every entry, every retry path, every place something can post twice.</p>
<hr>
<a href="mailto:azender1@yahoo.com" class="cta">Request an audit &rarr; azender1@yahoo.com</a>
<p style="margin-top: 32px; font-size: 0.85rem; color: #888;"><a href="/">&#8592; Back to SafeAgent</a></p>
</body>
</html>"""

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

    @app.get("/claim/{request_id}/proof")
    async def claim_proof(request_id: str) -> Dict[str, Any]:
        """
        Return the signed governance receipt for a claim — offline verifiable.

        The verifier_pubkey + signature + envelope_hash are sufficient to confirm
        that SafeAgent (an independent party) pre-authorized this action before
        execution, without calling back to this server.

        Verification (any BIP-340 library):
            msg32  = bytes.fromhex(envelope_hash)
            sig64  = bytes.fromhex(signature)
            pubkey = bytes.fromhex(verifier_pubkey)
            # pure stdlib: safeagent_governance._bip340_verify(pubkey, msg32, sig64)
            # bitcoin-lib: secp256k1.PublicKey(pubkey).schnorr_verify(msg32, sig64)
        """
        store: SQLiteExecutionStore = app.state.store
        existing = store.get(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="request_id not found")

        # If store has governance data, return it
        if hasattr(store, "get_governance"):
            gov_data = store.get_governance(request_id)
            if gov_data:
                return {
                    "request_id": request_id,
                    "status": existing.get("status"),
                    **gov_data,
                    "conformance_suite": "https://github.com/babyblueviper1/preaction-governance-conformance",
                }

        # Fallback: recompute on the fly if key is available
        if _GOV_ENABLED and existing.get("claimed_at"):
            import time as _time
            claimed_at_ms = int(existing["claimed_at"] * 1000) if existing.get("claimed_at") else int(_time.time() * 1000)
            built = _gov.build_envelope(
                request_id=request_id,
                action=existing.get("action", ""),
                agent_id=existing.get("agent_id"),
                claimed_at_ms=claimed_at_ms,
            )
            sig_data = _gov.sign_envelope(built["envelope_hash"])
            if sig_data:
                return {
                    "request_id": request_id,
                    "status": existing.get("status"),
                    "envelope_hash": built["envelope_hash"],
                    "canonical_bytes_utf8": built["canonical_bytes_utf8"],
                    "verifier_pubkey": sig_data["verifier_pubkey"],
                    "signature": sig_data["signature"],
                    "sig_scheme": sig_data["sig_scheme"],
                    "note": "signature recomputed on demand (governance not persisted for this claim)",
                    "conformance_suite": "https://github.com/babyblueviper1/preaction-governance-conformance",
                }

        raise HTTPException(
            status_code=503,
            detail="governance signing not configured (SAFEAGENT_GOVERNANCE_PRIVKEY not set)",
        )

    @app.get("/claim/{request_id}/anchor")
    async def claim_anchor(request_id: str) -> Dict[str, Any]:
        """
        Return the OpenTimestamps proof for a claim's governance envelope.

        The .ots proof anchors the claim to Bitcoin — proving the authorization
        existed before a specific block time, independent of any clock SafeAgent controls.

        Verify offline:
            pip install opentimestamps-client
            ots verify <downloaded .ots file>
        """
        store: SQLiteExecutionStore = app.state.store
        existing = store.get(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="request_id not found")

        if hasattr(store, "get_governance"):
            gov_data = store.get_governance(request_id)
            if gov_data and gov_data.get("ots_proof_hex"):
                confirmed = gov_data.get("ots_confirmed", False)
                block_time = gov_data.get("ots_block_time", None)
                return {
                    "request_id": request_id,
                    "envelope_hash": gov_data.get("envelope_hash"),
                    "ots_proof_hex": gov_data["ots_proof_hex"],
                    "anchor_status": "confirmed" if confirmed else "submitted",
                    "block_time": block_time,
                    "ordering_assertable": confirmed,
                    "verify_command": "ots verify <proof.ots>",
                    "note": (
                        f"Bitcoin-confirmed. Block time: {block_time}. Ordering is assertable."
                        if confirmed else
                        "OTS submitted to Bitcoin calendar. Confirmation typically 1-2 hours. "
                        "Recheck — anchor_status will update to confirmed and ordering_assertable will become true."
                    ),
                }

        return {
            "request_id": request_id,
            "anchor_status": "not_submitted",
            "ordering_assertable": False,
            "note": "OTS anchoring runs as a background task after claim. Retry in ~30 seconds.",
        }

    @app.get("/governance/pubkey")
    async def governance_pubkey() -> Dict[str, Any]:
        """
        Return SafeAgent's published governance public key.
        Use this to verify claim signatures offline without trusting this server.
        """
        pubkey = _gov.get_pubkey_hex() if _GOV_ENABLED else None
        if pubkey is None:
            raise HTTPException(status_code=503, detail="governance not configured")
        return {
            "verifier_pubkey": pubkey,
            "sig_scheme": "bip340-schnorr",
            "description": (
                "SafeAgent's x-only secp256k1 public key. "
                "Use to verify claim receipts offline per BIP-340."
            ),
            "conformance_suite": "https://github.com/babyblueviper1/preaction-governance-conformance",
        }

    @app.post("/claim")
    async def claim(
        request: Request, background_tasks: BackgroundTasks, body: Optional[ClaimRequest] = None
    ) -> Dict[str, Any]:
        """
        Two-phase exactly-once claim.

        ``{"status": "PROCEED"}``  — new claim; execute your action, then POST /settle/{request_id}.
        ``{"status": "SKIP", "existing": {...}}`` — already COMMITTED; reuse the stored result.
        ``{"status": "PENDING"}`` — another caller has this in-flight; retry after the TTL.

        Requires x402 payment when SAFEAGENT_PAYMENT_ADDRESS is set.
        """
        if body is None:
            _log = logging.getLogger(__name__)
            try:
                raw = await request.body()
                _log.warning(
                    "FAILED_ATTEMPT POST /claim — body is None | raw_body: %s | content_type: %s | user_agent: %s",
                    raw.decode(errors="replace"),
                    request.headers.get("content-type", ""),
                    request.headers.get("user-agent", ""),
                )
            except Exception as _e:
                _log.warning("FAILED_ATTEMPT POST /claim — could not read body: %s", _e)
            raise HTTPException(
                status_code=422,
                detail="request_id and action are required",
            )

        agent_id = _extract_agent_id(request)
        store: SQLiteExecutionStore = app.state.store

        # ------------------------------------------------------------------
        # Attestation gate — verify AgentGraph safety verdict before
        # allowing any COMMITTED write. Keyed on body.action (the tool/
        # endpoint identity), not request_id (the exactly-once key).
        # require_attestation=False (default): SKIP on unreachable/absent,
        # recorded as safety_skipped — never a silent pass.
        # require_attestation=True: DENY before COMMITTED write.
        # ------------------------------------------------------------------
        _require_attestation = os.getenv("SAFEAGENT_REQUIRE_ATTESTATION", "false").lower() == "true"
        try:
            from safeagent_exec_guard.attestation_gate import gate as _attestation_gate
            import httpx as _httpx
            _agentgraph_jwks_url = os.getenv(
                "AGENTGRAPH_JWKS_URL",
                "https://agentgraph.co/.well-known/jwks.json"
            )
            _agentgraph_attestation_url = os.getenv(
                "AGENTGRAPH_ATTESTATION_URL",
                "https://agentgraph.co/x402/attestation"
            )
            # Fetch live attestation by tool/endpoint identity (body.action)
            _attestation = None
            _attestation_error = None
            try:
                async with _httpx.AsyncClient(timeout=5.0) as _http:
                    _resp = await _http.get(
                        _agentgraph_attestation_url,
                        params={"endpoint": body.action}
                    )
                    if _resp.status_code == 200:
                        _attestation = _resp.json()
            except Exception as _fetch_err:
                _attestation_error = str(_fetch_err)

            # Fetch JWKS for offline signature verification
            _jwks = None
            try:
                async with _httpx.AsyncClient(timeout=5.0) as _http:
                    _jwks_resp = await _http.get(_agentgraph_jwks_url)
                    if _jwks_resp.status_code == 200:
                        _jwks = _jwks_resp.json()
            except Exception:
                pass

            # Build preimage for binding_digest (amount/charge fields optional here)
            _preimage = {
                "agent_id": agent_id or "",
                "action_type": body.action,
                "scope": body.request_id,
                "timestamp": "",
            }
            _gate_result = _attestation_gate(
                _preimage,
                _attestation,
                jwks=_jwks,
                require_attestation=_require_attestation,
            )
            if _gate_result.get("decision") == "DENY":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "safety_denied",
                        "reason": _gate_result.get("reason", "safety verdict denied"),
                        "action": body.action,
                    },
                )
            # Record attestation outcome in store metadata for audit trail
            _safety_meta = {
                "safety_decision": _gate_result.get("decision"),
                "safety_reason": _gate_result.get("reason"),
                "attestation_error": _attestation_error,
            }
        except HTTPException:
            raise
        except ImportError:
            logging.getLogger(__name__).warning(
                "attestation_gate not importable — safety check skipped"
            )
            _safety_meta = {"safety_decision": "skipped", "safety_reason": "import_error"}
        except Exception as _gate_err:
            logging.getLogger(__name__).warning(
                "attestation_gate error (%s) — applying require_attestation policy", _gate_err
            )
            if _require_attestation:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "safety_denied", "reason": "attestation_gate_error"},
                )
            _safety_meta = {"safety_decision": "skipped", "safety_reason": str(_gate_err)}

        existing = store.get(body.request_id)
        if existing is not None:
            if existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": body.request_id,
                    "agent_id": existing.get("agent_id"),
                    "existing": existing.get("result"),
                }
            return {
                "status": "PENDING",
                "request_id": body.request_id,
                "agent_id": existing.get("agent_id"),
            }

        if not store.claim(body.request_id, body.action, agent_id=agent_id):
            existing = store.get(body.request_id)
            if existing and existing["status"] == "COMMITTED":
                return {
                    "status": "SKIP",
                    "request_id": body.request_id,
                    "agent_id": existing.get("agent_id"),
                    "existing": existing.get("result"),
                }
            return {
                "status": "PENDING",
                "request_id": body.request_id,
                "agent_id": existing.get("agent_id") if existing else None,
            }

        # -- Governance: sign claim envelope + schedule OTS anchor --
        import time as _time
        _claimed_at_ms = int(_time.time() * 1000)
        _gov_fields: Dict[str, Any] = {}
        if _GOV_ENABLED:
            try:
                _built = _gov.build_envelope(
                    request_id=body.request_id,
                    action=body.action,
                    agent_id=agent_id,
                    claimed_at_ms=_claimed_at_ms,
                )
                _sig = _gov.sign_envelope(_built["envelope_hash"])
                if _sig:
                    _gov_fields = {
                        "governance": {
                            "envelope_hash": _built["envelope_hash"],
                            "canonical_bytes_utf8": _built["canonical_bytes_utf8"],
                            "verifier_pubkey": _sig["verifier_pubkey"],
                            "signature": _sig["signature"],
                            "sig_scheme": _sig["sig_scheme"],
                            "anchor_endpoint": (
                                f"https://safeagent-production.up.railway.app"
                                f"/claim/{body.request_id}/anchor"
                            ),
                            "proof_endpoint": (
                                f"https://safeagent-production.up.railway.app"
                                f"/claim/{body.request_id}/proof"
                            ),
                        }
                    }
                    background_tasks.add_task(
                        _gov.attach_governance_async,
                        request_id=body.request_id,
                        action=body.action,
                        agent_id=agent_id,
                        claimed_at_ms=_claimed_at_ms,
                        store=store,
                    )
            except Exception as _gov_err:
                logging.getLogger(__name__).warning("governance signing error: %s", _gov_err)

        return {
            "status": "PROCEED",
            "request_id": body.request_id,
            "agent_id": agent_id,
            **_gov_fields,
        }

    @app.post("/claim/test")
    async def claim_test(request: Request, background_tasks: BackgroundTasks, body: TestClaimRequest) -> Dict[str, Any]:
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
        request_id = _derive_test_request_id(body)

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
            store.settle(request_id, {"skipped": True, "test": True})
            return {
                "status": "SKIP",
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
                "status": "SKIP",
                "request_id": request_id,
                "test": True,
                "calls_remaining": calls_remaining,
            }

        # -- Governance: sign claim envelope + schedule OTS anchor --
        import time as _time
        _claimed_at_ms_t = int(_time.time() * 1000)
        _gov_fields_t: Dict[str, Any] = {}
        if _GOV_ENABLED:
            try:
                _built_t = _gov.build_envelope(
                    request_id=request_id,
                    action=body.action_type,
                    agent_id=body.agent_id,
                    claimed_at_ms=_claimed_at_ms_t,
                )
                _sig_t = _gov.sign_envelope(_built_t["envelope_hash"])
                if _sig_t:
                    _gov_fields_t = {
                        "governance": {
                            "envelope_hash": _built_t["envelope_hash"],
                            "canonical_bytes_utf8": _built_t["canonical_bytes_utf8"],
                            "verifier_pubkey": _sig_t["verifier_pubkey"],
                            "signature": _sig_t["signature"],
                            "sig_scheme": _sig_t["sig_scheme"],
                            "anchor_endpoint": (
                                f"https://safeagent-production.up.railway.app"
                                f"/claim/{request_id}/anchor"
                            ),
                            "proof_endpoint": (
                                f"https://safeagent-production.up.railway.app"
                                f"/claim/{request_id}/proof"
                            ),
                        }
                    }
                    background_tasks.add_task(
                        _gov.attach_governance_async,
                        request_id=request_id,
                        action=body.action_type,
                        agent_id=body.agent_id,
                        claimed_at_ms=_claimed_at_ms_t,
                        store=store,
                    )
            except Exception as _gov_err_t:
                logging.getLogger(__name__).warning("governance signing error (test): %s", _gov_err_t)

        return {
            "status": "PROCEED",
            "request_id": request_id,
            "test": True,
            "calls_remaining": calls_remaining,
            **_gov_fields_t,
        }

    @app.post("/settle/{request_id}")
    async def settle(request_id: str, body: SettleRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
        """Transition PENDING → COMMITTED with the execution result. Not payment-gated."""
        store: SQLiteExecutionStore = app.state.store
        existing = store.get(request_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="request_id not found")
        if existing["status"] == "COMMITTED":
            return {"status": "already_committed", "request_id": request_id}
        store.settle(request_id, body.result)

        if mycelium_trail.enabled():
            background_tasks.add_task(
                mycelium_trail.submit_trail_async,
                request_id=request_id,
                action=existing["action"],
                agent_id=existing.get("agent_id"),
                claimed_at=existing["claimed_at"],
                result=body.result,
            )

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

    @app.post("/sweep/anchor/upgrade")
    async def sweep_anchor_upgrade() -> Dict[str, Any]:
        """
        Check OTS calendar for Bitcoin confirmation on all submitted-but-unconfirmed claims.
        Upgrades incomplete OTS timestamps and flips ots_confirmed=True + block_time when confirmed.
        Call on a schedule (every 15-30 min) — Bitcoin blocks confirm every ~10 min.
        Safe to call repeatedly — skips already-confirmed claims.
        """
        if not _GOV_ENABLED:
            return {"status": "governance_not_configured", "confirmed": 0, "pending": 0}

        store = app.state.store
        _log = logging.getLogger(__name__)
        confirmed = 0
        pending = 0
        errors = 0

        try:
            if not hasattr(store, "get_submitted_unconfirmed_claims"):
                return {"status": "store_not_supported", "confirmed": 0}

            candidates = store.get_submitted_unconfirmed_claims()
            _log.info("sweep/anchor/upgrade: checking %d submitted claims", len(candidates))

            for record in candidates:
                request_id = record.get("request_id")
                ots_proof_hex = record.get("gov_ots_proof_hex")
                if not request_id or not ots_proof_hex:
                    continue
                try:
                    result = _gov.check_ots_confirmation(ots_proof_hex)
                    if result and result.get("confirmed"):
                        store.confirm_governance(
                            request_id=request_id,
                            block_time=result["block_time"],
                        )
                        confirmed += 1
                        _log.info(
                            "sweep/anchor/upgrade: CONFIRMED %s block=%s",
                            request_id[:8],
                            result["block_time"],
                        )
                    else:
                        pending += 1
                except Exception as e:
                    errors += 1
                    _log.warning("sweep/anchor/upgrade: error on %s: %s", request_id[:8], e)

        except Exception as e:
            _log.warning("sweep/anchor/upgrade: outer error: %s", e)
            return {"status": "error", "error": str(e), "confirmed": confirmed}

        return {
            "status": "ok",
            "confirmed": confirmed,
            "pending": pending,
            "errors": errors,
        }

    @app.post("/sweep/anchor")
    async def sweep_anchor() -> Dict[str, Any]:
        """
        Cron job endpoint — submits OTS anchoring for any PROCEED claims
        that have a governance signature but no OTS proof yet.

        Wire this as a Railway cron: POST /sweep/anchor every 5 minutes.
        Safe to call repeatedly — skips claims that already have a proof.
        """
        if not _GOV_ENABLED:
            return {"status": "governance_not_configured", "submitted": 0}

        store: SQLiteExecutionStore = app.state.store
        submitted = 0
        skipped = 0
        failed = 0

        _log = logging.getLogger(__name__)
        try:
            # Get all records that have governance signing but no OTS yet
            if not hasattr(store, "get_unanchored_claims"):
                result = store.audit_claims(limit=500)
                # audit_claims returns {"items": [...], "total": N, ...}
                rows = result.get("items", []) if isinstance(result, dict) else []
                candidates = [
                    r for r in rows
                    if isinstance(r, dict) and r.get("status") in ("COMMITTED", "PENDING", "CLAIMABLE")
                ]
            else:
                candidates = store.get_unanchored_claims()

            for record in candidates:
                request_id = record.get("request_id") or record.get("id")
                if not request_id:
                    continue

                # Check if already anchored
                if hasattr(store, "get_governance"):
                    gov = store.get_governance(request_id)
                    if gov and gov.get("ots_proof_hex"):
                        skipped += 1
                        continue

                # Build envelope and submit OTS
                try:
                    import time as _time
                    claimed_at = record.get("claimed_at") or record.get("created_at")
                    claimed_at_ms = int(claimed_at * 1000) if claimed_at else int(_time.time() * 1000)

                    built = _gov.build_envelope(
                        request_id=request_id,
                        action=record.get("action", ""),
                        agent_id=record.get("agent_id"),
                        claimed_at_ms=claimed_at_ms,
                    )
                    sig_data = _gov.sign_envelope(built["envelope_hash"])
                    if not sig_data:
                        failed += 1
                        continue

                    ots_bytes = _gov.stamp_envelope(built["envelope_hash"])
                    ots_hex = ots_bytes.hex() if ots_bytes else None

                    if ots_hex:
                        # Persist if store supports it
                        if hasattr(store, "attach_governance"):
                            store.attach_governance(
                                request_id=request_id,
                                envelope_hash=built["envelope_hash"],
                                canonical_bytes=built["canonical_bytes_utf8"],
                                signature=sig_data["signature"],
                                verifier_pubkey=sig_data["verifier_pubkey"],
                                ots_proof_hex=ots_hex,
                            )
                        submitted += 1
                        _log.info("sweep/anchor: OTS submitted for %s hash=%s", request_id[:8], built["envelope_hash"][:16])
                    else:
                        failed += 1
                        _log.warning("sweep/anchor: OTS calendar unreachable for %s", request_id[:8])

                except Exception as e:
                    failed += 1
                    _log.warning("sweep/anchor: error on %s: %s", request_id[:8] if request_id else "?", e)

        except Exception as e:
            _log.warning("sweep/anchor: outer error: %s", e)
            return {"status": "error", "error": str(e), "submitted": submitted}

        return {
            "status": "ok",
            "submitted": submitted,
            "skipped": skipped,
            "failed": failed,
        }

    return app


# ---------------------------------------------------------------------------
# Default application instance (used by uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
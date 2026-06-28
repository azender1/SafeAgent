"""
mycelium_trail — best-effort Mycelium Trails submission for SafeAgent.

On a successful /settle (PENDING -> COMMITTED), optionally writes a
post-execution TrailRecord to Mycelium Trails (argentum-core /trails),
per docs/MYCELIUM_TRAILS_REFERENCE.md.

Design constraints
-------------------
- Never blocks or fails a /settle call. All network errors are caught
  and logged; the SafeAgent guard's own guarantees do not depend on
  Mycelium being reachable.
- Off by default. Enable with MYCELIUM_ENABLED=1.
- Fire-and-forget: submit_trail_async() schedules the POST on a
  background asyncio task.

action_ref derivation
----------------------
Uses the SAME derivation as SafeAgent's published conformance fixture
(docs/conformance/exactly-once-v1.json, "exactly-once-v1.1"):

    action_ref = SHA256( JCS({agent_id, action_type, scope, timestamp}) )

JCS = RFC 8785 canonical JSON (lexicographic key order, no whitespace,
UTF-8) -- see docs/conformance/verify_fixture.py:jcs() for the reference
implementation this mirrors.

Field mapping note (SafeAgent -> action_ref preimage)
------------------------------------------------------
SafeAgent's execution_requests table does not have a separate `scope`
column (the conformance fixture's example preimage includes one, but
it isn't part of SafeAgent's live /claim or /settle request bodies).
For this trail submission:

    agent_id     -> row["agent_id"]  (or "anonymous" if x402 not used)
    action_type  -> row["action"]
    scope        -> request_id itself (the caller's logical-operation
                     identifier is the closest analog SafeAgent has to
                     "scope")
    timestamp    -> row["claimed_at"], converted to RFC3339 UTC with
                     millisecond precision, e.g. "2026-06-08T20:00:00.000Z"
                     (using claimed_at rather than committed_at keeps
                     action_ref stable across PENDING -> COMMITTED, per
                     the conformance fixture's invariant #3)

This is an explicit adaptation, not a claim that SafeAgent implements a
`scope` field equivalent to argentum-core's. If/when SafeAgent's API
gains a real `scope` field, swap it in here.

payment_hash field
-------------------
The TrailRecord schema's `payment_hash` is documented as "Lightning
payment hash or on-chain tx hash". SafeAgent's /settle is not payment-
gated, so there is no real payment hash to report. We submit
SHA256(JCS(result)) as a content-addressed "settlement digest" instead
-- this lets a verifier confirm the trail corresponds to a specific
settlement result, without claiming it's a real payment reference.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("safeagent.mycelium_trail")

_DEFAULT_BASE_URL = "https://argentum.rgiskard.xyz"
_DEFAULT_SERVICE = "safeagent"
_DEFAULT_TIMEOUT = 8.0  # seconds


def enabled() -> bool:
    """True if MYCELIUM_ENABLED is set to a truthy value. Public so
    callers (e.g. main.py) can check before scheduling a background
    task at all."""
    return os.environ.get("MYCELIUM_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _base_url() -> str:
    return os.environ.get("MYCELIUM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _service() -> str:
    return os.environ.get("MYCELIUM_SERVICE", _DEFAULT_SERVICE)


def _agent_id_fallback() -> str:
    return os.environ.get("MYCELIUM_AGENT_ID", "safeagent-prod")


def _api_key() -> Optional[str]:
    return os.environ.get("MYCELIUM_API_KEY") or None


def jcs(obj: Dict[str, Any]) -> bytes:
    """RFC 8785 JCS: lexicographic key order, no spaces, UTF-8.

    Mirrors docs/conformance/verify_fixture.py:jcs() exactly, so
    action_ref values computed here are byte-identical to the
    conformance fixture's derivation for the same inputs.
    """
    return json.dumps(
        dict(sorted(obj.items())),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso_ms(ts: float) -> str:
    """Convert a Unix timestamp (float seconds) to RFC3339 UTC with
    millisecond precision, e.g. '2026-06-08T20:00:00.000Z'."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def compute_action_ref(agent_id: str, action_type: str, scope: str, timestamp_iso: str) -> str:
    """SHA256(JCS({agent_id, action_type, scope, timestamp})) -- same
    derivation as exactly-once-v1.1."""
    preimage = {
        "agent_id": agent_id,
        "action_type": action_type,
        "scope": scope,
        "timestamp": timestamp_iso,
    }
    return sha256hex(jcs(preimage))


def _settlement_digest(result: Dict[str, Any]) -> str:
    """Content-addressed digest of the settlement result. Used as
    payment_hash since SafeAgent /settle has no real payment hash."""
    try:
        canonical = jcs(result)
    except TypeError:
        # result may contain non-JCS-friendly values; fall back to
        # standard json with sorted keys.
        canonical = json.dumps(result, sort_keys=True, default=str).encode("utf-8")
    return sha256hex(canonical)


def build_trail_payload(
    *,
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at: float,
    result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the TrailRecord payload per MYCELIUM_TRAILS_REFERENCE.md,
    using the field mapping documented at the top of this module."""
    resolved_agent_id = agent_id or _agent_id_fallback()
    timestamp_iso = _iso_ms(claimed_at)
    action_ref = compute_action_ref(
        agent_id=resolved_agent_id,
        action_type=action,
        scope=request_id,
        timestamp_iso=timestamp_iso,
    )
    result_obj = result or {}

    return {
        "entity_id": resolved_agent_id,
        "entity_name": resolved_agent_id,
        "entity_type": "ai_agent",
        "action_type": action,
        "description": f"SafeAgent execution: {action} request_id={request_id}",
        "proof": action_ref,
        "timestamp": int(claimed_at),
        "author_id": resolved_agent_id,
        "author_name": resolved_agent_id,
        "name": f"SafeAgent: {action}",
        "steps": [],
        "price_sats": 0,
    }


async def submit_trail_async(
    *,
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at: float,
    result: Optional[Dict[str, Any]],
) -> None:
    """Fire-and-forget: POST a TrailRecord to Mycelium Trails.

    Never raises. Logs success/failure at DEBUG/WARNING. No-op unless
    MYCELIUM_ENABLED is truthy.
    """
    if not enabled():
        return

    payload = build_trail_payload(
        request_id=request_id,
        action=action,
        agent_id=agent_id,
        claimed_at=claimed_at,
        result=result,
    )

    headers = {"Content-Type": "application/json"}
    api_key = _api_key()
    if api_key:
        headers["X-API-Key"] = api_key

    url = f"{_base_url()}/trails"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "Mycelium trail submission failed (%s): %s",
                resp.status_code,
                resp.text[:500],
            )
            return
        data = resp.json()
        logger.info(
            "Mycelium trail recorded for request_id=%s action_ref=%s trail_id=%s",
            request_id,
            payload["action_ref"],
            data.get("trail_id", "?"),
        )
    except Exception as exc:  # noqa: BLE001 - intentionally broad, best-effort
        logger.warning("Mycelium trail submission error for request_id=%s: %s", request_id, exc)




"""
mycelium_trail — Mycelium Trails submission for SafeAgent.

Posts to /action/submit on every /settle call.
Returns trail_id for anchor sibling wiring.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("safeagent.mycelium_trail")

_DEFAULT_BASE_URL = "https://argentum.rgiskard.xyz"
_DEFAULT_SERVICE = "safeagent"
_DEFAULT_TIMEOUT = 30.0  # longer timeout for sync poll


def enabled() -> bool:
    return os.environ.get("MYCELIUM_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _base_url() -> str:
    return os.environ.get("MYCELIUM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _agent_id_fallback() -> str:
    return os.environ.get("MYCELIUM_AGENT_ID", "safeagent-prod")


def _api_key() -> Optional[str]:
    return os.environ.get("MYCELIUM_API_KEY") or None


def jcs(obj: Dict[str, Any]) -> bytes:
    return json.dumps(
        dict(sorted(obj.items())),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso_ms(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def compute_action_ref(agent_id: str, action_type: str, scope: str, timestamp_iso: str) -> str:
    preimage = {
        "agent_id": agent_id,
        "action_type": action_type,
        "scope": scope,
        "timestamp": timestamp_iso,
    }
    return sha256hex(jcs(preimage))


def build_trail_payload(
    *,
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at: float,
    result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    resolved_agent_id = agent_id or _agent_id_fallback()
    timestamp_iso = _iso_ms(claimed_at)
    action_ref = compute_action_ref(
        agent_id=resolved_agent_id,
        action_type=action,
        scope=request_id,
        timestamp_iso=timestamp_iso,
    )

    return {
        "api_key": _api_key(),
        "action_ref": action_ref,
        "service": "safeagent",
        "preimage": {
            "agent_id": resolved_agent_id,
            "action_type": action,
            "scope": request_id,
            "timestamp": timestamp_iso,
        },
    }


async def get_trail_anchor(trail_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch trail anchor data from Mycelium — block_time and tx_hash.
    Returns anchor dict or None if not yet confirmed.
    """
    headers = {"Content-Type": "application/json"}
    api_key = _api_key()
    if api_key:
        headers["X-API-Key"] = api_key

    url = f"{_base_url()}/mycelium/trails/{trail_id}/verify_chain"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("valid"):
                return data
    except Exception as e:
        logger.warning("Mycelium anchor fetch error for trail_id=%s: %s", trail_id, e)
    return None


async def submit_trail_async(
    *,
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at: float,
    result: Optional[Dict[str, Any]],
) -> Optional[str]:
    """
    POST to /action/submit. Returns trail_id on success, None on failure.
    Never raises.
    """
    if not enabled():
        return None

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

    url = f"{_base_url()}/action/submit"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "Mycelium trail submission failed (%s): %s",
                resp.status_code,
                resp.text[:500],
            )
            return None
        data = resp.json()
        trail_id = data.get("action_id") or data.get("id") or data.get("trail_id")
        logger.info(
            "Mycelium trail recorded for request_id=%s proof=%s trail_id=%s",
            request_id,
            payload["proof"],
            trail_id,
        )
        return trail_id
    except Exception as exc:
        logger.warning("Mycelium trail submission error for request_id=%s: %s", request_id, exc)
        return None







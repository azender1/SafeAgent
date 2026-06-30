"""
mycelium_trail.py — Mycelium on-chain anchor for SafeAgent

Submits a Nexus trail to Arbitrum via argentum-api.rgiskard.xyz/nexus/trail.
Returns trail_id (full UUID). Anchor confirmation polled via verify_chain.

Used by main.py _submit_and_anchor background task on every /settle.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

_log = logging.getLogger(__name__)

MYCELIUM_API_KEY = os.environ.get("MYCELIUM_API_KEY", "9d4cd6ce64ec43abb8a7db41b7f40c56")
MYCELIUM_AGENT_ID = os.environ.get("MYCELIUM_AGENT_ID", "safeagent-prod")
MYCELIUM_ENABLED = os.environ.get("MYCELIUM_ENABLED", "1") == "1"

NEXUS_TRAIL_URL = "https://argentum.rgiskard.xyz/nexus/trail"
VERIFY_CHAIN_URL = "https://argentum.rgiskard.xyz/mycelium/trails/{trail_id}/verify_chain"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def jcs(obj: dict) -> bytes:
    """RFC 8785 canonical JSON — sorted keys, no whitespace."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def enabled() -> bool:
    return MYCELIUM_ENABLED and bool(MYCELIUM_API_KEY)


# ---------------------------------------------------------------------------
# action_ref derivation — argentum-core action-ref-v1
# ---------------------------------------------------------------------------

def compute_action_ref(agent_id: str, action_type: str, scope: str, ts: int) -> str:
    """SHA-256 of JCS({agent_id, action_type, scope, timestamp})."""
    preimage = {
        "agent_id": agent_id,
        "action_type": action_type,
        "scope": scope,
        "timestamp": ts,
    }
    return sha256hex(jcs(preimage))


# ---------------------------------------------------------------------------
# Nexus trail submission
# ---------------------------------------------------------------------------

async def submit_trail_async(
    request_id: str,
    action: str,
    agent_id: Optional[str],
    claimed_at: float,
    result: Dict[str, Any],
) -> Optional[str]:
    """
    Submit a Mycelium Nexus trail. Returns trail_id (UUID) or None on failure.
    Called as a background task from main.py _submit_and_anchor.
    """
    if not enabled():
        return None

    _ct = claimed_at if claimed_at else time.time()
    ts = int(_ct)
    ts_str = datetime.fromtimestamp(_ct, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{int(_ct * 1000) % 1000:03d}Z'
    _agent_id = agent_id or MYCELIUM_AGENT_ID
    scope = request_id

    preimage = {
        "agent_id": _agent_id,
        "action_type": action,
        "scope": scope,
        "timestamp": ts_str,
    }
    action_ref = sha256hex(jcs(preimage))
    payment_hash = sha256hex(f"payment:{action_ref}".encode())
    output_hash = sha256hex(json.dumps(result, sort_keys=True).encode())

    payload = {
        "packet_version": "1.0",
        "canonicalization_profile_id": "8c7f71754e3daae1a0390d5e0287d51097d011e40df36bf15cad5c0f47efa05a",
        "action_ref": action_ref,
        "service": "safeagent",
        "preimage": preimage,
        "payment_hash": payment_hash,
        "output_hash": output_hash,
        "hash_algo": "SHA-256",
        "preimage_format": "jcs",
        "timestamp": int(_ct * 1000),
        "api_key": MYCELIUM_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(NEXUS_TRAIL_URL, json=payload)
            if resp.status_code != 200:
                _log.warning("Mycelium 422 body: %s", resp.text)
            resp.raise_for_status()
            data = resp.json()
            trail_id = data.get("trail_id") or data.get("id")
            if trail_id:
                _log.info("Mycelium trail submitted: trail_id=%s action_ref=%s", trail_id, action_ref)
                return trail_id
            _log.warning("Mycelium trail: no trail_id in response: %s", data)
            return None
    except Exception as exc:
        _log.warning("Mycelium trail submission failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Anchor polling — verify_chain
# ---------------------------------------------------------------------------

async def get_trail_anchor(trail_id: str) -> Optional[Dict[str, Any]]:
    """
    Poll verify_chain for Arbitrum confirmation.
    Returns anchor dict with block_time and tx_hash when confirmed, else None.
    """
    url = VERIFY_CHAIN_URL.format(trail_id=trail_id)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            block_time = (
                data.get("block_time")
                or data.get("anchor_block_time")
                or data.get("anchor", {}).get("block_time")
            )
            if not block_time:
                return None
            return {
                "block_time": block_time,
                "tx_hash": (
                    data.get("tx_hash")
                    or data.get("transaction_hash")
                    or data.get("anchor", {}).get("tx_hash")
                ),
                "trail_id": trail_id,
            }
    except Exception as exc:
        _log.warning("Mycelium verify_chain failed for %s: %s", trail_id, exc)
        return None

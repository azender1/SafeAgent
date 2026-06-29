from __future__ import annotations
import hashlib, json, logging, os
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger("safeagent.mycelium_trail")
_NEXUS_URL = "https://argentum-api.rgiskard.xyz/nexus/trail"
_VERIFY_URL = "https://argentum-api.rgiskard.xyz/mycelium/trails/{trail_id}/verify_chain"

def enabled():
    return os.environ.get("MYCELIUM_ENABLED","").strip().lower() in ("1","true","yes")

def _agent_id_fallback():
    return os.environ.get("MYCELIUM_AGENT_ID","safeagent-prod")

def _api_key():
    return os.environ.get("MYCELIUM_API_KEY") or None

def jcs(obj):
    return json.dumps(dict(sorted(obj.items())),separators=(",",":"),ensure_ascii=False).encode("utf-8")

def sha256hex(data):
    return hashlib.sha256(data).hexdigest()

def build_trail_payload(*, request_id, action, agent_id, claimed_at, result):
resolved = agent_id or _agent_id_fallback()
ts = int(claimed_at)
preimage = {"agent_id": resolved, "action_type": action, "scope": request_id, "ts": ts}
action_ref = sha256hex(jcs(preimage))
return {
"action_ref": action_ref,
"service": "safeagent",
"preimage": preimage,
"payment_hash": sha256hex(request_id.encode()),
"output_hash": sha256hex(json.dumps(result or {}, sort_keys=True).encode()),
"hash_algo": "sha256",
"preimage_format": "jcs-v1",
"timestamp": ts,
}

async def get_trail_anchor(trail_id):
headers = {"Content-Type": "application/json"}
if _api_key(): headers["X-API-Key"] = _api_key()
url = _VERIFY_URL.format(trail_id=trail_id)
try:
async with httpx.AsyncClient(timeout=10.0) as client:
resp = await client.get(url, headers=headers)
if resp.status_code == 200:
data = resp.json()
if data.get("valid"): return data
except Exception as e:
logger.warning("anchor fetch error trail_id=%s: %s", trail_id, e)
return None

async def submit_trail_async(*, request_id, action, agent_id, claimed_at, result):
if not enabled(): return None
payload = build_trail_payload(request_id=request_id, action=action, agent_id=agent_id, claimed_at=claimed_at, result=result)
headers = {"Content-Type": "application/json"}
if _api_key(): headers["X-API-Key"] = _api_key()
try:
async with httpx.AsyncClient(timeout=30.0) as client:
resp = await client.post(_NEXUS_URL, json=payload, headers=headers)
if resp.status_code >= 400:
logger.warning("Mycelium trail submission failed (%s): %s", resp.status_code, resp.text[:500])
return None
data = resp.json()
trail_id = data.get("trail_id") or data.get("id")
logger.info("Mycelium trail recorded request_id=%s trail_id=%s", request_id, trail_id)
return trail_id
except Exception as exc:
logger.warning("Mycelium trail submission error request_id=%s: %s", request_id, exc)
return None

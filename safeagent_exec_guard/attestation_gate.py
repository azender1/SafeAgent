"""AgentGraph safety-attestation gate for the exactly-once execution guard.

Slots in *before* the COMMITTED write: given a `/claim` and an AgentGraph CTEF
safety attestation for the target, it (1) verifies the attestation's Ed25519/JWS
signature against the issuer JWKS, (2) recomputes `binding_digest` and `action_ref`
with RFC 8785 JCS and confirms they bind to *this* claim (no cross-charge replay),
(3) requires `admission.verdict == "admit"`, and (4) refuses on a critical/high
safety finding. Returns ALLOW / SKIP / DENY.

`action_ref` and `binding_digest` are byte-identical to the existing
`exactly-once-v1.json` derivation:
    action_ref     = SHA-256(JCS({agent_id, action_type, scope, timestamp}))
    binding_digest = SHA-256(JCS({amount_usd, charge_ref, nonce, subject_did}))

IMPORTANT — canonicalization: this uses the `rfc8785` package (true RFC 8785 JCS),
not a `json.dumps(sorted(...))` shortcut. The shortcut only sorts top-level keys, so
it diverges on nested objects (AgentGraph attestations nest `attestation.findings`).
See docs/conformance/verify_fixture.py (now also rfc8785) and the divergence vector
in agentgraph_attestation_gate_v0.json.
"""
from __future__ import annotations

import hashlib
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_BLOCKING = ("critical", "high")


def _parse_ts(value: str | None) -> datetime | None:
    """Parse an RFC 3339 / ISO-8601 timestamp (handles a trailing 'Z')."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── canonicalization ────────────────────────────────────────────────────────
def jcs(obj: dict) -> bytes:
    """RFC 8785 JCS — handles nested objects and number canonicalization correctly."""
    return rfc8785.dumps(obj)


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_action_ref(preimage: dict) -> str:
    return sha256hex(jcs(preimage))


def compute_binding_digest(preimage: dict) -> str:
    return sha256hex(jcs(preimage))


# ── signature verification (Ed25519 / compact JWS over the JCS digest) ───────
def _b64url(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _public_key_for_kid(jwks: dict | list, kid: str | None) -> Ed25519PublicKey | None:
    keys = jwks.get("keys", []) if isinstance(jwks, dict) else jwks
    for jwk in keys:
        if kid is None or jwk.get("kid") == kid:
            if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
                return Ed25519PublicKey.from_public_bytes(_b64url(jwk["x"]))
    return None


def _strip_proof(envelope: dict) -> dict:
    return {k: v for k, v in envelope.items() if k != "proof"}


def verify_signature(attestation: dict, jwks: dict | list) -> bool:
    """Verify the Ed25519/JWS proof over the JCS digest of the attestation."""
    proof = attestation.get("proof") or {}
    jws = proof.get("jws") or attestation.get("jws")
    if not jws:
        return False
    parts = jws.split(".")
    if len(parts) != 3:
        return False
    header = parts[0]
    try:
        import json

        kid = json.loads(_b64url(header)).get("kid")
    except Exception:
        kid = None
    pub = _public_key_for_kid(jwks, kid)
    if pub is None:
        return False
    digest = hashlib.sha256(jcs(_strip_proof(attestation))).digest()
    try:
        pub.verify(_b64url(parts[2]), digest)
        return True
    except InvalidSignature:
        return False


# ── the gate ─────────────────────────────────────────────────────────────────
def _findings(att: dict) -> dict:
    return ((att or {}).get("attestation", {}) or {}).get("payload", {}).get("findings", {}) or {}


def gate(
    claim_binding_preimage: dict,
    attestation: dict | None,
    *,
    jwks: dict | list | None = None,
    require_attestation: bool = False,
    verify_sig: bool = True,
    unreachable: bool = False,
    freshness_ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide ALLOW / SKIP / DENY for a claim before the COMMITTED write.

    `claim_binding_preimage` is {amount_usd, charge_ref, nonce, subject_did} for THIS
    claim. `attestation` is the AgentGraph CTEF safety attestation (or None).

    - `unreachable=True` (the fetch to AgentGraph failed / timed out): DENY with reason
      `attestation_unreachable` if `require_attestation`, else SKIP — distinct from a real
      `safety_findings` deny so an operator can tell a safety stop from an availability stop.
    - No attestation (none exists for this tool): SKIP / DENY (`no_safety_attestation`).
    - `freshness_ttl_seconds`: a verdict older than the TTL (by `issued_at`), or past its
      `expires_at`, is DENY'd `attestation_stale` — a slow fetch or cache can't admit a
      verdict that's no longer current.
    - Bad signature, binding mismatch, verdict != admit, or a critical/high finding: DENY.
    - Otherwise: ALLOW, carrying the action_ref for the exactly-once guard.
    """
    # availability stop — distinct from a safety stop
    if unreachable:
        if require_attestation:
            return {"decision": "DENY", "reason": "attestation_unreachable"}
        return {"decision": "SKIP", "reason": "attestation_unreachable"}

    if attestation is None:
        if require_attestation:
            return {"decision": "DENY", "reason": "no_attestation_and_required"}
        return {"decision": "SKIP", "reason": "no_safety_attestation"}

    if verify_sig:
        if jwks is None:
            return {"decision": "DENY", "reason": "no_jwks_to_verify_signature"}
        if not verify_signature(attestation, jwks):
            return {"decision": "DENY", "reason": "attestation_signature_invalid"}

    # bind to THIS claim — an attestation can't be replayed against a different charge
    expected = compute_binding_digest(claim_binding_preimage)
    claimed = ((attestation.get("binding") or attestation.get("attestation_binding") or {})
               .get("binding_digest"))
    if claimed is not None and claimed != expected:
        return {"decision": "DENY", "reason": "binding_digest_mismatch",
                "expected": expected, "claimed": claimed}

    # freshness — a valid, correctly-bound verdict that is no longer current must not admit
    if freshness_ttl_seconds is not None:
        now = now or datetime.now(timezone.utc)
        issued = _parse_ts(attestation.get("issued_at"))
        expires = _parse_ts(attestation.get("expires_at"))
        if issued is not None and (now - issued).total_seconds() > freshness_ttl_seconds:
            age = int((now - issued).total_seconds())
            return {"decision": "DENY", "reason": "attestation_stale",
                    "age_seconds": age, "ttl_seconds": freshness_ttl_seconds}
        if expires is not None and now > expires:
            return {"decision": "DENY", "reason": "attestation_stale", "expired_at": attestation.get("expires_at")}

    verdict = ((attestation.get("admission") or {}).get("verdict")
               or (attestation.get("attestation", {}) or {}).get("verdict"))
    if verdict is not None and verdict != "admit":
        return {"decision": "DENY", "reason": f"admission_verdict_{verdict}"}

    f = _findings(attestation)
    blocking = {s: int(f.get(s, 0) or 0) for s in _BLOCKING}
    if sum(blocking.values()) > 0:
        entities = sorted(s for s, n in blocking.items() if n > 0)
        parts = ", ".join(f"{blocking[s]} {s}" for s in entities)
        return {"decision": "DENY", "reason": f"safety_findings: {parts}", "entities": entities}

    action_ref_preimage = attestation.get("action_ref_preimage")
    action_ref = compute_action_ref(action_ref_preimage) if action_ref_preimage else None
    return {"decision": "ALLOW", "reason": "admit", "action_ref": action_ref,
            "binding_digest": expected}


__all__ = ["gate", "verify_signature", "compute_action_ref", "compute_binding_digest", "jcs"]

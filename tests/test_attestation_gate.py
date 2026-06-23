"""Tests for the AgentGraph safety-attestation gate.

Reproduces docs/conformance/agentgraph_attestation_gate_v0.json from scratch:
the ALLOW / DENY / SKIP decisions, signature verification, binding-to-this-claim,
and the byte-identical action_ref / binding_digest with exactly-once-v1.json.

Standalone — no DB or running server required:  pytest tests/test_attestation_gate.py
"""
from __future__ import annotations

import hashlib
import json
from base64 import urlsafe_b64encode

import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from safeagent_exec_guard import attestation_gate as ag

CLAIM = {
    "amount_usd": 50,
    "charge_ref": "charge_ref_abc123",
    "nonce": "nonce_xyz789",
    "subject_did": "did:web:safeagent-prod",
}
ACTION_REF_PREIMAGE = {
    "agent_id": "safeagent-prod",
    "action_type": "payment.send",
    "scope": "trade:execute:authorized",
    "timestamp": "2026-06-08T20:00:00.000Z",
}


def _b64u(b: bytes) -> str:
    return urlsafe_b64encode(b).rstrip(b"=").decode()


def _issuer():
    sk = Ed25519PrivateKey.generate()
    raw = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = "agentgraph-test-1"
    jwks = {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": _b64u(raw), "kid": kid}]}
    return sk, kid, jwks


def _attestation(grade, crit, high, med=0):
    return {
        "type": "TrustAttestation", "version": "0.3.3", "claim_type": "authority",
        "subject": {"resource_url": "https://api.example.com/x402"},
        "admission": {"verdict": "admit"},
        "attestation": {"type": "SecurityAttestation", "confidence": 0.9, "payload": {
            "grade": grade, "findings": {
                "critical": crit, "high": high, "medium": med, "total": crit + high + med}}},
        "binding": {"binding_digest": ag.compute_binding_digest(CLAIM)},
        "action_ref_preimage": ACTION_REF_PREIMAGE,
    }


def _sign(att, sk, kid):
    att = json.loads(json.dumps(att))
    header = {"alg": "EdDSA", "kid": kid}
    digest = hashlib.sha256(rfc8785.dumps({k: v for k, v in att.items() if k != "proof"})).digest()
    att["proof"] = {"jws": f"{_b64u(json.dumps(header).encode())}.{_b64u(b'')}.{_b64u(sk.sign(digest))}"}
    return att


def test_allow_clean():
    sk, kid, jwks = _issuer()
    att = _sign(_attestation("A", 0, 0, 1), sk, kid)
    r = ag.gate(CLAIM, att, jwks=jwks, require_attestation=True)
    assert r["decision"] == "ALLOW"
    # action_ref byte-identical to exactly-once-v1.json
    assert r["action_ref"] == "281c2b33068cd63b2f89f09ea49b75edddf2d0c298f939a8eac13c2e34433b18"


def test_deny_critical():
    sk, kid, jwks = _issuer()
    att = _sign(_attestation("F", 2, 1, 3), sk, kid)
    r = ag.gate(CLAIM, att, jwks=jwks, require_attestation=True)
    assert r["decision"] == "DENY"
    assert "critical" in r.get("entities", [])


def test_skip_unattested_by_default():
    assert ag.gate(CLAIM, None)["decision"] == "SKIP"


def test_deny_unattested_when_required():
    assert ag.gate(CLAIM, None, require_attestation=True)["decision"] == "DENY"


def test_deny_bad_signature():
    sk, kid, jwks = _issuer()
    att = _sign(_attestation("A", 0, 0), sk, kid)
    att["attestation"]["payload"]["grade"] = "F"  # mutate after signing
    r = ag.gate(CLAIM, att, jwks=jwks)
    assert r["decision"] == "DENY" and r["reason"] == "attestation_signature_invalid"


def test_deny_wrong_charge():
    sk, kid, jwks = _issuer()
    att = _sign(_attestation("A", 0, 0), sk, kid)
    other_claim = {**CLAIM, "charge_ref": "charge_ref_DIFFERENT"}
    r = ag.gate(other_claim, att, jwks=jwks)
    assert r["decision"] == "DENY" and r["reason"] == "binding_digest_mismatch"


def test_jcs_handles_nested_unlike_naive_sort():
    nested = {"attestation": {"payload": {"findings": {
        "total": 7, "critical": 0, "high": 2, "medium": 5}, "grade": "B"}}}
    naive = json.dumps(dict(sorted(nested.items())), separators=(",", ":")).encode()
    assert ag.jcs(nested) != naive  # the bug this module avoids

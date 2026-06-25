"""Tests for the attestation gate's availability (unreachable) + freshness paths.

Reproduces docs/conformance/agentgraph_attestation_gate_fail_closed_v0.json: the
attestation_unreachable distinction (availability stop != safety stop) and attestation_stale
(a verdict older than freshness_ttl, or past expires_at, must not admit).

Standalone — no DB/server:  pytest tests/test_attestation_gate_fail_closed.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from safeagent_exec_guard import attestation_gate as ag

CLAIM = {
    "amount_usd": 50, "charge_ref": "charge_ref_abc123",
    "nonce": "nonce_xyz789", "subject_did": "did:web:safeagent-prod",
}
NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
TTL = 300


def _clean(issued=None, expires=None):
    att = {
        "admission": {"verdict": "admit"},
        "attestation": {"payload": {"grade": "A", "findings": {
            "critical": 0, "high": 0, "medium": 1, "total": 1}}},
        "binding": {"binding_digest": ag.compute_binding_digest(CLAIM)},
    }
    if issued:
        att["issued_at"] = issued
    if expires:
        att["expires_at"] = expires
    return att


# ── availability: unreachable is distinct from a safety deny ──────────────────
def test_unreachable_skips_when_optional():
    r = ag.gate(CLAIM, None, unreachable=True, require_attestation=False, verify_sig=False)
    assert r == {"decision": "SKIP", "reason": "attestation_unreachable"}


def test_unreachable_denies_when_required():
    r = ag.gate(CLAIM, None, unreachable=True, require_attestation=True, verify_sig=False)
    assert r == {"decision": "DENY", "reason": "attestation_unreachable"}


def test_unreachable_reason_is_not_a_safety_deny():
    """An operator must be able to tell an availability stop from a safety stop."""
    avail = ag.gate(CLAIM, None, unreachable=True, require_attestation=True, verify_sig=False)
    safety = ag.gate(CLAIM, {**_clean(), "attestation": {"payload": {"findings": {
        "critical": 1, "high": 0, "medium": 0, "total": 1}}}}, verify_sig=False)
    assert avail["reason"] == "attestation_unreachable"
    assert safety["reason"].startswith("safety_findings")
    assert avail["reason"] != safety["reason"]


# ── freshness ────────────────────────────────────────────────────────────────
def test_fresh_verdict_allows():
    r = ag.gate(CLAIM, _clean(issued="2026-06-25T11:59:00.000Z"),
                verify_sig=False, freshness_ttl_seconds=TTL, now=NOW)
    assert r["decision"] == "ALLOW"


def test_stale_verdict_denied_by_ttl():
    r = ag.gate(CLAIM, _clean(issued="2026-06-25T11:00:00.000Z"),
                verify_sig=False, freshness_ttl_seconds=TTL, now=NOW)
    assert r["decision"] == "DENY" and r["reason"] == "attestation_stale"


def test_expired_verdict_denied():
    r = ag.gate(CLAIM, _clean(issued="2026-06-25T11:59:00.000Z", expires="2026-06-25T11:30:00.000Z"),
                verify_sig=False, freshness_ttl_seconds=TTL, now=NOW)
    assert r["decision"] == "DENY" and r["reason"] == "attestation_stale"


def test_no_ttl_means_no_freshness_check():
    """Backward-compat: without freshness_ttl_seconds an old verdict still admits."""
    r = ag.gate(CLAIM, _clean(issued="2020-01-01T00:00:00.000Z"), verify_sig=False)
    assert r["decision"] == "ALLOW"

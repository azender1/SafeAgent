"""
SafeAgent exactly-once guard — conformance fixture verifier.

Reproduces all byte vectors in exactly-once-v1.json from scratch.
Run on a fresh clone to verify conformance:

    python verify_fixture.py

All assertions must pass for the fixture to be valid.
"""
import hashlib
import json
import sys

import rfc8785


def jcs(obj: dict) -> bytes:
    """RFC 8785 JCS canonicalization.

    Uses the ``rfc8785`` package — a true RFC 8785 implementation. The previous
    ``json.dumps(dict(sorted(obj.items())))`` shortcut only sorted TOP-LEVEL keys and
    skipped number canonicalization, so it diverged on nested objects (e.g. an
    AgentGraph attestation's ``attestation.payload.findings``). See
    ``agentgraph_attestation_gate_v0.json`` → ``jcs_divergence_proof``. This is the
    same canonicalizer cross-validated byte-for-byte across ~8 implementations.
    """
    return rfc8785.dumps(obj)


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main():
    with open("exactly-once-v1.json") as f:
        fixture = json.load(f)

    errors = []

    # ── 1. Verify action_ref ─────────────────────────────────────────────────
    v = fixture["verification"]["action_ref_derivation"]
    preimage = fixture["vectors"]["1_pending"]["action_ref_preimage"]
    canonical = jcs(preimage)
    computed_ref = sha256hex(canonical)
    expected_ref = fixture["vectors"]["1_pending"]["action_ref"]

    if canonical.hex() != v["canonical_bytes_hex"]:
        errors.append(f"action_ref canonical bytes mismatch")
    if computed_ref != expected_ref:
        errors.append(f"action_ref hash mismatch: got {computed_ref}")
    if computed_ref != v["expected"]:
        errors.append(f"action_ref verification.expected mismatch")

    print(f"{'✓' if not errors else '✗'} action_ref: {computed_ref}")

    # ── 2. Verify attestation binding_digest ─────────────────────────────────
    att = fixture["vectors"]["1_pending"]["attestation_binding"]
    att_preimage = att["binding_preimage"]
    att_canonical = jcs(att_preimage)
    computed_binding = sha256hex(att_canonical)
    expected_binding = att["binding_digest"]

    binding_errors = []
    if att_canonical.hex() != att["binding_canonical_hex"]:
        binding_errors.append("attestation canonical bytes mismatch")
    if computed_binding != expected_binding:
        binding_errors.append(f"attestation binding_digest mismatch: got {computed_binding}")

    errors.extend(binding_errors)
    print(f"{'✓' if not binding_errors else '✗'} attestation_binding_digest: {computed_binding}")

    # ── 3. Verify action_ref is stable across PENDING → COMMITTED → SKIP ────
    for vector_key in ["1_pending", "2_committed", "3_skip"]:
        v_ref = fixture["vectors"][vector_key]["action_ref"]
        if v_ref != computed_ref:
            errors.append(f"action_ref not stable in {vector_key}: {v_ref}")
    print(f"{'✓' if not errors else '✗'} action_ref stable across all three vectors")

    # ── 4. Verify COMMITTED carries the PENDING preimage unchanged ───────────
    p_pre = fixture["vectors"]["1_pending"]["action_ref_preimage"]
    c_pre = fixture["vectors"]["2_committed"]["action_ref_preimage"]
    if p_pre != c_pre:
        errors.append("preimage differs between PENDING and COMMITTED")
    print(f"{'✓' if p_pre == c_pre else '✗'} preimage stable PENDING → COMMITTED")

    # ── 5. Verify SKIP cached_result matches COMMITTED result ────────────────
    committed_result = fixture["vectors"]["2_committed"]["result"]
    skip_cached = fixture["vectors"]["3_skip"]["cached_result"]
    if committed_result != skip_cached:
        errors.append("SKIP cached_result does not match COMMITTED result")
    print(f"{'✓' if committed_result == skip_cached else '✗'} SKIP returns COMMITTED result unchanged")

    # ── summary ──────────────────────────────────────────────────────────────
    print()
    if errors:
        print(f"FAIL — {len(errors)} error(s):")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)
    else:
        print("PASS — all vectors byte-match. Fixture is conformant.")


if __name__ == "__main__":
    main()

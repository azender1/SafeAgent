"""
SafeAgent exactly-once guard — conformance fixture verifier.

Reproduces all byte vectors in exactly-once-v1.json from scratch.
Run on a fresh clone to verify conformance:

    python verify_fixture.py

All assertions must pass for the fixture to be valid.

v1.2 — added checks 6-9 after a negative-test pass (mutate-one-field-at-a-time,
per chopmob-cloud's method in a2aproject/A2A#1920) found four fields the v1.1
verifier published but never reconciled: request_id vs action_ref, an
independently-forgeable attestation_binding on the COMMITTED record, no status
validation, and a cross_impl block with no assertions at all. See
mutation_battery.py for the negative-test harness this was found with.
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

    # ── 6. Verify request_id == action_ref on every vector ──────────────────
    # v1.1 never asserted this — a vector could carry an unrelated request_id
    # and still pass. request_id IS the correlation key the runtime claims
    # against; if it can drift from action_ref, the guard's own dedup key
    # isn't provably the thing being verified above.
    reqid_errors = []
    for vector_key in ["1_pending", "2_committed", "3_skip"]:
        req_id = fixture["vectors"][vector_key]["request_id"]
        v_ref = fixture["vectors"][vector_key]["action_ref"]
        if req_id != v_ref:
            reqid_errors.append(f"request_id != action_ref in {vector_key}: {req_id} != {v_ref}")
    errors.extend(reqid_errors)
    print(f"{'✓' if not reqid_errors else '✗'} request_id == action_ref on every vector")

    # ── 7. Independently recompute attestation_binding on 2_committed ───────
    # v1.1 only ever recomputed 1_pending's binding_digest, then just read
    # 2_committed's copy back without recomputing it. Two copies of the same
    # value, only one ever checked — the COMMITTED record's binding could be
    # forged independently of the PENDING record's and this would not catch
    # it. Recompute from 2_committed's OWN binding_preimage, and also assert
    # the two records agree.
    committed_att = fixture["vectors"]["2_committed"]["attestation_binding"]
    committed_att_canonical = jcs(committed_att["binding_preimage"])
    committed_computed_binding = sha256hex(committed_att_canonical)

    committed_binding_errors = []
    if committed_att_canonical.hex() != committed_att["binding_canonical_hex"]:
        committed_binding_errors.append("2_committed attestation canonical bytes mismatch")
    if committed_computed_binding != committed_att["binding_digest"]:
        committed_binding_errors.append(
            f"2_committed attestation_binding_digest mismatch: got {committed_computed_binding}"
        )
    if committed_att["binding_digest"] != expected_binding:
        committed_binding_errors.append(
            "2_committed attestation_binding diverges from 1_pending's binding "
            f"({committed_att['binding_digest']} != {expected_binding})"
        )
    pending_limit = att.get("dynamic_limit_usd")
    committed_limit = committed_att.get("dynamic_limit_usd")
    if pending_limit != committed_limit:
        committed_binding_errors.append(
            f"dynamic_limit_usd changed post-admission: PENDING={pending_limit} "
            f"COMMITTED={committed_limit}"
        )
    errors.extend(committed_binding_errors)
    print(f"{'✓' if not committed_binding_errors else '✗'} "
          f"2_committed attestation_binding independently recomputed: {committed_computed_binding}")

    # ── 8. Verify status is the expected value per vector ────────────────────
    expected_status = {"1_pending": "PENDING", "2_committed": "COMMITTED", "3_skip": "SKIP"}
    status_errors = []
    for vector_key, expected in expected_status.items():
        actual = fixture["vectors"][vector_key].get("status")
        if actual != expected:
            status_errors.append(f"{vector_key} status mismatch: expected {expected}, got {actual}")
    errors.extend(status_errors)
    print(f"{'✓' if not status_errors else '✗'} status field matches expected value per vector")

    # ── 9. Verify cross_impl.vectors map verdict/limit to the correct outcome
    # v1.1 published this block (the part actually shown publicly) with zero
    # verification code — a flipped safeagent_outcome would pass silently.
    # Rule, derived from the fixture's own vector descriptions: verdict !=
    # "admit" -> SKIP; dual_approval_required -> PENDING; dynamic_limit_usd
    # in (None, 0) -> SKIP; otherwise -> PROCEED.
    def expected_outcome(vec: dict) -> str:
        if vec.get("verdict") != "admit":
            return "SKIP"
        if vec.get("dual_approval_required"):
            return "PENDING"
        if vec.get("dynamic_limit_usd") in (None, 0):
            return "SKIP"
        return "PROCEED"

    cross_impl_errors = []
    cross_impl = fixture.get("cross_impl", {}).get("vectors", {})
    for name, vec in cross_impl.items():
        exp = expected_outcome(vec)
        actual = vec.get("safeagent_outcome")
        if actual != exp:
            cross_impl_errors.append(
                f"cross_impl.{name}: expected safeagent_outcome={exp}, got {actual}"
            )
    errors.extend(cross_impl_errors)
    print(f"{'✓' if not cross_impl_errors else '✗'} "
          f"cross_impl.vectors outcomes match verdict/limit rule ({len(cross_impl)} vectors checked)")

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

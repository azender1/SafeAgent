"""
Mutation / negative-test battery for SafeAgent's exactly-once-v1.json conformance fixture.

Method (same as chopmob-cloud / haroldmalikfrimpong-ops used against their own fixtures
in a2aproject/A2A#1920): take every field the fixture publishes, mutate it one at a time,
re-run the SAME verification logic verify_fixture.py uses, and check whether the verifier
actually rejects the tampered copy. A field that can be replaced with garbage and still
verify clean is a value the fixture publishes and nothing checks.

This does NOT re-derive its own verifier from scratch (that would risk the same
self-referential trap chopmob-cloud hit). It reuses verify_fixture.py's exact checks,
refactored into a pure function, plus explicit additional probes for fields that
verify_fixture.py's five checks never touch at all.
"""
import copy
import hashlib
import json
import sys

import rfc8785


def jcs(obj: dict) -> bytes:
    return rfc8785.dumps(obj)


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_verifier(fixture: dict) -> tuple[bool, list[str]]:
    """Exact logic of verify_fixture.py's five checks, as a pure function."""
    errors = []

    try:
        v = fixture["verification"]["action_ref_derivation"]
        preimage = fixture["vectors"]["1_pending"]["action_ref_preimage"]
        canonical = jcs(preimage)
        computed_ref = sha256hex(canonical)
        expected_ref = fixture["vectors"]["1_pending"]["action_ref"]

        if canonical.hex() != v["canonical_bytes_hex"]:
            errors.append("action_ref canonical bytes mismatch")
        if computed_ref != expected_ref:
            errors.append(f"action_ref hash mismatch: got {computed_ref}")
        if computed_ref != v["expected"]:
            errors.append("action_ref verification.expected mismatch")

        att = fixture["vectors"]["1_pending"]["attestation_binding"]
        att_preimage = att["binding_preimage"]
        att_canonical = jcs(att_preimage)
        computed_binding = sha256hex(att_canonical)
        expected_binding = att["binding_digest"]

        if att_canonical.hex() != att["binding_canonical_hex"]:
            errors.append("attestation canonical bytes mismatch")
        if computed_binding != expected_binding:
            errors.append(f"attestation binding_digest mismatch: got {computed_binding}")

        for vector_key in ["1_pending", "2_committed", "3_skip"]:
            v_ref = fixture["vectors"][vector_key]["action_ref"]
            if v_ref != computed_ref:
                errors.append(f"action_ref not stable in {vector_key}: {v_ref}")

        p_pre = fixture["vectors"]["1_pending"]["action_ref_preimage"]
        c_pre = fixture["vectors"]["2_committed"]["action_ref_preimage"]
        if p_pre != c_pre:
            errors.append("preimage differs between PENDING and COMMITTED")

        committed_result = fixture["vectors"]["2_committed"]["result"]
        skip_cached = fixture["vectors"]["3_skip"]["cached_result"]
        if committed_result != skip_cached:
            errors.append("SKIP cached_result does not match COMMITTED result")

        # v1.2 checks
        for vector_key in ["1_pending", "2_committed", "3_skip"]:
            req_id = fixture["vectors"][vector_key]["request_id"]
            v_ref = fixture["vectors"][vector_key]["action_ref"]
            if req_id != v_ref:
                errors.append(f"request_id != action_ref in {vector_key}")

        committed_att = fixture["vectors"]["2_committed"]["attestation_binding"]
        committed_att_canonical = jcs(committed_att["binding_preimage"])
        committed_computed_binding = sha256hex(committed_att_canonical)
        if committed_att_canonical.hex() != committed_att["binding_canonical_hex"]:
            errors.append("2_committed attestation canonical bytes mismatch")
        if committed_computed_binding != committed_att["binding_digest"]:
            errors.append("2_committed attestation_binding_digest mismatch")
        if committed_att["binding_digest"] != expected_binding:
            errors.append("2_committed attestation_binding diverges from 1_pending's binding")
        if att.get("dynamic_limit_usd") != committed_att.get("dynamic_limit_usd"):
            errors.append("dynamic_limit_usd changed post-admission")

        expected_status = {"1_pending": "PENDING", "2_committed": "COMMITTED", "3_skip": "SKIP"}
        for vector_key, expected in expected_status.items():
            if fixture["vectors"][vector_key].get("status") != expected:
                errors.append(f"{vector_key} status mismatch")

        def expected_outcome(vec):
            if vec.get("verdict") != "admit":
                return "SKIP"
            if vec.get("dual_approval_required"):
                return "PENDING"
            if vec.get("dynamic_limit_usd") in (None, 0):
                return "SKIP"
            return "PROCEED"

        cross_impl = fixture.get("cross_impl", {}).get("vectors", {})
        for name, vec in cross_impl.items():
            if vec.get("safeagent_outcome") != expected_outcome(vec):
                errors.append(f"cross_impl.{name} outcome mismatch")

    except Exception as e:
        errors.append(f"verifier crashed: {e}")

    return (len(errors) == 0), errors


def load_fixture():
    with open("exactly-once-v1.json") as f:
        return json.load(f)


def set_path(d, path, value):
    obj = d
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


def get_path(d, path):
    obj = d
    for k in path:
        obj = obj[k]
    return obj


def main():
    base = load_fixture()
    ok, errs = run_verifier(copy.deepcopy(base))
    assert ok, f"Baseline should be clean: {errs}"
    print("Baseline: PASS (as expected)\n")

    # ── Battery A: fields the verifier DOES claim to check ────────────────
    # Each should cause a rejection when tampered. An escape here = the
    # verifier's own stated checks don't actually hold.
    checked_mutations = [
        ("action_ref (1_pending), leave preimage untouched",
         ["vectors", "1_pending", "action_ref"], "0" * 64),
        ("action_ref_preimage.agent_id (1_pending)",
         ["vectors", "1_pending", "action_ref_preimage", "agent_id"], "attacker-controlled"),
        ("action_ref_preimage.scope (1_pending)",
         ["vectors", "1_pending", "action_ref_preimage", "scope"], "trade:execute:UNAUTHORIZED"),
        ("canonical_bytes_hex (verification block)",
         ["verification", "action_ref_derivation", "canonical_bytes_hex"], "00" * 10),
        ("attestation_binding.binding_digest (1_pending)",
         ["vectors", "1_pending", "attestation_binding", "binding_digest"], "f" * 64),
        ("attestation_binding.binding_preimage.amount_usd (1_pending)",
         ["vectors", "1_pending", "attestation_binding", "binding_preimage", "amount_usd"], 999999),
        ("attestation_binding.binding_preimage.charge_ref (1_pending)",
         ["vectors", "1_pending", "attestation_binding", "binding_preimage", "charge_ref"], "charge_ref_STOLEN"),
        ("action_ref (2_committed) diverges from 1_pending",
         ["vectors", "2_committed", "action_ref"], "1" * 64),
        ("action_ref (3_skip) diverges from 1_pending",
         ["vectors", "3_skip", "action_ref"], "2" * 64),
        ("action_ref_preimage.timestamp (2_committed) diverges from 1_pending",
         ["vectors", "2_committed", "action_ref_preimage", "timestamp"], "2099-01-01T00:00:00.000Z"),
        ("result.provider_object_id (2_committed) diverges from SKIP cached_result",
         ["vectors", "2_committed", "result", "provider_object_id"], "pi_FORGED999"),
    ]

    print("── Battery A: fields verify_fixture.py claims to check ──")
    caught, escaped = 0, []
    for label, path, mutated_value in checked_mutations:
        f = copy.deepcopy(base)
        set_path(f, path, mutated_value)
        ok, errs = run_verifier(f)
        if ok:
            escaped.append(label)
            print(f"  ✗ ESCAPE  — {label}")
        else:
            caught += 1
            print(f"  ✓ caught  — {label}")
    print(f"\n  {caught}/{len(checked_mutations)} caught, {len(escaped)} escapes\n")

    # ── Battery B: fields the verifier NEVER checks at all ────────────────
    # These aren't "the check missed a mutation" — there is no check here,
    # by construction. This is the chopmob-cloud-style blind spot: values
    # the fixture publishes and nothing reconciles.
    unchecked_probes = [
        ("request_id (1_pending) set to a value unrelated to action_ref",
         ["vectors", "1_pending", "request_id"], "totally-unrelated-request-id"),
        ("status field (1_pending) set to something nonsensical",
         ["vectors", "1_pending", "status"], "GHOST_STATE"),
        ("attestation_binding.binding_digest (2_committed) — a SEPARATE copy of the",
         "same binding, never cross-checked against 1_pending's copy or recomputed",
         ["vectors", "2_committed", "attestation_binding", "binding_digest"], "deadbeef" * 8),
        ("attestation_binding.dynamic_limit_usd (2_committed) changed post-admission",
         ["vectors", "2_committed", "attestation_binding", "dynamic_limit_usd"], 999999999),
        ("cross_impl.vectors.admit.safeagent_outcome flipped to SKIP",
         ["cross_impl", "vectors", "admit", "safeagent_outcome"], "SKIP"),
        ("cross_impl.vectors.scope_deny.safeagent_outcome flipped to PROCEED",
         ["cross_impl", "vectors", "scope_deny", "safeagent_outcome"], "PROCEED"),
        ("cross_impl.action_ref_byte_identical flag lied to true after breaking it",
         ["cross_impl", "action_ref_byte_identical"], True),
    ]
    # normalize tuples (some accidentally 4-tuple due to line wrap above)
    norm = []
    i = 0
    raw = unchecked_probes
    cleaned = []
    for item in raw:
        if len(item) == 4:
            cleaned.append((item[0] + " " + item[1], item[2], item[3]))
        else:
            cleaned.append(item)

    print("── Battery B: fields verify_fixture.py never checks (structural blind spots) ──")
    blind_spots = []
    for label, path, mutated_value in cleaned:
        f = copy.deepcopy(base)
        try:
            set_path(f, path, mutated_value)
        except (KeyError, TypeError) as e:
            print(f"  (skip, path not present: {label} — {e})")
            continue
        ok, errs = run_verifier(f)
        if ok:
            blind_spots.append(label)
            print(f"  ⚠ UNCHECKED (still reports PASS) — {label}")
        else:
            print(f"  ✓ (unexpectedly caught) — {label}")

    print(f"\n  {len(blind_spots)} confirmed blind spots\n")

    print("=" * 70)
    print(f"SUMMARY: Battery A {caught}/{len(checked_mutations)} caught "
          f"({len(escaped)} escapes) | Battery B {len(blind_spots)} blind spots "
          f"of {len(cleaned)} probed")
    if escaped:
        print("\nBattery A escapes (the verifier's OWN stated checks failed to catch these):")
        for e in escaped:
            print(f"  - {e}")
    if blind_spots:
        print("\nBattery B blind spots (fields published with NO reconciling check at all):")
        for b in blind_spots:
            print(f"  - {b}")

    # ── Pin the residual escape as an exact set ───────────────────────────
    # Per eriknewton (a2aproject/A2A#1920): a documented-but-unfixed escape
    # should be pinned in CI as an exact set, so a NEW escape fails the build,
    # and so does the pinned one quietly getting fixed without the label
    # being updated. Either direction of drift must be caught here, not left
    # to someone noticing the docs are stale.
    PINNED_RESIDUAL_ESCAPES = {
        "cross_impl.action_ref_byte_identical flag lied to true after breaking it",
    }

    print("\n" + "=" * 70)
    print("PINNED CHECK: residual Battery B escapes must equal the documented set exactly")

    exit_code = 0

    if escaped:
        print(f"\nFAIL — Battery A has {len(escaped)} escape(s). These are checks "
              "verify_fixture.py claims to make; any escape here is a real "
              "regression, not a documented limitation. Fix before merging.")
        exit_code = 1

    actual_blind_spots = set(blind_spots)
    if actual_blind_spots != PINNED_RESIDUAL_ESCAPES:
        new_escapes = actual_blind_spots - PINNED_RESIDUAL_ESCAPES
        closed_escapes = PINNED_RESIDUAL_ESCAPES - actual_blind_spots
        print("\nFAIL — Battery B blind spots drifted from the pinned set.")
        if new_escapes:
            print("  NEW, undocumented escape(s) — these need either a fix in "
                  "verify_fixture.py or an explicit label + addition to "
                  "PINNED_RESIDUAL_ESCAPES with a stated reason it can't be closed:")
            for e in new_escapes:
                print(f"    + {e}")
        if closed_escapes:
            print("  Previously-pinned escape(s) no longer reproduce — good news, "
                  "but PINNED_RESIDUAL_ESCAPES and the fixture's *_note field(s) "
                  "are now stale and must be updated to match reality:")
            for e in closed_escapes:
                print(f"    - {e}")
        exit_code = 1
    else:
        print(f"\nPASS — Battery B blind spots match the pinned, documented set exactly "
              f"({len(PINNED_RESIDUAL_ESCAPES)} residual escape(s), unchanged).")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

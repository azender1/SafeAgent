"""
v12 output validator. Checks the two-layer model directly:

  - every input order has exactly one disposition
  - no order disappears, no order has multiple dispositions
    (both trivially true by construction of the disposition dict itself,
    but verified here by shape-checking against the actual order list
    rather than assumed)
  - findings may reference an order multiple times (not an error)
  - every finding references valid input records (claim/order actually in
    the input, not a stray object)
  - claim/order permutations cannot change dispositions, the claim->order
    relation, or normalized finding signatures (asserted by the property
    tests and the systematic permutation test, using the helpers below)

IDENTITY: every comparison in this module keys claims by claim.stable_id
and orders by order.stable_id -- never request_id (a caller-supplied
label; D26 documents two distinct claims legitimately sharing one) and
never list index (meaningless across a permutation).
"""
from __future__ import annotations

from reconcile_v12 import OrderDisposition, Result, ReasonCode


def validate_disposition_shape(broker_orders: list, disposition: dict) -> None:
    """Every input order has exactly one disposition; none disappear."""
    order_indices = set(range(len(broker_orders)))
    disposition_indices = set(disposition.keys())
    assert order_indices == disposition_indices, (
        f"Disposition dict does not cover every order exactly once. "
        f"Missing: {order_indices - disposition_indices}, "
        f"extra/unexpected: {disposition_indices - order_indices}"
    )
    valid_values = {member.value for member in OrderDisposition}
    for i, d in disposition.items():
        assert hasattr(d, "value") and d.value in valid_values, f"Order {i} has non-disposition value {d!r}"


def validate_findings_reference_valid_records(
    claims: list, broker_orders: list, results: list,
) -> None:
    """Every finding references a claim/order that genuinely exists in the
    input -- never a stray or reconstructed object."""
    claim_ids = {id(c) for c in claims}
    order_ids = {id(o) for o in broker_orders}
    order_stable_ids = {o.stable_id for o in broker_orders}
    for r in results:
        if r.claim is not None:
            assert id(r.claim) in claim_ids, "Finding references a claim not in the input list"
        if r.matched_broker_order is not None:
            assert id(r.matched_broker_order) in order_ids, "Finding references an order not in the input list"
        for sid in r.involved_order_stable_ids:
            assert sid in order_stable_ids, f"Finding references unknown stable_id {sid}"



def disposition_totals(disposition: dict) -> dict:
    counts: dict = {}
    for d in disposition.values():
        counts[d.value] = counts.get(d.value, 0) + 1
    return counts


def normalized_finding_signature(results: list) -> tuple:
    """
    A permutation-invariant, MULTIPLICITY-PRESERVING summary of every
    finding: (claim STABLE_ID or None, matched order stable_id or None,
    result, sorted reason codes, sorted involved order stable_ids,
    correlation_id).

    Keyed by claim.stable_id, not request_id (v12.1 fix): request_id is a
    caller-supplied label, and D26 documents two distinct claims that may
    legitimately share one. A request_id-keyed signature cannot always
    tell such claims apart -- see normalized_assignment_relation()'s
    docstring for the concrete case this fails on (an assignment SWAP
    between two claims sharing a request_id is invisible to a
    request_id-keyed set, because the set of pairs is identical either
    way). stable_id is computed from genuine content (see
    assign_claim_stable_ids()), so distinct claims always get distinct
    keys.

    Returned as a SORTED TUPLE, deliberately NOT a set/frozenset. A
    frozenset silently collapses two identical findings into one entry,
    which means a bug that emits the same finding twice would be invisible
    to every test that compares signatures -- exactly the class of defect
    these tests exist to catch. A multiset representation makes duplicate
    findings observable.
    """
    sig = []
    for r in results:
        sig.append((
            r.claim.stable_id if r.claim else None,
            r.matched_broker_order.stable_id if r.matched_broker_order else None,
            r.result.value,
            tuple(sorted(c.value for c in r.reason_codes)),
            tuple(sorted(r.involved_order_stable_ids)),
            r.correlation_id,
        ))
    return tuple(sorted(sig, key=lambda t: str(t)))


_NON_ASSIGNMENT_REASONS = frozenset({
    ReasonCode.AMBIGUOUS_MULTIPLE_IDENTIFIER_MATCHES,
    ReasonCode.AMBIGUOUS_MULTIPLE_PARTIAL_MATCHES,
    ReasonCode.AMBIGUOUS_MULTIPLE_WEAK_MATCHES,
    ReasonCode.IDENTIFIER_CONFLICT,
    ReasonCode.EXTERNAL_IDENTIFIER_CONFLICT,
})


def normalized_assignment_relation(results: list) -> frozenset:
    """
    The genuine claim->order assignment relation, keyed by STABLE IDENTITY
    on both sides -- never request_id and never list index -- so it can be
    compared across permutation, re-ingestion, or two independent engines
    (the reference model). Excludes findings whose reason codes mark them
    as non-assignment outcomes (ambiguous/conflict); those never represent
    a real pairing.

    This closes a real hole in v12: the pairwise relation check previously
    keyed pairs by (claim.request_id, order.stable_id). Because a set of
    pairs does not remember WHICH claim object contributed which pair, two
    claims sharing one request_id and assigned to two orders produce the
    exact same set of (request_id, order_id) pairs whether the assignment
    is correct or SWAPPED between them -- {(rid, o1), (rid, o2)} is
    identical either way. A genuine assignment-swap bug between such
    claims was therefore undetectable by that comparison. Keying by
    claim.stable_id instead distinguishes them, since two claims that are
    genuinely different always get different stable_ids (see
    assign_claim_stable_ids()). See test_claimid04 for a direct,
    deterministic proof of this.
    """
    return frozenset(
        (r.claim.stable_id, r.matched_broker_order.stable_id)
        for r in results
        if r.claim is not None and r.matched_broker_order is not None
        and r.result not in (Result.UNRECORDED_INTERNALLY, Result.CONTRADICTION)
        and not (_NON_ASSIGNMENT_REASONS & set(r.reason_codes))
    )


def normalized_dispositions(disposition: dict, orders: list) -> dict:
    """
    Disposition re-keyed by deterministic ORDER IDENTITY (stable_id)
    instead of list index, so two runs over the same orders in a DIFFERENT
    list order can be compared directly. Comparing the raw index-keyed
    dicts would be meaningless under permutation (index 0 is a different
    order in each run); comparing nothing at all -- which is what v11
    actually did, computing d1/d2 and never asserting on them -- verified
    findings while leaving the accounting layer unchecked.
    """
    assert len(disposition) == len(orders), (
        f"disposition covers {len(disposition)} entries but {len(orders)} orders were given"
    )
    keyed = {orders[i].stable_id: v for i, v in disposition.items()}
    assert len(keyed) == len(orders), (
        "Two orders share a stable_id -- dispositions cannot be compared by identity. "
        "Inputs must pass through normalize_ingested_orders() first."
    )
    return keyed
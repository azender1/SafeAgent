"""
Independent semantic reference model (section 7A), v10 -- rebuilt to be
genuinely TIER-SEQUENTIAL, not a single flat enumeration.

This is a real, second bug found in the reference model itself (not the
engine): a single flat lexicographic-score enumeration still let a weaker
edge "reach into" an order that a stronger-tier ambiguity had already
claimed in the engine's real, phased execution -- because the flat
enumeration doesn't model consumption between phases, only total counts.

The spec's own language is explicitly phased ("Maximize compatible
full-identifier matches. THEN maximize partial-identifier matches. THEN
consider weak heuristic matches.") -- so a faithful reference model must
ALSO be phased: resolve full-tier exhaustively first, lock in what that
determines, then resolve partial-tier using only what's left, then weak.
This is still a completely independent implementation from the engine
(separate code, no shared functions) -- it just follows the same required
tier-sequential principle, because that principle is the actual spec, not
an engine implementation detail.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from reconcile_v12 import (
    InternalClaim, BrokerOrder, FailureType, REJECTED_COMPATIBLE_STATUSES, FILLED_STATUSES,
)

FULL, PARTIAL, WEAK = "FULL", "PARTIAL", "WEAK"
MATCH_TOLERANCE_SECONDS = 300


def _claim_is_skipped(c) -> bool:
    return c.outcome == "SKIP"


def _claim_is_rejected(c) -> bool:
    return c.failure_type == FailureType.BROKER_REJECTED


def _tier_for_edge(c, o):
    """Independently reimplements tier classification from first
    principles -- returns FULL/PARTIAL/WEAK/None (no edge at all).
    Conflicting identifiers (partial agreement + partial disagreement)
    also return None here -- conflict is handled entirely separately,
    not as a competing edge in the assignment graph."""
    has_ids = bool(c.broker_order_id or c.client_order_id)
    if has_ids:
        agree = []
        if c.broker_order_id is not None and o.broker_order_id is not None:
            agree.append(c.broker_order_id == o.broker_order_id)
        if c.client_order_id is not None and o.client_order_id is not None:
            agree.append(c.client_order_id == o.client_order_id)
        if not agree or not all(agree):
            return None
        return FULL if len(agree) == 2 else PARTIAL
    same_trade = (o.symbol == c.symbol and o.side == c.side and abs(o.qty - c.qty) < 1e-6)
    close_in_time = abs((o.submitted_at - c.claimed_at).total_seconds()) <= MATCH_TOLERANCE_SECONDS
    if not (same_trade and close_in_time):
        return None
    return WEAK


def _rejected_status_ok(c, o) -> bool:
    if not _claim_is_rejected(c):
        return True
    return o.status in REJECTED_COMPATIBLE_STATUSES


def _max_matchings(claim_ids, order_ids, compatible):
    """Exhaustively enumerate every matching of maximum size for one tier's
    compatibility set. Independent brute-force implementation (not shared
    with the engine's own version), correct by construction at the small
    scale property tests generate."""
    best_size = -1
    best = []
    for k in range(min(len(claim_ids), len(order_ids)), -1, -1):
        found = False
        for claim_subset in itertools.combinations(claim_ids, k):
            for order_perm in itertools.permutations(order_ids, k):
                pairing = dict(zip(claim_subset, order_perm))
                if all((c, o) in compatible for c, o in pairing.items()):
                    found = True
                    if k > best_size:
                        best_size, best = k, [pairing]
                    elif k == best_size and pairing not in best:
                        best.append(pairing)
        if found and best_size == k:
            break
    return best


@dataclass
class ReferenceOutcome:
    optimal_score: int
    is_unique: bool
    optimal_assignments: list
    varying_claims: set
    varying_orders: set
    unassigned_claims: set
    conflict_claims: dict
    genuinely_unmatched_orders: set
    contradiction_claims: dict
    forced_assignments: dict  # claim_idx -> order_idx, the ACTUAL relation for
                               # uniquely-resolved claims -- exposed so a
                               # comparison can catch two engines that agree on
                               # every order's CATEGORY while assigning
                               # completely different claim/order pairs.


def compute_reference_outcome(claims: list, broker_orders: list) -> ReferenceOutcome:
    eligible = [i for i, c in enumerate(claims) if not _claim_is_skipped(c)]
    m = len(broker_orders)

    # Independently detect conflicts (partial agreement + partial
    # disagreement) up front -- these never enter the tier-sequential
    # assignment graph at all, same as the engine's own separation.
    conflict_claims: dict = {}
    for i in eligible:
        c = claims[i]
        if not (c.broker_order_id or c.client_order_id):
            continue
        for j, o in enumerate(broker_orders):
            checks = []
            if c.broker_order_id is not None and o.broker_order_id is not None:
                checks.append(c.broker_order_id == o.broker_order_id)
            if c.client_order_id is not None and o.client_order_id is not None:
                checks.append(c.client_order_id == o.client_order_id)
            if checks and any(checks) and not all(checks):
                conflict_claims.setdefault(i, []).append(j)

    available_orders = set(range(m))
    resolved_claims: dict = {}     # claim_idx -> order_idx (if uniquely assigned)
    varying_claims: set = set()
    varying_orders: set = set()
    assigned_orders: set = set()
    last_tier_assignments = []     # kept from the final tier processed, for optimal_assignments/is_unique

    for tier in (FULL, PARTIAL, WEAK):
        # BUG FIX (found during v12.1 acceptance testing, 2026-09-09): this
        # must also exclude varying_claims, not just resolved_claims. A
        # claim that became AMBIGUOUS at a stronger tier (in varying_claims)
        # was previously left in `unresolved` and got reprocessed at every
        # WEAKER tier too -- directly contradicting this function's own
        # docstring ("resolve full-tier exhaustively first, LOCK IN what
        # that determines, then resolve partial-tier using only what's
        # left"). "What that determines" has to include ambiguity, not
        # just unique resolution -- an ambiguous claim's evidence at that
        # tier is exhausted; it must not get a second attempt on weaker
        # evidence. Concretely: two claims ambiguous over one order at
        # FULL tier could leak into PARTIAL tier and consume MORE orders
        # into ambiguity there too, making an unrelated WEAK-tier claim
        # look "uniquely forced" onto whatever order happened to survive
        # untouched -- an artifact of the leak, not a real signal. The
        # engine (resolve_assignments) never has this problem: a claim
        # that receives ANY assignment record, ambiguous or not, is
        # removed from `assignment`'s complement and excluded from every
        # subsequent tier's edge computation.
        unresolved = [i for i in eligible if i not in resolved_claims and i not in varying_claims]
        compatible = set()
        for i in unresolved:
            c = claims[i]
            for j in available_orders:
                o = broker_orders[j]
                if _tier_for_edge(c, o) == tier and _rejected_status_ok(c, o):
                    compatible.add((i, j))
        if not compatible:
            continue
        claim_ids = sorted({c for c, _ in compatible})
        order_ids = sorted({o for _, o in compatible})
        matchings = _max_matchings(claim_ids, order_ids, compatible)
        if not matchings or len(matchings[0]) == 0:
            continue
        last_tier_assignments = matchings

        for i in claim_ids:
            partners = {a.get(i) for a in matchings if i in a}
            if len(partners) == 1 and all(i in a for a in matchings):
                order_idx = next(iter(partners))
                resolved_claims[i] = order_idx
                assigned_orders.add(order_idx)
                available_orders.discard(order_idx)
            elif partners:
                varying_claims.add(i)
                for o in partners:
                    if o in available_orders:
                        varying_orders.add(o)
                        available_orders.discard(o)

        for j in order_ids:
            if j not in available_orders:
                continue
            holders = {c for a in matchings for c, o in a.items() if o == j}
            if len(holders) > 1:
                varying_orders.add(j)
                available_orders.discard(j)
                varying_claims.update(holders)

    unassigned_claims = {i for i in eligible if i not in resolved_claims
                          and i not in varying_claims and i not in conflict_claims}

    contradiction_claims: dict = {}
    for i in eligible:
        c = claims[i]
        if not _claim_is_rejected(c):
            continue
        for j, o in enumerate(broker_orders):
            if o.status not in FILLED_STATUSES:
                continue
            agree = []
            if c.broker_order_id is not None and o.broker_order_id is not None:
                agree.append(c.broker_order_id == o.broker_order_id)
            if c.client_order_id is not None and o.client_order_id is not None:
                agree.append(c.client_order_id == o.client_order_id)
            if agree and all(agree):
                contradiction_claims[i] = j
                break

    contradiction_orders = set(contradiction_claims.values())
    conflict_involved_orders = {o for lst in conflict_claims.values() for o in lst}
    genuinely_unmatched_orders = {
        j for j in range(m)
        if j not in assigned_orders and j not in varying_orders
        and j not in conflict_involved_orders and j not in contradiction_orders
    }

    is_unique = len(last_tier_assignments) <= 1 and not varying_orders and not varying_claims
    optimal_assignments = last_tier_assignments if last_tier_assignments else [dict(resolved_claims)]

    return ReferenceOutcome(
        optimal_score=len(resolved_claims), is_unique=is_unique,
        optimal_assignments=optimal_assignments, varying_claims=varying_claims,
        varying_orders=varying_orders, unassigned_claims=unassigned_claims,
        conflict_claims=conflict_claims, genuinely_unmatched_orders=genuinely_unmatched_orders,
        contradiction_claims=contradiction_claims, forced_assignments=dict(resolved_claims),
    )
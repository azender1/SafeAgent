"""
bipartite_solver_reference.py -- TEST-ONLY.

The original brute-force implementation of what is now
reconcile_v12._max_matchings_for_tier(), preserved here verbatim in
behavior (not imported by any core or CLI module) so the new
polynomial-time solver can be exhaustively cross-checked against it on
small inputs. This is O(n!) -- itertools.permutations(order_ids, k) with
no early exit -- which is exactly the measured cause of the v13.1
performance blocker (8 claims: 0.04s; 9: 0.37s; 10: hung). NEVER use this
at runtime or on any input larger than a handful of claims/orders.

derive_old_style_results() reproduces exactly what the OLD
resolve_assignments() computed from this function's output
(`{m.get(c) for m in matchings if c in m}`, `all(c in m for m in
matchings)`, `{cc for m in matchings for cc,oo in m.items() if oo==o}`),
so a test can compare it field-for-field against the new
TierMatchResult.
"""
from __future__ import annotations

import itertools


def brute_force_max_matchings_for_tier(
    claim_ids: list[int], order_ids: list[int], compatible: set[tuple[int, int]],
) -> list[dict[int, int]]:
    """Verbatim copy of the original v12/v13.1 implementation."""
    best_size = -1
    best_matchings: list[dict[int, int]] = []
    for k in range(min(len(claim_ids), len(order_ids)), -1, -1):
        if k < best_size:
            break
        found_any = False
        for claim_subset in itertools.combinations(claim_ids, k):
            for order_perm in itertools.permutations(order_ids, k):
                pairing = dict(zip(claim_subset, order_perm))
                if all((c, o) in compatible for c, o in pairing.items()):
                    found_any = True
                    if k > best_size:
                        best_size = k
                        best_matchings = [pairing]
                    elif k == best_size and pairing not in best_matchings:
                        best_matchings.append(pairing)
        if found_any and best_size == k:
            break
    return best_matchings


def derive_old_style_results(
    claim_ids: list[int], order_ids: list[int], matchings: list[dict[int, int]],
) -> tuple[int, dict[int, set[int]], dict[int, set[int]], set[int]]:
    """
    Reproduces exactly what the OLD resolve_assignments() derived from a
    brute-force `matchings` list, in the same shape as the new
    TierMatchResult (max_size, possible_partners, possible_holders,
    always_matched), so the two can be compared directly.
    """
    max_size = max((len(m) for m in matchings), default=0)
    possible_partners = {c: set() for c in claim_ids}
    possible_holders = {o: set() for o in order_ids}
    for m in matchings:
        for c, o in m.items():
            possible_partners[c].add(o)
            possible_holders[o].add(c)
    always_matched = {c for c in claim_ids if matchings and all(c in m for m in matchings)}
    return max_size, possible_partners, possible_holders, always_matched

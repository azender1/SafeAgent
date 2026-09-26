"""
test_bipartite_solver.py

Exhaustively (for small sizes) and randomly (for slightly larger ones)
cross-checks reconcile_v12._max_matchings_for_tier() -- the new
polynomial-time solver -- against bipartite_solver_reference.py's
preserved brute-force original, on every one of the fields
resolve_assignments() actually consumes: max_size, possible_partners,
possible_holders, always_matched.

Also contains the scale/load tests (10/25/50/100/250+ claims and orders)
and the targeted mutants for forced-edge / forced-claim detection.
"""
from __future__ import annotations

import itertools
import random
import sys
import time

from reconcile_v12 import _max_matchings_for_tier, _bipartite_max_matching, TierMatchResult
from bipartite_solver_reference import brute_force_max_matchings_for_tier, derive_old_style_results


def _compare_one(claim_ids, order_ids, compatible, label=""):
    old_matchings = brute_force_max_matchings_for_tier(claim_ids, order_ids, compatible)
    old_max, old_partners, old_holders, old_forced = derive_old_style_results(claim_ids, order_ids, old_matchings)

    new_result = _max_matchings_for_tier(claim_ids, order_ids, compatible)

    assert new_result.max_size == old_max, (
        f"{label}: max_size mismatch: new={new_result.max_size} old={old_max} "
        f"claims={claim_ids} orders={order_ids} compatible={compatible}"
    )
    if old_max == 0:
        return  # both sides agree there's nothing to compare further
    for c in claim_ids:
        assert new_result.possible_partners[c] == old_partners[c], (
            f"{label}: possible_partners[{c}] mismatch: new={new_result.possible_partners[c]} "
            f"old={old_partners[c]} claims={claim_ids} orders={order_ids} compatible={compatible}"
        )
    for o in order_ids:
        assert new_result.possible_holders[o] == old_holders[o], (
            f"{label}: possible_holders[{o}] mismatch: new={new_result.possible_holders[o]} "
            f"old={old_holders[o]} claims={claim_ids} orders={order_ids} compatible={compatible}"
        )
    assert new_result.always_matched == old_forced, (
        f"{label}: always_matched mismatch: new={new_result.always_matched} old={old_forced} "
        f"claims={claim_ids} orders={order_ids} compatible={compatible}"
    )


# ===================== Exhaustive small-graph cross-check =====================

def test_exhaustive_small_bipartite_graphs():
    """Every possible bipartite graph on up to 4 claims x 4 orders (every
    subset of the up-to-16 possible edges), compared field-for-field
    against the brute-force oracle. 2^16 = 65536 graphs at the largest
    size -- the brute-force oracle itself is only tractable at exactly
    this scale, which is the point of testing here rather than at the
    load-test sizes below."""
    checked = 0
    for n_claims in range(1, 5):
        for n_orders in range(1, 5):
            claim_ids = list(range(n_claims))
            order_ids = list(range(100, 100 + n_orders))  # offset so claim/order ids never collide
            all_possible_edges = [(c, o) for c in claim_ids for o in order_ids]
            for edge_mask in range(2 ** len(all_possible_edges)):
                compatible = {e for i, e in enumerate(all_possible_edges) if edge_mask & (1 << i)}
                if not compatible:
                    continue
                _compare_one(claim_ids, order_ids, compatible, label=f"exhaustive n={n_claims}x{n_orders}")
                checked += 1
    print(f"exhaustive small bipartite graphs: {checked} graphs checked, new solver matches oracle on every one: PASS")


# ===================== Randomized larger-graph cross-check =====================

def test_randomized_larger_bipartite_graphs():
    """5x5 through 7x7, randomly sampled edge sets (exhaustive at this
    size would be 2^49 -- intractable), many trials per size, still using
    the brute-force oracle (tractable up to ~7x7 given sparse-to-moderate
    density) as ground truth."""
    rng = random.Random(20260909)
    checked = 0
    for n in (5, 6, 7):
        claim_ids = list(range(n))
        order_ids = list(range(100, 100 + n))
        all_possible_edges = [(c, o) for c in claim_ids for o in order_ids]
        for trial in range(60):
            density = rng.choice([0.15, 0.3, 0.5, 0.7])
            compatible = {e for e in all_possible_edges if rng.random() < density}
            if not compatible:
                continue
            _compare_one(claim_ids, order_ids, compatible, label=f"random n={n} trial={trial} density={density}")
            checked += 1
    print(f"randomized larger bipartite graphs (5x5-7x7): {checked} graphs checked, all match: PASS")


def test_randomized_disconnected_components():
    """Graphs made of several independent disconnected components,
    checked as a single combined graph -- the new solver must handle
    disconnection correctly without being told about it explicitly.
    Kept within the SAME small total-size bound as the other
    oracle-cross-checked tests (the brute-force oracle is exponential;
    the combined multi-component graph, not just each component, is what
    it has to solve here). Scale/disconnection AT LARGE SIZE is covered
    separately by test_load_large_disconnected_case(), which does not
    use the oracle at all."""
    rng = random.Random(20260910)
    checked = 0
    for trial in range(40):
        n_components = rng.randint(2, 3)
        claim_ids, order_ids, compatible = [], [], set()
        next_c, next_o = 0, 1000
        for _ in range(n_components):
            size = rng.randint(1, 2)
            comp_claims = list(range(next_c, next_c + size))
            comp_orders = list(range(next_o, next_o + size))
            next_c += size
            next_o += size
            claim_ids += comp_claims
            order_ids += comp_orders
            density = rng.choice([0.3, 0.5, 0.8])
            for c in comp_claims:
                for o in comp_orders:
                    if rng.random() < density:
                        compatible.add((c, o))
        if not compatible or len(claim_ids) > 6:
            continue
        _compare_one(claim_ids, order_ids, compatible, label=f"disconnected trial={trial}")
        checked += 1
    print(f"randomized disconnected-component graphs: {checked} graphs checked, all match: PASS")


# ===================== Load / scale tests =====================

def _build_unambiguous_case(n):
    """n claims, n orders, each claim compatible with EXACTLY one order --
    a trivial bijection, zero real ambiguity. This is precisely the shape
    that made the old brute-force solver hang at n=10 (see
    BUILD_STATUS.md): itertools.permutations(order_ids, n) = n!
    permutations checked even though only one is ever valid."""
    claim_ids = list(range(n))
    order_ids = list(range(1000, 1000 + n))
    compatible = {(i, 1000 + i) for i in range(n)}
    return claim_ids, order_ids, compatible


def _run_load_case(n, budget_seconds):
    claim_ids, order_ids, compatible = _build_unambiguous_case(n)
    t0 = time.time()
    result = _max_matchings_for_tier(claim_ids, order_ids, compatible)
    elapsed = time.time() - t0
    assert result.max_size == n
    for i in range(n):
        assert result.possible_partners[i] == {1000 + i}
    assert result.always_matched == set(claim_ids)
    assert elapsed < budget_seconds, (
        f"n={n} took {elapsed:.3f}s, over the {budget_seconds}s budget -- "
        f"see BUILD_STATUS.md's scalability correction"
    )
    print(f"load test n={n} unambiguous claims/orders: {elapsed:.4f}s (budget {budget_seconds}s): PASS")
    return elapsed


def test_load_10_unambiguous():
    _run_load_case(10, budget_seconds=1.0)


def test_load_25_unambiguous():
    _run_load_case(25, budget_seconds=1.0)


def test_load_50_unambiguous():
    _run_load_case(50, budget_seconds=1.0)


def test_load_100_unambiguous():
    """The explicit requirement: 100 unique one-to-one matches, comfortably
    under one second, actual timing reported rather than a weakened
    threshold."""
    _run_load_case(100, budget_seconds=1.0)


def test_load_250_unambiguous():
    _run_load_case(250, budget_seconds=1.0)


def test_load_large_ambiguous_case():
    """A genuinely ambiguous large case: 40 claims, 40 orders, each claim
    compatible with a moderate random subset of orders (not a trivial
    bijection). Kept smaller than the unambiguous benchmarks above on
    purpose -- per-edge viability testing (_edge_is_viable) does a full
    residual-matching recomputation per non-reference-matching edge, so a
    dense ambiguous graph is the genuinely more expensive case; 40x40 at
    moderate density already exercises that path heavily while staying
    fast."""
    rng = random.Random(20260911)
    n = 40
    claim_ids = list(range(n))
    order_ids = list(range(1000, 1000 + n))
    compatible = set()
    for c in claim_ids:
        for o in order_ids:
            if rng.random() < 0.15:
                compatible.add((c, o))
    for c in claim_ids:  # guarantee every claim has at least one edge
        if not any(cc == c for cc, _ in compatible):
            compatible.add((c, rng.choice(order_ids)))

    t0 = time.time()
    result = _max_matchings_for_tier(claim_ids, order_ids, compatible)
    elapsed = time.time() - t0
    assert result.max_size > 0
    assert elapsed < 5.0, f"large ambiguous case took {elapsed:.3f}s"
    print(f"load test: 40x40 ambiguous case ({len(compatible)} edges): {elapsed:.4f}s: PASS")


def test_load_large_disconnected_case():
    """Many small disconnected components combined into one large input,
    proving components don't interfere with each other's timing or
    correctness at scale."""
    claim_ids, order_ids, compatible = [], [], set()
    next_c, next_o = 0, 1000
    for _ in range(50):  # 50 independent 3x3 unambiguous components = 150 claims/orders total
        for i in range(3):
            compatible.add((next_c + i, next_o + i))
        claim_ids += list(range(next_c, next_c + 3))
        order_ids += list(range(next_o, next_o + 3))
        next_c += 3
        next_o += 3

    t0 = time.time()
    result = _max_matchings_for_tier(claim_ids, order_ids, compatible)
    elapsed = time.time() - t0
    assert result.max_size == len(claim_ids)
    assert elapsed < 1.0
    print(f"load test: 50 disconnected 3x3 components ({len(claim_ids)} claims total): {elapsed:.4f}s: PASS")


# ===================== Permutation invariance at larger sizes =====================

def test_claim_permutation_invariance_at_scale():
    """Shuffling claim_ids must not change max_size, possible_partners,
    possible_holders, or always_matched -- the new solver must not be
    accidentally order-dependent the way the old one's tie-breaking could
    have been (see the original v12.1 permutation-invariance work)."""
    rng = random.Random(20260912)
    n = 60
    claim_ids = list(range(n))
    order_ids = list(range(1000, 1000 + n))
    compatible = set()
    for c in claim_ids:
        for o in order_ids:
            if rng.random() < 0.1:
                compatible.add((c, o))
    for c in claim_ids:
        if not any(cc == c for cc, _ in compatible):
            compatible.add((c, rng.choice(order_ids)))

    base = _max_matchings_for_tier(claim_ids, order_ids, compatible)
    shuffled_claims = claim_ids[:]
    rng.shuffle(shuffled_claims)
    shuffled_result = _max_matchings_for_tier(shuffled_claims, order_ids, compatible)

    assert shuffled_result.max_size == base.max_size
    assert shuffled_result.possible_partners == base.possible_partners
    assert shuffled_result.possible_holders == base.possible_holders
    assert shuffled_result.always_matched == base.always_matched
    print("claim permutation invariance at scale (n=60): PASS")


def test_order_permutation_invariance_at_scale():
    rng = random.Random(20260913)
    n = 60
    claim_ids = list(range(n))
    order_ids = list(range(1000, 1000 + n))
    compatible = set()
    for c in claim_ids:
        for o in order_ids:
            if rng.random() < 0.1:
                compatible.add((c, o))
    for c in claim_ids:
        if not any(cc == c for cc, _ in compatible):
            compatible.add((c, rng.choice(order_ids)))

    base = _max_matchings_for_tier(claim_ids, order_ids, compatible)
    shuffled_orders = order_ids[:]
    rng.shuffle(shuffled_orders)
    shuffled_result = _max_matchings_for_tier(claim_ids, shuffled_orders, compatible)

    assert shuffled_result.max_size == base.max_size
    assert shuffled_result.possible_partners == base.possible_partners
    assert shuffled_result.possible_holders == base.possible_holders
    assert shuffled_result.always_matched == base.always_matched
    print("order permutation invariance at scale (n=60): PASS")


# ===================== Targeted mutants: forced-edge / forced-claim detection =====================
# These live here (not in mutants/generate_mutants.py) because they mutate
# the NEW solver's internal helpers directly via monkeypatching, which is
# simpler and more direct than the source-patch-and-reimport mechanism
# used for the reconciliation-semantics mutants -- this is testing an
# internal algorithmic property (forced-edge / forced-claim detection),
# not an observable reconcile() behavior.

def test_mutant_edge_viability_always_true_is_caught():
    """
    If _edge_is_viable always returned True, possible_partners would
    include edges that can never actually appear in any maximum matching.

    Constructing a genuinely non-viable edge requires an "if you use me,
    something else provably loses a slot" structure -- with claim 0
    compatible with {A, B} and claim 1 compatible with {A} only, using
    edge (0,A) strands claim 1 (its only option, A, is taken) AND leaves
    B unused, capping total matching size at 1 -- worse than the true
    maximum of 2 (achieved via {0:B, 1:A}). So (0,A) is compatible but
    never viable.
    """
    import reconcile_v12 as core
    claim_ids = [0, 1]
    order_ids = ["A", "B"]
    compatible = {(0, "A"), (0, "B"), (1, "A")}

    original = core._edge_is_viable
    try:
        core._edge_is_viable = lambda *a, **k: True
        mutated_result = core._max_matchings_for_tier(claim_ids, order_ids, compatible)
        assert "A" in mutated_result.possible_partners[0], (
            "test setup invalid: mutant should have forced (0,'A') into possible_partners[0]"
        )
    finally:
        core._edge_is_viable = original

    real_result = _max_matchings_for_tier(claim_ids, order_ids, compatible)
    assert real_result.possible_partners[0] == {"B"}, (
        f"real solver: expected claim 0's only viable partner to be 'B', got {real_result.possible_partners[0]}"
    )
    print("mutant: _edge_is_viable always True is caught (real solver excludes the non-viable edge): PASS")


def test_mutant_claim_can_be_unmatched_always_true_is_caught():
    """If _claim_can_be_unmatched always returned True, no claim would
    ever be reported as 'always_matched', even a claim with only one
    possible order and nothing else competing for it -- which real
    reconciliation semantics require to become a firm ASSIGNED outcome,
    not an ambiguous ('could go unmatched') one."""
    import reconcile_v12 as core
    original = core._claim_can_be_unmatched
    try:
        core._claim_can_be_unmatched = lambda *a, **k: True
        claim_ids = [0]
        order_ids = ["A"]
        compatible = {(0, "A")}
        result = core._max_matchings_for_tier(claim_ids, order_ids, compatible)
        assert result.always_matched == set(), (
            "test setup invalid: mutant should have forced always_matched to empty"
        )
    finally:
        core._claim_can_be_unmatched = original

    real_result = _max_matchings_for_tier([0], ["A"], {(0, "A")})
    assert real_result.always_matched == {0}, (
        f"real solver: a claim with its only possible order should be forced-matched, got {real_result.always_matched}"
    )
    print("mutant: _claim_can_be_unmatched always True is caught (real solver marks the sole claim forced): PASS")


# ===================== Auto-discovering runner =====================

if __name__ == "__main__":
    import inspect

    _module = sys.modules[__name__]
    tests = sorted(
        (n, f) for n, f in vars(_module).items()
        if n.startswith("test_") and inspect.isfunction(f) and f.__module__ == __name__
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failures.append((name, str(e)[:500]))
            print(f"{name}: FAIL -- {str(e)[:500]}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed.")
    if failures:
        print(f"\n{len(failures)} FAILURES")
        for name, err in failures:
            print(f"  - {name}: {err}")
    sys.exit(1 if failures else 0)
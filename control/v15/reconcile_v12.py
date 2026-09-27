"""
SafeAgent Control -- Reconciliation Engine (v0.9)

Rebuilt as a specification-driven core, per explicit direction: four
separated stages, execution-eligibility classified before graph
construction, an immutable evidence graph, global (not greedy) tier-based
optimal assignment, sticky order accounting, and independent result
dimensions. NOT called stable, frozen, or production-ready -- see the
accompanying acceptance-status summary for the honest scorecard against
all 12 gate conditions.

STAGE SEPARATION (section 1):
  normalize_inputs()     -- classifies execution eligibility
  build_evidence_graph()  -- computes every claim/order edge, consuming nothing
  resolve_assignments()   -- global tier-based optimal matching over the graph
  classify_results()      -- turns assignments into ReconciliationResults

Order accounting and incident summarization happen strictly after
resolve_assignments() -- classify_results() never modifies assignments.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class ExecutionEligibility(str, Enum):
    EXECUTION_EXPECTED = "EXECUTION_EXPECTED"
    EXECUTION_SKIPPED = "EXECUTION_SKIPPED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"


class EvidenceTier(str, Enum):
    FULL_IDENTIFIER = "FULL_IDENTIFIER"
    PARTIAL_IDENTIFIER = "PARTIAL_IDENTIFIER"
    WEAK_HEURISTIC = "WEAK_HEURISTIC"
    IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT"
    INCOMPATIBLE = "INCOMPATIBLE"


class OrderDisposition(str, Enum):
    """
    The single, authoritative accounting category for an external order.
    Computed exactly once, by resolve_assignments() alone. Nothing
    downstream (finding generation) may ever write to this -- findings may
    reference an order's disposition freely, but never change it.
    """
    ASSIGNED = "ASSIGNED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT"
    # IGNORED_BY_POLICY intentionally removed (was present in v10). No real
    # input field or policy exists yet to produce it, and it had zero test
    # coverage -- an unused terminal category presented as implemented
    # functionality is worse than not having it. Re-add only alongside a
    # real policy field, documented semantics, and tests, if/when a genuine
    # use case exists (e.g., an order explicitly excluded from
    # consideration by an operator-configured rule).


# Backward-compatible alias while the rest of the codebase is migrated.
OrderCategory = OrderDisposition


class Result(str, Enum):
    CONFIRMED = "CONFIRMED"
    DUPLICATE_BLOCKED = "DUPLICATE_BLOCKED"
    MISSING_EXTERNALLY = "MISSING_EXTERNALLY"
    UNRECORDED_INTERNALLY = "UNRECORDED_INTERNALLY"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"
    CONTRADICTION = "CONTRADICTION"


class PositionState(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CONFIRMED_OPEN = "CONFIRMED_OPEN"
    CONFIRMED_FLAT = "CONFIRMED_FLAT"
    UNKNOWN = "UNKNOWN"


class MatchConfidence(str, Enum):
    NONE = "NONE"
    STRONG_FULL = "STRONG_FULL"
    STRONG_PARTIAL = "STRONG_PARTIAL"
    WEAK_HEURISTIC = "WEAK_HEURISTIC"


class Timeliness(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    OVERDUE = "OVERDUE"


class CaseStatus(str, Enum):
    NO_CASE = "NO_CASE"
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class Interpretation(str, Enum):
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class FailureType(str, Enum):
    NONE = "NONE"
    BROKER_REJECTED = "BROKER_REJECTED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN = "UNKNOWN"


class ReasonCode(str, Enum):
    NONE = "NONE"
    CALLER_IGNORED_SKIP = "CALLER_IGNORED_SKIP"
    DEDUP_KEY_COLLISION = "DEDUP_KEY_COLLISION"
    BROKER_REJECTED = "BROKER_REJECTED"
    BROKER_MATCH_NOT_FOUND = "BROKER_MATCH_NOT_FOUND"
    UNMATCHED_BROKER_ORDER = "UNMATCHED_BROKER_ORDER"
    IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT"
    EXTERNAL_IDENTIFIER_CONFLICT = "EXTERNAL_IDENTIFIER_CONFLICT"
    AMBIGUOUS_MULTIPLE_IDENTIFIER_MATCHES = "AMBIGUOUS_MULTIPLE_IDENTIFIER_MATCHES"
    AMBIGUOUS_MULTIPLE_PARTIAL_MATCHES = "AMBIGUOUS_MULTIPLE_PARTIAL_MATCHES"
    AMBIGUOUS_MULTIPLE_WEAK_MATCHES = "AMBIGUOUS_MULTIPLE_WEAK_MATCHES"
    PARTIAL_IDENTIFIER_EVIDENCE = "PARTIAL_IDENTIFIER_EVIDENCE"
    AMBIGUOUS_PROVIDER_RESPONSE = "AMBIGUOUS_PROVIDER_RESPONSE"
    REJECTION_EXECUTION_CONTRADICTION = "REJECTION_EXECUTION_CONTRADICTION"


REJECTED_COMPATIBLE_STATUSES = {"rejected", "canceled", "cancelled"}
FILLED_STATUSES = {"filled", "partially_filled"}


@dataclass
class InternalClaim:
    request_id: str
    symbol: str
    side: str
    qty: float
    stage: str
    claimed_at: datetime
    outcome: str
    caller_interpretation: Interpretation
    broker_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    failure_type: FailureType = FailureType.NONE
    known_reason_codes: tuple[ReasonCode, ...] = ()
    # Source identity for a DETERMINISTIC stable_id (v12.1). request_id is a
    # caller-supplied label, not a unique identity -- D26 explicitly proves
    # two distinct claims may legitimately share one (e.g. two attempts
    # logged under the same client-side request tag). Comparing, grouping,
    # or correlating claims by request_id therefore cannot reliably tell
    # them apart; see assign_claim_stable_ids() below, which is what must
    # be used instead anywhere claim IDENTITY (as opposed to a
    # human-readable label) matters.
    source_system: Optional[str] = None
    source_account: Optional[str] = None
    immutable_source_event_id: Optional[str] = None
    stable_id: str = field(default="", compare=False)
    stable_id_basis: str = field(default="", compare=False)  # 'immutable_source_event_id' | 'fingerprint'


def assign_claim_stable_ids(claims: list["InternalClaim"]) -> None:
    """
    Computes and assigns stable_id in place for every claim in the batch,
    deterministically and independent of list order. Must be called once,
    on the full batch, before claims are used for identity-sensitive
    comparison, correlation, or grouping -- reconcile() does this
    automatically. Mirrors assign_stable_ids() for BrokerOrder.

    Priority:
      1. source_system + source_account + immutable_source_event_id
      2. a canonical fingerprint of normalized immutable fields (deliberately
         excluding request_id, which is a label, not identity), with
         multiplicity retained -- not collapsed -- for genuinely identical
         claims: multiplicity rank is assigned via a stable sort over the
         full field tuple, exactly as for identifier-less BrokerOrders.
    """
    fingerprint_groups: dict[tuple, list[int]] = {}
    for idx, c in enumerate(claims):
        if c.immutable_source_event_id:
            c.stable_id = (
                f"{c.source_system or 'UNKNOWN'}:{c.source_account or 'UNKNOWN'}:"
                f"evt:{c.immutable_source_event_id}"
            )
            c.stable_id_basis = "immutable_source_event_id"
        else:
            fp = (
                c.symbol, c.side, round(c.qty, 8), c.stage, c.claimed_at.isoformat(),
                c.outcome, c.caller_interpretation.value, c.broker_order_id,
                c.client_order_id, c.failure_type.value, c.source_system, c.source_account,
            )
            fingerprint_groups.setdefault(fp, []).append(idx)

    for fp, indices in fingerprint_groups.items():
        # Stable sort by original index -- the same last-resort tiebreak
        # used for identifier-less orders. Claims sharing this fingerprint
        # are, by construction, identical in every field this function
        # looks at, so which specific label each gets cannot affect any
        # semantic engine outcome; only that the labels are distinct and
        # deterministic matters.
        ranked = sorted(indices)
        fp_str = "|".join(str(x) for x in fp)
        for rank, idx in enumerate(ranked):
            claims[idx].stable_id = f"fingerprint:{fp_str}#{rank}"
            claims[idx].stable_id_basis = "fingerprint"


@dataclass
class BrokerOrder:
    symbol: str
    side: str
    qty: float
    status: str
    submitted_at: datetime
    filled_at: Optional[datetime] = None
    broker_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    # Source identity, used to compute a DETERMINISTIC stable_id -- the
    # same real broker record re-imported must get the same identity every
    # time. A random UUID (the v10 default) fails this: the same order
    # pulled from the broker twice would get two different identities,
    # defeating the entire purpose of a stable identifier for repeated
    # ingestion. source_system/source_account are required for a properly
    # deterministic identity; if omitted, identity falls back to a content
    # fingerprint (see assign_stable_ids()), which is honest but weaker --
    # flagged via stable_id_basis below rather than silently accepted as
    # equally strong.
    source_system: Optional[str] = None
    source_account: Optional[str] = None
    immutable_source_record_id: Optional[str] = None
    stable_id: str = field(default="", compare=False)
    stable_id_basis: str = field(default="", compare=False)  # 'broker_order_id' | 'immutable_record_id' | 'fingerprint'
    # How many RAW source records this object represents after ingestion
    # normalization collapsed exact duplicates. 1 means "seen once". Set by
    # normalize_ingested_orders(); never used by the matching engine, which
    # is why it is compare=False -- it is provenance, not business content.
    ingestion_count: int = field(default=1, compare=False)


def assign_stable_ids(orders: list["BrokerOrder"]) -> None:
    """
    Computes and assigns stable_id in place for every order in the batch,
    deterministically and independent of list order. Must be called once,
    on the full batch, before orders are used -- reconcile() does this
    automatically.

    Priority, per spec:
      1. source_system + source_account + broker_order_id
      2. source_system + source_account + immutable_source_record_id
      3. a canonical fingerprint of normalized immutable fields, with
         multiplicity retained (not collapsed) for genuinely identical
         records -- multiplicity rank is assigned via a stable sort over
         the FULL field tuple of every record sharing that fingerprint, so
         two records identical in every business field are the only case
         where the specific string label could vary under permutation --
         and since they are content-identical, that has no effect on any
         engine outcome, only on which of two indistinguishable labels a
         given object happens to receive.
    """
    fingerprint_groups: dict[tuple, list[int]] = {}
    for idx, o in enumerate(orders):
        if o.broker_order_id:
            o.stable_id = f"{o.source_system or 'UNKNOWN'}:{o.source_account or 'UNKNOWN'}:boid:{o.broker_order_id}"
            o.stable_id_basis = "broker_order_id"
        elif o.immutable_source_record_id:
            o.stable_id = f"{o.source_system or 'UNKNOWN'}:{o.source_account or 'UNKNOWN'}:rid:{o.immutable_source_record_id}"
            o.stable_id_basis = "immutable_record_id"
        else:
            fp = (o.symbol, o.side, round(o.qty, 8), o.status, o.submitted_at.isoformat(),
                  o.filled_at.isoformat() if o.filled_at else None,
                  o.client_order_id, o.source_system, o.source_account)
            fingerprint_groups.setdefault(fp, []).append(idx)

    for fp, indices in fingerprint_groups.items():
        # Stable sort by the SAME full tuple used as the fingerprint plus
        # original index as the final, last-resort tiebreak for records
        # that are literally identical in every business field -- at that
        # point they are truly interchangeable, so which specific label
        # each gets cannot affect any semantic engine outcome.
        ranked = sorted(indices)
        fp_str = "|".join(str(x) for x in fp)
        for rank, idx in enumerate(ranked):
            orders[idx].stable_id = f"fingerprint:{fp_str}#{rank}"
            orders[idx].stable_id_basis = "fingerprint"


@dataclass(frozen=True)
class Edge:
    claim_idx: int
    order_idx: int
    tier: EvidenceTier
    identifier_evidence: str
    status_compatible: bool
    symbol_side_qty_compatible: bool
    timestamp_distance_seconds: float
    conflict_reasons: tuple[str, ...] = ()


@dataclass
class Assignment:
    claim_idx: int
    order_idx: Optional[int]
    kind: str
    tier: Optional[EvidenceTier] = None
    involved_order_indices: tuple[int, ...] = ()


@dataclass
class ReconciliationResult:
    claim: Optional[InternalClaim]
    matched_broker_order: Optional[BrokerOrder]
    result: Result
    reason_codes: tuple[ReasonCode, ...]
    case_status: CaseStatus
    timeliness: Timeliness
    match_confidence: MatchConfidence
    position_state: PositionState
    evidence: str
    correlation_id: Optional[str] = None
    involved_order_stable_ids: tuple[str, ...] = ()
    accounting_category: Optional[OrderCategory] = None


STALE_AFTER = timedelta(minutes=10)
OVERDUE_AFTER = timedelta(hours=1)
QTY_EPSILON = 1e-6
MATCH_TOLERANCE = timedelta(minutes=5)


def _qty_eq(a: float, b: float) -> bool:
    return abs(a - b) < QTY_EPSILON


def _within(a: datetime, b: datetime, tolerance: timedelta) -> bool:
    return abs((a - b).total_seconds()) <= tolerance.total_seconds()


def _timeliness(age: timedelta) -> Timeliness:
    if age >= OVERDUE_AFTER:
        return Timeliness.OVERDUE
    if age >= STALE_AFTER:
        return Timeliness.STALE
    return Timeliness.CURRENT


def normalize_inputs(
    claims: list[InternalClaim], broker_orders: list[BrokerOrder],
) -> dict[int, ExecutionEligibility]:
    eligibility: dict[int, ExecutionEligibility] = {}
    for i, c in enumerate(claims):
        if c.outcome == "SKIP":
            eligibility[i] = ExecutionEligibility.EXECUTION_SKIPPED
        elif c.failure_type == FailureType.BROKER_REJECTED:
            eligibility[i] = ExecutionEligibility.EXECUTION_REJECTED
        elif c.outcome == "PENDING":
            eligibility[i] = ExecutionEligibility.EXECUTION_PENDING
        elif c.outcome == "COMMITTED":
            eligibility[i] = ExecutionEligibility.EXECUTION_EXPECTED
        else:
            eligibility[i] = ExecutionEligibility.EXECUTION_UNKNOWN
    return eligibility


def build_evidence_graph(
    claims: list[InternalClaim],
    broker_orders: list[BrokerOrder],
    eligibility: dict[int, ExecutionEligibility],
) -> list[Edge]:
    edges: list[Edge] = []
    for i, c in enumerate(claims):
        if eligibility[i] == ExecutionEligibility.EXECUTION_SKIPPED:
            continue

        has_ids = bool(c.broker_order_id or c.client_order_id)

        for j, o in enumerate(broker_orders):
            dist = abs((o.submitted_at - c.claimed_at).total_seconds())

            if has_ids:
                checks = []
                if c.broker_order_id and o.broker_order_id:
                    checks.append(c.broker_order_id == o.broker_order_id)
                if c.client_order_id and o.client_order_id:
                    checks.append(c.client_order_id == o.client_order_id)
                if not checks:
                    continue
                if all(checks):
                    tier = EvidenceTier.FULL_IDENTIFIER if len(checks) == 2 else EvidenceTier.PARTIAL_IDENTIFIER
                    evidence_str = ("both identifiers agree" if len(checks) == 2
                                     else "one identifier agrees, other side missing")
                    conflicts: tuple[str, ...] = ()
                elif any(checks):
                    # A genuine conflict requires PARTIAL agreement: at least
                    # one identifier matches (suggesting these two records
                    # might really be about the same thing) while another
                    # disagrees (a real anomaly). Two records where EVERY
                    # compared identifier disagrees are not "conflicting" --
                    # they are simply unrelated, and must not generate an
                    # edge at all. Flagging every differing ID pair as a
                    # conflict was a real bug: it made any two unrelated
                    # orders with different IDs look like a data anomaly.
                    tier = EvidenceTier.IDENTIFIER_CONFLICT
                    evidence_str = "one identifier agrees, another disagrees"
                    conflicts = ("identifier_mismatch",)
                else:
                    continue  # no identifier agrees at all -- genuinely unrelated, no edge
            else:
                symbol_side_qty_ok = (o.symbol == c.symbol and o.side == c.side and _qty_eq(o.qty, c.qty))
                time_ok = dist <= MATCH_TOLERANCE.total_seconds()
                if symbol_side_qty_ok and time_ok:
                    tier = EvidenceTier.WEAK_HEURISTIC
                    evidence_str = "no identifiers on either side; symbol/side/qty/time only"
                    conflicts = ()
                else:
                    continue

            symbol_side_qty_compatible = (o.symbol == c.symbol and o.side == c.side and _qty_eq(o.qty, c.qty))

            # Status compatibility (section 2): a REJECTED-eligibility claim
            # is only status-compatible with a rejected/canceled order. A
            # match against a FILLED order is real identifier evidence but
            # contradictory -- tracked here, surfaced explicitly in
            # classify_results, never silently treated as a normal match.
            if eligibility[i] == ExecutionEligibility.EXECUTION_REJECTED:
                status_compatible = o.status in REJECTED_COMPATIBLE_STATUSES
            else:
                status_compatible = True

            edges.append(Edge(
                claim_idx=i, order_idx=j, tier=tier,
                identifier_evidence=evidence_str,
                status_compatible=status_compatible,
                symbol_side_qty_compatible=symbol_side_qty_compatible,
                timestamp_distance_seconds=dist,
                conflict_reasons=conflicts,
            ))
    return edges


@dataclass
class TierMatchResult:
    """
    Everything resolve_assignments() actually needs from a tier's
    bipartite compatibility graph, computed in polynomial time --
    replaces the old brute-force enumeration of every maximum matching
    (see the module-level note above _bipartite_max_matching for why that
    was replaced, and _bipartite_solver_reference.py for the preserved
    original, kept ONLY as a small-input cross-check oracle in tests).

    max_size: the maximum matching cardinality.
    possible_partners[c]: every order o such that edge (c,o) appears in
        AT LEAST ONE maximum matching -- the direct replacement for the
        old `{m.get(c) for m in matchings if c in m}`.
    possible_holders[o]: the same relation from the order's side --
        replaces `{cc for m in matchings for cc,oo in m.items() if oo==o}`.
    always_matched: claims that are matched in EVERY maximum matching --
        replaces `all(c in m for m in matchings)`.
    """
    max_size: int
    possible_partners: dict[int, set[int]]
    possible_holders: dict[int, set[int]]
    always_matched: set[int]


def _bipartite_max_matching(
    claim_ids: list[int], order_ids: list[int], compatible: set[tuple[int, int]],
) -> tuple[dict[int, int], dict[int, int]]:
    """
    ONE maximum-cardinality bipartite matching via Kuhn's algorithm
    (repeated augmenting-path search), O(V*E). Deterministic: claims and
    their candidate orders are always visited in sorted order, so which
    particular maximum matching is returned (there can be several of the
    same size) is reproducible given the same input.

    Returns (claim_to_order, order_to_claim) -- both directions, since
    every caller in this module needs to walk the matching from one side
    or the other and recomputing the reverse mapping repeatedly would be
    wasteful.
    """
    adj_by_claim: dict[int, list[int]] = {c: [] for c in claim_ids}
    for c, o in sorted(compatible):
        adj_by_claim[c].append(o)
    order_to_claim: dict[int, int] = {}

    def try_augment(c: int, visited_orders: set[int]) -> bool:
        for o in adj_by_claim[c]:
            if o in visited_orders:
                continue
            visited_orders.add(o)
            if o not in order_to_claim or try_augment(order_to_claim[o], visited_orders):
                order_to_claim[o] = c
                return True
        return False

    for c in sorted(claim_ids):
        try_augment(c, set())

    claim_to_order = {c: o for o, c in order_to_claim.items()}
    return claim_to_order, order_to_claim


def _claim_can_be_unmatched(
    c: int, claim_to_order: dict[int, int], order_to_claim: dict[int, int],
    adj_by_order: dict[int, list[int]],
) -> bool:
    """
    True iff SOME maximum matching leaves claim c unmatched. If c is
    already unmatched in the reference matching, trivially true. Otherwise,
    frees c's current order o and attempts a SINGLE augmenting-path search
    starting from o (over claims other than c) to see whether o -- and
    transitively, whatever it displaces -- can be rematched without ever
    reusing c. If that search succeeds, a maximum matching of the same
    size exists that doesn't touch c. This is one DFS, O(E), not a full
    matching recomputation.
    """
    if c not in claim_to_order:
        return True
    o = claim_to_order[c]
    temp_order_to_claim = dict(order_to_claim)
    del temp_order_to_claim[o]
    temp_claim_to_order = {cc: oo for oo, cc in temp_order_to_claim.items()}

    def try_augment_order(oo: int, visited_claims: set[int]) -> bool:
        for cc in adj_by_order.get(oo, []):
            if cc == c or cc in visited_claims:
                continue
            visited_claims.add(cc)
            cc_order = temp_claim_to_order.get(cc)
            if cc_order is None or try_augment_order(cc_order, visited_claims):
                temp_order_to_claim[oo] = cc
                temp_claim_to_order[cc] = oo
                return True
        return False

    return try_augment_order(o, set())


def _edge_is_viable(
    c: int, o: int, claim_ids: list[int], order_ids: list[int],
    compatible: set[tuple[int, int]], max_size: int,
) -> bool:
    """
    True iff edge (c,o) -- known not to be in the reference maximum
    matching -- appears in AT LEAST ONE maximum matching. Implements the
    literal method requested: force the edge by removing both endpoints
    from the graph, compute the residual maximum matching on what's left,
    and check whether 1 + residual_size == max_size. Only called for
    edges outside the reference matching (edges IN it are trivially
    viable), which in every case this project's fuzzers and load tests
    exercise is a small fraction of the total -- see BUILD_STATUS.md for
    the complexity discussion.
    """
    residual_claims = [cc for cc in claim_ids if cc != c]
    residual_orders = [oo for oo in order_ids if oo != o]
    residual_compatible = {(cc, oo) for cc, oo in compatible if cc != c and oo != o}
    if not residual_compatible:
        residual_size = 0
    else:
        residual_claim_to_order, _ = _bipartite_max_matching(residual_claims, residual_orders, residual_compatible)
        residual_size = len(residual_claim_to_order)
    return 1 + residual_size == max_size


def _max_matchings_for_tier(
    claim_ids: list[int], order_ids: list[int], compatible: set[tuple[int, int]],
) -> TierMatchResult:
    """
    Polynomial-time replacement for the original brute-force
    implementation, which enumerated `itertools.permutations(order_ids,
    k)` for k = min(len(claim_ids), len(order_ids)) -- O(n!) with no
    early exit, even for a completely unambiguous one-to-one matching.
    Measured against real production data: 8 claims ran in 0.04s, 9 in
    0.37s, 10 hung (>3s and climbing factorially). See
    BUILD_STATUS.md's "scalability correction" section for the full
    writeup and measurements at this replacement's scale (10-250+
    claims/orders, well under one second).

    Computes exactly the three things resolve_assignments() consumes,
    without ever materializing individual maximum matchings:
      1. The maximum matching cardinality (one Kuhn's-algorithm run).
      2. For every compatible edge, whether it can appear in some maximum
         matching -- trivially true for edges in the reference matching;
         tested via _edge_is_viable() (force + residual recompute, as
         specified) for every other edge.
      3. For every claim, whether it's matched in every maximum matching
         -- tested via _claim_can_be_unmatched() (single augmenting-path
         search from its freed order).

    The original function's return type (a list of every distinct
    maximum matching) is fundamentally incompatible with polynomial
    output size -- a complete bipartite graph on n claims and n orders
    has n! distinct maximum matchings. resolve_assignments() never
    actually needed the individual matchings, only the derived
    partners/holders/forced sets this returns directly; see its comments
    at the call site for the exact equivalence.
    """
    claim_to_order, order_to_claim = _bipartite_max_matching(claim_ids, order_ids, compatible)
    max_size = len(claim_to_order)

    possible_partners: dict[int, set[int]] = {c: set() for c in claim_ids}
    possible_holders: dict[int, set[int]] = {o: set() for o in order_ids}
    if max_size == 0:
        return TierMatchResult(max_size=0, possible_partners=possible_partners,
                                possible_holders=possible_holders, always_matched=set())

    for c, o in claim_to_order.items():
        possible_partners[c].add(o)
        possible_holders[o].add(c)

    adj_by_order: dict[int, list[int]] = {o: [] for o in order_ids}
    for c, o in sorted(compatible):
        adj_by_order[o].append(c)

    reference_edges = set(claim_to_order.items())
    for c, o in sorted(compatible):
        if (c, o) in reference_edges:
            continue
        if _edge_is_viable(c, o, claim_ids, order_ids, compatible, max_size):
            possible_partners[c].add(o)
            possible_holders[o].add(c)

    always_matched = {
        c for c in claim_ids
        if not _claim_can_be_unmatched(c, claim_to_order, order_to_claim, adj_by_order)
    }

    return TierMatchResult(max_size=max_size, possible_partners=possible_partners,
                            possible_holders=possible_holders, always_matched=always_matched)


def resolve_assignments(
    edges: list[Edge], n_claims: int, n_orders: int,
) -> tuple[dict[int, Assignment], dict[int, OrderDisposition]]:
    """
    Returns (assignment, disposition). disposition is the SOLE authoritative
    accounting record -- computed once, here, and never touched again.
    Downstream finding-generation code may read it but must never write to
    it; a finding referencing an order does not and cannot change that
    order's disposition.
    """
    assignment: dict[int, Assignment] = {}
    order_state: dict[int, str] = {i: "open" for i in range(n_orders)}

    def available_orders() -> set[int]:
        return {i for i, s in order_state.items() if s == "open"}

    def edges_for_tier(tier, avail_orders, unresolved_claims):
        return {(e.claim_idx, e.order_idx) for e in edges
                if e.tier == tier and e.order_idx in avail_orders
                and e.claim_idx in unresolved_claims and e.status_compatible}

    for tier in (EvidenceTier.FULL_IDENTIFIER, EvidenceTier.PARTIAL_IDENTIFIER, EvidenceTier.WEAK_HEURISTIC):
        unresolved = {i for i in range(n_claims) if i not in assignment}
        avail = available_orders()
        compatible = edges_for_tier(tier, avail, unresolved)
        if not compatible:
            continue
        claim_ids = sorted({c for c, _ in compatible})
        order_ids = sorted({o for _, o in compatible})
        # NOTE: matchings used to be `list[dict[int,int]]` (every distinct
        # maximum matching, enumerated by brute force). It is now a
        # TierMatchResult computed in polynomial time -- see
        # _max_matchings_for_tier()'s docstring. The two lines just below
        # are the exact semantic replacements:
        #   old: partners = {m.get(c) for m in matchings if c in m}
        #   new: partners = result.possible_partners[c]
        #   old: all(c in m for m in matchings)   (c matched in EVERY max matching)
        #   new: c in result.always_matched
        #   old: holders = {cc for m in matchings for cc,oo in m.items() if oo==o}
        #   new: holders = result.possible_holders[o]
        # Every downstream conditional (len==1 and forced -> assigned;
        # elif partners -> ambiguous; len(holders)>1 -> order ambiguous)
        # is unchanged.
        result = _max_matchings_for_tier(claim_ids, order_ids, compatible)
        if result.max_size == 0:
            continue

        for c in claim_ids:
            partners = result.possible_partners[c]
            if len(partners) == 1 and c in result.always_matched:
                order_idx = next(iter(partners))
                assignment[c] = Assignment(claim_idx=c, order_idx=order_idx, kind="assigned", tier=tier)
                order_state[order_idx] = "assigned"
            elif partners:
                assignment[c] = Assignment(claim_idx=c, order_idx=None, kind="ambiguous",
                                            tier=tier, involved_order_indices=tuple(sorted(partners)))
                for o in partners:
                    if order_state[o] == "open":
                        order_state[o] = "ambiguous"

        for o in order_ids:
            if order_state[o] != "open":
                continue
            holders = result.possible_holders[o]
            if len(holders) > 1:
                order_state[o] = "ambiguous"
                for c in holders:
                    if c not in assignment:
                        assignment[c] = Assignment(claim_idx=c, order_idx=None, kind="ambiguous",
                                                    tier=tier, involved_order_indices=(o,))

    # Snapshot availability ONCE before this loop -- not per-claim. Multiple
    # claims can genuinely conflict with the SAME order simultaneously; a
    # conflict relationship doesn't "consume" the order the way an
    # assignment does. Re-fetching available_orders() inside the loop was a
    # real order-dependence bug: whichever claim happened to be processed
    # first marked the order 'conflict' (no longer 'open'), silently
    # dropping every other equally-valid conflicting claim's own relationship
    # to that same order depending purely on list position.
    conflict_pass_avail = available_orders()
    for i in range(n_claims):
        if i in assignment:
            continue
        conflicts = tuple(sorted({e.order_idx for e in edges
                                   if e.claim_idx == i and e.tier == EvidenceTier.IDENTIFIER_CONFLICT
                                   and e.order_idx in conflict_pass_avail}))
        if conflicts:
            assignment[i] = Assignment(claim_idx=i, order_idx=None, kind="conflict",
                                        involved_order_indices=conflicts)
            for o in conflicts:
                if order_state[o] == "open":
                    order_state[o] = "conflict"

    for i in range(n_claims):
        if i not in assignment:
            assignment[i] = Assignment(claim_idx=i, order_idx=None, kind="none")

    # Final conflict sweep, still inside the single disposition-owning
    # function: a claim that got ASSIGNED via a full/partial/weak match can
    # separately hold a conflict-tier edge against a DIFFERENT, still-open
    # order. That edge is never visited above, since an already-resolved
    # claim's assignment kind is 'assigned', not 'conflict'. Per spec,
    # conflicting evidence must not be silently discarded just because a
    # stronger match existed elsewhere for that claim.
    for e in edges:
        if e.tier != EvidenceTier.IDENTIFIER_CONFLICT:
            continue
        if order_state[e.order_idx] == "open":
            order_state[e.order_idx] = "conflict"

    disposition: dict[int, OrderDisposition] = {}
    for i in range(n_orders):
        disposition[i] = {
            "open": OrderDisposition.UNMATCHED,
            "assigned": OrderDisposition.ASSIGNED,
            "ambiguous": OrderDisposition.AMBIGUOUS,
            "conflict": OrderDisposition.IDENTIFIER_CONFLICT,
        }[order_state[i]]

    return assignment, disposition
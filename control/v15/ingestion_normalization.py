"""
Ingestion normalization contract (v12, item 4).

WHY THIS EXISTS
---------------
v11's fuzzer was constrained so that two records could never share a
broker_order_id, on the stated grounds that a broker guarantees uniqueness
and such input is therefore not realistic. That reasoning was wrong in a
specific way: the broker's uniqueness guarantee is about REAL ORDERS, not
about the rows in a file we happened to import. Real imported data
routinely contains, for the SAME real order:

  - the same source record repeated across two exports
  - overlapping pagination windows returning the row twice
  - duplicate CSV rows
  - the same identifier appearing again with a progressed status
  - corrupted records sharing an identifier but disagreeing on fields that
    can never legitimately change

So the correct response is not to exclude the input, it is to define what
happens to it -- BEFORE any record reaches the matching engine. That is
what this module does, and it is the contract the raw-log parser will be
written against.

THE THREE DOCUMENTED BEHAVIORS
------------------------------
1. EXACT DUPLICATES (identical in every business field, including status)
   sharing a stable identity are deduplicated to one record, with
   `ingestion_count` retaining how many raw copies were seen. The
   duplication is collapsed but never silently lost -- the count is
   provenance.

2. COMPATIBLE LIFECYCLE CHANGE: same identity, all immutable fields agree,
   status differs but only as a forward progression through
   STATUS_PRECEDENCE (e.g. pending_new -> filled). Consolidated to a
   single record carrying the most advanced status. This is the ordinary
   case of an order updating between two pulls of the same feed, not an
   anomaly, so it produces no finding.

3. CONTRADICTION: same identity, but either
     (a) an immutable field disagrees, or
     (b) two mutually exclusive TERMINAL statuses are reported
         (e.g. filled and rejected for what claims to be one order),
   Never silently collapsed. A SOURCE_IDENTITY_CONFLICT ingestion finding
   is emitted listing every conflicting raw record. One deterministically
   chosen representative still passes through, so downstream matching has
   exactly one candidate per identity, but the disagreement itself stays
   visible and auditable rather than being resolved by luck of list order.

DELIBERATE BOUNDARY: records whose identity is fingerprint-based (no
broker_order_id and no immutable_source_record_id) are NOT deduplicated
here. assign_stable_ids() already gives each such record a distinct
identity on purpose, because two identifier-less records that are
identical in every field are genuinely indistinguishable from two separate
real executions of the same size at the same instant. Collapsing them
would destroy real multiplicity to tidy up a case we cannot actually
resolve. Deduplication is only safe when a real source identifier says
"these are the same record."

AFTER THIS STAGE, reconcile() may assume every order has a unique
stable_id. That assumption is now guaranteed by a real code path with its
own tests, not by a constrained fuzzer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from reconcile_v12 import BrokerOrder, assign_stable_ids


# A genuine order lifecycle only ever moves FORWARD through this ranking.
# Two DIFFERENT statuses at the same terminal rank are not a progression --
# they are mutually exclusive outcomes, i.e. a contradiction.
STATUS_PRECEDENCE = {
    "pending_new": 0,
    "new": 0,
    "accepted": 0,
    "partially_filled": 1,
    "filled": 2,
    "rejected": 2,
    "canceled": 2,
    "cancelled": 2,
    "expired": 2,
}
TERMINAL_STATUSES = {"filled", "rejected", "canceled", "cancelled", "expired"}

SOURCE_IDENTITY_CONFLICT = "SOURCE_IDENTITY_CONFLICT"
# A status outside STATUS_PRECEDENCE means "forward progression" cannot be
# trusted for that group -- see the check in normalize_ingested_orders()
# for why this must not fall through to max()'s default rank-0 treatment.
UNMAPPED_STATUS_UNRESOLVED = "UNMAPPED_STATUS_UNRESOLVED"


@dataclass
class IngestionFinding:
    """
    An ingestion-layer finding. Deliberately a separate type from
    ReconciliationResult: this is about the integrity of the input data
    itself, detected before matching, and it must not be mistaken for a
    reconciliation outcome about a claim.
    """
    finding_type: str
    stable_id: str
    conflicting_records: list = field(default_factory=list)
    detail: str = ""


def _immutable_fields(o: BrokerOrder) -> tuple:
    """
    Fields that can never legitimately change for one real order.

    client_order_id is included as of v12.1 -- it is one of the engine's
    strongest identifiers (see build_evidence_graph's FULL_IDENTIFIER
    tier), so two records sharing a broker order ID but reporting
    DIFFERENT client order IDs must not be silently consolidated as an
    ordinary lifecycle update. See _immutable_fields_conflict() for the
    one deliberate exception: a missing (None) client_order_id becoming
    known in a later observation is enrichment, not a contradiction --
    plain tuple equality on this return value would treat that as a
    conflict, so conflict detection does NOT compare this tuple directly;
    it goes through _immutable_fields_conflict() instead.
    """
    return (
        o.symbol, o.side, round(o.qty, 8), o.submitted_at,
        o.source_system, o.source_account,
        o.broker_order_id, o.immutable_source_record_id,
        o.client_order_id,
    )


def _immutable_fields_conflict(group: list[BrokerOrder]) -> bool:
    """
    True if the group disagrees on any immutable field, honoring ONE
    deliberate exception: client_order_id may go from None to a value
    (enrichment -- a later observation simply learned it) without that
    counting as a conflict, PROVIDED every other immutable field agrees.
    Two DISTINCT non-null client_order_id values are always a conflict,
    regardless of anything else -- this is one of the engine's strongest
    identifiers, so silently keeping whichever value was seen last is not
    acceptable (this is exactly the class of bug behind the September
    dedup-key incident).
    """
    other_fields = {_immutable_fields(o)[:-1] for o in group}  # everything except client_order_id
    if len(other_fields) > 1:
        return True  # something besides client_order_id disagrees: always a conflict
    non_null_cids = {o.client_order_id for o in group if o.client_order_id is not None}
    return len(non_null_cids) > 1


def _enrich_representative(rep: BrokerOrder, group: list[BrokerOrder]) -> None:
    """
    If the chosen representative is missing client_order_id but exactly
    one non-null value exists elsewhere in the group (i.e. this is the
    None -> value enrichment case, not a multi-value conflict -- a
    conflict already produced its own finding and returned before this is
    ever called), carry that value forward. Without this, "enrichment
    isn't a conflict" would only affect whether a finding fires, while the
    surviving record could still silently lose the client_order_id if the
    representative-selection logic happened to pick the record that
    hadn't learned it yet.
    """
    if rep.client_order_id is not None:
        return
    non_null = {o.client_order_id for o in group if o.client_order_id is not None}
    if len(non_null) == 1:
        rep.client_order_id = next(iter(non_null))


def _all_business_fields(o: BrokerOrder) -> tuple:
    """Every business field, including mutable ones. Used for exact-duplicate
    detection. stable_id / stable_id_basis / ingestion_count are excluded:
    they are derived or provenance, not source content. client_order_id is
    already the last element of _immutable_fields() as of v12.1, so it is
    not repeated here."""
    return _immutable_fields(o) + (o.status, o.filled_at)


def normalize_ingested_orders(
    raw_orders: list[BrokerOrder],
) -> tuple[list[BrokerOrder], list[IngestionFinding]]:
    """
    The single entry point of this contract.

    Takes raw records as a real feed might deliver them -- possibly with
    duplicates and contradictions -- and returns
    (clean_orders, ingestion_findings) where clean_orders holds exactly one
    record per genuine stable identity, safe to hand to reconcile().

    Deterministic and independent of input list order: the same raw batch
    in any permutation produces the same clean set and the same findings.
    """
    assign_stable_ids(raw_orders)

    groups: dict[str, list[BrokerOrder]] = {}
    for o in raw_orders:
        groups.setdefault(o.stable_id, []).append(o)

    normalized: list[BrokerOrder] = []
    findings: list[IngestionFinding] = []

    for stable_id in sorted(groups):  # sorted -> output order is deterministic
        group = groups[stable_id]

        if len(group) == 1:
            group[0].ingestion_count = 1
            normalized.append(group[0])
            continue

        # --- Behavior 1: exact duplicates ---------------------------------
        first_fields = _all_business_fields(group[0])
        if all(_all_business_fields(o) == first_fields for o in group):
            rep = group[0]
            rep.ingestion_count = len(group)
            normalized.append(rep)
            continue

        # --- Behavior 3a: an immutable field disagrees --------------------
        # Uses _immutable_fields_conflict(), NOT a plain tuple comparison:
        # a None -> value client_order_id change alone is enrichment, not
        # a conflict (see that function's docstring).
        if _immutable_fields_conflict(group):
            differing = sorted({
                name for name, a, b in _field_diffs(group)
            })
            findings.append(IngestionFinding(
                finding_type=SOURCE_IDENTITY_CONFLICT,
                stable_id=stable_id,
                conflicting_records=list(group),
                detail=(
                    "Records sharing one stable identity disagree on immutable "
                    f"field(s): {differing}. These cannot be the same real order; "
                    "the disagreement is reported rather than resolved."
                ),
            ))
            rep = _deterministic_representative(group)
            _enrich_representative(rep, group)
            rep.ingestion_count = len(group)
            normalized.append(rep)
            continue

        statuses = {o.status for o in group}

        # --- Behavior 3b: mutually exclusive terminal statuses ------------
        terminal_present = statuses & TERMINAL_STATUSES
        if len(terminal_present) > 1:
            findings.append(IngestionFinding(
                finding_type=SOURCE_IDENTITY_CONFLICT,
                stable_id=stable_id,
                conflicting_records=list(group),
                detail=(
                    "Records sharing one stable identity report mutually exclusive "
                    f"terminal statuses: {sorted(terminal_present)}."
                ),
            ))
            rep = _deterministic_representative(group)
            _enrich_representative(rep, group)
            rep.ingestion_count = len(group)
            normalized.append(rep)
            continue

        # --- Unmapped status guard --------------------------------------
        # STATUS_PRECEDENCE.get(o.status, 0) defaults anything it doesn't
        # recognize to rank 0 -- the SAME rank as "pending_new"/"new". Left
        # unchecked, that means an unrecognized status silently loses to
        # ANY known status in the max() below, or silently WINS over
        # another unrecognized status via the arbitrary "o.status" string
        # tiebreak, in both cases with no signal that the ordering was
        # ever untrustworthy. Only matters when there's more than one
        # DISTINCT status in the group -- a group that's unanimous on one
        # unmapped status has nothing to order, and behavior 2 handles it
        # as a harmless no-op tie below.
        unmapped_statuses = {s for s in statuses if s not in STATUS_PRECEDENCE}
        if len(statuses) > 1 and unmapped_statuses:
            findings.append(IngestionFinding(
                finding_type=UNMAPPED_STATUS_UNRESOLVED,
                stable_id=stable_id,
                conflicting_records=list(group),
                detail=(
                    "Records sharing one stable identity report different statuses "
                    f"{sorted(statuses)}, and at least one "
                    f"({sorted(unmapped_statuses)}) is not in STATUS_PRECEDENCE -- "
                    "a forward-progression ordering cannot be trusted, so it is "
                    "reported rather than silently resolved."
                ),
            ))
            rep = _deterministic_representative(group)
            _enrich_representative(rep, group)
            rep.ingestion_count = len(group)
            normalized.append(rep)
            continue

        # --- Behavior 2: compatible lifecycle progression -----------------
        rep = max(
            group,
            key=lambda o: (STATUS_PRECEDENCE.get(o.status, 0), o.status),
        )
        _enrich_representative(rep, group)
        rep.ingestion_count = len(group)
        normalized.append(rep)

    return normalized, findings


def _field_diffs(group: list[BrokerOrder]):
    """Yields (field_name, value_a, value_b) for every immutable field where
    the group disagrees. Used only to make the finding's detail specific.

    client_order_id is reported here only for a genuine conflict (2+
    distinct non-null values) -- a None -> value enrichment is not a
    disagreement worth naming in the finding, consistent with
    _immutable_fields_conflict() not treating it as one."""
    names = ("symbol", "side", "qty", "submitted_at", "source_system",
             "source_account", "broker_order_id", "immutable_source_record_id")
    base = _immutable_fields(group[0])[:-1]
    for o in group[1:]:
        for name, a, b in zip(names, base, _immutable_fields(o)[:-1]):
            if a != b:
                yield (name, a, b)
    non_null_cids = sorted({o.client_order_id for o in group if o.client_order_id is not None})
    if len(non_null_cids) > 1:
        yield ("client_order_id", non_null_cids[0], non_null_cids[1])


def _deterministic_representative(group: list[BrokerOrder]) -> BrokerOrder:
    """
    Picks one record to pass downstream when the group is contradictory.
    Chosen by a total order over content (never by list position), so the
    choice cannot vary with input permutation. The unchosen records are not
    discarded silently -- they are attached to the finding.
    """
    return min(group, key=lambda o: (
        -STATUS_PRECEDENCE.get(o.status, 0),
        o.submitted_at,
        o.status,
        str(o.filled_at),
        str(o.client_order_id),
        str(_immutable_fields(o)),
    ))
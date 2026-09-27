"""
v12 test suite.

Inherits the v11 suite in full and closes the four gaps found in review of
the delivered v11 package (see ACCEPTANCE_REPORT_V12.md):

  - permutation properties now compare DISPOSITION, not just findings
  - finding signatures preserve multiplicity (duplicate findings visible)
  - duplicate/contradictory source identifiers have a defined contract
    (ingestion_normalization.py) instead of being excluded by the fuzzer
  - the mid-file __main__ block that could only see tests defined above it
    was replaced with an auto-discovering runner at the end of the file;
    that block is what made it possible to run "the suite" while silently
    skipping most of it

Original v11 header follows.

v11 test suite. Closes six gaps identified in review of v10:

  1. Deterministic stable_id (source identity), not a random UUID.
  2. Full claim->order assignment-relation comparison against the reference
     model, not just per-order category agreement.
  3. A REAL findings-independence test: stages called directly, disposition
     snapshotted and deep-copied before classify_results runs, asserted
     unchanged afterward -- not "run reconcile() twice and diff."
  4. (Handled by run_mutation_suite.py, not this file) a reproducible,
     machine-readable mutation harness.
  5. Explicit, pinned Hypothesis reproducibility (derandomize + settings
     profile), plus systematic (not single-random-shuffle) permutation
     checks for small cases.
  6. IGNORED_BY_POLICY removed from the engine entirely (see reconcile_v12.py).
"""
from __future__ import annotations

import copy
import csv
import dataclasses
import io
import itertools
import json
import random
from datetime import datetime, timedelta

from hypothesis import given, strategies as st, settings, HealthCheck, seed

from reconcile_v12 import (
    InternalClaim, BrokerOrder, Interpretation, FailureType, ReasonCode,
    Result, OrderDisposition, assign_stable_ids, assign_claim_stable_ids,
    normalize_inputs, build_evidence_graph, resolve_assignments,
)
from reconcile_v12_classify import (
    reconcile, reconcile_with_disposition, classify_results, summarize_incidents,
)
from reference_model_v12 import compute_reference_outcome
from output_validator_v12 import (
    validate_disposition_shape, validate_findings_reference_valid_records,
    normalized_finding_signature, disposition_totals, normalized_dispositions,
    normalized_assignment_relation,
)
from ingestion_normalization import (
    normalize_ingested_orders, IngestionFinding, SOURCE_IDENTITY_CONFLICT,
    UNMAPPED_STATUS_UNRESOLVED,
)
from regression_fixtures_v12 import (
    FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY,
    FIXTURE_2_CONSISTENT_ORDER_DESPITE_OTHER_AMBIGUITY,
)

T = datetime(2026, 9, 8, 9, 30, 0)

# --- Explicit, pinned reproducibility (gap 5) ---------------------------
# derandomize=True makes Hypothesis deterministic across runs without
# needing the session-local example database -- the SAME sequence of
# examples is generated every time this file is executed, anywhere.
settings.register_profile(
    "v12_pinned",
    max_examples=3000,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile("v12_pinned")


def _mk_claim(rid, outcome="COMMITTED", interp=Interpretation.SUCCESS, bid=None, cid=None,
              ft=FailureType.NONE, t=T, symbol="TQQQ", side="buy", qty=7, stage="base",
              source_system=None, source_account=None, event_id=None):
    return InternalClaim(rid, symbol, side, qty, stage, t, outcome, interp,
                          broker_order_id=bid, client_order_id=cid, failure_type=ft,
                          source_system=source_system, source_account=source_account,
                          immutable_source_event_id=event_id)


def _mk_order(bid=None, cid=None, status="filled", t=T, symbol="TQQQ", side="buy", qty=7,
              source_system="ALPACA", source_account="ACC1", rid=None):
    return BrokerOrder(symbol, side, qty, status, t, t if status in ("filled", "partially_filled") else None,
                        broker_order_id=bid, client_order_id=cid,
                        source_system=source_system, source_account=source_account,
                        immutable_source_record_id=rid)


# ===================== Gap 1: deterministic stable identity =====================

def test_id01_same_record_imported_twice_same_stable_id():
    o1 = _mk_order(bid="B1")
    o2 = _mk_order(bid="B1")  # separately constructed, same business fields
    assign_stable_ids([o1])
    assign_stable_ids([o2])
    assert o1.stable_id == o2.stable_id, "Same real order re-imported must get the same stable_id"
    print("ID01 same record imported twice -> same stable_id: PASS")


def test_id02_different_accounts_same_broker_id_stay_distinct():
    o1 = _mk_order(bid="B1", source_account="ACC1")
    o2 = _mk_order(bid="B1", source_account="ACC2")
    assign_stable_ids([o1, o2])
    assert o1.stable_id != o2.stable_id, "Same broker_order_id under different accounts must NOT collide"
    print("ID02 different accounts, same broker ID -> distinct: PASS")


def test_id03_identical_id_less_records_retain_multiplicity():
    o1 = _mk_order()  # no broker_order_id, no immutable_source_record_id
    o2 = _mk_order()  # identical in every field
    orders = [o1, o2]
    assign_stable_ids(orders)
    assert o1.stable_id != o2.stable_id, "Two genuinely distinct ID-less records must not collapse to one identity"
    assert o1.stable_id_basis == o2.stable_id_basis == "fingerprint"
    print("ID03 identical ID-less records retain multiplicity: PASS")


def test_id04_permutation_and_reinstantiation_preserve_results():
    """The v10 gap named explicitly: shuffling the SAME Python objects
    doesn't test re-ingestion. This constructs completely FRESH objects
    with identical field values and confirms the reconciliation OUTCOME
    (not necessarily the literal stable_id string for degenerate
    all-identical-field cases) is unchanged."""
    def build():
        return (
            [_mk_claim("c1", bid="X1", cid="Y1")],
            [_mk_order(bid="X1", cid="Y1"), _mk_order(bid="OTHER")],
        )

    claims_a, orders_a = build()
    results_a, disp_a = reconcile_with_disposition(claims_a, orders_a, now=T + timedelta(hours=1))

    claims_b, orders_b = build()  # completely fresh objects, same field values
    random.shuffle(orders_b)
    results_b, disp_b = reconcile_with_disposition(claims_b, orders_b, now=T + timedelta(hours=1))

    sig_a = {(r.claim.request_id if r.claim else None, r.result.value) for r in results_a}
    sig_b = {(r.claim.request_id if r.claim else None, r.result.value) for r in results_b}
    assert sig_a == sig_b, "Complete re-instantiation + permutation changed the outcome"
    print("ID04 permutation + complete re-instantiation preserve results: PASS")


def test_id05_stable_id_survives_json_csv_roundtrip():
    o = _mk_order(bid="B1")
    assign_stable_ids([o])
    original_id = o.stable_id

    # JSON round trip
    as_dict = {
        "symbol": o.symbol, "side": o.side, "qty": o.qty, "status": o.status,
        "submitted_at": o.submitted_at.isoformat(),
        "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        "broker_order_id": o.broker_order_id, "client_order_id": o.client_order_id,
        "source_system": o.source_system, "source_account": o.source_account,
        "immutable_source_record_id": o.immutable_source_record_id,
    }
    json_str = json.dumps(as_dict)
    reloaded = json.loads(json_str)
    rebuilt = BrokerOrder(
        symbol=reloaded["symbol"], side=reloaded["side"], qty=reloaded["qty"], status=reloaded["status"],
        submitted_at=datetime.fromisoformat(reloaded["submitted_at"]),
        filled_at=datetime.fromisoformat(reloaded["filled_at"]) if reloaded["filled_at"] else None,
        broker_order_id=reloaded["broker_order_id"], client_order_id=reloaded["client_order_id"],
        source_system=reloaded["source_system"], source_account=reloaded["source_account"],
        immutable_source_record_id=reloaded["immutable_source_record_id"],
    )
    assign_stable_ids([rebuilt])
    assert rebuilt.stable_id == original_id, "stable_id did not survive a JSON round trip"

    # CSV round trip
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(as_dict.keys()))
    writer.writeheader()
    writer.writerow(as_dict)
    buf.seek(0)
    csv_row = next(csv.DictReader(buf))
    rebuilt_csv = BrokerOrder(
        symbol=csv_row["symbol"], side=csv_row["side"], qty=float(csv_row["qty"]), status=csv_row["status"],
        submitted_at=datetime.fromisoformat(csv_row["submitted_at"]),
        filled_at=datetime.fromisoformat(csv_row["filled_at"]) if csv_row["filled_at"] else None,
        broker_order_id=csv_row["broker_order_id"] or None, client_order_id=csv_row["client_order_id"] or None,
        source_system=csv_row["source_system"] or None, source_account=csv_row["source_account"] or None,
        immutable_source_record_id=csv_row["immutable_source_record_id"] or None,
    )
    assign_stable_ids([rebuilt_csv])
    assert rebuilt_csv.stable_id == original_id, "stable_id did not survive a CSV round trip"
    print("ID05 stable_id survives JSON/CSV round trips: PASS")


# ===================== v12.1 item 1: claim stable identity =====================

def test_claimid01_distinct_claims_sharing_request_id_do_not_collapse():
    """D26 documents that two DISTINCT claims may share a request_id. Their
    stable_ids -- computed from genuine content, never request_id -- must
    still differ, and the reconciliation relation must show two separate
    entries, not one collapsed by a request_id-keyed comparison."""
    claims = [_mk_claim("same-id", bid="P", cid="p1"),
              _mk_claim("same-id", bid="Q", cid="q1", t=T + timedelta(seconds=1))]
    orders = [_mk_order(bid="P", cid="p1"), _mk_order(bid="Q", cid="q1", t=T + timedelta(seconds=1))]
    assign_claim_stable_ids(claims)
    assert claims[0].stable_id != claims[1].stable_id, (
        "Two distinct claims sharing a request_id must not share a stable_id"
    )
    results = reconcile(claims, orders, now=T + timedelta(hours=1))
    claim_results = [r for r in results if r.claim is not None]
    assert len(claim_results) == 2, "Both claims must produce their own finding, not one collapsed"
    relation = normalized_assignment_relation(claim_results)
    assert len(relation) == 2, "The relation must show two distinct (claim, order) pairs"
    print("CLAIMID01 distinct claims sharing request_id do not collapse: PASS")


def test_claimid02_repeated_ingestion_of_same_claim_retains_identity():
    """The same real claim, re-ingested as a completely fresh object with
    identical source identity, must get the same stable_id -- the claim
    analogue of ID01 for orders."""
    def build():
        return _mk_claim("r1", bid="B1", cid="C1", source_system="SAFEAGENT",
                          source_account="ACC1", event_id="EVT-100")
    c1, c2 = build(), build()
    assign_claim_stable_ids([c1])
    assign_claim_stable_ids([c2])
    assert c1.stable_id == c2.stable_id, "Same real claim re-ingested must get the same stable_id"
    assert c1.stable_id_basis == c2.stable_id_basis == "immutable_source_event_id"
    print("CLAIMID02 repeated ingestion of same claim retains identity: PASS")


def test_claimid03_identical_id_less_claims_preserve_multiplicity():
    """Two claims with no immutable_source_event_id, identical in every
    other field, must NOT collapse to one identity -- the claim analogue
    of ID03 for orders. They are genuinely indistinguishable content-wise,
    which is exactly why multiplicity (not collapse) is correct: two real,
    separate claims can legitimately look identical."""
    c1 = _mk_claim("r1")
    c2 = _mk_claim("r1")  # separately constructed, identical fields
    claims = [c1, c2]
    assign_claim_stable_ids(claims)
    assert c1.stable_id != c2.stable_id, "Two genuinely distinct ID-less claims must not collapse to one identity"
    assert c1.stable_id_basis == c2.stable_id_basis == "fingerprint"
    print("CLAIMID03 identical ID-less claims preserve multiplicity: PASS")


def test_claimid04_shared_request_id_swap_detected_via_stable_id_not_request_id():
    """
    Direct, deterministic proof of the vulnerability the fix closes and of
    the fix itself. Two claims share a request_id but have genuinely
    different identities (different broker_order_id/client_order_id).

    A relation keyed by (request_id, order_stable_id) CANNOT tell a
    genuine assignment apart from a SWAPPED one here: the set of pairs is
    {(rid, o1), (rid, o2)} either way, since a set forgets which claim
    object contributed which pair. Keying by claim.stable_id instead
    distinguishes them, because the two claims have different genuine
    identities.
    """
    claims = [_mk_claim("shared", bid="P", cid="p1"),
              _mk_claim("shared", bid="Q", cid="q1", t=T + timedelta(seconds=1))]
    orders = [_mk_order(bid="P", cid="p1"), _mk_order(bid="Q", cid="q1", t=T + timedelta(seconds=1))]
    results = reconcile(claims, orders, now=T + timedelta(hours=1))
    claim_results = [r for r in results if r.claim is not None]
    assert len(claim_results) == 2

    request_id_relation = {(r.claim.request_id, r.matched_broker_order.stable_id) for r in claim_results}
    swapped = [
        dataclasses.replace(x, matched_broker_order=claim_results[1 - i].matched_broker_order)
        for i, x in enumerate(claim_results)
    ]
    swapped_request_id_relation = {(r.claim.request_id, r.matched_broker_order.stable_id) for r in swapped}
    assert request_id_relation == swapped_request_id_relation, (
        "This IS the vulnerability: a genuine assignment swap must be invisible under a request_id key "
        "for this test to be proving what it claims -- if this assertion fails, the premise changed"
    )

    stable_id_relation = normalized_assignment_relation(claim_results)
    swapped_stable_id_relation = normalized_assignment_relation(swapped)
    assert stable_id_relation != swapped_stable_id_relation, (
        "Swapping assignments between claims sharing a request_id must be detectable via stable_id"
    )
    print("CLAIMID04 shared-request-id swap detected via stable_id, invisible via request_id: PASS")


def test_claimid05_stable_id_survives_json_csv_roundtrip():
    c = _mk_claim("r1", bid="B1", cid="C1", source_system="SAFEAGENT",
                  source_account="ACC1", event_id="EVT-200")
    assign_claim_stable_ids([c])
    original_id = c.stable_id

    as_dict = {
        "request_id": c.request_id, "symbol": c.symbol, "side": c.side, "qty": c.qty,
        "stage": c.stage, "claimed_at": c.claimed_at.isoformat(), "outcome": c.outcome,
        "caller_interpretation": c.caller_interpretation.value,
        "broker_order_id": c.broker_order_id, "client_order_id": c.client_order_id,
        "failure_type": c.failure_type.value,
        "source_system": c.source_system, "source_account": c.source_account,
        "immutable_source_event_id": c.immutable_source_event_id,
    }

    json_str = json.dumps(as_dict)
    reloaded = json.loads(json_str)
    rebuilt = InternalClaim(
        request_id=reloaded["request_id"], symbol=reloaded["symbol"], side=reloaded["side"],
        qty=reloaded["qty"], stage=reloaded["stage"],
        claimed_at=datetime.fromisoformat(reloaded["claimed_at"]), outcome=reloaded["outcome"],
        caller_interpretation=Interpretation(reloaded["caller_interpretation"]),
        broker_order_id=reloaded["broker_order_id"], client_order_id=reloaded["client_order_id"],
        failure_type=FailureType(reloaded["failure_type"]),
        source_system=reloaded["source_system"], source_account=reloaded["source_account"],
        immutable_source_event_id=reloaded["immutable_source_event_id"],
    )
    assign_claim_stable_ids([rebuilt])
    assert rebuilt.stable_id == original_id, "claim stable_id did not survive a JSON round trip"

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(as_dict.keys()))
    writer.writeheader()
    writer.writerow(as_dict)
    buf.seek(0)
    csv_row = next(csv.DictReader(buf))
    rebuilt_csv = InternalClaim(
        request_id=csv_row["request_id"], symbol=csv_row["symbol"], side=csv_row["side"],
        qty=float(csv_row["qty"]), stage=csv_row["stage"],
        claimed_at=datetime.fromisoformat(csv_row["claimed_at"]), outcome=csv_row["outcome"],
        caller_interpretation=Interpretation(csv_row["caller_interpretation"]),
        broker_order_id=csv_row["broker_order_id"] or None, client_order_id=csv_row["client_order_id"] or None,
        failure_type=FailureType(csv_row["failure_type"]),
        source_system=csv_row["source_system"] or None, source_account=csv_row["source_account"] or None,
        immutable_source_event_id=csv_row["immutable_source_event_id"] or None,
    )
    assign_claim_stable_ids([rebuilt_csv])
    assert rebuilt_csv.stable_id == original_id, "claim stable_id did not survive a CSV round trip"
    print("CLAIMID05 claim stable_id survives JSON/CSV round trips: PASS")


# ===================== Gap 2: full assignment-relation comparison =====================

def _full_check(claims, orders, now=None):
    now = now or (T + timedelta(hours=1))
    results, disposition = reconcile_with_disposition(claims, orders, now)
    validate_disposition_shape(orders, disposition)
    validate_findings_reference_valid_records(claims, orders, results)

    ref = compute_reference_outcome(claims, orders)

    for i in ref.genuinely_unmatched_orders:
        assert disposition[i] == OrderDisposition.UNMATCHED, (
            f"Reference: order {i} unmatched; engine: {disposition[i]}"
        )
    for i in ref.varying_orders:
        assert disposition[i] == OrderDisposition.AMBIGUOUS, (
            f"Reference: order {i} ambiguous; engine: {disposition[i]}"
        )
    conflict_orders = {o for lst in ref.conflict_claims.values() for o in lst}
    for i in conflict_orders:
        acceptable = (OrderDisposition.IDENTIFIER_CONFLICT, OrderDisposition.ASSIGNED)
        if i in ref.varying_orders:
            acceptable = acceptable + (OrderDisposition.AMBIGUOUS,)
        assert disposition[i] in acceptable, f"Reference: order {i} conflicting; engine: {disposition[i]}"
    for claim_idx, order_idx in ref.contradiction_claims.items():
        assert any(r.claim is claims[claim_idx] and r.result == Result.CONTRADICTION for r in results), (
            f"Reference: claim {claim_idx} contradicts order {order_idx}; no CONTRADICTION finding"
        )

    # --- Gap 2: the actual pairwise relation, not just per-order category ---
    # Two engines could agree every order is ASSIGNED while assigning
    # completely different claims to them. Compare the real pairs, keyed by
    # STABLE IDENTITY on both sides (v12.1: was request_id, which cannot
    # always tell two distinct claims apart -- see
    # normalized_assignment_relation()'s docstring).
    engine_pairs = normalized_assignment_relation(results)
    reference_pairs = frozenset(
        (claims[c].stable_id, orders[o].stable_id)
        for c, o in ref.forced_assignments.items()
    )
    assert engine_pairs == reference_pairs, (
        f"Assignment relation mismatch.\n  engine:    {engine_pairs}\n  reference: {reference_pairs}"
    )

    return results, disposition, ref


# ===================== Gap 3: real findings-independence test =====================

def test_findings_do_not_alter_disposition_direct_stage_check():
    """Calls the four stages directly, per the exact requested shape --
    not 'run reconcile() twice and diff', which only proves determinism."""
    claims = FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY["claims"]
    orders = [copy.deepcopy(o) for o in FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY["orders"]]
    now = T + timedelta(hours=1)

    assign_stable_ids(orders)
    eligibility = normalize_inputs(claims, orders)
    edges = build_evidence_graph(claims, orders, eligibility)
    assignments, disposition = resolve_assignments(edges, len(claims), len(orders))

    original_disposition_snapshot = copy.deepcopy(disposition)
    original_totals = disposition_totals(disposition)

    findings = classify_results(claims, orders, eligibility, edges, assignments, disposition, now)
    assert disposition == original_disposition_snapshot, (
        "classify_results mutated the disposition dict it was given"
    )

    # Append, remove, reorder, and duplicate findings -- disposition_totals
    # on the SAME disposition object must be completely unaffected, since
    # findings are a separate list with no back-reference into disposition.
    mutated_findings = list(findings)
    mutated_findings.append(mutated_findings[0])          # duplicate
    mutated_findings.pop(0)                                 # remove
    mutated_findings.reverse()                              # reorder
    mutated_findings.extend(mutated_findings[:2])           # append more
    assert disposition_totals(disposition) == original_totals, (
        "Mutating the findings list changed disposition_totals() -- "
        "findings must have zero effect on accounting"
    )
    print("Findings-independence (direct stage check): PASS")


# ===================== Gap 5: systematic permutation, not one shuffle =====================

def test_systematic_permutation_small_case():
    """
    Exhaustively enumerates every permutation of claims AND orders for a
    small, real (fixture-derived) case -- not a single random.shuffle()
    per generated example.

    v12.1 fix: this used to compute `d` (disposition) on every iteration
    and never assert anything about it -- literally calculated and
    discarded, both for the base case and every permutation. It now
    establishes a base normalized disposition AND a base normalized
    claim->order relation up front, and asserts both stay identical to
    the base on every single one of the 144 permutations, in addition to
    the finding signature that was already checked.
    """
    claims = FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY["claims"]
    orders = FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY["orders"]
    now = T + timedelta(hours=1)

    base_orders = [copy.deepcopy(o) for o in orders]
    base_results, base_disp = reconcile_with_disposition(list(claims), base_orders, now)
    base_sig = normalized_finding_signature(base_results)
    base_dispositions = normalized_dispositions(base_disp, base_orders)
    base_relation = normalized_assignment_relation(base_results)

    checked = 0
    for claim_perm in itertools.permutations(claims):
        for order_perm in itertools.permutations(orders):
            fresh_orders = [copy.deepcopy(o) for o in order_perm]
            r, d = reconcile_with_disposition(list(claim_perm), fresh_orders, now)
            assert normalized_dispositions(d, fresh_orders) == base_dispositions, (
                f"Permutation {checked} changed disposition"
            )
            assert normalized_assignment_relation(r) == base_relation, (
                f"Permutation {checked} changed the claim->order relation"
            )
            sig = normalized_finding_signature(r)
            assert sig == base_sig, f"Permutation changed findings: {checked}th permutation"
            checked += 1
    print(f"Systematic permutation check: {checked} full (claim x order) permutations, "
          f"disposition + relation + findings all identical -- PASS")


# ===================== Deterministic + fixture tests (as v10, source_system added) =====================

def test_d01():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="x1")])
    assert any(x.result == Result.CONFIRMED for x in r if x.claim)
    print("D01: PASS")

def test_d09_non_trivial_global_assignment():
    c = [_mk_claim("c1", bid="shared", cid="x1"), _mk_claim("c2", bid="shared", cid="x2", t=T+timedelta(seconds=1)),
         _mk_claim("c3", bid="shared", cid="x3", t=T+timedelta(seconds=2))]
    o = [_mk_order(bid="shared", cid="x1"), _mk_order(bid="shared", cid="x2", t=T+timedelta(seconds=1)),
         _mk_order(bid="shared", cid="x3", t=T+timedelta(seconds=2))]
    r, d, ref = _full_check(c, o)
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert all(by_id[k].result == Result.CONFIRMED for k in ("c1", "c2", "c3"))
    print("D09 non-trivial global assignment: PASS")

def test_d12_rejected_vs_committed_for_filled_order():
    c = [_mk_claim("c1", bid="b1", cid="x1"), _mk_claim("c2", bid="b1", cid="x1", ft=FailureType.BROKER_REJECTED, t=T+timedelta(seconds=1))]
    r, d, ref = _full_check(c, [_mk_order(bid="b1", cid="x1", status="filled")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["c1"].result == Result.CONFIRMED
    print("D12: PASS")

def test_d14_contradiction():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", ft=FailureType.BROKER_REJECTED)], [_mk_order(bid="b1", status="filled")])
    assert any(x.result == Result.CONTRADICTION for x in r)
    print("D14: PASS")

def test_fixture_1():
    f = FIXTURE_1_REJECTED_VS_UNRELATED_AMBIGUITY
    results, disposition = reconcile_with_disposition(f["claims"], [copy.deepcopy(o) for o in f["orders"]], now=T+timedelta(hours=1))
    validate_disposition_shape(f["orders"], disposition)
    assert disposition[3] == OrderDisposition.AMBIGUOUS
    assert any(r.result == Result.CONTRADICTION for r in results)
    print("Fixture 1: PASS")

def test_fixture_2():
    f = FIXTURE_2_CONSISTENT_ORDER_DESPITE_OTHER_AMBIGUITY
    results, disposition = reconcile_with_disposition(f["claims"], [copy.deepcopy(o) for o in f["orders"]], now=T+timedelta(hours=1))
    validate_disposition_shape(f["orders"], disposition)
    assert disposition[3] == OrderDisposition.ASSIGNED
    print("Fixture 2: PASS")


# ===================== Property tests, pinned reproducibility =====================

_ids = st.one_of(st.none(), st.sampled_from(["A", "B", "C"]))
_outcomes = st.sampled_from(["COMMITTED", "SKIP", "ERROR", "PENDING"])
_interps = st.sampled_from(list(Interpretation))
_statuses = st.sampled_from(["filled", "rejected", "canceled", "pending_new", "partially_filled", "unknown"])


@st.composite
def _full_random_case(draw):
    n_claims = draw(st.integers(min_value=0, max_value=4))
    n_orders = draw(st.integers(min_value=0, max_value=4))
    claims = []
    for i in range(n_claims):
        outcome = draw(_outcomes)
        ft = draw(st.sampled_from([FailureType.NONE, FailureType.BROKER_REJECTED])) if outcome == "ERROR" else FailureType.NONE
        claims.append(InternalClaim(f"claim-{i}", "TQQQ", "buy", 3, "base", T, outcome, draw(_interps),
                                     broker_order_id=draw(_ids), client_order_id=draw(_ids), failure_type=ft))
    orders = []
    # This generator produces POST-NORMALIZATION input: one record per
    # stable identity, which is what reconcile() contractually requires.
    #
    # v11 justified the same constraint by calling duplicate broker IDs
    # unrealistic. That was wrong -- duplicated and contradictory rows are
    # entirely realistic in imported data (repeated exports, overlapping
    # pagination, corrupted rows), they just are not reconcile()'s job. The
    # constraint stays, but it now rests on a real code path rather than on
    # declaring the input impossible: ingestion_normalization.py guarantees
    # uniqueness, and it is itself fuzzed WITH duplicates by
    # _raw_ingest_case() below.
    for i in range(n_orders):
        status = draw(_statuses)
        orders.append(BrokerOrder("TQQQ", "buy", 3, status, T, T if status in ("filled", "partially_filled") else None,
                                   broker_order_id=draw(_ids), client_order_id=draw(_ids),
                                   source_system="ALPACA", source_account="ACC1"))
    # Duplicates ARE generated freely above, then resolved by the real
    # contract rather than by a retry loop. v11 tried to enforce uniqueness
    # with a bounded "draw again up to 5 times" hack, which was both a rig
    # AND leaky -- it could still emit colliding identifiers, and the
    # dispositions those cases produced were never compared to anything.
    orders, _ingestion_findings = normalize_ingested_orders(orders)
    return claims, orders


@seed(20260908)
@given(_full_random_case())
def test_prop_disposition_shape_holds(data):
    claims, orders = data
    _, disposition = reconcile_with_disposition(claims, orders, now=T+timedelta(hours=1))
    validate_disposition_shape(orders, disposition)


@seed(20260908)
@given(_full_random_case())
def test_prop_engine_agrees_with_reference_model_full_relation(data):
    claims, orders = data
    _full_check(claims, orders)


@seed(20260908)
@given(_full_random_case())
def test_prop_claim_permutation_invariance(data):
    claims, orders = data
    if not claims:
        return
    orders_a = [copy.deepcopy(o) for o in orders]
    r1, d1 = reconcile_with_disposition(claims, orders_a, now=T+timedelta(hours=1))
    shuffled = claims[:]
    random.shuffle(shuffled)
    orders_b = [copy.deepcopy(o) for o in orders]
    r2, d2 = reconcile_with_disposition(shuffled, orders_b, now=T+timedelta(hours=1))
    # v11 computed d1/d2 here and never compared them, so the accounting
    # layer was unverified under claim permutation. Both must hold.
    assert normalized_dispositions(d1, orders_a) == normalized_dispositions(d2, orders_b), (
        "Claim permutation changed order disposition"
    )
    assert normalized_finding_signature(r1) == normalized_finding_signature(r2), (
        "Claim permutation changed the findings multiset"
    )


@seed(20260908)
@given(_full_random_case())
def test_prop_order_permutation_invariance(data):
    claims, orders = data
    if not orders:
        return
    orders_a = [copy.deepcopy(o) for o in orders]
    r1, d1 = reconcile_with_disposition(claims, orders_a, now=T+timedelta(hours=1))
    shuffled = orders[:]
    random.shuffle(shuffled)
    orders_b = [copy.deepcopy(o) for o in shuffled]
    r2, d2 = reconcile_with_disposition(claims, orders_b, now=T+timedelta(hours=1))
    # normalized_dispositions keys by stable_id, which is content-derived and
    # permutation-independent, so this comparison is meaningful even though
    # orders_a and orders_b are different objects in a different order.
    assert normalized_dispositions(d1, orders_a) == normalized_dispositions(d2, orders_b), (
        "Order permutation changed order disposition"
    )
    assert normalized_finding_signature(r1) == normalized_finding_signature(r2), (
        "Order permutation changed the findings multiset"
    )


# ===================== Remaining deterministic tests D02-D08, D10-D11, D13, D15-D30 (restored) =====================

def test_d02():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")],
                             [_mk_order(bid="b1", cid="x1"), _mk_order(bid="b1", cid="x1", t=T+timedelta(minutes=1))])
    assert not ref.is_unique
    print("D02: PASS")

def test_d03():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1"), _mk_claim("c2", bid="b1", cid="x1", t=T+timedelta(seconds=1))],
                             [_mk_order(bid="b1", cid="x1")])
    assert all(x.result != Result.CONFIRMED for x in r if x.claim)
    print("D03: PASS")

def test_d04():
    r, d, ref = _full_check([_mk_claim("exact", bid="b1", cid="x1"), _mk_claim("partial", bid="b1", t=T+timedelta(seconds=1))],
                             [_mk_order(bid="b1", cid="x1")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["exact"].result == Result.CONFIRMED
    print("D04: PASS")

def test_d05():
    r, d, ref = _full_check([_mk_claim("exact", bid="b1", cid="x1"), _mk_claim("weak")], [_mk_order(bid="b1", cid="x1")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["exact"].result == Result.CONFIRMED
    assert by_id["weak"].result != Result.CONFIRMED
    print("D05: PASS")

def test_d06():
    r, d, ref = _full_check([_mk_claim("partial", bid="b1"), _mk_claim("weak")], [_mk_order(bid="b1", cid="x1")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["partial"].result == Result.CONFIRMED
    print("D06: PASS")

def test_d07():
    r, d, ref = _full_check([_mk_claim("c1"), _mk_claim("c2", t=T+timedelta(seconds=1))], [_mk_order()])
    assert all(x.result != Result.CONFIRMED for x in r if x.claim)
    print("D07: PASS")

def test_d08():
    r, d, ref = _full_check([_mk_claim("c1")], [_mk_order(), _mk_order(t=T+timedelta(minutes=1))])
    assert r[0].result != Result.CONFIRMED
    print("D08: PASS")

def test_d10():
    c = [_mk_claim("c1", bid="b1", cid="x1"), _mk_claim("c1-retry", outcome="SKIP", interp=Interpretation.SKIPPED, bid="b1", cid="x1", t=T+timedelta(seconds=1))]
    r, d, ref = _full_check(c, [_mk_order(bid="b1", cid="x1")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["c1"].result == Result.CONFIRMED
    assert by_id["c1-retry"].result == Result.DUPLICATE_BLOCKED
    print("D10: PASS")

def test_d11():
    c = [_mk_claim("c1", bid="b1", cid="x1"), _mk_claim("c1-retry", outcome="SKIP", interp=Interpretation.SUCCESS, bid="b1", cid="x1", t=T+timedelta(seconds=1))]
    r, d, ref = _full_check(c, [_mk_order(bid="b1", cid="x1")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["c1-retry"].result == Result.MISSING_EXTERNALLY
    assert ReasonCode.CALLER_IGNORED_SKIP in by_id["c1-retry"].reason_codes
    print("D11: PASS")

def test_d13():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", ft=FailureType.BROKER_REJECTED)], [_mk_order(bid="b1", status="rejected")])
    assert r[0].result == Result.REJECTED
    print("D13: PASS")

def test_d15():
    c = [_mk_claim("c1", bid="b1", cid="x1"), _mk_claim("c2", bid="b1", cid="x1", t=T+timedelta(seconds=1)), _mk_claim("c3")]
    r, d, ref = _full_check(c, [_mk_order(bid="b1", cid="x1")])
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["c3"].result != Result.CONFIRMED
    print("D15: PASS")

def test_d16():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="x1"), _mk_order(bid="b1", cid="WRONG")])
    claim_result = next(x for x in r if x.claim)
    assert claim_result.result == Result.CONFIRMED
    print("D16: PASS")

def test_d17():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1")], [_mk_order(bid="b1", cid="WRONG")])
    assert not any(x.result == Result.UNRECORDED_INTERNALLY for x in r)
    print("D17: PASS")

def test_d18():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="x1"), _mk_order(bid="b1", cid="x1", t=T+timedelta(minutes=1))])
    assert not any(x.result == Result.UNRECORDED_INTERNALLY for x in r)
    print("D18: PASS")

def test_d19():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="x1"), _mk_order(bid="unrelated", t=T+timedelta(hours=2))])
    assert sum(1 for x in r if x.result == Result.UNRECORDED_INTERNALLY) == 1
    print("D19: PASS")

def test_d20():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="x1"), _mk_order(bid="b1", cid="WRONG")])
    inc = summarize_incidents(r)
    assert sum(inc.values()) == 1 and inc["OPEN"] == 1
    print("D20: PASS")

def test_d21():
    c = [_mk_claim("aug18", symbol="TQQQ", side="sell", qty=3, stage="exit", outcome="ERROR", interp=Interpretation.FAILED,
                    ft=FailureType.BROKER_REJECTED, t=datetime(2026, 8, 18, 9, 34, 11))]
    r = reconcile(c, [], now=datetime(2026, 8, 18, 9, 36, 0))
    assert r[0].result == Result.REJECTED
    print("D21: PASS")

def test_d22():
    c = [InternalClaim("order:RV_QQQ_V23:TQQQ:buy:7:2026-09-03T09:39:00", "TQQQ", "buy", 7, "add2",
                        datetime(2026, 9, 3, 9, 40, 34), "SKIP", Interpretation.SUCCESS,
                        known_reason_codes=(ReasonCode.DEDUP_KEY_COLLISION,))]
    r = reconcile(c, [], now=datetime(2026, 9, 3, 10, 0, 0))
    assert ReasonCode.DEDUP_KEY_COLLISION in r[0].reason_codes and ReasonCode.CALLER_IGNORED_SKIP in r[0].reason_codes
    print("D22: PASS")

def test_d23():
    from reconcile_v12 import STALE_AFTER, OVERDUE_AFTER, Timeliness as TL
    c = [_mk_claim("c1", ft=FailureType.BROKER_REJECTED, t=T)]
    r_before = reconcile(c, [], now=T + STALE_AFTER - timedelta(seconds=1))[0]
    r_at = reconcile(c, [], now=T + STALE_AFTER)[0]
    r_overdue = reconcile(c, [], now=T + OVERDUE_AFTER)[0]
    assert r_before.timeliness == TL.CURRENT and r_at.timeliness == TL.STALE and r_overdue.timeliness == TL.OVERDUE
    assert r_before.result == r_at.result == r_overdue.result == Result.REJECTED
    print("D23: PASS")

def test_d24():
    order = _mk_order()
    r, d = reconcile_with_disposition([], [order], now=T)
    validate_disposition_shape([order], d)
    assert len(r) == 1 and r[0].result == Result.UNRECORDED_INTERNALLY
    print("D24: PASS")

def test_d25():
    r = reconcile([_mk_claim("c1")], [], now=T)
    assert len(r) == 1 and r[0].result != Result.CONFIRMED
    print("D25: PASS")

def test_d26():
    c = [_mk_claim("same-id", bid="b1", cid="x1"), _mk_claim("same-id", bid="b2", cid="x2", t=T+timedelta(seconds=1))]
    o = [_mk_order(bid="b1", cid="x1"), _mk_order(bid="b2", cid="x2", t=T+timedelta(seconds=1))]
    r = reconcile(c, o, now=T+timedelta(hours=1))
    assert len([x for x in r if x.claim]) == 2
    print("D26: PASS")

def test_d27():
    o1, o2 = _mk_order(bid="b1", cid="x1"), _mk_order(bid="b1", cid="x1", t=T+timedelta(minutes=1))
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [o1, o2])
    print("D27: PASS")

def test_d28():
    r = reconcile([_mk_claim("c1", bid="b1")], [_mk_order(cid="x1")], now=T+timedelta(hours=1))
    assert r[0].result != Result.CONFIRMED
    print("D28: PASS")

def test_d29():
    r, d, ref = _full_check([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="WRONG")])
    claim_result = next(x for x in r if x.claim)
    assert claim_result.result == Result.UNCERTAIN and ReasonCode.IDENTIFIER_CONFLICT in claim_result.reason_codes
    print("D29: PASS")

def test_d30():
    for status in ("rejected", "canceled", "pending_new", "partially_filled", "unknown_status"):
        r = reconcile([_mk_claim("c1", bid="b1", cid="x1")], [_mk_order(bid="b1", cid="x1", status=status)], now=T+timedelta(hours=1))
        cr = next(x for x in r if x.claim)
        if status == "partially_filled":
            assert cr.result == Result.CONFIRMED
        else:
            assert cr.result != Result.CONFIRMED
    print("D30: PASS")


def test_d31_assignment_swap_detected():
    """New, dedicated test for gap 2 / mutant 7: three claims that each
    have a UNIQUE full-identifier match to a DIFFERENT order. If an engine
    swapped which claim got which order while keeping every order's
    disposition ASSIGNED, per-order-category checks alone would miss it --
    only the full pairwise relation comparison in _full_check catches it."""
    c = [
        _mk_claim("c1", bid="P", cid="P"),
        _mk_claim("c2", bid="Q", cid="Q", t=T+timedelta(seconds=1)),
        _mk_claim("c3", bid="R", cid="R", t=T+timedelta(seconds=2)),
    ]
    o = [
        _mk_order(bid="P", cid="P"),
        _mk_order(bid="Q", cid="Q", t=T+timedelta(seconds=1)),
        _mk_order(bid="R", cid="R", t=T+timedelta(seconds=2)),
    ]
    r, d, ref = _full_check(c, o)  # _full_check's pairwise comparison must pass
    by_id = {x.claim.request_id: x for x in r if x.claim}
    assert by_id["c1"].matched_broker_order.broker_order_id == "P"
    assert by_id["c2"].matched_broker_order.broker_order_id == "Q"
    assert by_id["c3"].matched_broker_order.broker_order_id == "R"
    print("D31 assignment-swap detection: PASS")


# ============ Item 4: the ingestion normalization contract ============

def test_ing01_exact_duplicates_deduplicated_with_provenance():
    """Behavior 1: the same record repeated across exports / overlapping
    pagination / duplicate CSV rows collapses to one, and the count is kept."""
    raw = [_mk_order(bid="B1"), _mk_order(bid="B1"), _mk_order(bid="B1")]
    clean, findings = normalize_ingested_orders(raw)
    assert len(clean) == 1, "Three exact duplicates must collapse to one record"
    assert clean[0].ingestion_count == 3, "Duplication is collapsed but must not be lost"
    assert findings == [], "An exact duplicate is not a contradiction"
    print("ING01 exact duplicates deduplicated with provenance: PASS")


def test_ing02_compatible_lifecycle_change_consolidated():
    """Behavior 2: same identity, status genuinely progressed."""
    pending = _mk_order(bid="B2", status="pending_new")
    filled = _mk_order(bid="B2", status="filled")
    clean, findings = normalize_ingested_orders([pending, filled])
    assert len(clean) == 1, "A forward lifecycle progression consolidates to one record"
    assert clean[0].status == "filled", "Consolidated record must carry the most advanced status"
    assert clean[0].ingestion_count == 2
    assert findings == [], "A normal status update is not an anomaly"
    print("ING02 compatible lifecycle change consolidated: PASS")


def test_ing02b_consolidation_is_order_independent():
    """The consolidated result must not depend on which row arrived first."""
    a, _ = normalize_ingested_orders([_mk_order(bid="B2", status="pending_new"),
                                      _mk_order(bid="B2", status="filled")])
    b, _ = normalize_ingested_orders([_mk_order(bid="B2", status="filled"),
                                      _mk_order(bid="B2", status="pending_new")])
    assert a[0].status == b[0].status == "filled"
    print("ING02b consolidation is order-independent: PASS")


def test_ing03_contradictory_immutable_fields_produce_finding():
    """Behavior 3a: same identifier, different qty -- a real anomaly that
    must never be silently collapsed."""
    raw = [_mk_order(bid="B3", qty=7), _mk_order(bid="B3", qty=99)]
    clean, findings = normalize_ingested_orders(raw)
    assert len(findings) == 1
    assert findings[0].finding_type == SOURCE_IDENTITY_CONFLICT
    assert len(findings[0].conflicting_records) == 2, "Every conflicting record must be attached"
    assert "qty" in findings[0].detail
    assert len(clean) == 1, "One representative still passes through for matching"
    assert clean[0].ingestion_count == 2
    print("ING03 contradictory immutable fields produce a finding: PASS")


def test_ing04_contradictory_terminal_statuses_produce_finding():
    """Behavior 3b: same identifier reported both filled and rejected."""
    raw = [_mk_order(bid="B4", status="filled"), _mk_order(bid="B4", status="rejected")]
    clean, findings = normalize_ingested_orders(raw)
    assert len(findings) == 1
    assert findings[0].finding_type == SOURCE_IDENTITY_CONFLICT
    assert "filled" in findings[0].detail and "rejected" in findings[0].detail
    assert len(clean) == 1
    print("ING04 contradictory terminal statuses produce a finding: PASS")


def test_ing05_contradiction_representative_is_deterministic():
    """The representative passed downstream is chosen by content, never by
    list position -- otherwise the whole pipeline inherits an input-order
    dependence at its very first stage."""
    raw = [_mk_order(bid="B5", status="filled"), _mk_order(bid="B5", status="rejected")]
    a, _ = normalize_ingested_orders([copy.deepcopy(o) for o in raw])
    b, _ = normalize_ingested_orders([copy.deepcopy(o) for o in reversed(raw)])
    assert a[0].status == b[0].status, "Representative choice changed with input order"
    print("ING05 contradiction representative is deterministic: PASS")


def test_ing06_identifierless_records_are_not_deduplicated():
    """Deliberate boundary: two identical records with NO source identifier
    are indistinguishable from two real separate executions. Collapsing them
    would destroy genuine multiplicity, so they are left alone."""
    raw = [_mk_order(), _mk_order()]  # no bid, no rid -> fingerprint identity
    clean, findings = normalize_ingested_orders(raw)
    assert len(clean) == 2, "ID-less identical records must retain multiplicity"
    assert findings == []
    print("ING06 identifier-less records are not deduplicated: PASS")


def test_ing07_normalized_output_feeds_reconcile_cleanly():
    """End to end: duplicated input, normalized, then matched -- the result
    is exactly what it would have been had the duplicate never existed."""
    raw = [_mk_order(bid="B7"), _mk_order(bid="B7")]
    clean, findings = normalize_ingested_orders(raw)
    claim = _mk_claim("c1", bid="B7")
    results = reconcile([claim], clean, now=T + timedelta(hours=1))
    claim_result = next(r for r in results if r.claim is not None)
    assert claim_result.result == Result.CONFIRMED
    assert len(clean) == 1
    print("ING07 normalized output feeds reconcile() cleanly: PASS")


def test_ing08_no_raw_record_is_silently_dropped():
    """Accounting invariant for the ingestion layer itself: every raw record
    is represented in exactly one surviving record's ingestion_count."""
    raw = [_mk_order(bid="B8"), _mk_order(bid="B8"), _mk_order(bid="B9"),
           _mk_order(bid="B9", status="rejected"), _mk_order()]
    clean, _ = normalize_ingested_orders(raw)
    assert sum(o.ingestion_count for o in clean) == len(raw)
    print("ING08 no raw record silently dropped: PASS")


# ============ v12.1 item 2: client_order_id immutability ============

def test_ing09_same_broker_id_different_client_id_is_conflict():
    """Same broker order ID, but the client order IDs disagree -- one of
    the engine's strongest identifiers, so this must never be silently
    consolidated. Must produce SOURCE_IDENTITY_CONFLICT."""
    raw = [_mk_order(bid="B9", cid="X1"), _mk_order(bid="B9", cid="X2")]
    clean, findings = normalize_ingested_orders(raw)
    assert len(findings) == 1
    assert findings[0].finding_type == SOURCE_IDENTITY_CONFLICT
    assert "client_order_id" in findings[0].detail
    assert len(clean) == 1
    assert clean[0].ingestion_count == 2
    print("ING09 same broker ID, different client ID -> conflict: PASS")


def test_ing10_same_broker_and_client_id_forward_progression_consolidates():
    """Same broker order ID AND same client order ID, status genuinely
    progresses -- ordinary lifecycle update, must consolidate cleanly with
    no finding, same as ING02 but with client_order_id populated on both
    sides (proving it doesn't trip the new conflict check when it agrees)."""
    raw = [_mk_order(bid="B10", cid="X1", status="pending_new"),
           _mk_order(bid="B10", cid="X1", status="filled")]
    clean, findings = normalize_ingested_orders(raw)
    assert findings == []
    assert len(clean) == 1
    assert clean[0].status == "filled"
    assert clean[0].client_order_id == "X1"
    print("ING10 same broker+client ID, forward progression -> consolidate: PASS")


def test_ing11_missing_then_populated_client_id_is_enrichment_not_conflict():
    """
    Same broker order ID; one observation hasn't learned the client order
    ID yet (None), a later one has. This is ENRICHMENT, not a
    contradiction -- explicitly defined per the v12.1 spec's recommended
    rule: None -> value is fine when every other immutable field agrees;
    two DISTINCT non-null values (ING09) is the actual conflict.

    The surviving representative must carry the ENRICHED (non-null) value
    forward -- not just "no finding fires" while the information is lost.
    Deliberately puts the None-cid observation on the HIGHER-precedence
    status ("filled"), so plain max()-by-precedence would pick that record
    as the representative and, without the enrichment backfill, silently
    lose the client_order_id learned from the other (lower-precedence)
    observation -- this is what actually exercises _enrich_representative
    rather than it being a no-op that happens to already hold.
    """
    raw = [_mk_order(bid="B11", cid="X1", status="pending_new"),
           _mk_order(bid="B11", cid=None, status="filled")]
    clean, findings = normalize_ingested_orders(raw)
    assert findings == [], "None -> value on client_order_id alone must not be reported as a conflict"
    assert len(clean) == 1
    assert clean[0].status == "filled", "Sanity check: precedence still picks the more-advanced status"
    assert clean[0].client_order_id == "X1", "The enriched client_order_id must survive, not be dropped"
    print("ING11 missing->populated client ID is enrichment, representative carries it forward: PASS")


def test_ing11b_enrichment_representative_choice_is_order_independent():
    """The enriched value must survive regardless of which raw record
    (the None one or the populated one) appears first in the input."""
    a, _ = normalize_ingested_orders([_mk_order(bid="B11", cid="X1", status="pending_new"),
                                      _mk_order(bid="B11", cid=None, status="filled")])
    b, _ = normalize_ingested_orders([_mk_order(bid="B11", cid=None, status="filled"),
                                      _mk_order(bid="B11", cid="X1", status="pending_new")])
    assert a[0].client_order_id == b[0].client_order_id == "X1"
    print("ING11b enrichment representative choice is order-independent: PASS")


def test_ing12_unmapped_status_history_is_not_silently_ordered():
    """
    A status STATUS_PRECEDENCE has never heard of, alongside a KNOWN
    status, must not be silently resolved via max()'s default rank-0
    treatment for the unknown one -- that would let an unrecognized status
    silently lose (or, via the string tiebreak, silently win) with no
    signal the ordering was ever untrustworthy. Must produce
    UNMAPPED_STATUS_UNRESOLVED instead.
    """
    raw = [_mk_order(bid="B12", status="filled"), _mk_order(bid="B12", status="pending_broker_migration")]
    clean, findings = normalize_ingested_orders(raw)
    assert len(clean) == 1, "Unmapped status history keeps a deterministic representative for accounting"
    assert clean[0].ingestion_count == 2
    assert len(findings) == 1, "Unmapped status history must produce exactly one ingestion finding"
    assert findings[0].finding_type == UNMAPPED_STATUS_UNRESOLVED
    assert "pending_broker_migration" in findings[0].detail
    print("ING12 unmapped status history is unresolved rather than silently ordered: PASS")

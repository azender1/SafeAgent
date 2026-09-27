"""
Deterministic mutant generator (v12, item 1).

v11 shipped a mutation_runner.py that imported eight mutant modules which
were never in the package, so the suite could not run from a clean
extraction at all. Rather than remember to copy eight files, the mutants
are now GENERATED from the delivered source every run, here, by applying
one exact textual patch each.

Every mutation asserts its anchor appears EXACTLY ONCE in the target file.
If the source moves and an anchor stops matching, generation fails loudly
instead of quietly producing a mutant identical to the original -- which
would "survive" and look like a coverage gap, or worse, be reported as
killed by an unrelated failure.

Run directly to inspect what it produces:
    python3 mutants/generate_mutants.py
"""
from __future__ import annotations

import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(HERE)
GENERATED = os.path.join(HERE, "generated")

# target: "engine"    -> mutates reconcile_v12.py / reconcile_v12_classify.py
#         "ingestion" -> mutates ingestion_normalization.py
MUTATIONS = {
    1: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "Assignment priority: tier order reversed (weak resolved before full/partial)",
        "old": "    for tier in (EvidenceTier.FULL_IDENTIFIER, EvidenceTier.PARTIAL_IDENTIFIER, EvidenceTier.WEAK_HEURISTIC):",
        "new": "    for tier in (EvidenceTier.WEAK_HEURISTIC, EvidenceTier.PARTIAL_IDENTIFIER, EvidenceTier.FULL_IDENTIFIER):  # MUTATED",
    },
    2: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "Identifier conflict handling: zero-agreement records treated as conflicting",
        "old": "                elif any(checks):",
        "new": "                elif True:  # MUTATED",
    },
    3: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "Ambiguity handling: silently picks the first candidate instead of reporting ambiguity",
        "old": """        for c in claim_ids:
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
                        order_state[o] = "ambiguous\"""",
        "new": """        for c in claim_ids:  # MUTATED
            partners = result.possible_partners[c]
            if partners:
                order_idx = sorted(partners)[0]
                assignment[c] = Assignment(claim_idx=c, order_idx=order_idx, kind="assigned", tier=tier)
                order_state[order_idx] = "assigned\"""",
    },
    4: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "SKIP exclusion removed -- SKIP claims re-enter the evidence graph and can compete",
        "old": """        if eligibility[i] == ExecutionEligibility.EXECUTION_SKIPPED:
            continue

        has_ids = bool(c.broker_order_id or c.client_order_id)""",
        "new": """        # MUTATED: SKIP exclusion removed
        has_ids = bool(c.broker_order_id or c.client_order_id)""",
    },
    5: {
        "target": "engine",
        "file": "reconcile_v12_classify.py",
        "description": "Rejection contradictions: contradiction detection disabled entirely",
        "old": """            contradiction_orders = [
                e.order_idx for e in edges_by_claim.get(i, [])
                if e.tier in (EvidenceTier.FULL_IDENTIFIER, EvidenceTier.PARTIAL_IDENTIFIER)
                and broker_orders[e.order_idx].status in FILLED_STATUSES
            ]""",
        "new": "            contradiction_orders = []  # MUTATED",
    },
    6: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "Accounting disposition: final conflict sweep removed (reintroduces the silent-drop bug)",
        "old": """    for e in edges:
        if e.tier != EvidenceTier.IDENTIFIER_CONFLICT:
            continue
        if order_state[e.order_idx] == "open":
            order_state[e.order_idx] = "conflict\"""",
        "new": "    pass  # MUTATED: conflict sweep removed",
    },
    7: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "Assignment relation: swaps two valid assigned claim/order pairs, dispositions unchanged",
        "old": """    disposition: dict[int, OrderDisposition] = {}
    for i in range(n_orders):""",
        "new": """    # MUTATED: swap order_idx between the first two assigned claims. Every
    # per-order disposition stays identical, so only a test that compares the
    # full claim->order RELATION can catch this.
    _assigned = [c for c, a in assignment.items() if a.kind == "assigned"]
    if len(_assigned) >= 2:
        _c1, _c2 = _assigned[0], _assigned[1]
        _o1, _o2 = assignment[_c1].order_idx, assignment[_c2].order_idx
        assignment[_c1] = Assignment(claim_idx=_c1, order_idx=_o2, kind="assigned", tier=assignment[_c1].tier)
        assignment[_c2] = Assignment(claim_idx=_c2, order_idx=_o1, kind="assigned", tier=assignment[_c2].tier)

    disposition: dict[int, OrderDisposition] = {}
    for i in range(n_orders):""",
    },
    8: {
        "target": "engine",
        "file": "reconcile_v12_classify.py",
        "description": "Findings/accounting independence: classify_results writes to the disposition it was given",
        "old": """def classify_results(claims, broker_orders, eligibility, edges, assignments, disposition, now):
    results: list[ReconciliationResult] = []""",
        "new": """def classify_results(claims, broker_orders, eligibility, edges, assignments, disposition, now):
    results: list[ReconciliationResult] = []
    if len(broker_orders) > 0:
        disposition[0] = OrderDisposition.ASSIGNED  # MUTATED""",
    },
    9: {
        "target": "engine",
        "file": "reconcile_v12_classify.py",
        "description": "Finding multiplicity: the last finding is emitted twice",
        "old": """    return results


def reconcile(claims, broker_orders, now, match_tolerance=timedelta(minutes=5)):""",
        "new": """    if results:
        results.append(results[-1])  # MUTATED: duplicate finding
    return results


def reconcile(claims, broker_orders, now, match_tolerance=timedelta(minutes=5)):""",
    },
    10: {
        "target": "ingestion",
        "file": "ingestion_normalization.py",
        "description": "Ingestion: contradictory records silently collapsed, no SOURCE_IDENTITY_CONFLICT emitted",
        "old": """        if _immutable_fields_conflict(group):""",
        "new": """        if False:  # MUTATED: immutable-field contradictions no longer reported""",
    },
    11: {
        "target": "ingestion",
        "file": "ingestion_normalization.py",
        "description": "Ingestion: duplicates collapsed without retaining provenance count",
        "old": """            rep = group[0]
            rep.ingestion_count = len(group)
            normalized.append(rep)
            continue""",
        "new": """            rep = group[0]
            rep.ingestion_count = 1  # MUTATED: provenance count dropped
            normalized.append(rep)
            continue""",
    },
    12: {
        "target": "engine",
        "file": "reconcile_v12.py",
        "description": "Claim identity: assign_claim_stable_ids reverted to using request_id (the v12.1 vulnerability itself)",
        "old": '''    fingerprint_groups: dict[tuple, list[int]] = {}
    for idx, c in enumerate(claims):
        if c.immutable_source_event_id:''',
        "new": '''    for c in claims:  # MUTATED: identity reverted to request_id
        c.stable_id = c.request_id
        c.stable_id_basis = "request_id"
    return
    fingerprint_groups: dict[tuple, list[int]] = {}
    for idx, c in enumerate(claims):
        if c.immutable_source_event_id:''',
    },
    13: {
        "target": "ingestion",
        "file": "ingestion_normalization.py",
        "description": "Ingestion: client_order_id conflicts ignored (two distinct non-null values silently consolidated)",
        "old": """    non_null_cids = {o.client_order_id for o in group if o.client_order_id is not None}
    return len(non_null_cids) > 1""",
        "new": """    non_null_cids = {o.client_order_id for o in group if o.client_order_id is not None}
    return False  # MUTATED: client_order_id conflicts no longer reported""",
    },
    14: {
        "target": "ingestion",
        "file": "ingestion_normalization.py",
        "description": "Ingestion: unmapped status history silently resolved via default rank-0 precedence",
        "old": """        unmapped_statuses = {s for s in statuses if s not in STATUS_PRECEDENCE}
        if len(statuses) > 1 and unmapped_statuses:""",
        "new": """        unmapped_statuses = {s for s in statuses if s not in STATUS_PRECEDENCE}
        if False:  # MUTATED: unmapped-status guard disabled""",
    },
}


def _read(name: str) -> str:
    with open(os.path.join(PACKAGE_ROOT, name)) as f:
        return f.read()


def generate(verbose: bool = False) -> list[int]:
    """(Re)generates every mutant module into mutants/generated/ and returns
    the list of mutant numbers produced. Idempotent: the directory is wiped
    first, so a stale mutant from an earlier run can never be executed."""
    if os.path.isdir(GENERATED):
        shutil.rmtree(GENERATED)
    os.makedirs(GENERATED)

    engine_src = _read("reconcile_v12.py")
    classify_src = _read("reconcile_v12_classify.py")
    ingestion_src = _read("ingestion_normalization.py")

    for n, spec in sorted(MUTATIONS.items()):
        old, new = spec["old"], spec["new"]
        source = {"reconcile_v12.py": engine_src,
                  "reconcile_v12_classify.py": classify_src,
                  "ingestion_normalization.py": ingestion_src}[spec["file"]]
        count = source.count(old)
        if count != 1:
            raise SystemExit(
                f"Mutant {n}: anchor matched {count} times in {spec['file']} (expected exactly 1). "
                f"The source changed -- fix the anchor rather than shipping a mutant that "
                f"is either identical to the original or patched in the wrong place."
            )

        if spec["target"] == "engine":
            eng = engine_src if spec["file"] != "reconcile_v12.py" else source.replace(old, new)
            cls = classify_src if spec["file"] != "reconcile_v12_classify.py" else source.replace(old, new)
            eng_name = f"reconcile_v12_mut{n}"
            cls_name = f"reconcile_v12_classify_mut{n}"
            cls = cls.replace("from reconcile_v12 import", f"from {eng_name} import")
            _write(f"{eng_name}.py", eng)
            _write(f"{cls_name}.py", cls)
        else:
            ing_name = f"ingestion_normalization_mut{n}"
            _write(f"{ing_name}.py", source.replace(old, new))

        if verbose:
            print(f"  mutant {n:>2}: {spec['description']}")

    return sorted(MUTATIONS)


def _write(filename: str, content: str) -> None:
    with open(os.path.join(GENERATED, filename), "w") as f:
        f.write(content)


if __name__ == "__main__":
    print(f"Generating {len(MUTATIONS)} mutants into {GENERATED}\n")
    generate(verbose=True)
    print(f"\nDone. Every anchor matched exactly once.")
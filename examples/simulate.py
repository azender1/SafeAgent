import sys
import os
import json
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case, OutcomeSignal
from settlement.store import InMemoryStore
from settlement.reconciliation import ingest_signal, resolve_reconciliation
from settlement.gate import attempt_settlement, SettlementError


def write_trace(name: str, case):
    """Write a deterministic trace artifact for repo visitors.

    Robust to different signal storage formats (objects, dicts, strings).
    """
    def normalize_signal(s):
        # If it's already a dict-like object
        if isinstance(s, dict):
            return {
                "source": s.get("source"),
                "outcome": s.get("outcome"),
                "raw": s,
            }

        # If it's a simple string (common if signals are stored as "source:outcome" or similar)
        if isinstance(s, str):
            # Try to parse a couple common formats; otherwise store as raw string
            if ":" in s:
                left, right = s.split(":", 1)
                return {"source": left.strip(), "outcome": right.strip(), "raw": s}
            if "|" in s:
                left, right = s.split("|", 1)
                return {"source": left.strip(), "outcome": right.strip(), "raw": s}
            return {"source": None, "outcome": None, "raw": s}

        # If it's an object with attributes like OutcomeSignal
        source = getattr(s, "source", None)
        outcome = getattr(s, "outcome", None)
        if source is not None or outcome is not None:
            return {"source": source, "outcome": outcome, "raw": repr(s)}

        # Fallback
        return {"source": None, "outcome": None, "raw": repr(s)}

    signals = getattr(case, "signals", [])
    artifact = {
        "scenario": name,
        "case_id": getattr(case, "case_id", None),
        "state": str(getattr(case, "state", None)),
        "final_outcome": getattr(case, "final_outcome", None),
        "signals": [normalize_signal(s) for s in (signals or [])],
        "reconciliation_reason": getattr(case, "reconciliation_reason", None),
        "settlement_id": getattr(case, "settlement_id", None),
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    os.makedirs("examples/traces", exist_ok=True)
    path = os.path.join("examples", "traces", f"{name}_{artifact['case_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    print(f"trace written: {path}")


def scenario_clean():
    print("\n--- scenario_clean ---")
    store = InMemoryStore()
    case = Case(case_id="case_1")
    store.put_case(case)

    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_1", source="oracle_A", outcome="YES"))
    print("ingest 1:", ok, reason, "state:", case.state)

    # finalize without dispute
    resolve_reconciliation(case, chosen_outcome="YES")
    print("finalized:", case.final_outcome, "state:", case.state)

    sid = attempt_settlement(case)
    print("settled:", sid, "state:", case.state)

    # replay settlement
    sid2 = attempt_settlement(case)
    print("replay settled:", sid2, "(same id)", sid2 == sid)

    write_trace("scenario_clean", case)


def scenario_conflict():
    print("\n--- scenario_conflict ---")
    store = InMemoryStore()
    case = Case(case_id="case_2")
    store.put_case(case)

    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_2", source="oracle_A", outcome="YES"))
    print("ingest A:", ok, reason, "state:", case.state)

    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_2", source="oracle_B", outcome="NO"))
    print("ingest B:", ok, reason, "state:", case.state, "recon:", case.reconciliation_reason)

    try:
        attempt_settlement(case)
    except SettlementError as e:
        print("settlement blocked:", e)

    resolve_reconciliation(case, chosen_outcome="YES")
    print("resolved:", case.final_outcome, "state:", case.state)

    sid = attempt_settlement(case)
    print("settled:", sid, "state:", case.state)

    write_trace("scenario_conflict", case)


def scenario_duplicate_and_late():
    print("\n--- scenario_duplicate_and_late ---")
    store = InMemoryStore()
    case = Case(case_id="case_3")
    store.put_case(case)

    # Initial signal arrives
    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_3", source="oracle_A", outcome="YES"))
    print("ingest initial:", ok, reason, "state:", case.state)

    # Duplicate retry of the same signal (common in real systems)
    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_3", source="oracle_A", outcome="YES"))
    print("ingest duplicate:", ok, reason, "state:", case.state)

    # Finalize (no dispute)
    resolve_reconciliation(case, chosen_outcome="YES")
    print("finalized:", case.final_outcome, "state:", case.state)

    # Settle once
    sid = attempt_settlement(case)
    print("settled:", sid, "state:", case.state)

    # Late conflicting signal arrives AFTER finalization/settlement
    try:
        ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_3", source="oracle_B", outcome="NO"))
        print("late ingest:", ok, reason, "state:", case.state, "recon:", case.reconciliation_reason)
    except Exception as e:
        print("late signal blocked:", e)

    # Replay settlement attempt (should be idempotent / same id)
    sid2 = attempt_settlement(case)
    print("replay settled:", sid2, "(same id)", sid2 == sid)

    write_trace("scenario_duplicate_and_late", case)


def scenario_three_oracles_majority():
    print("\n--- scenario_three_oracles_majority ---")
    store = InMemoryStore()
    case = Case(case_id="case_4")
    store.put_case(case)

    # Oracle A says YES
    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_4", source="oracle_A", outcome="YES"))
    print("ingest A:", ok, reason, "state:", case.state)

    # Oracle B disagrees -> conflict -> reconciliation
    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_4", source="oracle_B", outcome="NO"))
    print("ingest B:", ok, reason, "state:", case.state, "recon:", case.reconciliation_reason)

    # Oracle C also says NO (adds more evidence while in reconciliation)
    ok, reason = ingest_signal(case, OutcomeSignal(case_id="case_4", source="oracle_C", outcome="NO"))
    print("ingest C:", ok, reason, "state:", case.state, "recon:", case.reconciliation_reason)

    # Settlement should still be blocked
    try:
        attempt_settlement(case)
    except SettlementError as e:
        print("settlement blocked:", e)

    # Resolve reconciliation by choosing the majority outcome (NO)
    resolve_reconciliation(case, chosen_outcome="NO")
    print("resolved:", case.final_outcome, "state:", case.state)

    sid = attempt_settlement(case)
    print("settled:", sid, "state:", case.state)

    # Replay should be idempotent
    sid2 = attempt_settlement(case)
    print("replay settled:", sid2, "(same id)", sid2 == sid)

    write_trace("scenario_three_oracles_majority", case)


if __name__ == "__main__":
    scenario_clean()
    scenario_conflict()
    scenario_duplicate_and_late()
    scenario_three_oracles_majority()
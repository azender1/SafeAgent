import sys
import os
import uuid
import json
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case, OutcomeSignal
from settlement.store import SQLiteStore
from settlement.reconciliation import ingest_signal, resolve_reconciliation
from settlement.settlement_requests import SettlementRequestRegistry


def fake_agent_decision():
    """
    Pretend an LLM agent decided to perform an irreversible action.
    """
    return {
        "action": "create_support_ticket",
        "payload": {"customer_id": "C123", "severity": "high", "message": "Agent-triggered ticket"},
        "confidence": 0.86,
        "proposed_outcome": "YES",
    }


def main():
    print("\n--- safe_agent_demo (exactly-once AI action execution) ---")

    # Durable store so state survives restarts
    db_path = os.path.join("examples", "traces", "safeagent_demo.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    store = SQLiteStore(db_path)

    # NEW case_id each run (so demo is always clean)
    case_id = f"safeagent_case_{uuid.uuid4().hex[:8]}"
    case = Case(case_id=case_id)
    store.put_case(case)
    print("created case:", case_id, "state:", case.state)

    # Agent produces a decision
    decision = fake_agent_decision()

    # Ingest the agent signal (moves to RESOLVED_PROVISIONAL via state machine)
    s = OutcomeSignal(case_id=case.case_id, source="agent_llm", outcome=decision["proposed_outcome"])
    ok, reason = ingest_signal(case, s)
    store.put_case(case)
    print("ingest:", ok, reason, "state:", case.state)

    # Finalize (now allowed because state is RESOLVED_PROVISIONAL)
    resolve_reconciliation(case, chosen_outcome=decision["proposed_outcome"])
    store.put_case(case)
    print("finalized outcome:", case.final_outcome, "state:", case.state)

    registry = SettlementRequestRegistry()

    # First execution attempt
    req1 = str(uuid.uuid4())
    r1 = registry.submit(case, req1)
    store.put_case(case)

    receipt1 = {
        "request_id": req1,
        "settlement_id": r1.settlement_id,
        "ok": r1.ok,
        "reason": r1.reason,
        "action": decision["action"],
        "payload": decision["payload"],
        "confidence": decision["confidence"],
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    print("\nFIRST EXECUTION RECEIPT:")
    print(json.dumps(receipt1, indent=2))

    # Replay same request id (dedup)
    r1b = registry.submit(case, req1)
    store.put_case(case)

    receipt1b = {
        "request_id": req1,
        "settlement_id": r1b.settlement_id,
        "ok": r1b.ok,
        "reason": r1b.reason,
        "action": decision["action"],
        "payload": decision["payload"],
        "confidence": decision["confidence"],
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    print("\nREPLAY (SAME request_id) RECEIPT:")
    print(json.dumps(receipt1b, indent=2))

    # New request id after already settled (same settlement_id)
    req2 = str(uuid.uuid4())
    r2 = registry.submit(case, req2)
    store.put_case(case)

    receipt2 = {
        "request_id": req2,
        "settlement_id": r2.settlement_id,
        "ok": r2.ok,
        "reason": r2.reason,
        "action": decision["action"],
        "payload": decision["payload"],
        "confidence": decision["confidence"],
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    print("\nNEW REQUEST AFTER SETTLED RECEIPT:")
    print(json.dumps(receipt2, indent=2))

    print("\nSame settlement_id across requests:", r1.settlement_id == r2.settlement_id)
    print("DB path:", db_path)


if __name__ == "__main__":
    main()
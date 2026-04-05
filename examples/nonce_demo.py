import sys
import os
import uuid

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case, OutcomeSignal
from settlement.store import InMemoryStore
from settlement.reconciliation import ingest_signal, resolve_reconciliation
from settlement.settlement_requests import SettlementRequestRegistry


def main():
    print("\n--- nonce_demo ---")
    store = InMemoryStore()
    case = Case(case_id="nonce_case_1")
    store.put_case(case)

    # Resolve to FINAL
    ingest_signal(case, OutcomeSignal(case_id=case.case_id, source="oracle_A", outcome="YES"))
    resolve_reconciliation(case, chosen_outcome="YES")
    print("finalized:", case.final_outcome, "state:", case.state)

    reg = SettlementRequestRegistry()

    # First request
    req1 = str(uuid.uuid4())
    r1 = reg.submit(case, req1)
    print("req1:", req1, "ok=", r1.ok, "sid=", r1.settlement_id, "reason=", r1.reason)

    # Replay same request id (dedup)
    r1b = reg.submit(case, req1)
    print("req1 replay:", req1, "ok=", r1b.ok, "sid=", r1b.settlement_id, "reason=", r1b.reason)

    # Different request id after settlement (should return same settlement_id)
    req2 = str(uuid.uuid4())
    r2 = reg.submit(case, req2)
    print("req2:", req2, "ok=", r2.ok, "sid=", r2.settlement_id, "reason=", r2.reason)

    print("same settlement id across req1/req2:", r1.settlement_id == r2.settlement_id)


if __name__ == "__main__":
    main()

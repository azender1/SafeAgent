import sys
import os
import json
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case, OutcomeSignal
from settlement.store import InMemoryStore
from settlement.reconciliation import ingest_signal, resolve_reconciliation
from settlement.gate import attempt_settlement, SettlementError


def write_receipt(name: str, receipt: dict):
    os.makedirs("examples/receipts", exist_ok=True)
    path = os.path.join("examples", "receipts", f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
    print(f"receipt written: {path}")


def run_prediction_market_demo():
    print("\n--- prediction_market_demo ---")

    # Fake market: YES/NO outcome
    market_id = "pmkt_1"
    case_id = "pmkt_case_1"

    # Participants place stakes (toy ledger)
    stakes = {
        "alice": {"side": "YES", "amount": 50},
        "bob":   {"side": "NO",  "amount": 50},
    }
    pot = sum(x["amount"] for x in stakes.values())

    store = InMemoryStore()
    case = Case(case_id=case_id)
    store.put_case(case)

    # External resolution signals arrive (oracle/ref/ai/api/etc)
    signals = [
        OutcomeSignal(case_id=case_id, source="oracle_A", outcome="YES"),
        OutcomeSignal(case_id=case_id, source="oracle_B", outcome="YES"),
    ]
    for s in signals:
        ok, reason = ingest_signal(case, s)
        print("ingest:", s.source, s.outcome, ok, reason, "state:", case.state)

    # Gate requires explicit finalization step before payout
    resolve_reconciliation(case, chosen_outcome="YES")
    print("finalized:", case.final_outcome, "state:", case.state)

    # Settlement occurs exactly once
    try:
        settlement_id = attempt_settlement(case)
    except SettlementError as e:
        print("settlement blocked:", e)
        return

    # Payout ledger (toy): winners split pot
    winners = [u for u, s in stakes.items() if s["side"] == case.final_outcome]
    payout = {}
    if winners:
        share = pot / len(winners)
        for u in winners:
            payout[u] = share

    receipt = {
        "market_id": market_id,
        "case_id": case_id,
        "final_outcome": case.final_outcome,
        "settlement_id": settlement_id,
        "pot": pot,
        "stakes": stakes,
        "payout": payout,
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    print("PAYOUT RECEIPT:", json.dumps(receipt, indent=2))
    write_receipt("prediction_market_demo_receipt", receipt)


if __name__ == "__main__":
    run_prediction_market_demo()

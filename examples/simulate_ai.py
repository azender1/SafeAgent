import sys
import os
import random

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case, OutcomeSignal
from settlement.store import InMemoryStore
from settlement.reconciliation import ingest_signal, resolve_reconciliation
from settlement.gate import attempt_settlement
from settlement.policy import should_auto_finalize


def scenario_ai_majority_policy():
    print("\n--- scenario_ai_majority_policy ---")

    store = InMemoryStore()
    case = Case(case_id="ai_case_1")
    store.put_case(case)

    # Simulated AI agents producing stochastic outcomes
    agents = ["agent_A", "agent_B", "agent_C", "agent_D", "agent_E"]

    for agent in agents:
        outcome = random.choice(["YES", "NO"])
        ingest_signal(
            case,
            OutcomeSignal(
                case_id=case.case_id,
                source=agent,
                outcome=outcome
            )
        )
        print("signal:", agent, "->", outcome)

    # Confidence-based auto-finalization
    ok, chosen, conf, tally = should_auto_finalize(case, threshold=0.80)

    print("tally:", tally)
    print("confidence:", round(conf, 3))

    if ok:
        print("auto-finalize by threshold:", chosen)
    else:
        # fallback to simple majority
        chosen = "YES" if tally.get("YES", 0) >= tally.get("NO", 0) else "NO"
        print("threshold not met; fallback majority:", chosen)

    resolve_reconciliation(case, chosen_outcome=chosen)

    print("finalized:", case.final_outcome, "state:", case.state)

    settlement_id = attempt_settlement(case)
    print("settled:", settlement_id)


if __name__ == "__main__":
    scenario_ai_majority_policy()
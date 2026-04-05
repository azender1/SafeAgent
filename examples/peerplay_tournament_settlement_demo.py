"""
PeerPlay Tournament Settlement Demo Using SafeAgent

Shows how SafeAgent prevents duplicate payouts when settlement is retried.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict
import json

from safeagent_exec_guard import SettlementRequestRegistry


@dataclass
class TournamentState:
    tournament_id: str
    winner_id: str
    total_entry_fees: float
    platform_rake: float
    prize_pool: float
    escrow_released: bool = False


class EscrowEngine:
    def __init__(self):
        self.payout_history = []

    def release_prize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        print(f"      [ESCROW] Releasing ${payload['prize_amount']} to {payload['winner_id']}")
        print(f"      [ESCROW] Rake collected: ${payload['rake_amount']}")

        payout = {
            "tournament_id": payload["tournament_id"],
            "winner_id": payload["winner_id"],
            "prize_amount": payload["prize_amount"],
            "rake_amount": payload["rake_amount"],
            "escrow_status": "RELEASED",
            "timestamp": datetime.now().isoformat(),
        }

        self.payout_history.append(payout)
        return payout


def main():
    print("=" * 80)
    print("PEERPLAY TOURNAMENT SETTLEMENT DEMO")
    print("Demonstrating SafeAgent's Exactly-Once Execution Guarantee")
    print("=" * 80)

    registry = SettlementRequestRegistry()
    escrow = EscrowEngine()

    print("\n[TOURNAMENT SETUP]")
    print("  16 players join tournament, $5 entry fee each")

    tournament = TournamentState(
        tournament_id="TOURNAMENT_T123",
        winner_id="PLAYER_W456",
        total_entry_fees=80.00,
        platform_rake=8.00,
        prize_pool=72.00,
    )

    print(f"  Tournament ID: {tournament.tournament_id}")
    print(f"  Winner: {tournament.winner_id}")
    print(f"  Entry Fees Collected: ${tournament.total_entry_fees}")
    print(f"  Platform Rake: ${tournament.platform_rake}")
    print(f"  Prize Pool: ${tournament.prize_pool}")

    request_id = f"settlement:{tournament.tournament_id}:{tournament.winner_id}"

    payload = {
        "tournament_id": tournament.tournament_id,
        "winner_id": tournament.winner_id,
        "prize_amount": tournament.prize_pool,
        "rake_amount": tournament.platform_rake,
    }

    print("\n[ATTEMPT 1: FIRST SETTLEMENT]")
    receipt_1 = registry.execute(
        request_id=request_id,
        action="tournament_payout",
        payload=payload,
        execute_fn=escrow.release_prize,
    )

    print(json.dumps(receipt_1, indent=4))
    print(f"\n  Payouts executed: {len(escrow.payout_history)}")

    print("\n" + "=" * 80)
    print("[RETRY SCENARIO]")
    print("Verification layer retries settlement with the SAME request_id")
    print("=" * 80)

    print("\n[ATTEMPT 2: RETRY WITH SAME REQUEST_ID]")
    receipt_2 = registry.execute(
        request_id=request_id,
        action="tournament_payout",
        payload=payload,
        execute_fn=escrow.release_prize,
    )

    print(json.dumps(receipt_2, indent=4))
    print(f"\n  Payouts executed: {len(escrow.payout_history)}")

    print("\n" + "=" * 80)
    print("[VERIFICATION]")
    print("=" * 80)
    print(f"Receipt 1 == Receipt 2: {receipt_1 == receipt_2}")
    print(f"Payout count: {len(escrow.payout_history)}")
    print(f"Expected payout count: 1")
    print(f"Status: {'PASS' if len(escrow.payout_history) == 1 else 'FAIL'}")

    total_paid = sum(p["prize_amount"] for p in escrow.payout_history)
    total_rake = sum(p["rake_amount"] for p in escrow.payout_history)

    print(f"\nTotal paid: ${total_paid}")
    print(f"Expected: ${tournament.prize_pool}")
    print(f"Status: {'PASS' if total_paid == tournament.prize_pool else 'FAIL'}")

    print(f"\nTotal rake: ${total_rake}")
    print(f"Expected: ${tournament.platform_rake}")
    print(f"Status: {'PASS' if total_rake == tournament.platform_rake else 'FAIL'}")

    print("\n" + "=" * 80)
    print("[SUMMARY]")
    print("=" * 80)
    print("Without SafeAgent:")
    print("  retry -> duplicate payout")
    print("  retry -> duplicate rake")
    print("  retry -> corrupted settlement state")
    print("\nWith SafeAgent:")
    print("  retry -> cached receipt returned")
    print("  retry -> no duplicate execution")
    print("  retry -> settlement state remains consistent")


if __name__ == "__main__":
    main()
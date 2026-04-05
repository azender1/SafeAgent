from __future__ import annotations
import time
import uuid
from .models import Case, CaseState
from .state_machine import set_state


class SettlementError(Exception):
    pass


def attempt_settlement(case: Case) -> str:
    """
    Exactly-once settlement gate.
    Returns settlement_id if settled or already settled.
    """
    # If already settled, return the existing settlement ID (idempotent)
    if case.state == CaseState.SETTLED and case.settlement_id:
        return case.settlement_id

    if case.state != CaseState.FINAL:
        raise SettlementError(f"Case not FINAL (state={case.state}); cannot settle")

    # Settle once
    case.settlement_id = str(uuid.uuid4())
    case.settled_at = time.time()
    set_state(case, CaseState.SETTLED)
    return case.settlement_id

from __future__ import annotations

from settlement.models import CaseState


class InvalidTransitionError(Exception):
    """Raised when a case attempts an invalid state transition."""
    pass


# Allowed state transitions
VALID_TRANSITIONS = {
    CaseState.OPEN: {
        CaseState.RESOLVED_PROVISIONAL,
        CaseState.IN_RECONCILIATION,
    },
    CaseState.RESOLVED_PROVISIONAL: {
        CaseState.IN_RECONCILIATION,
        CaseState.FINAL,
    },
    CaseState.IN_RECONCILIATION: {
        CaseState.FINAL,
    },
    CaseState.FINAL: {
        CaseState.SETTLED,
    },
    CaseState.SETTLED: set(),  # terminal state
}


def validate_transition(from_state: CaseState, to_state: CaseState) -> None:
    """
    Raise InvalidTransitionError if a transition is not allowed.
    """
    allowed = VALID_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from {from_state} to {to_state}"
        )


def can_transition(from_state: CaseState, to_state: CaseState) -> bool:
    """
    Return True if the transition is allowed, False otherwise.
    """
    return to_state in VALID_TRANSITIONS.get(from_state, set())


def set_case_state(case, new_state: CaseState) -> None:
    """
    Safely transition a case to a new state.

    Example:
        set_case_state(case, CaseState.FINAL)
    """
    validate_transition(case.state, new_state)
    case.state = new_state

def set_state(case, new_state: CaseState) -> None:
    """
    Backward-compatible alias for older code paths.

    Existing modules (like gate.py) may still import set_state.
    Keep this thin wrapper so older imports continue to work.
    """
    set_case_state(case, new_state)
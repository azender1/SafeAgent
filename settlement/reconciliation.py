from __future__ import annotations

from typing import Dict, Tuple

from settlement.models import Case, CaseState, OutcomeSignal
from settlement.state_machine import set_case_state


def _normalize_signals(case: Case) -> Dict[str, OutcomeSignal]:
    """
    Ensure case.signals is always a dict[str, OutcomeSignal].
    """
    if getattr(case, "signals", None) is None:
        case.signals = {}
    return case.signals


def _outcome_tally(case: Case) -> Dict[str, int]:
    """
    Count how many signals support each outcome.
    """
    tally: Dict[str, int] = {}
    signals = _normalize_signals(case)

    for _, signal in signals.items():
        outcome = getattr(signal, "outcome", None)
        if not outcome:
            continue
        tally[outcome] = tally.get(outcome, 0) + 1

    return tally


def ingest_signal(case: Case, signal: OutcomeSignal) -> Tuple[bool, str]:
    """
    Ingest a new outcome signal into a case.

    Behavior:
    - If case is FINAL or SETTLED, late/conflicting signals are ignored.
    - If all known signals agree, transition to RESOLVED_PROVISIONAL.
    - If signals conflict, transition to IN_RECONCILIATION.
    - Returns (ok, reason).
    """

    # Hard finality: ignore late signals after final outcome is reached
    if case.state in {CaseState.FINAL, CaseState.SETTLED}:
        return False, "late_signal_ignored_after_finality"

    signals = _normalize_signals(case)

    # Upsert by source (one signal per source)
    source = getattr(signal, "source", None)
    if not source:
        return False, "missing_signal_source"

    signals[source] = signal

    tally = _outcome_tally(case)

    # No usable outcome
    if not tally:
        return False, "no_valid_outcome_signals"

    # If there is only one distinct outcome, case is provisionally resolved
    if len(tally) == 1:
        if case.state == CaseState.OPEN:
            set_case_state(case, CaseState.RESOLVED_PROVISIONAL)
        elif case.state == CaseState.IN_RECONCILIATION:
            # if reconciliation converged back to one outcome, allow provisional again
            # but only if this transition is valid in your model
            # current state machine does not allow IN_RECONCILIATION -> RESOLVED_PROVISIONAL
            # so we leave it as-is until explicit finalize
            pass
        return True, "consistent_outcome_signals"

    # Conflicting signals -> reconciliation state
    if case.state in {CaseState.OPEN, CaseState.RESOLVED_PROVISIONAL}:
        set_case_state(case, CaseState.IN_RECONCILIATION)

    return True, "conflicting_outcome_signals"


def resolve_reconciliation(case: Case, chosen_outcome: str) -> None:
    """
    Finalize reconciliation by selecting a final outcome.

    Allowed only from:
    - RESOLVED_PROVISIONAL
    - IN_RECONCILIATION

    After this, the case becomes FINAL and late signals are ignored.
    """
    if case.state not in {CaseState.RESOLVED_PROVISIONAL, CaseState.IN_RECONCILIATION}:
        raise ValueError("Can only finalize from RESOLVED_PROVISIONAL or IN_RECONCILIATION")

    case.final_outcome = chosen_outcome
    set_case_state(case, CaseState.FINAL)


def get_majority_outcome(case: Case) -> str | None:
    """
    Return the outcome with the highest vote count, or None if no signals exist.
    """
    tally = _outcome_tally(case)
    if not tally:
        return None
    return max(tally, key=tally.get)


def get_signal_count(case: Case) -> int:
    """
    Number of stored signals.
    """
    return len(_normalize_signals(case))
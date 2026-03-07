from __future__ import annotations

from typing import Dict, Tuple
from settlement.models import Case

def outcome_confidence(case: Case) -> Tuple[str, float, Dict[str, int]]:
    """
    Returns (winning_outcome, confidence, tally).
    confidence = winning_votes / total_votes
    """
    tally: Dict[str, int] = {}
    signals = getattr(case, "signals", {}) or {}
    for _, s in signals.items():
        outcome = getattr(s, "outcome", None)
        if not outcome:
            continue
        tally[outcome] = tally.get(outcome, 0) + 1

    total = sum(tally.values()) or 0
    if total == 0:
        return ("", 0.0, tally)

    winner = max(tally, key=lambda k: tally[k])
    conf = tally[winner] / total
    return (winner, conf, tally)

def should_auto_finalize(case: Case, threshold: float = 0.80) -> Tuple[bool, str, float, Dict[str, int]]:
    winner, conf, tally = outcome_confidence(case)
    ok = bool(winner) and conf >= threshold
    return ok, winner, conf, tally

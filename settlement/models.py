from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
import time
import uuid


class CaseState(str, Enum):
    OPEN = "OPEN"
    RESOLVED_PROVISIONAL = "RESOLVED_PROVISIONAL"
    IN_RECONCILIATION = "IN_RECONCILIATION"
    FINAL = "FINAL"
    SETTLED = "SETTLED"


@dataclass(frozen=True)
class OutcomeSignal:
    case_id: str
    source: str                 # e.g., "oracle_A", "referee", "agent_model_v3"
    outcome: str                # e.g., "YES", "NO", "PLAYER_A", "TEAM_X"
    confidence: float = 1.0
    received_at: float = field(default_factory=lambda: time.time())
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Case:
    case_id: str
    state: CaseState = CaseState.OPEN

    # Outcome chosen when FINAL; immutable once final.
    final_outcome: Optional[str] = None

    # All signals seen (for auditing / conflict detection)
    signals: Dict[str, OutcomeSignal] = field(default_factory=dict)

    # Settlement exactly-once fields
    settled_at: Optional[float] = None
    settlement_id: Optional[str] = None

    # Reconciliation / dispute notes
    reconciliation_reason: Optional[str] = None

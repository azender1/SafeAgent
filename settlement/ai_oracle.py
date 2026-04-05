from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import List, Optional

from settlement.models import OutcomeSignal

@dataclass
class AIGeneratorConfig:
    seed: int = 1337
    n_agents: int = 3
    conflict_rate: float = 0.35  # chance an agent disagrees
    base_outcome: str = "YES"    # default if prompt is neutral

def _heuristic_outcome(prompt: str, base: str) -> str:
    p = (prompt or "").lower()
    # very simple "AI-ish" heuristic (no external API)
    if any(w in p for w in ["win", "wins", "won", "success", "beat", "beats", "victory"]):
        return "YES"
    if any(w in p for w in ["lose", "loses", "lost", "failure", "forfeit", "dq", "disqual"]):
        return "NO"
    return base

def generate_ai_signals(
    case_id: str,
    prompt: str,
    config: Optional[AIGeneratorConfig] = None,
) -> List[OutcomeSignal]:
    """
    Generates OutcomeSignal objects from multiple 'AI agents'.
    Deterministic given seed; can simulate conflicts via conflict_rate.
    """
    cfg = config or AIGeneratorConfig()
    rng = Random(cfg.seed)

    base = _heuristic_outcome(prompt, cfg.base_outcome)
    agents = []
    for i in range(cfg.n_agents):
        # Decide whether this agent conflicts
        if rng.random() < cfg.conflict_rate:
            outcome = "NO" if base == "YES" else "YES"
        else:
            outcome = base
        agents.append(
            OutcomeSignal(case_id=case_id, source=f"ai_agent_{i+1}", outcome=outcome)
        )
    return agents

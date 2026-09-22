#!/usr/bin/env python3
"""Bounded Crashpoint action-readback -> SafeAgent Control adapter.

This adapter is intentionally separate from the frozen reconciliation core. It
accepts only a complete, internally verified action-readback manifest and fails
closed when the evidence contract is incomplete or contradictory.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


class EvidenceContractError(ValueError):
    """Retained evidence does not satisfy the bounded contract."""


OUTCOME_TO_CONTROL = {
    "ONE_EFFECT_MATCHING": "CONFIRMED",
    "NO_EFFECT": "MISSING_EXTERNALLY",
    "MULTIPLE_EFFECTS_MATCHING": "CONTRADICTION",
    "MULTIPLE_EFFECTS_DIVERGED": "CONTRADICTION",
    "ONE_EFFECT_MISMATCHED": "CONTRADICTION",
    "INDETERMINATE": "UNCERTAIN",
}


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise EvidenceContractError(f"missing required field: {key}")
    return data[key]


def classify_trial(trial: dict[str, Any]) -> dict[str, Any]:
    """Classify verified evidence without consulting its prediction/pass flag."""
    action_id = _require(trial, "action_id")
    if not action_id or not trial.get("admission_commit_confirmed"):
        raise EvidenceContractError("committed pre-dispatch action_id required")
    if not trial.get("protocol_valid"):
        raise EvidenceContractError("protocol_valid=true required")

    outcome = _require(trial, "external_outcome")
    if outcome not in OUTCOME_TO_CONTROL:
        raise EvidenceContractError(f"unsupported external_outcome: {outcome!r}")
    availability = _require(trial, "observation_availability")
    verified = _require(trial, "externally_verified")
    if outcome == "INDETERMINATE":
        if verified or availability != "UNAVAILABLE":
            raise EvidenceContractError("INDETERMINATE requires unavailable readback")
    elif not verified or availability != "FULL":
        raise EvidenceContractError(f"{outcome} requires full authoritative readback")

    count = _require(trial, "effect_count")
    matches = _require(trial, "digests_match_admission")
    valid = {
        "ONE_EFFECT_MATCHING": count == 1 and matches is True,
        "NO_EFFECT": count == 0,
        "MULTIPLE_EFFECTS_MATCHING": isinstance(count, int) and count > 1 and matches is True,
        "MULTIPLE_EFFECTS_DIVERGED": isinstance(count, int) and count > 1 and matches is False,
        "ONE_EFFECT_MISMATCHED": count == 1 and matches is False,
        "INDETERMINATE": count is None,
    }[outcome]
    if not valid:
        raise EvidenceContractError(f"fields contradict external_outcome={outcome}")

    return {
        "case": _require(trial, "case"),
        "external_outcome": outcome,
        "control_result": OUTCOME_TO_CONTROL[outcome],
        "replay_authorized": False,
    }


def compare_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if _require(manifest, "schema") != "crashpoint.action_readback.manifest.v1":
        raise EvidenceContractError("unsupported manifest schema")
    if manifest.get("status") != "COMPLETE" or not manifest.get("all_agree"):
        raise EvidenceContractError("manifest must be complete and internally verified")
    trials = _require(manifest, "trials")
    if len(trials) != manifest.get("trial_count") or len(trials) != manifest.get("expected_trial_count"):
        raise EvidenceContractError("trial count does not match manifest")

    findings = [classify_trial(trial) for trial in trials]
    return {
        "schema": "safeagent.control.action-readback-comparison.v1",
        "adapter_scope": "external evidence adapter; frozen reconciliation core unchanged",
        "source": {
            "repository": "mstevens843/crashpoint",
            "pinned_commit": "bb9cd47c4b0b02527aab7b369d17b32829cc4e20",
        },
        "trial_count": len(findings),
        "result_counts": dict(sorted(Counter(x["control_result"] for x in findings).items())),
        "findings": findings,
        "interpretation": {
            "prediction_passed_is_not_confirmation": True,
            "missing_externally_is_not_replay_permission": True,
            "claim_sweep_semantics_applied": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

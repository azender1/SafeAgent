"""Classify Safal's wallet-watch evidence without inventing intent data.

The public bundles contain observations and retrieval-state evidence, but no
SafeAgent claim or pre-dispatch payment-intent identifier. This adapter remains
separate from claim/order reconciliation so an observed blockchain event is not
silently reinterpreted as a logical action.
"""
from __future__ import annotations

from typing import Any


OBSERVED_EVENT_ONLY = "OBSERVED_EVENT_ONLY"
INCOMPLETE_RETRIEVAL = "INCOMPLETE_RETRIEVAL"


class EvidenceContractError(ValueError):
    """Raised when a bundle summary violates the bounded evidence contract."""


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise EvidenceContractError(f"missing required field: {key}")
    return data[key]


def classify_wallet_watch_result(data: dict[str, Any]) -> dict[str, Any]:
    """Return only conclusions supported by a wallet-watch result summary."""
    status = _require(data, "status")
    source_head_sha = _require(data, "source_head_sha")

    if status == "PASS":
        event = _require(data, "event")
        initial = _require(data, "initial_scan")
        resumed = _require(data, "resumed_scan")
        replay = _require(data, "transaction_replay")
        if initial.get("status") != "scanned" or resumed.get("status") != "scanned":
            raise EvidenceContractError("PASS requires completed initial and resumed scans")
        if replay.get("status") != "transaction_verified":
            raise EvidenceContractError("PASS requires verified transaction replay")

        return {
            "classification": OBSERVED_EVENT_ONLY,
            "safeagent_control_result": None,
            "source_head_sha": source_head_sha,
            "logical_action_id": None,
            "external_identity": {
                "event_id": _require(event, "event_id"),
                "tx_hash": _require(event, "tx_hash"),
                "log_index": _require(event, "log_index"),
                "block_number": _require(event, "block_number"),
                "block_hash": _require(event, "block_hash"),
                "block_timestamp": _require(event, "block_timestamp"),
            },
            "retrieval_complete_for_bounded_run": True,
            "provider_completeness_independently_proven": False,
            "checkpoint_before": _require(data, "checkpoint_before"),
            "checkpoint_after": _require(data, "checkpoint_after"),
            "event_retained_after_resume": data.get("final_history_events") == 1,
            "duplicate_local_event_on_replay": replay.get("new_events") != 0,
            "duplicate_console_notification_on_replay": replay.get("console_notifications") != 0,
            "claims_not_supported": [
                "SafeAgent action confirmation",
                "payment intent matching",
                "provider completeness",
                "exactly-once external execution",
            ],
        }

    if status == "INCOMPLETE":
        attempts = _require(data, "attempts")
        completed = _require(data, "completed_scan_attempts")
        checkpoints = _require(data, "checkpoint_after_each_attempt")
        checkpoint_before = _require(data, "checkpoint_before")
        if completed != 0:
            raise EvidenceContractError("INCOMPLETE control must not report a completed scan")
        if len(checkpoints) != attempts or any(cp != checkpoint_before for cp in checkpoints):
            raise EvidenceContractError("INCOMPLETE control requires unchanged checkpoints")

        return {
            "classification": INCOMPLETE_RETRIEVAL,
            "safeagent_control_result": "UNCERTAIN",
            "source_head_sha": source_head_sha,
            "logical_action_id": None,
            "external_identity": None,
            "retrieval_complete_for_bounded_run": False,
            "provider_completeness_independently_proven": False,
            "attempts": attempts,
            "completed_scan_attempts": completed,
            "checkpoint_before": checkpoint_before,
            "checkpoint_after_each_attempt": checkpoints,
            "state_unchanged": data.get("state_and_database_bytes_unchanged_each_attempt") is True,
            "must_not_classify_as": ["MISSING_EXTERNALLY", "CONFIRMED", "REJECTED"],
        }

    raise EvidenceContractError(f"unsupported evidence status: {status!r}")

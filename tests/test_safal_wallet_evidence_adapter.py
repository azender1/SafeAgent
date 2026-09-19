import pytest

from safeagent_control.safal_wallet_evidence_adapter import (
    EvidenceContractError,
    INCOMPLETE_RETRIEVAL,
    OBSERVED_EVENT_ONLY,
    classify_wallet_watch_result,
)


def _success():
    return {
        "status": "PASS",
        "source_head_sha": "e2d644155b587da89b12116a9b94150c6e5d4837",
        "checkpoint_before": [25920399, "0xbefore"],
        "checkpoint_after": [25920400, "0xafter"],
        "final_history_events": 1,
        "initial_scan": {"status": "scanned"},
        "resumed_scan": {"status": "scanned"},
        "transaction_replay": {
            "status": "transaction_verified",
            "new_events": 0,
            "console_notifications": 0,
        },
        "event": {
            "event_id": "1:token:tx:13",
            "tx_hash": "0xtx",
            "log_index": 13,
            "block_number": 25920399,
            "block_hash": "0xblock",
            "block_timestamp": 1788722891,
        },
    }


def _incomplete():
    return {
        "status": "INCOMPLETE",
        "source_head_sha": "e2d644155b587da89b12116a9b94150c6e5d4837",
        "attempts": 2,
        "completed_scan_attempts": 0,
        "checkpoint_before": 25919603,
        "checkpoint_after_each_attempt": [25919603, 25919603],
        "state_and_database_bytes_unchanged_each_attempt": True,
    }


def test_success_is_observation_only_and_does_not_invent_intent():
    result = classify_wallet_watch_result(_success())
    assert result["classification"] == OBSERVED_EVENT_ONLY
    assert result["safeagent_control_result"] is None
    assert result["logical_action_id"] is None
    assert result["event_retained_after_resume"] is True
    assert result["duplicate_local_event_on_replay"] is False
    assert result["duplicate_console_notification_on_replay"] is False
    assert result["provider_completeness_independently_proven"] is False


def test_failed_retrieval_stays_uncertain_and_never_becomes_missing():
    result = classify_wallet_watch_result(_incomplete())
    assert result["classification"] == INCOMPLETE_RETRIEVAL
    assert result["safeagent_control_result"] == "UNCERTAIN"
    assert result["retrieval_complete_for_bounded_run"] is False
    assert result["completed_scan_attempts"] == 0
    assert result["state_unchanged"] is True
    assert "MISSING_EXTERNALLY" in result["must_not_classify_as"]


def test_incomplete_requires_unchanged_checkpoint():
    data = _incomplete()
    data["checkpoint_after_each_attempt"] = [25919603, 25919604]
    with pytest.raises(EvidenceContractError, match="unchanged checkpoints"):
        classify_wallet_watch_result(data)


def test_unknown_status_fails_closed():
    with pytest.raises(EvidenceContractError, match="unsupported evidence status"):
        classify_wallet_watch_result({"status": "MAYBE", "source_head_sha": "test"})

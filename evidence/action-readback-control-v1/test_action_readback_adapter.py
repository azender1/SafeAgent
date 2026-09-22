import copy

import pytest

from action_readback_adapter import EvidenceContractError, classify_trial, compare_manifest


def trial(outcome="ONE_EFFECT_MATCHING", count=1, matches=True):
    return {
        "action_id": "fixture-action-id",
        "admission_commit_confirmed": True,
        "protocol_valid": True,
        "external_outcome": outcome,
        "observation_availability": "FULL",
        "externally_verified": True,
        "effect_count": count,
        "digests_match_admission": matches,
        "case": "fixture-case",
        "passed": True,
    }


def test_confirmation_requires_matching_single_effect():
    assert classify_trial(trial())["control_result"] == "CONFIRMED"


def test_prediction_pass_is_not_confirmation_input():
    item = trial()
    item["passed"] = False
    assert classify_trial(item)["control_result"] == "CONFIRMED"


@pytest.mark.parametrize(
    ("outcome", "count", "matches", "expected"),
    [
        ("NO_EFFECT", 0, None, "MISSING_EXTERNALLY"),
        ("MULTIPLE_EFFECTS_MATCHING", 2, True, "CONTRADICTION"),
        ("MULTIPLE_EFFECTS_DIVERGED", 2, False, "CONTRADICTION"),
        ("ONE_EFFECT_MISMATCHED", 1, False, "CONTRADICTION"),
    ],
)
def test_bounded_mapping(outcome, count, matches, expected):
    assert classify_trial(trial(outcome, count, matches))["control_result"] == expected


def test_unavailable_readback_is_uncertain():
    item = trial("INDETERMINATE", None, None)
    item.update(observation_availability="UNAVAILABLE", externally_verified=False)
    assert classify_trial(item)["control_result"] == "UNCERTAIN"


def test_missing_action_id_fails_closed():
    item = trial()
    item["action_id"] = None
    with pytest.raises(EvidenceContractError, match="pre-dispatch"):
        classify_trial(item)


def test_incomplete_readback_cannot_confirm():
    item = trial()
    item["externally_verified"] = False
    with pytest.raises(EvidenceContractError, match="authoritative readback"):
        classify_trial(item)


def test_contradictory_fields_fail_closed():
    item = trial()
    item["effect_count"] = 2
    with pytest.raises(EvidenceContractError, match="contradict"):
        classify_trial(item)


def test_manifest_contract_and_aggregate():
    items = [trial() for _ in range(2)]
    manifest = {
        "schema": "crashpoint.action_readback.manifest.v1",
        "status": "COMPLETE",
        "all_agree": True,
        "trial_count": 2,
        "expected_trial_count": 2,
        "trials": items,
    }
    report = compare_manifest(manifest)
    assert report["result_counts"] == {"CONFIRMED": 2}
    assert report["interpretation"]["missing_externally_is_not_replay_permission"] is True


def test_manifest_must_be_complete():
    manifest = {"schema": "crashpoint.action_readback.manifest.v1", "status": "INCOMPLETE", "all_agree": False, "trials": []}
    with pytest.raises(EvidenceContractError, match="complete"):
        compare_manifest(copy.deepcopy(manifest))

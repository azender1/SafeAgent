import pytest

from mercor_external_effect_verifier import VerifierInputError, verify_external_effects


def test_all_confirmed_passes():
    result = verify_external_effects(
        {"findings": [
            {"case": "a", "control_result": "CONFIRMED"},
            {"case": "b", "control_result": "CONFIRMED"},
        ]}
    )
    assert result["score"] == 1.0
    assert result["passed"] is True
    assert result["blocking_reasons"] == []


def test_nonconfirmed_blocks_and_reduces_score():
    result = verify_external_effects(
        {"findings": [
            {"case": "a", "control_result": "CONFIRMED"},
            {"case": "b", "control_result": "CONTRADICTION"},
            {"case": "c", "control_result": "UNCERTAIN"},
            {"case": "d", "control_result": "MISSING_EXTERNALLY"},
        ]}
    )
    assert result["score"] == 0.25
    assert result["passed"] is False
    assert len(result["blocking_reasons"]) == 3
    assert result["replay_authorized"] is False


@pytest.mark.parametrize("bad", [None, [], {}, "CONFIRMED"])
def test_findings_must_be_nonempty_list(bad):
    with pytest.raises(VerifierInputError):
        verify_external_effects({"findings": bad})


def test_unknown_classification_fails_closed():
    with pytest.raises(VerifierInputError, match="unsupported"):
        verify_external_effects(
            {"findings": [{"control_result": "LOOKS_FINE"}]}
        )

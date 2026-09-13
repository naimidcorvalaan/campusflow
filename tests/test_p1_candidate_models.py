from src.p1_candidate_models import CandidateDecisionStatus, ExecutionContext


def test_candidate_enums_are_small_and_explicit():
    assert CandidateDecisionStatus.PROPOSED.value == "proposed"
    assert ExecutionContext.FREE_WINDOW.value == "free_window"

from src.p1_planning_models import PlanningStatus


def test_planning_status_has_final_and_tentative_states():
    assert PlanningStatus.FINAL_CANDIDATE.value == "final_candidate"
    assert PlanningStatus.TENTATIVE_CANDIDATE.value == "tentative_candidate"

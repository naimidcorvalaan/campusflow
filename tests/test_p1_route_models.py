from src.p1_route_models import CandidateValidationStatus, RouteDataTrust


def test_route_enums_keep_calculation_and_data_trust_separate():
    assert CandidateValidationStatus.FEASIBLE.value == "feasible"
    assert RouteDataTrust.SYNTHETIC_TEST.value == "synthetic_test"

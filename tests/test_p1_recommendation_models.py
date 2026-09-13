from src.p1_recommendation_models import QuickAction, RecommendationStatus, SecondCallReason


def test_recommendation_enums_are_stable_and_python38_plain_enums():
    assert RecommendationStatus.REGENERATED_SUCCESS.value == "regenerated_success"
    assert SecondCallReason.SCHEMA_REPAIR.value == "schema_repair"
    assert QuickAction.UPDATE_LOCATION.value == "update_location"

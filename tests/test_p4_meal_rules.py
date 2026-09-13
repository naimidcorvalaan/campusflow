from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_map_schema import TransportMode
from src.p4_meal_rules import AUTO_CANTEEN_LABEL, DEFAULT_MEAL_DURATION_MINUTES, choose_nearest_canteen


def test_weijinlu_canteen_selection_uses_only_selected_map_and_real_route():
    campus_map = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    choice = choose_nearest_canteen(campus_map, "友园", TransportMode.WALK)
    assert choice is not None
    assert choice.node_id.startswith("weijinlu_canteen_")
    assert choice.distance_m > 0


def test_meal_defaults_are_separate_from_movement_and_have_a_clear_label():
    assert DEFAULT_MEAL_DURATION_MINUTES == 40
    assert AUTO_CANTEEN_LABEL == "【已为您选择就近食堂】"

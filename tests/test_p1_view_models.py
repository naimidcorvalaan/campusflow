from src.p1_view_models import P1ResultView, QuickOptionView


def test_view_models_are_small_read_only_python38_dataclasses():
    option = QuickOptionView("确认", "confirm")
    assert option.label == "确认"
    assert hasattr(P1ResultView, "__dataclass_fields__")

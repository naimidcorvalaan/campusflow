from datetime import datetime

from src.p1_offline_demo import EXAMPLE_ONE, EXAMPLE_TWO, _window_reply, is_supported_example, run_offline_demo
import json


def test_only_builtin_examples_run_and_use_synthetic_notice():
    assert is_supported_example(EXAMPLE_ONE) and is_supported_example(EXAMPLE_TWO)
    assert not is_supported_example("任意自由输入")
    view = run_offline_demo(EXAMPLE_ONE, datetime(2026, 5, 1, 10, 5))
    assert view is not None and "演示路线数据" in view.route_data_notice.message
    assert run_offline_demo("任意自由输入", datetime(2026, 5, 1, 10, 5)) is None


def test_demo_target_time_is_next_occurrence_including_equal_and_midnight():
    for text, reference, expected_day in ((EXAMPLE_ONE, datetime(2026,5,1,11,0), 1), (EXAMPLE_ONE, datetime(2026,5,1,12,0), 2), (EXAMPLE_TWO, datetime(2026,5,1,23,0), 2)):
        payload = json.loads(_window_reply(text, reference))
        assert datetime.fromisoformat(payload["commitments"][0]["starts_at"]).day == expected_day


def test_both_examples_produce_a_view_and_do_not_mutate_reference():
    reference = datetime(2026, 5, 1, 23, 30)
    for example in (EXAMPLE_ONE, EXAMPLE_TWO):
        assert run_offline_demo(example, reference) is not None
    assert reference == datetime(2026, 5, 1, 23, 30)


def test_demo_source_does_not_import_test_python_helpers():
    import inspect
    import src.p1_offline_demo as module
    assert "from tests" not in inspect.getsource(module)

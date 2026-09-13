import importlib
from datetime import datetime

from src.p1_main import handle_submission
from src.p1_page_state import ERROR_KEY, INPUT_KEY, TIME_KEY, VIEW_KEY
from src.p1_offline_demo import EXAMPLE_ONE


def test_importing_page_does_not_run_streamlit_or_demo():
    module = importlib.import_module("src.p1_main")
    assert callable(module.main)


def test_submission_boundary_calls_runner_once_only_for_supported_submit_and_caches():
    state = {INPUT_KEY: EXAMPLE_ONE, TIME_KEY: datetime(2026,5,1,10,5)}; calls=[]; view=object()
    def runner(text, time): calls.append((text,time)); return view
    assert not handle_submission(state, False, runner) and not calls
    assert handle_submission(state, True, runner) and calls and state[VIEW_KEY] is view
    assert not handle_submission(state, False, runner) and len(calls) == 1
    state[INPUT_KEY] = "自由输入"
    assert not handle_submission(state, True, runner) and len(calls) == 1 and VIEW_KEY not in state


def test_submission_exception_is_safe_and_not_retried():
    state = {INPUT_KEY: EXAMPLE_ONE, TIME_KEY: datetime(2026,5,1,10,5), VIEW_KEY:object()}; calls=[]
    def runner(text, time): calls.append(1); raise RuntimeError("Authorization Bearer https://secret")
    assert not handle_submission(state, True, runner) and len(calls) == 1 and VIEW_KEY not in state
    assert "Bearer" not in state[ERROR_KEY] and "secret" not in state[ERROR_KEY]


def test_success_submission_clears_previous_error():
    state={INPUT_KEY:EXAMPLE_ONE,TIME_KEY:datetime(2026,5,1,10,5),ERROR_KEY:"old"}
    handle_submission(state,True,lambda text,time: object())
    assert ERROR_KEY not in state and VIEW_KEY in state


def test_rerun_prefers_new_api_and_uses_legacy_when_needed():
    from src.p1_main import _rerun
    class New(object):
        def __init__(self): self.called=[]
        def rerun(self): self.called.append("new")
    class Old(object):
        def __init__(self): self.called=[]
        def experimental_rerun(self): self.called.append("old")
    new=New(); old=Old(); _rerun(new); _rerun(old)
    assert new.called == ["new"] and old.called == ["old"]


def test_streamlit_apptest_starts_without_result_or_exception():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file("src/p1_main.py").run()
    assert not app.exception
    assert any("离线固定样例演示" in item.value for item in app.info)

import importlib
import json
import sys
from datetime import datetime, timedelta

from src.p1_live_main import (
    INPUT_ERROR,
    PLANNING_ERROR,
    PROGRESS_TEXT,
    TIME_ERROR,
    _request_rerun,
    build_live_planner_runner,
    main,
    run_live_submission,
)
from src.p1_live_page_state import (
    ERROR_KEY,
    INPUT_KEY,
    INTERACTION_KEY,
    NEXT_INPUT_KEY,
    OPTION_KEY,
    PENDING_INPUT_KEY,
    PENDING_KEY,
    TIME_TEXT_KEY,
    VIEW_KEY,
)
from src.p1_route_models import LocationResolutionStatus, RouteDataTrust
from src.p1_tju_llm_adapter import TJUP1CallAdapter
from src.p1_view_models import (
    NoticeView,
    P1ResultView,
    QuestionView,
    QuickOptionView,
)


NOW = datetime(2026, 5, 1, 10, 5)


def make_view(question=None, route_notice=None):
    return P1ResultView(
        "当前建议",
        "建议先完成任务。",
        None,
        None,
        None,
        None,
        route_notice,
        question,
        (),
        (),
        (),
        False,
    )


class _Context(object):
    def __init__(self, on_enter=None):
        self.on_enter = on_enter

    def __enter__(self):
        if self.on_enter is not None:
            self.on_enter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeSt(object):
    def __init__(self, submitted=False, clicks=None, state=None):
        self.session_state = {} if state is None else state
        self.submitted = submitted
        self.clicks = set() if clicks is None else set(clicks)
        self.titles = []
        self.warnings = []
        self.infos = []
        self.writes = []
        self.form_count = 0
        self.spinner_count = 0
        self.spinner_messages = []
        self.rerun_count = 0
        self.experimental_rerun_count = 0
        self.submit_labels = []

    def title(self, value):
        self.titles.append(value)

    def warning(self, value):
        self.warnings.append(value)

    def info(self, value):
        self.infos.append(value)

    def write(self, value):
        self.writes.append(str(value))

    def subheader(self, value):
        self.writes.append(str(value))

    def markdown(self, value):
        self.writes.append(str(value))

    def button(self, label, key=None):
        return key in self.clicks

    def expander(self, label):
        return _Context()

    def form(self, key):
        self.form_count += 1
        return _Context()

    def text_input(self, label, key=None):
        return self.session_state.get(key, "")

    def text_area(self, label, key=None):
        return self.session_state.get(key, "")

    def number_input(self, label, min_value=None, value=None, step=None, key=None):
        return self.session_state.get(key, value)

    def form_submit_button(self, label):
        self.submit_labels.append(label)
        return self.submitted

    def spinner(self, message):
        self.spinner_messages.append(message)
        return _Context(lambda: setattr(self, "spinner_count", self.spinner_count + 1))

    def rerun(self):
        self.rerun_count += 1

    def experimental_rerun(self):
        self.experimental_rerun_count += 1


def valid_state(view=None):
    state = {
        INPUT_KEY: "任意非空自然语言输入",
        TIME_TEXT_KEY: "2026-05-01 10:05",
    }
    if view is not None:
        state[VIEW_KEY] = view
    return state


def test_import_live_page_does_not_import_tju_client_or_call_network():
    import src.p1_live_main as module
    previous = sys.modules.pop("src.tju_llm_client", None)
    try:
        importlib.reload(module)
        assert "src.tju_llm_client" not in sys.modules
    finally:
        if previous is not None:
            sys.modules["src.tju_llm_client"] = previous


def test_apptest_without_configuration_loads_page_without_network(monkeypatch):
    from streamlit.testing.v1 import AppTest

    for name in ("TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr("dotenv.load_dotenv",
                        lambda path, override=False: calls.append((path, override)) or False)
    app = AppTest.from_file("src/p1_live_main.py").run(timeout=10)
    assert not app.exception
    assert app.title[0].value == "CampusFlow P1 真实模型实验页"
    assert calls and all(override is False for _, override in calls)


def test_unsubmitted_page_does_not_construct_or_call_runner():
    calls = []
    st = FakeSt(state=valid_state())
    main(st=st, runner_factory=lambda: calls.append("factory"),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert calls == []
    assert st.spinner_count == 0


def test_normal_page_with_no_injected_runner_builds_production_runner_once():
    calls = []
    view = make_view()

    def factory():
        calls.append("factory")
        return lambda text, reference: calls.append((text, reference)) or view

    st = FakeSt(submitted=True, state=valid_state())
    main(st=st, planner_runner=None, runner_factory=factory,
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert calls == ["factory", ("任意非空自然语言输入", NOW)]
    assert st.session_state[VIEW_KEY] is view


def test_valid_submission_enters_single_spinner():
    st = FakeSt(submitted=True, state=valid_state())
    main(st=st, planner_runner=lambda text, reference: make_view(),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert st.spinner_count == 1
    assert st.spinner_messages == [PROGRESS_TEXT]


def test_ordinary_rerun_reuses_identical_cached_view_without_factory():
    view = make_view()
    calls = []
    st = FakeSt(state=valid_state(view))
    main(st=st, runner_factory=lambda: calls.append("factory"),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert calls == []
    assert st.session_state[VIEW_KEY] is view


def test_missing_configuration_does_not_construct_runner_or_show_values():
    calls = []
    st = FakeSt(submitted=True, state=valid_state())
    main(st=st, runner_factory=lambda: calls.append("factory"),
         now_provider=lambda: NOW,
         configuration_loader=lambda: ("TJU_LLM_API_KEY",))
    assert calls == []
    assert any("TJU_LLM_API_KEY" in item for item in st.infos)
    assert all("secret-value" not in item for item in st.infos)


def test_missing_configuration_submission_clears_stale_result():
    state = valid_state(make_view())
    calls = []
    assert not run_live_submission(
        state, True, False, runner_factory=lambda: calls.append("factory"))
    assert calls == [] and VIEW_KEY not in state


def test_blank_input_rejects_before_factory_and_clears_old_view():
    state = valid_state(make_view())
    state[INPUT_KEY] = "   "
    calls = []
    assert not run_live_submission(
        state, True, True, runner_factory=lambda: calls.append("factory"))
    assert calls == [] and VIEW_KEY not in state and state[ERROR_KEY] == INPUT_ERROR


def test_invalid_time_rejects_before_factory_and_preserves_fixed_error():
    state = valid_state(make_view())
    state[TIME_TEXT_KEY] = "2026-02-30 10:05"
    calls = []
    assert not run_live_submission(
        state, True, True, runner_factory=lambda: calls.append("factory"))
    assert calls == [] and VIEW_KEY not in state and state[ERROR_KEY] == TIME_ERROR


def test_runner_exception_is_safe_called_once_and_clears_old_cache():
    state = valid_state(make_view())
    calls = []

    def runner(text, reference):
        calls.append(text)
        raise RuntimeError("Authorization Bearer API_KEY https://invalid object at 0x123")

    assert not run_live_submission(state, True, True, runner=runner)
    assert calls == ["任意非空自然语言输入"]
    assert state[ERROR_KEY] == PLANNING_ERROR
    assert state[VIEW_KEY].primary_card is None
    assert state[VIEW_KEY].diagnostics[0].status_label == "内部处理失败"
    assert "Bearer" not in state[ERROR_KEY] and "https" not in state[ERROR_KEY]


def test_runner_factory_or_map_construction_exception_is_safely_hidden():
    state = valid_state(make_view())
    calls = []

    def failing_factory():
        calls.append("factory")
        raise RuntimeError("API_KEY .env response body https://invalid object at 0x123")

    assert not run_live_submission(state, True, True, runner_factory=failing_factory)
    assert calls == ["factory"] and state[ERROR_KEY] == PLANNING_ERROR
    assert state[VIEW_KEY].diagnostics[0].error_category_label == "内部"


def test_successful_submission_clears_old_error_and_caches_view():
    state = valid_state()
    state[ERROR_KEY] = "旧错误"
    view = make_view()
    assert run_live_submission(state, True, True, runner=lambda text, ref: view)
    assert state[VIEW_KEY] is view and ERROR_KEY not in state


def test_quick_option_click_saves_selection_without_running_planner():
    question = QuestionView("地点在哪里？", (
        QuickOptionView("填写地点", "update_location", "图书馆",
                        "current_context", "current_location_text", "当前位置"),
    ))
    view = make_view(question=question)
    st = FakeSt(clicks={"p1_option_0_update_location"}, state=valid_state(view))
    calls = []
    main(st=st, runner_factory=lambda: calls.append("factory"),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert calls == []
    assert st.session_state[OPTION_KEY] == {
        "action": "update_location", "value": "图书馆", "label": "填写地点",
        "target_ref": "current_context", "field_name": "current_location_text",
        "target_label": "当前位置"}
    assert st.session_state[PENDING_KEY] == st.session_state[OPTION_KEY]
    assert "已选择：填写地点" in st.infos


def test_confirm_estimate_apply_calls_runner_once_with_independent_context():
    view = make_view()
    state = valid_state(view)
    state[PENDING_KEY] = {
        "action": "confirm_estimate", "value": "120", "label": "就按这个",
        "target_ref": "input-task-1", "field_name": "estimated_total_minutes",
        "target_label": "完成计组实验3"}
    state[OPTION_KEY] = dict(state[PENDING_KEY])
    calls = []
    st = FakeSt(clicks={"p1_live_pending_apply"}, state=state)

    def runner(text, reference, interaction_context=None):
        calls.append((text, reference, interaction_context))
        return make_view()

    main(st=st, planner_runner=runner, now_provider=lambda: NOW,
         configuration_loader=lambda: ())
    assert len(calls) == 1
    assert calls[0][0] == "任意非空自然语言输入"
    assert calls[0][2]["confirmation"]["selected_value"] == "120"
    assert "预计时长按120分钟" in calls[0][2]["confirmation"]["supplement_text"]
    assert state[INPUT_KEY] == "任意非空自然语言输入"
    assert state[INTERACTION_KEY] is calls[0][2]
    assert PENDING_KEY not in state and OPTION_KEY not in state
    assert st.spinner_count == 1 and st.rerun_count == 1


def test_adjust_duration_and_location_inputs_generate_structured_supplements():
    for pending, supplied, expected in (
        ({"action": "adjust_duration", "value": None, "label": "调整时长",
          "target_ref": "task-1", "field_name": "estimated_total_minutes",
          "target_label": "实验"}, 45, "预计时长按45分钟"),
        ({"action": "set_location", "value": None, "label": "填写地点",
          "target_ref": "current_context", "field_name": "current_location_text",
          "target_label": "当前位置"}, "31教学楼", "我现在在31教学楼"),
    ):
        state = valid_state(make_view())
        state[PENDING_KEY] = pending
        state[OPTION_KEY] = dict(pending)
        state[PENDING_INPUT_KEY] = supplied
        calls = []
        st = FakeSt(clicks={"p1_live_pending_apply"}, state=state)
        def runner(text, ref, interaction_context=None):
            calls.append(interaction_context)
            return make_view()
        main(st=st, planner_runner=runner,
             now_provider=lambda: NOW, configuration_loader=lambda: ())
        assert len(calls) == 1
        assert expected in calls[0]["confirmation"]["supplement_text"]


def test_pending_cancel_and_ordinary_rerun_never_call_runner():
    pending = {"action": "adjust_duration", "value": None, "label": "调整时长",
               "target_ref": "task-1", "field_name": "estimated_total_minutes",
               "target_label": "实验"}
    for clicks in (set(), {"p1_live_pending_cancel"}):
        state = valid_state(make_view())
        state[PENDING_KEY] = dict(pending)
        calls = []
        st = FakeSt(clicks=clicks, state=state)
        main(st=st, planner_runner=lambda text, ref: calls.append(text),
             now_provider=lambda: NOW, configuration_loader=lambda: ())
        assert calls == []
        if clicks:
            assert PENDING_KEY not in state


def test_pending_panel_never_displays_internal_reference_or_sensitive_value():
    state = valid_state(make_view())
    state[PENDING_KEY] = {
        "action": "confirm_estimate", "value": "30", "label": "就按这个",
        "target_ref": "SECRET-task_ref-Authorization-Bearer-https://invalid",
        "field_name": "estimated_total_minutes", "target_label": "背单词"}
    st = FakeSt(state=state)
    main(st=st, planner_runner=lambda text, ref: make_view(),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    visible = " ".join(st.infos + st.writes + st.warnings)
    assert "SECRET" not in visible and "Authorization" not in visible
    assert "Bearer" not in visible and "https://" not in visible


def test_start_new_plan_reruns_then_returns_without_form_or_runner():
    st = FakeSt(clicks={"p1_live_new"}, state={
        "p0_keep": "yes", INPUT_KEY: "old", VIEW_KEY: make_view(),
        PENDING_KEY: {"action": "confirm_estimate"}})
    calls = []
    main(st=st, runner_factory=lambda: calls.append("factory"),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert st.rerun_count == 1 and st.form_count == 0 and calls == []
    assert st.session_state["p0_keep"] == "yes"
    assert st.session_state[INPUT_KEY] == ""
    assert PENDING_KEY not in st.session_state


def test_rerun_compatibility_prefers_modern_method():
    st = FakeSt()
    _request_rerun(st)
    assert st.rerun_count == 1 and st.experimental_rerun_count == 0


def test_rerun_compatibility_uses_experimental_fallback():
    st = FakeSt()
    st.rerun = None
    _request_rerun(st)
    assert st.experimental_rerun_count == 1


def test_build_runner_wires_p1h_formatter_and_synthetic_provider_without_api():
    captured = {}

    class FakeAdapter(object):
        task_caller = staticmethod(lambda system, user: "task")
        window_caller = staticmethod(lambda system, user: "window")
        candidate_caller = staticmethod(lambda system, user: "candidate")

    def planning(text, reference, task, window, candidate, provider):
        captured.update(text=text, reference=reference, provider=provider)
        assert task("s", "u") == "task"
        assert window("s", "u") == "window"
        assert candidate("s", "u") == "candidate"
        return "planning-result"

    runner = build_live_planner_runner(
        adapter_factory=FakeAdapter,
        planning_function=planning,
        formatter=lambda result: ("formatted", result),
        map_loader=lambda path: object(),
        map_path="fake-map.json",
    )
    assert runner("用户输入", NOW) == ("formatted", "planning-result")
    assert captured["provider"].route_data_trust is RouteDataTrust.SYNTHETIC_TEST


def test_default_production_runner_wires_dual_channel_and_interaction(monkeypatch):
    captured = {}
    view = make_view()

    class FakeAdapter(object):
        task_caller = staticmethod(lambda system, user: "task")
        window_caller = staticmethod(lambda system, user: "window")
        candidate_caller = staticmethod(lambda system, user: "candidate")
        agent_caller = staticmethod(lambda system, user: "agent")

    def dual(text, reference, task, window, candidate, provider,
             agent_caller=None, interaction_context=None):
        captured.update(text=text, reference=reference, provider=provider,
                        agent=agent_caller, interaction=interaction_context)
        return type("Dual", (), {"view": view})()

    monkeypatch.setattr("src.p1_live_main.run_p1_dual_planning", dual)
    runner = build_live_planner_runner(
        adapter_factory=FakeAdapter, map_loader=lambda path: object(),
        map_path="fake-map.json")
    interaction = {"confirmation": {"selected_value": "120"}}
    assert runner("原始输入", NOW, interaction_context=interaction) is view
    assert captured["text"] == "原始输入"
    assert captured["interaction"] is interaction
    assert captured["agent"]("s", "u") == "agent"
    assert captured["provider"].route_data_trust is RouteDataTrust.SYNTHETIC_TEST


def test_real_build_runner_routes_strict_failure_to_adapter_backed_agent_plan():
    calls = []

    def fake_tju(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if len(calls) <= 4:
            return "not-json"
        if len(calls) == 5:
            return ('{"status":"proposed","steps":[{"title":"背单词",'
                    '"minutes":30,"timing_note":"预留步行和10分钟缓冲后出发"}],'
                    '"estimated_walking_minutes_min":10,"estimated_walking_minutes_max":20}')
        if len(calls) == 6:
            return '{"decision":"accept"}'
        return "建议先背单词 30 分钟，然后预留步行和缓冲。"

    runner = build_live_planner_runner(
        adapter_factory=lambda: TJUP1CallAdapter(fake_tju))
    view = runner("我在宿舍，90分钟后上课，想背30分钟单词。", NOW)
    assert len(calls) == 7
    assert view.primary_card is not None
    assert view.primary_card.steps[0].planned_minutes == 30
    assert all(item[1]["temperature"] == 0 for item in calls)
    assert all("system_prompt" in item[1] and item[0] != item[1]["system_prompt"] for item in calls)


def test_real_build_runner_all_structured_calls_can_succeed_without_agent_fallback():
    calls = []

    def fake_tju(prompt, **kwargs):
        calls.append((prompt, kwargs))
        system = kwargs["system_prompt"]
        if "p1.task-extraction.v2" in system:
            return json.dumps({
                "schema_version": "p1.task-extraction.v2", "tasks": [{
                    "title": "背单词", "original_text": "背30分钟单词",
                    "features": {"location_requirement": "no_specific_location",
                                 "estimated_total_minutes": 30, "is_splittable": True,
                                 "minimum_slice_minutes": 15, "attention_required": "low",
                                 "interruption_allowed": True, "may_have_open_hours": False}}]},
                ensure_ascii=False)
        if "p1.window-extraction.v2" in system:
            return json.dumps({
                "schema_version": "p1.window-extraction.v2",
                "current_location": {"text": "宿舍", "fragment": "宿舍"},
                "commitments": [{"title": "上课", "original_text": "90分钟后去教学楼上课",
                                  "starts_at": (NOW + timedelta(minutes=90)).isoformat(),
                                  "ends_at": None, "location_text": "教学楼",
                                  "availability_during": None}],
                "free_duration_minutes": None, "ends_at": None, "user_buffer_minutes": None},
                ensure_ascii=False)
        if "p1.candidate.v1" in system:
            return json.dumps({
                "schema_version": "p1.candidate.v1", "decision_status": "proposed",
                "primary_candidate": {"candidate_ref": "primary", "steps": [{
                    "task_ref": "input-task-1", "planned_minutes": 30,
                    "execution_context": "free_window", "commitment_ref": None}],
                    "rationale": "先背单词", "assumptions": [], "warnings": []},
                "alternative_candidate": None, "safe_summary": "暂定建议"}, ensure_ascii=False)
        raise AssertionError("unexpected system prompt")

    runner = build_live_planner_runner(adapter_factory=lambda: TJUP1CallAdapter(fake_tju))
    view = runner("我在宿舍，90分钟后去教学楼上课，想背30分钟单词。", NOW)
    assert view.primary_card is not None
    assert view.primary_card.steps[0].title == "背单词"
    assert len(calls) == 3
    assert view.diagnostics == ()


def test_real_build_runner_callable_signature_failure_has_safe_internal_diagnostics():
    def wrong_signature(prompt):
        return "unreachable"

    runner = build_live_planner_runner(
        adapter_factory=lambda: TJUP1CallAdapter(wrong_signature))
    view = runner("我有90分钟，想背30分钟单词。", NOW)
    assert view.primary_card is None
    assert view.diagnostics
    assert any(item.error_category_label == "内部" for item in view.diagnostics)
    visible = " ".join(item.status_label for item in view.diagnostics)
    assert "Authorization" not in visible and "https://" not in visible


def test_blank_pending_value_does_not_call_runner():
    state = valid_state(make_view())
    state[PENDING_KEY] = {
        "action": "set_location", "value": None, "label": "填写地点",
        "target_ref": "task-1", "field_name": "location_text",
        "target_label": "取材料"}
    state[PENDING_INPUT_KEY] = "   "
    calls = []
    st = FakeSt(clicks={"p1_live_pending_apply"}, state=state)
    main(st=st, planner_runner=lambda text, ref: calls.append(text),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert calls == []
    assert state[ERROR_KEY] == "请先填写有效的补充内容。"


def test_real_fixture_provider_stays_synthetic_and_unknown_location_is_not_mapped():
    captured = {}

    class FakeAdapter(object):
        task_caller = staticmethod(lambda system, user: "")
        window_caller = staticmethod(lambda system, user: "")
        candidate_caller = staticmethod(lambda system, user: "")

    def planning(text, reference, task, window, candidate, provider):
        captured["provider"] = provider
        return "result"

    runner = build_live_planner_runner(
        adapter_factory=FakeAdapter,
        planning_function=planning,
        formatter=lambda result: result,
    )
    runner("不会调用模型", NOW)
    provider = captured["provider"]
    assert provider.route_data_trust is RouteDataTrust.SYNTHETIC_TEST
    assert provider.resolve_location("不存在的真实天津大学地点").status is LocationResolutionStatus.UNKNOWN


def test_page_and_cached_result_both_show_synthetic_route_warning():
    notice = NoticeView("当前使用演示路线数据，不代表真实北洋园步行时间。", "warning")
    st = FakeSt(state=valid_state(make_view(route_notice=notice)))
    main(st=st, now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert len(st.warnings) == 2
    assert "当前路线仍为演示数据" in st.warnings[0]
    assert "当前使用演示路线数据" in st.warnings[1]


def test_submit_label_is_start_without_result_and_refresh_with_cached_result():
    st = FakeSt(submitted=False, state=valid_state())
    main(st=st, runner_factory=lambda: lambda text, reference: make_view(),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert st.submit_labels == ["开始规划"]

    st = FakeSt(submitted=False, state=valid_state(make_view()))
    main(st=st, runner_factory=lambda: lambda text, reference: make_view(),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert st.submit_labels == ["刷新方案"]


def test_refresh_submit_calls_runner_once_with_current_input_and_context():
    state = valid_state(make_view())
    state[INTERACTION_KEY] = {
        "original_user_input": "旧输入",
        "confirmation": {"field_name": "estimated_total_minutes",
                         "target_label": "完成计组实验3",
                         "selected_value": "120"},
    }
    calls = []
    st = FakeSt(submitted=True, state=state)

    def runner(text, reference, interaction_context=None):
        calls.append((text, interaction_context))
        return make_view()

    main(st=st, planner_runner=runner, now_provider=lambda: NOW,
         configuration_loader=lambda: ())
    assert st.submit_labels == ["刷新方案"]
    assert len(calls) == 1
    assert calls[0][0] == "任意非空自然语言输入"
    assert calls[0][1] is state[INTERACTION_KEY]
    assert st.spinner_count == 1


def test_refresh_success_replaces_cached_view():
    old_view = make_view()
    new_view = make_view(route_notice=NoticeView("刷新后的路线提示", "warning"))
    st = FakeSt(submitted=True, state=valid_state(old_view))
    main(st=st, planner_runner=lambda text, reference: new_view,
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert st.session_state[VIEW_KEY] is new_view
    assert st.session_state[VIEW_KEY] is not old_view


def test_refresh_failure_never_masquerades_old_result_as_new():
    old_view = make_view()
    state = valid_state(old_view)
    calls = []
    st = FakeSt(submitted=True, state=state)

    def runner(text, reference):
        calls.append(text)
        raise RuntimeError("Authorization Bearer API_KEY https://invalid")

    main(st=st, planner_runner=runner, now_provider=lambda: NOW,
         configuration_loader=lambda: ())
    assert len(calls) == 1
    assert st.session_state[VIEW_KEY] is not old_view
    assert st.session_state[VIEW_KEY].primary_card is None
    assert st.session_state[ERROR_KEY] == PLANNING_ERROR
    assert "Bearer" not in st.session_state[ERROR_KEY]


def test_edited_input_without_submit_never_calls_runner():
    state = valid_state(make_view())
    state[INPUT_KEY] = "修改后的输入，但没有提交"
    calls = []
    st = FakeSt(submitted=False, state=state)
    main(st=st, runner_factory=lambda: calls.append("factory"),
         now_provider=lambda: NOW, configuration_loader=lambda: ())
    assert calls == [] and st.spinner_count == 0

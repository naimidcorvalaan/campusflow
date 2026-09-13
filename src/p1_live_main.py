"""CampusFlow P1 live-model experiment page; import has no network side effects."""
from contextlib import nullcontext
from datetime import datetime
import inspect
from pathlib import Path

from src.campus_map import load_campus_map
from src.p1_live_page_state import (
    ERROR_KEY,
    INTERACTION_KEY,
    INPUT_KEY,
    OPTION_KEY,
    PENDING_INPUT_KEY,
    PENDING_KEY,
    TIME_TEXT_KEY,
    VIEW_KEY,
    apply_deferred_input,
    cancel_pending,
    initialize,
    parse_reference_time,
    prepare_confirmation_application,
    reset,
    save_option,
)
from src.p1_dual_planning_pipeline import run_p1_dual_planning
from src.p1_result_formatter import format_p1_result
from src.p1_route_models import RouteDataTrust
from src.p1_route_provider import CampusMapRouteProvider
from src.p1_streamlit_renderer import render_view
from src.p1_tju_llm_adapter import TJUP1CallAdapter, load_project_configuration
from src.p1_view_models import DiagnosticView, P1ResultView


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_MAP_PATH = PROJECT_ROOT / "tests" / "fixtures" / "sample_campus_map.json"
PROGRESS_TEXT = "正在理解任务、整理时间窗口并验证方案，请稍候……"
TIME_ERROR = "假定当前时间格式无效，请使用 YYYY-MM-DD HH:MM。"
INPUT_ERROR = "请先输入你现在的情况和想完成的任务。"
PLANNING_ERROR = "规划暂时无法完成，请稍后重试。"


def _safe_live_failure_view():
    """Fallback for failures outside the dual pipeline, without exception details."""
    return P1ResultView(
        "暂时无法完成规划", PLANNING_ERROR, None, None, None, None, None,
        None, (), (), (), False,
        (DiagnosticView("最终展示", "内部处理失败", 1, 0, "内部"),), 0)


def build_live_planner_runner(
        adapter_factory=TJUP1CallAdapter,
        planning_function=None,
        formatter=format_p1_result,
        map_loader=load_campus_map,
        map_path=None):
    """Build the real-model runner with an explicitly synthetic route provider."""
    adapter = adapter_factory()
    campus_map = map_loader(SYNTHETIC_MAP_PATH if map_path is None else map_path)
    route_provider = CampusMapRouteProvider(campus_map, RouteDataTrust.SYNTHETIC_TEST)

    def runner(user_text, reference_datetime, interaction_context=None):
        if planning_function is not None:
            # Compatibility seam retained for existing isolated P1k tests.
            result = planning_function(
                user_text, reference_datetime, adapter.task_caller,
                adapter.window_caller, adapter.candidate_caller, route_provider)
            return formatter(result)
        result = run_p1_dual_planning(
            user_text, reference_datetime, adapter.task_caller,
            adapter.window_caller, adapter.candidate_caller, route_provider,
            agent_caller=adapter.agent_caller,
            interaction_context=interaction_context)
        return result.view

    return runner


def run_live_submission(state, submitted, configured, runner=None,
                        runner_factory=None, progress_context=None,
                        user_text_override=None, interaction_context=None):
    """The single submit boundary. It never retries or exposes exception details."""
    if not submitted:
        return False
    if not configured:
        state.pop(VIEW_KEY, None)
        return False
    user_text = (state.get(INPUT_KEY, "") if user_text_override is None
                 else user_text_override)
    if not isinstance(user_text, str) or not user_text.strip():
        state.pop(VIEW_KEY, None)
        state[ERROR_KEY] = INPUT_ERROR
        return False
    reference_datetime = parse_reference_time(state)
    if reference_datetime is None:
        state.pop(VIEW_KEY, None)
        state[ERROR_KEY] = TIME_ERROR
        return False
    try:
        selected_runner = runner
        if selected_runner is None:
            selected_runner = (build_live_planner_runner if runner_factory is None
                               else runner_factory)()
        context = nullcontext() if progress_context is None else progress_context()
        with context:
            parameters = inspect.signature(selected_runner).parameters
            supports_context = (
                "interaction_context" in parameters or
                any(item.kind is inspect.Parameter.VAR_KEYWORD
                    for item in parameters.values()))
            if interaction_context is None or not supports_context:
                view = selected_runner(user_text.strip(), reference_datetime)
            else:
                view = selected_runner(
                    user_text.strip(), reference_datetime,
                    interaction_context=interaction_context)
        state[VIEW_KEY] = view
        state.pop(ERROR_KEY, None)
        return True
    except Exception:
        state[VIEW_KEY] = _safe_live_failure_view()
        state[ERROR_KEY] = PLANNING_ERROR
        return False


def _request_rerun(st):
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()
        return
    fallback = getattr(st, "experimental_rerun", None)
    if callable(fallback):
        fallback()


def _pending_value_widget(st, pending):
    action = pending.get("action")
    selected_value = pending.get("value")
    if action == "adjust_duration":
        default = int(selected_value) if str(selected_value or "").isdigit() else 30
        return st.number_input(
            "新时长分钟数", min_value=1, value=default, step=1,
            key=PENDING_INPUT_KEY)
    if action in ("set_duration", "extend_time") and selected_value is None:
        return st.number_input(
            "新的可用时长（分钟）", min_value=1, value=30, step=1,
            key=PENDING_INPUT_KEY)
    if action in ("set_location", "update_location"):
        return st.text_input("地点", key=PENDING_INPUT_KEY)
    if action in ("change_task", "add_task"):
        return st.text_input("新的任务", key=PENDING_INPUT_KEY)
    if action == "set_location_requirement" and selected_value == "specific_location":
        return st.text_input("具体地点", key=PENDING_INPUT_KEY)
    return None


def _pending_description(pending):
    label = pending.get("target_label") or "任务"
    action = pending.get("action")
    value = pending.get("value")
    if action == "confirm_estimate":
        return "准备确认：“%s”的预计时长按 %s 分钟。" % (label, value)
    if action == "adjust_duration":
        return "准备调整：“%s”的预计时长。" % label
    if action in ("set_location", "update_location"):
        return "准备补充地点信息。"
    if action in ("change_task", "add_task"):
        return "准备补充新的任务。"
    if action == "set_location_requirement":
        return "准备确认：“%s”的地点要求。" % label
    if action in ("set_duration", "extend_time"):
        return "准备调整当前可用时间。"
    return "准备应用这项补充信息。"


def render_pending_confirmation(st, state, configured, runner=None,
                                runner_factory=None):
    pending = state.get(PENDING_KEY)
    if not isinstance(pending, dict):
        return False
    st.markdown("### 确认并重新规划")
    st.info(_pending_description(pending))
    supplied_value = _pending_value_widget(st, pending)
    if st.button("取消", key="p1_live_pending_cancel"):
        cancel_pending(state)
        return False
    if not st.button("应用并重新规划", key="p1_live_pending_apply"):
        return False
    if not configured:
        state[ERROR_KEY] = "当前缺少模型配置，暂时不能重新规划。"
        return False
    interaction = prepare_confirmation_application(state, supplied_value)
    if interaction is None:
        state[ERROR_KEY] = "请先填写有效的补充内容。"
        return False
    run_live_submission(
        state, True, True, runner=runner, runner_factory=runner_factory,
        progress_context=lambda: st.spinner(PROGRESS_TEXT),
        interaction_context=interaction)
    _request_rerun(st)
    return True


def main(st=None, planner_runner=None, runner_factory=None, now_provider=None,
         configuration_loader=None):
    if st is None:
        import streamlit as st
    now_provider = datetime.now if now_provider is None else now_provider
    configuration_loader = (load_project_configuration if configuration_loader is None
                            else configuration_loader)
    apply_deferred_input(st.session_state)
    initialize(st.session_state, now_provider().replace(second=0, microsecond=0))

    st.title("CampusFlow P1 真实模型实验页")
    st.warning("自然语言理解使用天津大学模型；当前路线仍为演示数据，不代表真实北洋园步行时间。")
    if st.button("开始新计划", key="p1_live_new"):
        reset(st.session_state, now_provider().replace(second=0, microsecond=0))
        _request_rerun(st)
        return

    missing = configuration_loader()
    with st.expander("更多设置"):
        st.write("默认预留 10 分钟。")
        st.text_input("假定当前时间（YYYY-MM-DD HH:MM）", key=TIME_TEXT_KEY)
    submit_label = "刷新方案" if VIEW_KEY in st.session_state else "开始规划"
    with st.form("p1_live_form"):
        st.text_area("现在想做什么？", key=INPUT_KEY)
        submitted = st.form_submit_button(submit_label)

    if missing:
        st.info("缺少配置：" + "、".join(missing))

    factory = build_live_planner_runner if runner_factory is None else runner_factory
    run_live_submission(
        st.session_state,
        submitted,
        not missing,
        runner=planner_runner,
        runner_factory=factory,
        progress_context=lambda: st.spinner(PROGRESS_TEXT),
        interaction_context=st.session_state.get(INTERACTION_KEY),
    )

    if ERROR_KEY in st.session_state:
        st.warning(st.session_state[ERROR_KEY])
    if VIEW_KEY in st.session_state:
        render_view(st, st.session_state[VIEW_KEY],
                    lambda option: save_option(st.session_state, option))
    if OPTION_KEY in st.session_state:
        st.info("已选择：" + st.session_state[OPTION_KEY]["label"])
    if render_pending_confirmation(
            st, st.session_state, not missing, runner=planner_runner,
            runner_factory=factory):
        return


if __name__ == "__main__":
    main()

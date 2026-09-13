"""CampusFlow 的 Streamlit 演示页面。"""

import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import streamlit as st

from src.campus_map import CampusMap, load_campus_map
from src.planning_pipeline import PlanningPipelineResult, plan_from_text
from src.result_formatter import format_planning_result, format_dynamic_planning_result
from src.models import PlanningRequest, Task
from src.extraction_models import ParseResult
from src.dynamic_planning_pipeline import DynamicPlanningPipelineResult, replan_from_text
from src.dynamic_replanner import DynamicReplanningResult
from src.route_plan import RoutePlanResult
from src.task_selector import TaskSelectionResult


DEMO_MAP_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "sample_campus_map.json"
)

CURRENT_REQUEST_KEY = "current_planning_request"
PLANNING_DISPLAY_KEY = "planning_display"
DYNAMIC_DISPLAY_KEY = "dynamic_display"
DYNAMIC_HISTORY_KEY = "dynamic_history"
INITIAL_TIME_INPUT_KEY = "initial_current_time"
RESET_INITIAL_TIME_KEY = "reset_initial_current_time"


def _system_time_text() -> str:
    return datetime.now().strftime("%H:%M")


def initialize_initial_time_state(state, time_provider=None) -> None:
    """初始化或按重置标志更新初始规划时间输入状态。"""
    provider = _system_time_text if time_provider is None else time_provider
    if state.get(RESET_INITIAL_TIME_KEY) is True:
        state.pop(RESET_INITIAL_TIME_KEY, None)
    elif INITIAL_TIME_INPUT_KEY in state:
        return

    value = provider()
    if not isinstance(value, str):
        raise TypeError("time_provider 必须返回字符串。")
    state[INITIAL_TIME_INPUT_KEY] = value


def build_effective_request_from_initial_result(result: PlanningPipelineResult) -> Optional[PlanningRequest]:
    if not isinstance(result, PlanningPipelineResult) or result.status not in ("ok", "infeasible"):
        return None
    source = getattr(result.parse_result, "planning_request", None)
    route = result.route_plan
    selection = result.task_selection_result
    if not isinstance(result.parse_result, ParseResult) or not isinstance(source, PlanningRequest) or not isinstance(route, RoutePlanResult):
        return None
    if selection is not None:
        if not isinstance(selection, TaskSelectionResult) or not isinstance(selection.kept_tasks, tuple):
            return None
        tasks = selection.kept_tasks
    else:
        if not isinstance(route.tasks, tuple):
            return None
        tasks = route.tasks
    if not all(isinstance(task, Task) for task in tasks):
        return None
    return PlanningRequest(source.current_location, source.destination, source.current_time, list(tasks))


def build_effective_request_from_dynamic_result(result: DynamicPlanningPipelineResult) -> Optional[PlanningRequest]:
    if not isinstance(result, DynamicPlanningPipelineResult) or result.status not in ("ok", "infeasible"):
        return None
    replanning = result.replanning_result
    if not isinstance(replanning, DynamicReplanningResult) or not isinstance(replanning.updated_request, PlanningRequest):
        return None
    source = replanning.updated_request
    selection = replanning.task_selection_result
    if not isinstance(selection, TaskSelectionResult) or not isinstance(selection.kept_tasks, tuple):
        return None
    tasks = selection.kept_tasks
    if not all(isinstance(task, Task) for task in tasks):
        return None
    return PlanningRequest(source.current_location, source.destination, source.current_time, list(tasks))


def build_dynamic_planning_display(campus_map, current_request, change_text, replanner=replan_from_text, formatter=format_dynamic_planning_result):
    if not isinstance(change_text, str) or not change_text.strip():
        return "请输入计划变化信息。", None
    result = replanner(campus_map, current_request, change_text.strip())
    display = formatter(result)
    request = build_effective_request_from_dynamic_result(result)
    if result.status in ("ok", "infeasible") and request is not None:
        return display, request
    return display, None


def save_initial_plan_state(state, result, display):
    request = build_effective_request_from_initial_result(result)
    if request is not None and isinstance(display, str):
        state[CURRENT_REQUEST_KEY] = request
        state[PLANNING_DISPLAY_KEY] = display
        state[DYNAMIC_DISPLAY_KEY] = None
        state[DYNAMIC_HISTORY_KEY] = []
    return request


def save_dynamic_plan_state(state, request, display, change_text):
    if isinstance(request, PlanningRequest):
        state[CURRENT_REQUEST_KEY] = request
    if not isinstance(display, str):
        return
    state[DYNAMIC_DISPLAY_KEY] = display
    history = state.get(DYNAMIC_HISTORY_KEY)
    if not isinstance(history, list):
        history = []
        state[DYNAMIC_HISTORY_KEY] = history
    cleaned = re.sub(r"\s+", " ", change_text).strip()[:200]
    history.append({"change_text": cleaned, "display": display})
    del history[:-10]


def clear_planning_state(state):
    for key in (CURRENT_REQUEST_KEY, PLANNING_DISPLAY_KEY, DYNAMIC_DISPLAY_KEY, DYNAMIC_HISTORY_KEY):
        state.pop(key, None)


def _format_summary_text(value: object) -> str:
    if not isinstance(value, str):
        return "未说明"
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return "未说明"
    if len(cleaned) > 200:
        return f"{cleaned[:199]}…"
    return cleaned


def format_current_plan_summary(planning_request: PlanningRequest) -> str:
    """将当前有效计划格式化为不暴露内部对象的安全摘要。"""
    invalid_message = "当前计划信息异常，无法展示。"
    if not isinstance(planning_request, PlanningRequest):
        return invalid_message
    if not isinstance(planning_request.tasks, list):
        return invalid_message
    if not all(isinstance(task, Task) for task in planning_request.tasks):
        return invalid_message

    lines = [
        f"当前地点：{_format_summary_text(planning_request.current_location)}",
        f"当前时间：{_format_summary_text(planning_request.current_time)}",
        f"最终目的地：{_format_summary_text(planning_request.destination)}",
    ]
    if not planning_request.tasks:
        lines.append("当前保留任务：无")
        return "\n".join(lines)

    lines.append("当前保留任务：")
    for index, task in enumerate(planning_request.tasks, start=1):
        if task.is_mandatory is True:
            attribute = "必须"
        elif task.is_mandatory is False:
            attribute = "可选"
        else:
            attribute = "任务属性异常"
        lines.append(
            f"{index}. 地点：{_format_summary_text(task.location)}；"
            f"描述：{_format_summary_text(task.description)}；属性：{attribute}"
        )
    return "\n".join(lines)


def validate_planning_inputs(
    user_input: object, current_time: object
) -> Optional[str]:
    """返回输入错误提示；输入有效时返回 None。"""
    if not isinstance(user_input, str) or not user_input.strip():
        return "请输入任务信息后再开始规划。"
    if not isinstance(current_time, str) or not current_time.strip():
        return "请输入当前时间，格式为 HH:MM。"
    if re.fullmatch(r"[0-9]{2}:[0-9]{2}", current_time.strip()) is None:
        return "当前时间格式不正确，请使用 HH:MM。"

    hours, minutes = (int(part) for part in current_time.strip().split(":"))
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        return "当前时间格式不正确，请使用有效的 HH:MM。"
    return None


def load_demo_campus_map() -> CampusMap:
    """加载 P0 阶段专用的虚构测试地图。"""
    return load_campus_map(DEMO_MAP_PATH)


def run_planning_request(
    campus_map: CampusMap,
    user_input: str,
    current_time: str,
    planner: Callable[
        [CampusMap, str, str], PlanningPipelineResult
    ] = plan_from_text,
    formatter: Callable[[PlanningPipelineResult], str] = format_planning_result,
) -> str:
    """执行规划并返回安全的中文展示文本，依赖可在测试中替换。"""
    result = planner(campus_map, user_input, current_time)
    return formatter(result)


def build_planning_display(
    user_input: object,
    current_time: object,
    map_loader: Callable[[], CampusMap] = load_demo_campus_map,
    planner: Callable[
        [CampusMap, str, str], PlanningPipelineResult
    ] = plan_from_text,
    formatter: Callable[[PlanningPipelineResult], str] = format_planning_result,
) -> str:
    """校验输入、加载测试地图并生成可安全展示的中文文本。"""
    validation_error = validate_planning_inputs(user_input, current_time)
    if validation_error is not None:
        return validation_error

    try:
        campus_map = map_loader()
    except Exception:
        return "测试地图加载失败，请稍后重试。"

    try:
        return run_planning_request(
            campus_map,
            user_input.strip(),
            current_time.strip(),
            planner=planner,
            formatter=formatter,
        )
    except Exception:
        return "规划过程中发生错误，请检查输入信息后重试。"


def main() -> None:
    st.set_page_config(page_title="CampusFlow", layout="wide")
    st.title("CampusFlow")
    st.info("当前P0版本使用虚构测试地图；北洋园真实地点尚未采集步行时间，不能用于真实路线规划。")
    initialize_initial_time_state(st.session_state)
    with st.form("initial_plan_form"):
        user_input = st.text_area("自然语言任务")
        current_time = st.text_input("当前时间（HH:MM）", key=INITIAL_TIME_INPUT_KEY)
        submitted = st.form_submit_button("开始规划")
    if submitted:
        try:
            validation = validate_planning_inputs(user_input, current_time)
            if validation:
                st.text(validation)
                return
            result = plan_from_text(load_demo_campus_map(), user_input.strip(), current_time.strip())
            display = format_planning_result(result)
            st.session_state[PLANNING_DISPLAY_KEY] = display
            st.text(display)
            save_initial_plan_state(st.session_state, result, display)
        except Exception:
            st.text("规划过程中发生错误，请稍后重试。")
    if CURRENT_REQUEST_KEY in st.session_state:
        request = st.session_state[CURRENT_REQUEST_KEY]
        if isinstance(request, PlanningRequest):
            st.header("动态调整当前计划")
            st.text(format_current_plan_summary(request))
            with st.form("dynamic_plan_form"):
                change_text = st.text_area("描述计划变化", key="dynamic_change")
                dynamic_submitted = st.form_submit_button("重新规划")
            if dynamic_submitted:
                try:
                    display, updated = build_dynamic_planning_display(
                        load_demo_campus_map(), request, change_text
                    )
                    st.text(display)
                    if updated is not None:
                        save_dynamic_plan_state(
                            st.session_state, updated, display, change_text
                        )
                except Exception:
                    st.text("规划过程中发生错误，请稍后重试。")
            if st.button("开始新计划", key="clear_plan_button"):
                clear_planning_state(st.session_state)
                st.session_state[RESET_INITIAL_TIME_KEY] = True
                if hasattr(st, "rerun"):
                    st.rerun()
                else:
                    st.experimental_rerun()
                return


if __name__ == "__main__":
    main()

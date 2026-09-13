"""P2e 反馈路由（Python 3.8 兼容）。

轻量 LLM Router：判断一条用户反馈属于任务侧、固定安排侧、两者还是无法判断，
再由 P2SessionController 只调用相关 reconciler，避免“任务反馈被固定安排 Agent
当成新安排”或“组会反馈被任务 Agent 当成任务”的串台。

- 模型只输出 route，不执行任何修改；
- 程序校验 route 取值并最多做 1 次格式 repair；
- 全部失败时保守回退为 both（不丢用户事实），并返回 warning。
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_agentic_prompt_builder import build_repair_prompt
from src.p2_models import DayPlanningState

ROUTER_SCHEMA_VERSION = "p2.feedback-router.v1"

ROUTE_TASK = "task"
ROUTE_COMMITMENT = "commitment"
ROUTE_BOTH = "both"
ROUTE_UNKNOWN = "unclear"
ALLOWED_ROUTES = (ROUTE_TASK, ROUTE_COMMITMENT, ROUTE_BOTH, ROUTE_UNKNOWN)

MAX_ROUTER_HISTORY_LINES = 5
WARNING_ROUTER_FAILED = "本轮反馈路由判断暂未完成，已按任务与固定安排同时处理兜底。"


@dataclass(frozen=True)
class FeedbackRoute:
    """Router 的结构化输出；只描述反馈属于哪一侧，不包含任何修改。"""

    schema_version: str
    route: str
    reason: Optional[str]


def build_router_prompt(state: DayPlanningState, user_text: str) -> Tuple[str, str]:
    """构建 Router prompt：只给标题级信息，不做任何状态修改。"""
    system = (
        "你是 CampusFlow 的“反馈路由（feedback router）”助手。\n"
        "判断用户最新一句话属于哪一类，只输出路由 JSON，不执行任何修改。\n"
        "规则：\n"
        "1. 任务反馈（进度、跳过、放弃、完成、新增任务、任务时长）→ task。\n"
        "2. 固定安排反馈（上课/组会/会议等开始结束时间变化、新增、取消）→ commitment。\n"
        "3. 同时涉及任务与固定安排 → both。\n"
        "4. 无法判断 → unclear。\n"
        "不要为了保险而改成 both：能明确区分就区分。\n"
        "schema：" + ROUTER_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"p2.feedback-router.v1\", "
        "\"route\": \"task|commitment|both|unclear\", \"reason\": string|null}\n"
        "只输出 JSON。"
    )
    task_titles = "、".join(task.title for task in state.tasks) if state.tasks else "（无）"
    commitment_titles = (
        "、".join(
            "{}（{} - {}）".format(
                c.title, c.starts_at.strftime("%H:%M"), c.ends_at.strftime("%H:%M")
            )
            for c in state.commitments
        )
        if state.commitments
        else "（无）"
    )
    history_lines = state.history[-MAX_ROUTER_HISTORY_LINES:]
    history_text = "\n".join(history_lines) if history_lines else "（无）"
    user = (
        "当前任务：{task_titles}\n"
        "当前固定安排：{commitment_titles}\n\n"
        "最近历史：\n{history}\n\n"
        "用户最新输入：\n{user_text}\n\n"
        "只输出符合 schema 的 JSON 对象。"
    ).format(
        task_titles=task_titles,
        commitment_titles=commitment_titles,
        history=history_text,
        user_text=user_text,
    )
    return system, user


def parse_router_output(text: str) -> FeedbackRoute:
    """解析 Router 输出；route 取值非法或缺失时抛出 AgenticParseError。"""
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("输出必须是 JSON 对象")
    if payload.get("schema_version") != ROUTER_SCHEMA_VERSION:
        raise AgenticParseError("schema_version 不匹配")
    route = payload.get("route")
    if not isinstance(route, str) or route not in ALLOWED_ROUTES:
        raise AgenticParseError("route 必须为 task|commitment|both|unclear")
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise AgenticParseError("reason 必须是字符串或 null")
    if reason is not None and len(reason) > 400:
        raise AgenticParseError("reason 过长")
    return FeedbackRoute(schema_version=ROUTER_SCHEMA_VERSION, route=route, reason=reason)


def route_feedback(
    user_text: str,
    state: DayPlanningState,
    caller,
    repair_caller=None,
) -> Tuple[str, Optional[str]]:
    """执行一轮 Router：返回 (route, warning)。

    - 正常：1 次调用，warning=None；
    - 格式失败：最多 1 次 repair，仍失败则回退 both + warning（不丢用户事实）。
    """
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not callable(caller):
        raise TypeError("caller must be callable")
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_router_prompt(state, user_text)
    text = caller(system, user)
    result = _parse(text)
    if result is not None:
        return result.route, None
    repair_system, repair_user = build_repair_prompt(
        "feedback_router", text, ROUTER_SCHEMA_VERSION
    )
    repaired = repair(repair_system, repair_user)
    result = _parse(repaired)
    if result is not None:
        return result.route, None
    return ROUTE_BOTH, WARNING_ROUTER_FAILED


def _parse(text: Optional[str]) -> Optional[FeedbackRoute]:
    if text is None:
        return None
    try:
        return parse_router_output(text)
    except AgenticParseError:
        return None

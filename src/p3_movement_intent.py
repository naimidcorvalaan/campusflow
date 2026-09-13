"""P3e 移动意图解析（Python 3.8 兼容，全部 mock 可测）。

由 TJU Qwen 主导自然语言移动语义（起点/终点/交通方式/方式修改），
程序只做结构化校验：action 枚举、必填字段、walk/bike 校验、长度边界。
模型输出任何非法内容都会被拒绝并最多做一次格式 repair；全部失败时
视为“无移动意图”（不修改任何状态，避免模型输出破坏状态一致性）。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_agentic_prompt_builder import build_repair_prompt
from src.p3_map_schema import parse_transport_mode

MOVEMENT_INTENT_SCHEMA_VERSION = "p3.movement-intent.v1"

MAX_LOCATION_TEXT_LENGTH = 80
MAX_REASON_LENGTH = 200

WARNING_INTENT_FAILED = "移动意图判断暂未完成，本轮未修改地点或交通方式。"

AgentCaller = Callable[[str, str], str]


class MovementIntentAction(str, Enum):
    NONE = "none"
    ADD = "add"
    CHANGE_MODE = "change_mode"
    CHANGE_ORIGIN = "change_origin"
    CHANGE_DESTINATION = "change_destination"


_ALLOWED_ACTIONS = {action.value for action in MovementIntentAction}


@dataclass(frozen=True)
class MovementIntent:
    """一条用户反馈中的移动意图（程序校验后的只读结果）。

    模型不能直接写状态：origin/destination 只是自然语言原话，
    必须由 P3 地点解析 + 真实寻路 + 程序扣容量后才进入计划。
    """

    schema_version: str
    has_movement: bool
    action: str
    origin_text: Optional[str]
    destination_text: Optional[str]
    mode: Optional[str]
    reason: Optional[str]

    def __post_init__(self):
        if self.schema_version != MOVEMENT_INTENT_SCHEMA_VERSION:
            raise ValueError("movement intent schema_version 不匹配")
        if not isinstance(self.has_movement, bool):
            raise ValueError("has_movement 必须是布尔值")
        if self.action not in _ALLOWED_ACTIONS:
            raise ValueError("action 必须是 add|change_mode|change_origin|change_destination|none")
        if not self.has_movement and self.action != MovementIntentAction.NONE.value:
            raise ValueError("has_movement=false 时 action 必须为 none")
        if self.has_movement and self.action == MovementIntentAction.NONE.value:
            raise ValueError("has_movement=true 时 action 不能为 none")
        for name, value in (("origin_text", self.origin_text), ("destination_text", self.destination_text)):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("{} 必须是非空字符串".format(name))
                if len(value) > MAX_LOCATION_TEXT_LENGTH:
                    raise ValueError("{} 过长".format(name))
        if self.mode is not None:
            parse_transport_mode(self.mode)  # 拒绝非法交通方式与 bool
        if self.action == MovementIntentAction.ADD.value:
            if not self.origin_text or not self.destination_text:
                raise ValueError("add 必须同时给出起点与终点")
        if self.action == MovementIntentAction.CHANGE_MODE.value:
            if self.mode is None:
                raise ValueError("change_mode 必须给出 mode")
        if self.action == MovementIntentAction.CHANGE_ORIGIN.value:
            if not self.origin_text:
                raise ValueError("change_origin 必须给出 origin_text")
        if self.action == MovementIntentAction.CHANGE_DESTINATION.value:
            if not self.destination_text:
                raise ValueError("change_destination 必须给出 destination_text")
        if self.reason is not None:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("reason 必须是字符串或 null")
            if len(self.reason) > MAX_REASON_LENGTH:
                raise ValueError("reason 过长")


def build_movement_intent_prompt(user_text: str) -> Tuple[str, str]:
    """构造移动意图 prompt：只含 schema 规则与示例，不发送任何内部 ref / enum。"""
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    system = (
        "你是 CampusFlow 的“移动意图”解析器。用户会用自然语言描述校园内移动需求。\n"
        "你只输出结构化 JSON，不执行任何修改；地点只填写用户原话片段，不要编造地点。\n"
        "规则：\n"
        "1. 只有真正涉及“从某地到某地 / 交通方式 / 方式修改”时才 has_movement=true。\n"
        "2. action 取值：\n"
        "   - add：新增一次移动（必须给出起点与终点）；\n"
        "   - change_mode：修改最近一次移动的交通方式（给出 mode，不填地点）；\n"
        "   - change_origin / change_destination：修改最近一次移动的起点/终点；\n"
        "   - none：不是移动反馈。\n"
        "3. 地点用用户原话（如“9斋”“31教”“北菜”“图书馆”），不要改写或补全。\n"
        "4. 交通方式 mode：walk（步行/走路）或 bike（骑车/骑行/自行车）；未提及时为 null。\n"
        "5. “我现在在9斋，下午3点去31教，我骑车” → add，起点“9斋”，终点“31教”，mode=bike。\n"
        "6. “我在北菜，等会去图书馆” → add，起点“北菜”，终点“图书馆”，mode=null。\n"
        "7. “改成走路吧” → change_mode，mode=walk，起点终点都填 null。\n"
        "8. “今天先不背单词了” → has_movement=false，action=none。\n"
        "输出对象：{\"schema_version\": \"p3.movement-intent.v1\", \"has_movement\": bool, "
        "\"action\": \"add|change_mode|change_origin|change_destination|none\", "
        "\"origin_text\": string|null, \"destination_text\": string|null, "
        "\"mode\": \"walk\"|\"bike\"|null, \"reason\": string|null}\n"
        "只输出 JSON。"
    )
    return system, "用户反馈：\n" + user_text


def parse_movement_intent(text: str) -> MovementIntent:
    """解析模型输出；结构/取值非法抛 AgenticParseError。"""
    if not isinstance(text, str) or not text.strip():
        raise AgenticParseError("movement intent 输出为空")
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("输出必须是 JSON 对象")
    if payload.get("schema_version") != MOVEMENT_INTENT_SCHEMA_VERSION:
        raise AgenticParseError("schema_version 不匹配")
    has_movement = payload.get("has_movement")
    if not isinstance(has_movement, bool):
        raise AgenticParseError("has_movement 必须是布尔值")
    action = payload.get("action")
    if not isinstance(action, str):
        raise AgenticParseError("action 必须是字符串")
    origin = payload.get("origin_text")
    destination = payload.get("destination_text")
    mode = payload.get("mode")
    reason = payload.get("reason")
    for name, value in (
        ("origin_text", origin),
        ("destination_text", destination),
        ("reason", reason),
    ):
        if value is not None and not isinstance(value, str):
            raise AgenticParseError("{} 必须是字符串或 null".format(name))
    if mode is not None and not isinstance(mode, str):
        raise AgenticParseError("mode 必须是字符串或 null")
    try:
        return MovementIntent(
            schema_version=MOVEMENT_INTENT_SCHEMA_VERSION,
            has_movement=has_movement,
            action=action,
            origin_text=origin,
            destination_text=destination,
            mode=mode,
            reason=reason,
        )
    except (TypeError, ValueError) as exc:
        raise AgenticParseError(str(exc)) from None


def detect_movement_intent(
    user_text: str,
    caller: AgentCaller,
    repair_caller: Optional[AgentCaller] = None,
) -> Tuple[MovementIntent, Optional[str]]:
    """执行一轮移动意图判断：1 次调用 + 最多 1 次 repair；全部失败视为无移动意图。

    返回 (intent, warning)。warning 非 None 表示判断未完成（不修改任何状态）。
    """
    if not callable(caller):
        raise TypeError("caller must be callable")
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_movement_intent_prompt(user_text)
    try:
        text = caller(system, user)
        return parse_movement_intent(text), None
    except AgenticParseError:
        pass
    repair_system, repair_user = build_repair_prompt(
        "movement_intent", "", MOVEMENT_INTENT_SCHEMA_VERSION
    )
    try:
        repaired = repair(repair_system, repair_user)
        return parse_movement_intent(repaired), None
    except AgenticParseError:
        return (
            MovementIntent(
                schema_version=MOVEMENT_INTENT_SCHEMA_VERSION,
                has_movement=False,
                action=MovementIntentAction.NONE.value,
                origin_text=None,
                destination_text=None,
                mode=None,
                reason=None,
            ),
            WARNING_INTENT_FAILED,
        )

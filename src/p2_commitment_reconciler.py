"""P2d 固定安排反馈 reconcile（Python 3.8 兼容）。

输入：已有 DayPlanningState + 用户关于固定安排的自然语言反馈。
由 mock Agent 提议 stable commitment_ref，程序严格验证并应用：
- delay：结束时间 +delay_minutes（仅剩余当天计划，已结束/ends 未知不接受）；
- add：新增固定安排（时间由程序解析，start < end，默认 UNAVAILABLE）；
- cancel：从当天剩余 commitments 中移除；
- update_time：更新已有安排时间（保持 start < end）。
应用后：commitments -> 重新 derive DayWindow -> 保留 task ledger / progress / lifecycle。
identity ambiguous 时返回 questions，不修改任何 commitment。
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping, Optional, Sequence, Tuple

from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_models import DayPlanningState
from src.p2_window_derivation import derive_day_state

COMMITMENT_SCHEMA_VERSION = "p2.commitment-reconciliation.v1"
COMMITMENT_REF_PREFIX = "day_commitment_"
DEFAULT_SAFETY_BUFFER_MINUTES = 10
_MAX_TITLE_LENGTH = 100
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class CommitmentAction(str, Enum):
    DELAY = "delay"
    ADD = "add"
    CANCEL = "cancel"
    UPDATE_TIME = "update_time"
    UPDATE_LOCATION = "update_location"


@dataclass(frozen=True)
class CommitmentUpdate:
    """Agent 对固定安排的单条建议（时间以 "HH:MM" 字符串给出，程序解析）。"""

    target_commitment_ref: Optional[str]
    action: CommitmentAction
    title: Optional[str]
    starts_at: Optional[str]
    ends_at: Optional[str]
    delay_minutes: Optional[int]
    class_arrival_lead_minutes: Optional[int] = None
    location_text: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.action, CommitmentAction):
            raise ValueError("action must be a CommitmentAction")
        if self.action == CommitmentAction.UPDATE_LOCATION:
            _require_text("target_commitment_ref", self.target_commitment_ref)
            _require_text("location_text", self.location_text)
            if self.title is not None:
                _require_text("title", self.title)
            if any(value is not None for value in (self.starts_at, self.ends_at,
                                                   self.delay_minutes, self.class_arrival_lead_minutes)):
                raise ValueError("update_location 不允许修改时间或提前量")
            return
        if self.location_text is not None:
            raise ValueError("location_text 仅用于 update_location")
        if self.action == CommitmentAction.ADD:
            if self.target_commitment_ref is not None:
                raise ValueError("add 不允许指定 target_commitment_ref")
            _require_text("title", self.title)
            if self.delay_minutes is not None:
                raise ValueError("add 不允许 delay_minutes")
            _require_time("starts_at", self.starts_at)
            _require_time("ends_at", self.ends_at)
        else:
            _require_text("target_commitment_ref", self.target_commitment_ref)
            if self.action == CommitmentAction.DELAY:
                if self.title is not None or self.starts_at is not None or self.ends_at is not None or self.class_arrival_lead_minutes is not None:
                    raise ValueError("delay 只允许 target_commitment_ref + delay_minutes")
                _require_positive_int("delay_minutes", self.delay_minutes)
            elif self.action == CommitmentAction.CANCEL:
                if (
                    self.title is not None
                    or self.starts_at is not None
                    or self.ends_at is not None
                    or self.delay_minutes is not None
                    or self.class_arrival_lead_minutes is not None
                ):
                    raise ValueError("cancel 只允许 target_commitment_ref")
            else:  # UPDATE_TIME
                if self.title is not None or self.delay_minutes is not None:
                    raise ValueError("update_time 不允许 title / delay_minutes")
                if self.starts_at is None and self.ends_at is None and self.class_arrival_lead_minutes is None:
                    raise ValueError("update_time 至少提供一个时间或课程提前量")
                if self.starts_at is not None:
                    _require_time("starts_at", self.starts_at)
                if self.ends_at is not None:
                    _require_time("ends_at", self.ends_at)
                if self.class_arrival_lead_minutes is not None and not (0 <= self.class_arrival_lead_minutes <= 60):
                    raise ValueError("class_arrival_lead_minutes must be between 0 and 60")


@dataclass(frozen=True)
class CommitmentReconciliationResult:
    schema_version: str
    updates: Tuple[CommitmentUpdate, ...]
    questions: Tuple[str, ...]

    def __post_init__(self):
        if self.schema_version != COMMITMENT_SCHEMA_VERSION:
            raise ValueError("commitment schema_version mismatch")
        for update in self.updates:
            if not isinstance(update, CommitmentUpdate):
                raise TypeError("updates must contain CommitmentUpdate instances")
        for question in self.questions:
            _require_text("question", question)


@dataclass(frozen=True)
class CommitmentApplied:
    """Commitment 反馈应用后的纯输出；state 已重新派生窗口（无变化时为原 state）。"""

    state: DayPlanningState
    applied_entries: Tuple[str, ...]
    warnings: Tuple[str, ...]
    questions: Tuple[str, ...]
    new_commitment_refs: Tuple[str, ...]


def parse_commitment_reconciliation(text: str) -> CommitmentReconciliationResult:
    """解析 Commitment Agent 输出（复用 P2c 的 JSON 提取器）。"""
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("输出必须是 JSON 对象")
    if payload.get("schema_version") != COMMITMENT_SCHEMA_VERSION:
        raise AgenticParseError("schema_version 不匹配")
    updates = payload.get("updates")
    if updates is None:
        updates = []
    if not isinstance(updates, list):
        raise AgenticParseError("updates 必须是数组")
    questions = payload.get("questions")
    if questions is None:
        questions = []
    if not isinstance(questions, list):
        raise AgenticParseError("questions 必须是数组")
    parsed_updates = tuple(_parse_update(item) for item in updates)
    _reject_duplicate_targets(parsed_updates)
    parsed_questions = tuple(_parse_question(item) for item in questions)
    return CommitmentReconciliationResult(
        schema_version=COMMITMENT_SCHEMA_VERSION,
        updates=parsed_updates,
        questions=parsed_questions,
    )


def build_commitment_reconciliation_prompt(state: DayPlanningState, user_text: str) -> Tuple[str, str]:
    """构建 Commitment Agent prompt（静态示例在 system，当前数据在 user）。"""
    system = _commitment_system_prompt()
    user = (
        "当前时间：{now}\n"
        "day_end：{day_end}\n\n"
        "今日固定安排：\n{commitments}\n\n"
        "用户最新输入：\n{user_text}\n\n"
        "只输出一个符合 schema 的 JSON 对象。"
    ).format(
        now=state.now.strftime("%Y-%m-%d %H:%M"),
        day_end=state.day_end.strftime("%H:%M"),
        commitments=format_commitments(state.commitments),
        user_text=user_text,
    )
    return system, user


def format_commitments(commitments: Sequence[FixedCommitment]) -> str:
    from src.p3_class_prep import class_arrival_lead_minutes
    lines = []
    for commitment in commitments:
        lines.append(
            "- ref={} | 标题={} | {} - {} | availability={} | kind={} | class_arrival_lead_minutes={} | location={}".format(
                commitment.commitment_ref,
                commitment.title,
                _fmt_time(commitment.starts_at),
                _fmt_time(commitment.ends_at),
                commitment.availability_during.value,
                commitment.commitment_kind,
                class_arrival_lead_minutes(commitment),
                commitment.location_text or "unknown",
            )
        )
    return "\n".join(lines) if lines else "（无）"


def apply_commitment_reconciliation(
    state: DayPlanningState,
    result: CommitmentReconciliationResult,
    default_safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    travel_minutes_by_commitment: Optional[Mapping[str, int]] = None,
    history: Optional[Tuple[str, ...]] = None,
) -> CommitmentApplied:
    """按顺序应用 Commitment updates，然后重新派生全天窗口。

    questions 非空时不应用任何 updates（identity 不确定则不改 commitments）。
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(result, CommitmentReconciliationResult):
        raise TypeError("result must be a CommitmentReconciliationResult")
    if result.questions:
        return CommitmentApplied(
            state=state, applied_entries=(), warnings=(), questions=result.questions, new_commitment_refs=()
        )

    commitments = list(state.commitments)
    by_ref = {commitment.commitment_ref: commitment for commitment in commitments}
    applied_entries = []
    warnings = []
    new_refs = []
    next_number = _next_ref_number(tuple(by_ref.keys()))

    for update in result.updates:
        if update.action == CommitmentAction.ADD:
            ref = _format_ref(next_number)
            next_number += 1
            starts_at = _parse_time(update.starts_at, state.now)
            ends_at = _parse_time(update.ends_at, state.now)
            if starts_at is None or ends_at is None or not (starts_at < ends_at):
                warnings.append(
                    "新增安排“{}”时间不合法（start >= end 或无法解析），已忽略".format(update.title)
                )
                continue
            new_commitment = FixedCommitment(
                commitment_ref=ref,
                title=update.title,
                original_text=update.title,
                starts_at=starts_at,
                ends_at=ends_at,
                location_text=None,
                availability_during=AvailabilityLevel.UNAVAILABLE,
                field_evidence={},
                needs_confirmation=(),
            )
            commitments.append(new_commitment)
            by_ref[ref] = new_commitment
            new_refs.append(ref)
            applied_entries.append(
                "新增：{}（{} - {}）".format(
                    update.title, starts_at.strftime("%H:%M"), ends_at.strftime("%H:%M")
                )
            )
            continue

        target = by_ref.get(update.target_commitment_ref)
        if target is None:
            warnings.append(
                "固定安排反馈引用了未知 commitment_ref：{}，已忽略该条更新".format(
                    update.target_commitment_ref
                )
            )
            continue

        if update.action == CommitmentAction.DELAY:
            if target.ends_at is None:
                warnings.append("安排“{}”结束时间未知，无法推迟".format(target.title))
                continue
            if target.ends_at <= state.now:
                warnings.append("安排“{}”已结束，无法推迟".format(target.title))
                continue
            updated = _replace_commitment(target, ends_at=target.ends_at + timedelta(minutes=update.delay_minutes))
            _swap(commitments, by_ref, target, updated)
            applied_entries.append(
                "推迟：{} 结束时间延后{}分钟".format(target.title, update.delay_minutes)
            )
        elif update.action == CommitmentAction.CANCEL:
            commitments.remove(target)
            by_ref.pop(target.commitment_ref, None)
            applied_entries.append("取消：{}".format(target.title))
        elif update.action == CommitmentAction.UPDATE_LOCATION:
            title = update.title if update.title is not None else target.title
            # A title is a display cache, not a second venue authority. Remove
            # copies of the exact structured old/new venue values; this does
            # not resolve aliases or interpret words in any language.
            for venue in (target.location_text, update.location_text):
                if venue and venue in title:
                    remainder = title.replace(venue, '').strip(' ·-—|,，:：@()（）')
                    if remainder:
                        title = remainder
            updated = replace(target, location_text=update.location_text,
                              title=title)
            _swap(commitments, by_ref, target, updated)
            applied_entries.append("地点更新：{} → {}".format(target.title, update.location_text))
        elif update.action == CommitmentAction.UPDATE_TIME:
            if update.class_arrival_lead_minutes is not None and getattr(target, "commitment_kind", None) != "class":
                warnings.append("“{}”不是课程，无法设置提前到楼时间".format(target.title))
                continue
            starts_at = target.starts_at
            ends_at = target.ends_at
            if update.starts_at is not None and update.ends_at is not None:
                starts_at = _parse_time(update.starts_at, state.now)
                ends_at = _parse_time(update.ends_at, state.now)
            elif update.starts_at is not None:
                new_starts = _parse_time(update.starts_at, state.now)
                if new_starts is not None:
                    # 只有原 commitment 同时已知 starts_at 与 ends_at、可确定原时长时，
                    # 修改开始时间才按原时长联动移动 ends_at；partial commitment
                    # 只修改已有的开始时间，结束时间保持 None（不对 None 做 timedelta）。
                    if starts_at is not None and ends_at is not None:
                        ends_at = ends_at + (new_starts - starts_at)
                    starts_at = new_starts
            elif update.ends_at is not None:
                new_ends = _parse_time(update.ends_at, state.now)
                if new_ends is not None:
                    if starts_at is not None and ends_at is not None:
                        starts_at = starts_at + (new_ends - ends_at)
                    ends_at = new_ends
            if starts_at is None or (ends_at is not None and not (starts_at < ends_at)):
                warnings.append("安排“{}”时间更新不合法（start >= end 或无法解析），已忽略".format(target.title))
                continue
            updated = _replace_commitment(target, starts_at=starts_at, ends_at=ends_at,
                                          class_arrival_lead_minutes=update.class_arrival_lead_minutes)
            _swap(commitments, by_ref, target, updated)
            applied_entries.append(
                "改时间：{}（{} - {}）".format(
                    target.title, _fmt_time(starts_at), _fmt_time(ends_at)
                )
            )
            if update.class_arrival_lead_minutes is not None:
                applied_entries.append("课程提前到楼：{} {}分钟".format(target.title, update.class_arrival_lead_minutes))

    if not applied_entries:
        return CommitmentApplied(
            state=state, applied_entries=(), warnings=tuple(warnings), questions=(), new_commitment_refs=()
        )

    new_history = history if history is not None else state.history
    updated_state = derive_day_state(
        state.now,
        state.day_end,
        tuple(commitments),
        state.tasks,
        default_safety_buffer_minutes,
        travel_minutes_by_commitment if travel_minutes_by_commitment is not None else {},
        new_history,
        state.reference_datetime,
    )
    return CommitmentApplied(
        state=updated_state,
        applied_entries=tuple(applied_entries),
        warnings=tuple(warnings),
        questions=(),
        new_commitment_refs=tuple(new_refs),
    )


def _parse_update(item) -> CommitmentUpdate:
    if not isinstance(item, dict):
        raise AgenticParseError("updates 中的每一项必须是对象")
    action_value = item.get("action")
    if not isinstance(action_value, str) or action_value not in {a.value for a in CommitmentAction}:
        raise AgenticParseError("action 非法")
    action = CommitmentAction(action_value)
    target = _optional_text(item.get("target_commitment_ref"), "target_commitment_ref")
    title = _optional_text(item.get("title"), "title", _MAX_TITLE_LENGTH)
    starts_at = _optional_text(item.get("starts_at"), "starts_at")
    ends_at = _optional_text(item.get("ends_at"), "ends_at")
    delay = _optional_positive_int(item.get("delay_minutes"), "delay_minutes")
    lead = _optional_nonnegative_int(item.get("class_arrival_lead_minutes"), "class_arrival_lead_minutes", 60)
    try:
        return CommitmentUpdate(
        target_commitment_ref=target,
        action=action,
        title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            delay_minutes=delay,
            class_arrival_lead_minutes=lead,
            location_text=_optional_text(item.get("location_text"), "location_text"),
        )
    except ValueError as exc:
        raise AgenticParseError(str(exc))


def _reject_duplicate_targets(updates: Tuple[CommitmentUpdate, ...]) -> None:
    seen = set()
    for update in updates:
        if update.target_commitment_ref is None:
            continue
        if update.target_commitment_ref in seen:
            raise AgenticParseError(
                "updates 中同一个 target_commitment_ref 出现多次：{}".format(
                    update.target_commitment_ref
                )
            )
        seen.add(update.target_commitment_ref)


def _parse_question(item) -> str:
    if not isinstance(item, str) or not item.strip():
        raise AgenticParseError("questions 中的每一项必须是非空字符串")
    return item.strip()


def _optional_text(value, name, max_len=None):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} 必须是非空字符串".format(name))
    stripped = value.strip()
    if max_len is not None and len(stripped) > max_len:
        stripped = stripped[:max_len]
    return stripped


def _optional_positive_int(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgenticParseError("{} 必须是整数".format(name))
    if value <= 0:
        raise AgenticParseError("{} 必须 > 0".format(name))
    return value


def _optional_nonnegative_int(value, name, maximum):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise AgenticParseError("{} 必须是 0-{} 的整数".format(name, maximum))
    return value


def _require_text(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(name))


def _require_time(name, value):
    if not isinstance(value, str) or not _TIME_RE.match(value.strip()):
        raise ValueError("{} must be a valid HH:MM time".format(name))


def _require_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer".format(name))
    if value <= 0:
        raise ValueError("{} must be > 0".format(name))


def _parse_time(text: Optional[str], now: datetime) -> Optional[datetime]:
    if text is None or not isinstance(text, str):
        return None
    match = _TIME_RE.match(text.strip())
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _replace_commitment(commitment, starts_at=None, ends_at=None, class_arrival_lead_minutes=None):
    return FixedCommitment(
        commitment_ref=commitment.commitment_ref,
        title=commitment.title,
        original_text=commitment.original_text,
        starts_at=starts_at if starts_at is not None else commitment.starts_at,
        ends_at=ends_at if ends_at is not None else commitment.ends_at,
        location_text=commitment.location_text,
        availability_during=commitment.availability_during,
        field_evidence=commitment.field_evidence,
        needs_confirmation=commitment.needs_confirmation,
        commitment_kind=getattr(commitment, "commitment_kind", None),
        class_arrival_lead_minutes=(class_arrival_lead_minutes if class_arrival_lead_minutes is not None
                                    else getattr(commitment, "class_arrival_lead_minutes", None)),
    )


def _swap(commitments, by_ref, old, new):
    index = commitments.index(old)
    commitments[index] = new
    by_ref[new.commitment_ref] = new


def _next_ref_number(existing_refs: Sequence[str]) -> int:
    numbers = []
    for ref in existing_refs:
        if ref.startswith(COMMITMENT_REF_PREFIX):
            suffix = ref[len(COMMITMENT_REF_PREFIX):]
            if suffix.isdigit():
                numbers.append(int(suffix))
    return (max(numbers) + 1) if numbers else 1


def _format_ref(number: int) -> str:
    return "{}{:03d}".format(COMMITMENT_REF_PREFIX, number)


def _fmt_time(value: Optional[datetime]) -> str:
    return "未知" if value is None else value.strftime("%H:%M")


def _commitment_system_prompt() -> str:
    from src.p3_class_prep import CLASS_ARRIVAL_FEEDBACK_SEMANTICS
    return (
        "你是 CampusFlow 的“固定安排核对（commitment reconciliation）”助手。\n"
        "你的任务：根据用户最新一句自然语言，判断当天固定安排需要如何更新，并输出 JSON。\n\n"
        "规则：\n"
        "1. 只能引用安排列表中真实存在的 commitment_ref；不确定时不得猜测，应放入 questions。\n"
        "2. 新增安排：target_commitment_ref 必须为 null，action=add，并给出 title / starts_at / ends_at。\n"
        "3. delay：只给 target_commitment_ref + delay_minutes（正整数）。\n"
        "4. cancel：只给 target_commitment_ref。\n"
        "5. update_time：给 target_commitment_ref + 至少一个时间（starts_at / ends_at）或课程提前量。\n"
        "update_location：给 target_commitment_ref + location_text，保留原时间和身份；不要修改用户当前位置。"
        "若旧标题包含被更新的地点，title同步为同一活动的中性短标题，不再把地点复制进标题；"
        "否则title=null保留主题，不能借地点更新变成另一件事。\n"
        "6. 时间统一使用 24 小时制 HH:MM（例如 14:00）。\n"
        "7. 同一个结果内不得有两个 update 指向同一 commitment_ref。\n"
        "8. 程序负责时间解析、容量重算与路线事实；你只表达意图。\n"
        "9. 只输出 JSON，不要解释。\n\n"
        "schema：" + COMMITMENT_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"updates\": [...], \"questions\": []}\n"
        "update 字段：target_commitment_ref（string|null）、action（" + "|".join(item.value for item in CommitmentAction) + "）、"
        "title（string|null）、starts_at（string|null）、ends_at（string|null）、delay_minutes（int|null）、"
        "class_arrival_lead_minutes（0到60的整数|null，仅课程update_time可用）、location_text（string|null，仅update_location可用）。\n\n"
        "示例1（推迟下课）：用户说“下课晚了20分钟。”输出："
        "{\"schema_version\": \"p2.commitment-reconciliation.v1\", \"updates\": ["
        "{\"target_commitment_ref\": \"day_commitment_001\", \"action\": \"delay\", "
        "\"title\": null, \"starts_at\": null, \"ends_at\": null, \"delay_minutes\": 20}], "
        "\"questions\": []}\n"
        "示例2（新增组会）：用户说“14点临时有个组会，大概1小时。”输出："
        "{\"schema_version\": \"p2.commitment-reconciliation.v1\", \"updates\": ["
        "{\"target_commitment_ref\": null, \"action\": \"add\", \"title\": \"组会\", "
        "\"starts_at\": \"14:00\", \"ends_at\": \"15:00\", \"delay_minutes\": null}], "
        "\"questions\": []}\n"
        "示例3（取消组会）：用户说“下午的组会取消了。”输出："
        "{\"schema_version\": \"p2.commitment-reconciliation.v1\", \"updates\": ["
        "{\"target_commitment_ref\": \"day_commitment_003\", \"action\": \"cancel\", "
        "\"title\": null, \"starts_at\": null, \"ends_at\": null, \"delay_minutes\": null}], "
        "\"questions\": []}\n"
        "示例4（改时间）：用户说“组会改到15点。”输出："
        "{\"schema_version\": \"p2.commitment-reconciliation.v1\", \"updates\": ["
        "{\"target_commitment_ref\": \"day_commitment_003\", \"action\": \"update_time\", "
        "\"title\": null, \"starts_at\": \"15:00\", \"ends_at\": null, \"delay_minutes\": null}], "
        "\"questions\": []}\n"
        "示例5（身份不确定）：列表同时有多个相似安排且无法判断时，不要乱选，"
        "在 questions 中返回需要确认的问题。"
        + CLASS_ARRIVAL_FEEDBACK_SEMANTICS
    )

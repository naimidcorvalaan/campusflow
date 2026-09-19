"""P3e Companion Copy Agent：为最终“当前方案”生成轻量陪伴文案（Python 3.8 兼容）。

职责边界：
- 只根据“已经确定的计划事实”生成 opening / closing 两段自然语言；
- 区分三种 turn context：
  - initial_plan：首次生成全天计划，opening 为欢迎 + 当前计划概览；
  - feedback_update：用户反馈已成功生成新计划，opening 改为“更新反馈式文案”，
    并接收程序从真实 state diff 提取的 change summary；
  - refresh：只是刷新方案，不假装用户刚做了修改，使用普通概览式 opening。
- 不新增任务、不修改时间/地点/路线/耗时，不编造用户状态或外部事实；
- 输出独立 JSON（p3.companion-copy.v1），不混入计划数据结构；
- 调用失败或输出不安全时使用 context 对应的安全 fallback，不影响主计划。

模型调用策略：
- 计划最终确定后最多调用 1 次 + 最多 1 次格式 repair；
- 同一 plan 版本的结果由会话层缓存，普通 rerun 不重复调用。
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

from src.p2_agentic_parser import extract_json_object
from src.p2_models import TaskState

COMPANION_SCHEMA_VERSION = "p3.companion-copy.v1"
MAX_COPY_LENGTH = 200

CONTEXT_INITIAL = "initial_plan"
CONTEXT_FEEDBACK = "feedback_update"
CONTEXT_REFRESH = "refresh"
VALID_CONTEXTS = (CONTEXT_INITIAL, CONTEXT_FEEDBACK, CONTEXT_REFRESH)

OPENING_FALLBACK = "下面的计划已经帮你顺好，可以按这个节奏开始。"
CLOSING_FALLBACK = "照这个节奏往下走就好，祝你接下来顺利。"
FEEDBACK_OPENING_FALLBACK = "我按刚刚的变化把后面的安排重新顺好了。"
FEEDBACK_CLOSING_FALLBACK = "这样安排会更从容一些，接下来按新的节奏来。"

EVENING_START_HOUR = 18
URGENT_MINUTES = 30


def companion_fallbacks(context_type):
    """按 context 返回 (opening, closing) 安全 fallback。"""
    if context_type == CONTEXT_FEEDBACK:
        return FEEDBACK_OPENING_FALLBACK, FEEDBACK_CLOSING_FALLBACK
    return OPENING_FALLBACK, CLOSING_FALLBACK


@dataclass(frozen=True)
class CompanionCopy:
    opening: str
    closing: str
    generated: bool = False
    context_type: str = CONTEXT_INITIAL
    change_summary: Optional[str] = None
    gap_tip: Optional[str] = None
    gap_tip_kind: Optional[str] = None
    critic_approved: bool = True
    improved: bool = False
    lifestyle_hint: Optional[str] = None
    copy_reviewed: bool = False

    def __post_init__(self):
        if not isinstance(self.opening, str) or not isinstance(self.closing, str):
            raise ValueError("opening/closing must be str")
        if self.context_type not in VALID_CONTEXTS:
            raise ValueError("context_type must be one of {}".format(VALID_CONTEXTS))
        for name in (
            "change_summary",
            "gap_tip",
            "lifestyle_hint",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError("{} must be a str or None".format(name))
        if self.gap_tip_kind is not None and self.gap_tip_kind not in ("short", "free"):
            raise ValueError("gap_tip_kind must be short, free, or None")
        for name in ("critic_approved", "improved", "copy_reviewed"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError("{} must be a bool".format(name))


def _fmt_hm(value) -> str:
    if value is None:
        return "（时间待定）"
    return value.strftime("%H:%M")


def _mode_label(mode) -> str:
    value = getattr(mode, "value", mode)
    return {"walk": "步行", "bike": "骑行"}.get(value, "出行")


def extract_plan_facts(state, allocation_plan, movement_blocks=()):
    """从真实计划提取供文案使用的确定性事实（不输出内部 ref / schema）。"""
    now = state.now
    day_end = state.day_end
    task_lines = []
    from src.task_attention import allocation_spans
    for allocation, start, end in allocation_spans(state, allocation_plan):
        task_lines.append(
            "{}–{}：{} {}分钟".format(
                start.strftime("%H:%M"), end.strftime("%H:%M"),
                allocation.task_title, allocation.planned_minutes,
            )
        )
    commitments = [
        "{} {}-{}".format(
            c.title,
            _fmt_hm(c.starts_at),
            _fmt_hm(c.ends_at),
        )
        for c in state.commitments
    ]
    movement_lines = []
    for block in movement_blocks or ():
        origin = getattr(block, "origin_name", None) or getattr(block, "origin_text", "起点")
        dest = getattr(block, "destination_name", None) or getattr(block, "destination_text", "终点")
        start = getattr(block, "window_start", None)
        end = getattr(block, "end_time", None)
        mode_label = _mode_label(getattr(block, "mode", None))
        interval = ""
        if start is not None and end is not None:
            interval = " {}–{}".format(start.strftime("%H:%M"), end.strftime("%H:%M"))
        movement_lines.append("{}{}前往{}".format(interval, mode_label, dest))
    unallocated = [
        task.title for task in state.tasks
        if task.task_ref in allocation_plan.unallocated_task_refs
    ]
    return {
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "day_end": day_end.strftime("%H:%M"),
        "tasks": tuple(task_lines),
        "commitments": tuple(commitments),
        "movements": tuple(movement_lines),
        "unallocated": tuple(unallocated),
    }


def _task_state_label(state_value):
    return {
        TaskState.SKIPPED_TODAY: "今天先放一放",
        TaskState.COMPLETED: "已完成",
        TaskState.ACTIVE: "继续安排",
        TaskState.ABANDONED: "不再继续",
    }.get(state_value, state_value.value)


def extract_change_facts(
    before_state,
    after_state,
    before_blocks=(),
    after_blocks=(),
    before_location=None,
    after_location=None,
    before_allocation_plan=None,
    after_allocation_plan=None,
):
    """从程序已成功应用的真实 state diff 提取本轮变化摘要（不含内部 ref/schema）。

    - task progress / lifecycle / total 变化；
    - commitment 新增/取消/时间变化；
    - movement 方式变化（bike -> walk 等）与新增移动；
    - current location 变化；
    - 任务重新进入 / 退出计划；
    - 下一固定安排、距下一安排的分钟数、晚间可用时间、当前剩余可安排时间。
    """
    lines = []
    task_progress = []
    task_skipped = []
    commitment_changes = []
    movement_changes = []
    plan_enter = []
    plan_exit = []

    before_tasks = {t.task_ref: t for t in before_state.tasks}
    after_tasks = {t.task_ref: t for t in after_state.tasks}
    for ref, before in before_tasks.items():
        after = after_tasks.get(ref)
        if after is None:
            continue
        delta = after.completed_minutes - before.completed_minutes
        if delta > 0:
            line = "任务「{}」进度增加{}分钟".format(after.title, delta)
            task_progress.append(line)
            lines.append(line)
        elif delta < 0:
            line = "任务「{}」进度减少{}分钟".format(after.title, -delta)
            task_progress.append(line)
            lines.append(line)
        if before.state != after.state:
            line = "任务「{}」{}".format(after.title, _task_state_label(after.state))
            task_skipped.append(line)
            lines.append(line)
        if (
            after.total_minutes is not None
            and before.total_minutes is not None
            and after.total_minutes != before.total_minutes
        ):
            line = "任务「{}」预计总时长调整为{}分钟".format(after.title, after.total_minutes)
            lines.append(line)

    before_commitments = {c.commitment_ref: c for c in before_state.commitments}
    after_commitments = {c.commitment_ref: c for c in after_state.commitments}
    for ref, before in before_commitments.items():
        after = after_commitments.get(ref)
        if after is None:
            line = "固定安排「{}」已取消".format(before.title)
            commitment_changes.append(line)
            lines.append(line)
            continue
        if after.starts_at != before.starts_at:
            line = "固定安排「{}」开始时间改为{}".format(after.title, _fmt_hm(after.starts_at))
            commitment_changes.append(line)
            lines.append(line)
        if after.ends_at != before.ends_at:
            if after.ends_at is None:
                line = "固定安排「{}」结束时间暂未确定".format(after.title)
            elif before.ends_at is None:
                line = "固定安排「{}」已补充结束时间为{}".format(
                    after.title, _fmt_hm(after.ends_at)
                )
            else:
                line = "固定安排「{}」结束时间改为{}".format(after.title, _fmt_hm(after.ends_at))
            commitment_changes.append(line)
            lines.append(line)
    for ref, after in after_commitments.items():
        if ref not in before_commitments:
            line = "新增固定安排「{}」{}开始".format(after.title, _fmt_hm(after.starts_at))
            commitment_changes.append(line)
            lines.append(line)

    before_mode_by_dest = {
        (getattr(b, "destination_name", None) or getattr(b, "destination_text", "")): b.mode
        for b in (before_blocks or ())
    }
    after_mode_by_dest = {
        (getattr(a, "destination_name", None) or getattr(a, "destination_text", "")): a.mode
        for a in (after_blocks or ())
    }
    for dest, after_mode in after_mode_by_dest.items():
        before_mode = before_mode_by_dest.get(dest)
        if before_mode is not None and before_mode != after_mode:
            line = "前往「{}」的方式从{}改为{}".format(
                dest, _mode_label(before_mode), _mode_label(after_mode)
            )
            movement_changes.append(line)
            lines.append(line)
        elif before_mode is None and dest:
            line = "新增前往「{}」的移动（{}）".format(dest, _mode_label(after_mode))
            movement_changes.append(line)
            lines.append(line)

    if before_location != after_location and after_location:
        line = "当前地点更新为「{}」".format(after_location)
        lines.append(line)

    before_unalloc = (
        set(before_allocation_plan.unallocated_task_refs)
        if before_allocation_plan is not None else set()
    )
    after_unalloc = (
        set(after_allocation_plan.unallocated_task_refs)
        if after_allocation_plan is not None else set()
    )
    for ref in sorted(before_unalloc - after_unalloc):
        title = after_tasks.get(ref)
        if title is not None:
            line = "任务「{}」已重新安排进计划".format(title.title)
            plan_enter.append(line)
            lines.append(line)
    for ref in sorted(after_unalloc - before_unalloc):
        title = after_tasks.get(ref)
        if title is not None:
            line = "任务「{}」本轮暂未安排，之后可继续".format(title.title)
            plan_exit.append(line)
            lines.append(line)

    now = after_state.now
    future = [
        c for c in after_state.commitments
        if c.starts_at is not None and c.starts_at > now
    ]
    future.sort(key=lambda c: c.starts_at)
    next_commitment = None
    minutes_to_next = None
    if future:
        head = future[0]
        next_commitment = "{} {}".format(_fmt_hm(head.starts_at), head.title)
        minutes_to_next = int((head.starts_at - now).total_seconds() // 60)

    remaining_minutes = 0
    evening_free = False
    for window in after_state.windows:
        if window.ends_at <= now:
            continue
        usable_start = max(window.starts_at, now)
        mins = int((window.ends_at - usable_start).total_seconds() // 60)
        if mins <= 0:
            continue
        remaining_minutes += mins
        if (
            window.starts_at.hour >= EVENING_START_HOUR
            or window.ends_at.hour > EVENING_START_HOUR
        ):
            evening_free = True

    return {
        "changed": bool(lines),
        "lines": tuple(lines),
        "task_progress": tuple(task_progress),
        "task_skipped": tuple(task_skipped),
        "commitment_changes": tuple(commitment_changes),
        "movement_changes": tuple(movement_changes),
        "plan_enter": tuple(plan_enter),
        "plan_exit": tuple(plan_exit),
        "next_commitment": next_commitment,
        "minutes_to_next_commitment": minutes_to_next,
        "evening_free": evening_free,
        "remaining_minutes": remaining_minutes,
    }


def _context_system_block(context_type) -> str:
    if context_type == CONTEXT_FEEDBACK:
        return (
            "文案场景：feedback_update（用户刚刚提交反馈，计划已按真实状态重新生成）。\n"
            "opening：1~2句。先自然表达“计划已更新/已重新排好”（但不要机械复制固定句式），"
            "再针对“本轮已确认变化”和更新后的计划说一句温暖、有帮助的话；"
            "不要重新说“同学你好，以下是今天的计划”这类首次问候；"
            "不需要把所有变化机械复述一遍，挑最重要的即可。\n"
            "事实红线：\n"
            "- 只有下方“本轮已确认变化”里列出的内容才算真的发生，不得编造其他进度/取消/时间变化；\n"
            "- 只有计划中确实存在晚间可用时间且相关任务之后还能继续时，才能说“晚上再做也来得及”；"
            "否则应说“之后有合适的时间再继续就好”；\n"
            "- 只有当前时间距下一固定安排很近（例如不超过30分钟）时，才能说“上课/组会时间快到了/该准备出发”；"
            "否则不要提“快到了/快迟到”；\n"
            "- 不得说“你今天很累”，不得编造天气、情绪、健康状态、未安排的任务或不存在的地点/路线。"
        )
    if context_type == CONTEXT_REFRESH:
        return (
            "文案场景：refresh（用户点击刷新方案，计划按最新状态重新整理，没有新的用户修改）。\n"
            "opening：1~2句普通计划概览即可（例如“这是按最新状态整理好的当前安排”），"
            "不要假装用户刚刚做了修改，不要写“已为你更新计划”这类反馈式开头。"
        )
    return (
        "文案场景：initial_plan（首次生成全天计划）。\n"
        "opening：计划开头1~2句欢迎/概览，先问候，再简单概括当前安排"
        "（例如覆盖的时间段、今天主要做什么、已帮你考虑移动与碎片时间）。"
    )


def build_companion_prompt(facts, context_type=CONTEXT_INITIAL, change_facts=None) -> Tuple[str, str]:
    """构造 Companion Copy Agent prompt；只给已确定的计划事实。"""
    if context_type not in VALID_CONTEXTS:
        raise ValueError("context_type must be one of {}".format(VALID_CONTEXTS))
    system = (
        "你是 CampusFlow 的校园生活陪伴文案助手。\n"
        "输入是一份已经确定的当日计划事实。\n"
        "你的任务：只输出两段简短自然的自然语言——opening（计划开头的话）"
        "和 closing（计划结尾1~3句温暖贴合节奏的话），像校园生活助理。\n"
        "规则：\n"
        "1. 只描述和回应给定计划；不得新增任务、不得修改时间/地点/路线/耗时。\n"
        "地点没有给出时，不替买东西等任务发明商店或目的地。到达时刻不能写成出发时刻。"
        "任务已安排只表示计划工作量，不表示实际完成。\n"
        "2. 不得编造用户状态、天气、健康、情绪等外部事实；不做医疗/心理判断，不说教。\n"
        "3. 时间表述必须来自给定计划：只有下午计划就说“今天下午/接下来”，"
        "不要硬说全天时间段。\n"
        "4. 行程紧可自然提醒休息/吃饭/喝水；计划宽松就说节奏从容；"
        "跨地点多可提醒给移动留余量；长学习任务可自然提醒分段完成。\n"
        "5. 不要过度鸡汤、夸张鼓励、每次都机械说“今日行程繁忙”。\n"
        "6. 不出现任何内部编号、字段名、JSON 结构或工程术语。\n"
        + _context_system_block(context_type)
        + "\n"
        "closing：1~3句温暖自然的话；与 opening 不要重复表达同一件事"
        "（例如 opening 已说过时间紧/该出发，closing 就换更轻的角度收尾）。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + COMPANION_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"opening\": string, \"closing\": string}"
    )
    user_lines = ["当前时间：{}".format(facts["now"])]
    if context_type == CONTEXT_FEEDBACK and change_facts is not None:
        user_lines.append("本轮计划已更新，以下是更新后的计划事实：")
        if change_facts["lines"]:
            user_lines.append("本轮已确认变化：")
            user_lines.extend("- " + line for line in change_facts["lines"])
        if change_facts["next_commitment"] is not None:
            detail = "下一固定安排：{}".format(change_facts["next_commitment"])
            if change_facts["minutes_to_next_commitment"] is not None:
                detail += "（约{}分钟后）".format(change_facts["minutes_to_next_commitment"])
            user_lines.append(detail)
        user_lines.append("晚间可用时间：{}".format("有" if change_facts["evening_free"] else "无"))
        user_lines.append(
            "当前剩余可安排时间：约{}分钟".format(change_facts["remaining_minutes"])
        )
    else:
        user_lines.append("以下是当前计划事实：")
    if facts["tasks"]:
        user_lines.append("已安排任务：")
        user_lines.extend("- " + line for line in facts["tasks"])
    if facts["commitments"]:
        user_lines.append("固定安排：")
        user_lines.extend("- " + line for line in facts["commitments"])
    if facts["movements"]:
        user_lines.append("移动安排：")
        user_lines.extend("- " + line for line in facts["movements"])
    if facts["unallocated"]:
        user_lines.append("今天暂未安排但之后可继续：")
        user_lines.extend("- " + title for title in facts["unallocated"])
    user_lines.append("只输出一个符合 schema 的 JSON 对象。")
    return system, "\n".join(user_lines)


def parse_companion_copy(text: Optional[str], context_type=CONTEXT_INITIAL) -> Optional[CompanionCopy]:
    """解析并校验 Companion Copy JSON；失败返回 None。"""
    if context_type not in VALID_CONTEXTS:
        raise ValueError("context_type must be one of {}".format(VALID_CONTEXTS))
    if text is None:
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    opening = payload.get("opening")
    closing = payload.get("closing")
    if not isinstance(opening, str) or not isinstance(closing, str):
        return None
    opening = opening.strip()
    closing = closing.strip()
    if not opening or not closing:
        return None
    if len(opening) > MAX_COPY_LENGTH or len(closing) > MAX_COPY_LENGTH:
        return None
    return CompanionCopy(opening=opening, closing=closing, generated=True, context_type=context_type)


def generate_companion_copy(
    facts,
    caller,
    repair_caller=None,
    context_type=CONTEXT_INITIAL,
    change_facts=None,
) -> CompanionCopy:
    """生成陪伴文案：1 次调用 + 最多 1 次 repair；调用/解析失败返回 context fallback。"""
    if context_type not in VALID_CONTEXTS:
        raise ValueError("context_type must be one of {}".format(VALID_CONTEXTS))
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_companion_prompt(facts, context_type=context_type, change_facts=change_facts)
    try:
        parsed = parse_companion_copy(caller(system, user), context_type=context_type)
    except Exception:
        parsed = None
    if parsed is not None:
        return parsed
    repair_system, repair_user = _repair_prompt()
    try:
        repaired = parse_companion_copy(repair(repair_system, repair_user), context_type=context_type)
    except Exception:
        repaired = None
    if repaired is not None:
        return repaired
    opening, closing = companion_fallbacks(context_type)
    return CompanionCopy(
        opening=opening, closing=closing, generated=False, context_type=context_type
    )


def _repair_prompt() -> Tuple[str, str]:
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新解释计划，"
        "不添加新事实，只输出符合 " + COMPANION_SCHEMA_VERSION + " 的 JSON 对象。"
    )
    user = (
        "输出对象：{\"schema_version\": \"p3.companion-copy.v1\", "
        "\"opening\": string, \"closing\": string}\n只输出 JSON。"
    )
    return system, user

"""P2c Agent prompt 构建（Python 3.8 兼容）。

原则：
- schema 简单，字段少；
- 提供可复制的完整示例；
- program facts（remaining / capacity / allocation）明确不可修改；
- 不发送 Python repr / dataclass repr / 异常 / API Key / 内部路由 ID；
- history 最多最近 10 条。
"""

from typing import Optional, Tuple

from src.p2_agentic_models import (
    DAY_PLAN_INTENT_SCHEMA_VERSION,
    RECONCILIATION_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    DayPlanIntent,
    ReviewResult,
)
from src.p2_allocation_models import DayAllocationPlan
from src.p2_models import DayPlanningState

MAX_HISTORY_LINES = 10

# One workload ownership definition shared by normal/review/revision prompts.
# This is a hypothetical measurement boundary, never a user-location update.
TASK_EFFORT_BOUNDARY = (
    "工作量的计量场景：假设执行者已经到达任务执行地点，从开始执行任务动作计时，"
    "到任务动作结束停止。请先理解这一现场动作的范围，再估计现场分钟。"
    "这是估时边界，不表示用户现在已到达；实际出发、到达和返程由路线阶段另行安排。"
    "不要添加用户未要求的前置任务。\n"
    "将已知动作与未知外部等待区分；没有事实依据的排队、审批、额外准备不应作为必然工作量加入。"
    "按通常完成该动作所需工作量估计，不能为迎合窗口缩短，也不能把最坏情形当确定分钟。\n"
)

SPLITTABILITY_REVIEW_SCHEMA_VERSION = "p2.splittability-review.v1"


def build_reconciliation_prompt(state: DayPlanningState, user_text: str) -> Tuple[str, str]:
    """Reconciliation Agent：判断用户一句话对任务台账意味着什么。"""
    system = _reconciliation_system()
    user = (
        "当前时间：{now}\n"
        "day_end：{day_end}\n\n"
        "任务台账：\n{ledger}\n\n"
        "最近历史：\n{history}\n\n"
        "用户最新输入：\n{user_text}\n\n"
        "只输出一个符合 schema 的 JSON 对象。"
    ).format(
        now=state.now.strftime("%Y-%m-%d %H:%M"),
        day_end=state.day_end.strftime("%H:%M"),
        ledger=format_task_ledger(state.tasks),
        history=format_history(state.history),
        user_text=user_text,
    )
    return system, user


def build_day_plan_prompt(state: DayPlanningState, user_text: str) -> Tuple[str, str]:
    """Day Plan Agent：决定任务顺序与缺失信息估计，不输出具体 allocation。"""
    system = _day_plan_system()
    user = TASK_EFFORT_BOUNDARY + (
        "当前时间：{now}\n"
        "day_end：{day_end}\n\n"
        "任务台账：\n{ledger}\n\n"
        "今日剩余窗口：\n{windows}\n\n"
        "最近历史：\n{history}\n\n"
        "用户最新输入：\n{user_text}\n\n"
        "只输出一个符合 schema 的 JSON 对象。"
    ).format(
        now=state.now.strftime("%Y-%m-%d %H:%M"),
        day_end=state.day_end.strftime("%H:%M"),
        ledger=format_task_ledger(state.tasks),
        windows=format_windows(state.windows),
        history=format_history(state.history),
        user_text=user_text,
    )
    return system, user


def build_review_prompt(
    state: DayPlanningState,
    user_text: str,
    intent: Optional[DayPlanIntent],
    plan: DayAllocationPlan,
) -> Tuple[str, str]:
    """Review Agent：审查是否符合用户意图与常识（不重做硬约束校验）。"""
    system = _review_system()
    user = TASK_EFFORT_BOUNDARY + (
        "用户最新输入：\n{user_text}\n\n"
        "任务台账：\n{ledger}\n\n"
        "Day Plan intent：\n{intent}\n\n"
        "程序生成的初步 allocation（容量已校验，路线由后续阶段落实）：\n{plan}\n\n"
        "只输出一个符合 schema 的 JSON 对象。"
    ).format(
        user_text=user_text,
        ledger=format_task_ledger(state.tasks),
        intent=format_intent(intent),
        plan=format_plan(plan),
    )
    return system, user


def build_revision_prompt(
    state: DayPlanningState,
    user_text: str,
    intent: Optional[DayPlanIntent],
    plan: DayAllocationPlan,
    review: ReviewResult,
) -> Tuple[str, str]:
    """Revision Agent：根据 Review 意见输出新的 day-plan-intent。"""
    system = _revision_system()
    suggestions = []
    if review.suggested_task_order is not None:
        suggestions.append("suggested_task_order: " + ", ".join(review.suggested_task_order))
    if review.include_low_attention is not None:
        suggestions.append("include_low_attention: " + str(review.include_low_attention))
    if review.reason:
        suggestions.append("reason: " + review.reason)
    suggestion_text = "\n".join(suggestions) if suggestions else "（无）"
    user = TASK_EFFORT_BOUNDARY + (
        "用户最新输入：\n{user_text}\n\n"
        "任务台账：\n{ledger}\n\n"
        "原 Day Plan intent：\n{intent}\n\n"
        "原 allocation：\n{plan}\n\n"
        "Review 意见：\n{suggestions}\n\n"
        "只输出一个符合 day-plan-intent schema 的 JSON 对象。"
    ).format(
        user_text=user_text,
        ledger=format_task_ledger(state.tasks),
        intent=format_intent(intent),
        plan=format_plan(plan),
        suggestions=suggestion_text,
    )
    return system, user


def build_splittability_review_prompt(
    task: object,
    remaining_minutes: int,
    available_windows: Tuple,
    original_splittable: Optional[bool],
    original_minimum_slice: Optional[int],
) -> Tuple[str, str]:
    """Splittability Review Agent：任务因“不可拆”而完全未分配时，再判断一次。

    触发条件由程序判断；本函数只构造 prompt。模型只回答该任务是否真的必须
    一次完整完成、以及有意义的最小片段，不参与 capacity 计算。
    """
    system = (
        "你是 CampusFlow 的“任务可拆分复核”助手。\n"
        "有一个任务因被判断为不可拆分，当前窗口放不下整项而完全未安排。\n"
        "请重新判断：这项任务是否真的必须一次完整完成？\n"
        "如果现在只能做其中一部分，这一小段执行是否仍然有实际价值？\n"
        "规则：\n"
        "1. is_splittable=false 只用于“如果不能一次完整完成，做一小段几乎没有有效进展”"
        "的原子任务。\n"
        "2. 连续学习、阅读、写作、编程、实验推进、背诵等任务，做一部分通常仍能产生有效进度，"
        "应判断 is_splittable=true。\n"
        "3. 总剩余时长大于当前窗口，不代表当前窗口完全不能执行；"
        "可以只安排一个 >= minimum_slice_minutes 的片段，剩余之后继续。\n"
        "4. minimum_slice_minutes 表示“至少做多久才开始产生实际价值”；"
        "碎片化任务（背一会儿 / 看一会儿）可给出 5~10 分钟的小片段。\n"
        "5. 如果确实必须一次完成，才返回 false；不要为了“安排上”而强行改 true。\n"
        "只输出 JSON，不解释。\n\n"
        "schema：" + SPLITTABILITY_REVIEW_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"is_splittable\": bool, "
        "\"minimum_slice_minutes\": int|null, \"reason\": string|null}"
    )
    windows_text = "\n".join(
        "{} {}–{}，容量{}分钟".format(
            window.window_ref,
            window.starts_at.strftime("%H:%M"),
            window.ends_at.strftime("%H:%M"),
            window.capacity_minutes,
        )
        for window in available_windows
    )
    user = (
        "任务：{title}\n"
        "剩余时长：{remaining} 分钟\n"
        "当前可用窗口：\n{windows}\n"
        "原判断：is_splittable={orig_splittable}, minimum_slice={orig_slice}\n\n"
        "请复核该任务是否真的不可拆分。只输出 JSON。"
    ).format(
        title=task.title,
        remaining=remaining_minutes,
        windows=windows_text,
        orig_splittable=_fmt_optional_bool(original_splittable),
        orig_slice=_fmt_optional(original_minimum_slice),
    )
    return system, user


def build_repair_prompt(stage: str, previous_text: Optional[str], schema_name: str) -> Tuple[str, str]:
    """Repair Agent：只修格式，不重新解释用户意图，不添加新事实。"""
    system = (
        "你是格式修复助手。只修复 JSON 格式/结构问题，不重新解释用户意图，"
        "不添加新事实，不删除已有事实，只把给定内容转换为目标 schema。"
        "只输出符合目标 schema 的 JSON 对象。"
    )
    user = (
        "修复阶段：{stage}\n"
        "目标 schema：{schema_name}\n\n"
        "需要修复的内容：\n{previous_text}\n\n"
        "只输出 JSON。"
    ).format(stage=stage, schema_name=schema_name, previous_text=previous_text)
    return system, user


def format_task_ledger(tasks: Tuple) -> str:
    lines = []
    for task in tasks:
        lines.append(
            "- ref={} | 标题={} | total={} | completed={} | remaining={} | 状态={} | 可拆分={} | 最小片段={} | total_source={}".format(
                task.task_ref,
                task.title,
                _fmt_optional(task.total_minutes),
                task.completed_minutes,
                _fmt_optional(task.remaining_minutes),
                task.state.value,
                _fmt_optional_bool(task.is_splittable),
                _fmt_optional(task.minimum_slice_minutes),
                _fmt_optional(task.total_source.value if task.total_source is not None else None),
            )
        )
        lines.append('  attention={} | launch_ref={} | actually_running={} | evidence={}'.format(
            task.attention_mode, task.launch_task_ref, task.user_reported_running, task.background_reason))
    return "\n".join(lines) if lines else "（无）"


def format_windows(windows: Tuple) -> str:
    lines = []
    for window in windows:
        lines.append(
            "- ref={} | {} - {} | availability={} | capacity={}分钟".format(
                window.window_ref,
                window.starts_at.strftime("%H:%M"),
                window.ends_at.strftime("%H:%M"),
                window.availability.value,
                window.capacity_minutes,
            )
        )
    return "\n".join(lines) if lines else "（无）"


def format_history(history: Tuple[str, ...]) -> str:
    if not history:
        return "（无）"
    lines = []
    for entry in history[-MAX_HISTORY_LINES:]:
        lines.append("- " + entry)
    return "\n".join(lines)


def format_intent(intent: Optional[DayPlanIntent]) -> str:
    if intent is None:
        return "（无，使用默认任务顺序）"
    lines = [
        "task_order: " + (", ".join(intent.task_order) if intent.task_order else "（空）"),
        "include_low_attention: " + str(intent.include_low_attention),
    ]
    for estimate in intent.task_estimates:
        lines.append(
            "estimate: ref={} | total={} | splittable={} | min_slice={}".format(
                estimate.task_ref,
                estimate.estimated_total_minutes,
                _fmt_optional_bool(estimate.is_splittable),
                _fmt_optional(estimate.minimum_slice_minutes),
            )
        )
    if intent.rationale:
        lines.append("rationale: " + intent.rationale)
    return "\n".join(lines)


def format_plan(plan: DayAllocationPlan) -> str:
    lines = []
    for allocation in plan.allocations:
        lines.append(
            "- window_ref={} | task_ref={} | {} | {}分钟 | 片段{}".format(
                allocation.window_ref,
                allocation.task_ref,
                allocation.task_title,
                allocation.planned_minutes,
                allocation.sequence_index,
            )
        )
    if plan.unallocated_task_refs:
        lines.append("未安排任务：" + "、".join(plan.unallocated_task_refs))
    for warning in plan.warnings:
        lines.append("warning: " + warning)
    return "\n".join(lines) if lines else "（空计划）"


def _reconciliation_system() -> str:
    return (
        "你是 CampusFlow 的“任务状态核对（reconciliation）”助手。\n"
        "你的任务：根据用户最新一句自然语言，判断它对当天任务台账意味着什么，"
        "并输出 JSON。\n\n"
        "规则：\n"
        "1. 只能引用任务台账中真实存在的 task_ref；不确定时不得猜测，应放入 questions。\n"
        "2. 新增任务：target_task_ref 必须为 null，并给出 new_task_title。\n"
        "3. 不要输出 completed 绝对值；已完成工作只能用 progress_delta_minutes（额外完成分钟）。\n"
        "4. lifecycle_action 只能是 none / skip_today / abandon / complete / resume_today。\n"
        "5. “我想完成……”是规划意图，不是完成事实，不得使用 lifecycle=complete。\n"
        "6. “我刚又做了30分钟”表示 progress_delta_minutes=30。\n"
        "7. resume_today 只用于把“今天先跳过（skip_today）”的任务恢复为今天可安排；"
        "已完成或永久放弃的任务不得使用，程序会拒绝复活。\n"
        "8. 程序事实（remaining、capacity、最终 allocation）不由你计算或输出。\n"
        "9. 只输出 JSON，不要解释。\n\n"
        "schema：" + RECONCILIATION_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"updates\": [...], \"questions\": []}\n"
        "每个 update 字段：target_task_ref（string|null）、new_task_title（string|null）、"
        "progress_delta_minutes（int|null）、set_total_minutes（int|null）、"
        "set_total_source（user_text|ai_estimate|null）、lifecycle_action（none|skip_today|abandon|complete|resume_today）、"
        "is_splittable（bool|null）、minimum_slice_minutes（int|null）。\n\n"
        "示例1（进度报告）：用户说“我刚才又做了30分钟。”输出："
        "{\"schema_version\": \"p2.task-reconciliation.v1\", \"updates\": ["
        "{\"target_task_ref\": \"day_task_001\", \"new_task_title\": null, "
        "\"progress_delta_minutes\": 30, \"set_total_minutes\": null, "
        "\"set_total_source\": null, \"lifecycle_action\": \"none\", "
        "\"is_splittable\": null, \"minimum_slice_minutes\": null}], \"questions\": []}\n\n"
        "示例2（今天跳过）：用户说“今天先不背单词了。”：lifecycle_action=skip_today，其余字段为 null。\n"
        "示例3（永久放弃）：用户说“这个任务以后都不做了。”：lifecycle_action=abandon。\n"
        "示例4（明确完成）：用户说“我已经把实验报告做完了。”：lifecycle_action=complete。\n"
        "示例5（规划意图不等于完成）：用户说“我想完成计组实验3。”不得设为 complete，"
        "这是规划意图，应返回空 updates。\n"
        "示例6（身份不确定）：用户说“实验我又做了30分钟。”而台账同时有“计组实验3”与"
        "“物理实验报告”时，不得乱选，应在 questions 中返回需要确认的问题。\n"
        "示例7（新增任务）：用户说“另外今天还要整理操作系统实验。”输出："
        "{\"target_task_ref\": null, \"new_task_title\": \"整理操作系统实验\", 其余字段 null}。\n"
        "示例8（恢复今天跳过）：用户说“又想背单词了”“还是背单词吧”“把背单词加回来”"
        "“我改变主意了，单词还是要背”时，对该任务 lifecycle_action=resume_today，其余字段为 null。"
    )


def _day_plan_system() -> str:
    return (
        "你负责 CampusFlow 的任务工作量暂估与顺序提取（day plan intent），不是行程规划员。"
        "设想执行者已经站在任务执行地点，从开始操作计时到动作结束；"
        "这里不估去程、返程、赶截止或没有依据的排队。不要根据地理距离推算任务工作量。"
        "task_order只表达用户顺序或通常动作关系，不在本阶段按窗口安排分钟；"
        "可行性、路线和具体排程由后续阶段负责。\n"
        "task_order只用真实task_ref且不重复，涵盖本轮希望安排的活动任务。"
        "completed / skipped_today / abandoned不安排。用户明确关系优先，缺省顺序由你判断。\n"
        "task_estimates只补total未知的任务。estimated_total_minutes是任务动作本身的工作量，"
        "不是含路途的整段行程。路线、出发准备、返程由程序单独计时，绝不能再计入任务分钟。"
        "不把未要求的额外工作补进任务范围；不能为了填满窗口改变工作量。"
        "用户已经明确给出的总时长不得覆盖。\n"
        "is_splittable判断部分执行能否产生有用成果；一次性交接或原子办理不能通过切片声称完成。"
        "用户要求一次完成就不可拆。minimum_slice_minutes表示有效片段最小分钟；"
        "不是完整工作量。可拆长任务允许跨多个可用窗口，剩余由程序继续安排。\n"
        "include_low_attention默认false；只有任务确实适合已有低注意力窗口才true。"
        "未知必要范围无法估计时保留未知；不要创造精确开始时刻。rationale只简述实际假设。\n"
        '只返回一个JSON对象，根字段完整且仅有：schema_version="' + DAY_PLAN_INTENT_SCHEMA_VERSION
        + '"；task_order:string数组；include_low_attention:boolean；task_estimates:数组；rationale:string或null。'
        'task_estimates每项完整且仅有task_ref:string、estimated_total_minutes:正整数、'
        'is_splittable:boolean或null、minimum_slice_minutes:正整数或null。'
        'rationale是根字段，不属于估时元素；说明真实现场动作假设，不为窗口凑分钟。'
    )


def _review_system() -> str:
    return (
        "你是 CampusFlow 的“计划审查（review）”助手。\n"
        "程序已经完成台账与容量校验（不超 remaining / capacity、不复活 completed / "
        "skipped_today / abandoned、不可拆任务不拆分等）。\n"
        "你不要重新做数学求解，不要声称“程序路线错了”，不要要求修改 capacity。\n"
        "你只审查：计划是否符合用户意图与常识。\n\n"
        "先独立核对新AI估时的工作范围，不把候选rationale当事实。若任务分钟含通勤、返程或程序另算的准备，"
        "或把不能部分交付的原子动作判为可拆，应revise并指出具体范围错误；不能只调整顺序。"
        "同轮尚未确认的AI暂估可修正，用户明确/采用的分钟和既有进度不能修改。\n"
        "schema：" + REVIEW_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"decision\": \"accept|revise\", "
        "\"reason\": string|null, \"suggested_task_order\": [ref, ...]|null, "
        "\"include_low_attention\": bool|null}\n"
        "decision=revise 时必须给出明确理由，可附带 suggested_task_order / include_low_attention。"
    )


def _revision_system() -> str:
    return (_day_plan_system() + "\n这是一次有界day-plan-intent修订：依据Review具体问题修正候选，"
            "仍使用同一字段含义与事实边界，不另造估时或路线规则。")


def _fmt_optional(value: Optional[int]) -> str:
    return "未知" if value is None else str(value)


def _fmt_optional_bool(value: Optional[bool]) -> str:
    if value is None:
        return "未知"
    return "是" if value else "否"

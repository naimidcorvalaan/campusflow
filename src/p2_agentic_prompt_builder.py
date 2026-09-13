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
    user = (
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
    user = (
        "用户最新输入：\n{user_text}\n\n"
        "任务台账：\n{ledger}\n\n"
        "Day Plan intent：\n{intent}\n\n"
        "程序生成的 allocation（已通过全部硬约束）：\n{plan}\n\n"
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
    user = (
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
        "你是 CampusFlow 的“全天计划决策（day plan intent）”助手。\n"
        "输入：核对后的任务台账、今日剩余窗口与容量。\n"
        "你的输出决定“今天先做哪个任务、是否使用低注意力窗口、缺失信息如何估计”，"
        "但不输出具体 allocation 分钟表——真正的分钟分配由程序完成。\n\n"
        "规则：\n"
        "1. task_order 只包含台账中真实存在的 task_ref，不得重复。\n"
        "2. completed / skipped_today / abandoned 任务即使出现在 task_order 也不会被安排；"
        "不要主动选择它们。\n"
        "3. 只有 total 为“未知”的任务才需要 task_estimates 估计总时长。\n"
        "只估已理解范围的专注工作量；在 rationale 简述范围和关键假设。"
        "‘做一点’不是整份任务突然只需很少时间；按可拆分任务处理，保留剩余工作。"
        "范围不足以估计时不填该项，保留未知供用户使用任务估时；不要统一默认60分钟。"
        "已有默认补充仅用于相关任务，本次补充优先，不使用固定倍率。\n"
        "4. 用户已经明确给出的总时长不得覆盖（程序会忽略）。\n"
        "5. include_low_attention 默认 false；只有确有必要才 true。\n"
        "6. is_splittable / minimum_slice_minutes 只在原值未知时提供；能判断就不要留 null。"
        "minimum_slice_minutes 表示“至少做多久才开始产生实际价值”；"
        "用户说“做一会儿 / 背会儿 / 看一会儿”这类短碎片任务时，"
        "可合理给出 5~10 分钟这样的小片段。\n"
        "7. 长任务可以跨多个窗口，但由程序切分；你只需要在 task_order 中排它。\n"
        "8. is_splittable=false 只用于“如果不能一次完整完成，做一小段几乎没有有效进展”"
        "的原子任务；连续学习、阅读、写作、编程、实验推进、背诵等任务，做一部分通常仍"
        "能产生有效进度，应判断 is_splittable=true。\n"
        "9. 任务剩余时长大于单个窗口容量，不代表当前窗口完全不能执行："
        "程序会在窗口内先安排一部分（>= minimum_slice），剩余部分在后续窗口继续。"
        "不要因为放不下整项就把任务整体不安排。\n\n"
        "schema：" + DAY_PLAN_INTENT_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"task_order\": [ref, ...], "
        "\"include_low_attention\": bool, \"task_estimates\": ["
        "{\"task_ref\": \"...\", \"estimated_total_minutes\": int, "
        "\"is_splittable\": bool|null, \"minimum_slice_minutes\": int|null}], "
        "\"rationale\": string|null}\n\n"
        "示例1（长任务跨多个窗口）：计组实验3 remaining=120，今日有两个窗口，"
        "task_estimates=[{\"task_ref\": \"day_task_001\", \"estimated_total_minutes\": 120, "
        "\"is_splittable\": true, \"minimum_slice_minutes\": 20}]，程序会先在一个窗口安排一部分，"
        "剩余在后续窗口继续，不会因为放不下整项就不安排。\n"
        "示例2（未知总时长）：某任务 total=null 且 completed=20，可以估计："
        "task_estimates=[{\"task_ref\": \"day_task_003\", \"estimated_total_minutes\": 100, "
        "\"is_splittable\": true, \"minimum_slice_minutes\": 30}]。\n"
        "示例3（用户明确总时长不覆盖）：台账中任务 total=120 且来源是用户文本时，"
        "不得再给该任务估计总时长。\n"
        "示例4（task_order）：需要“先做 B 再做 A”时：task_order=[\"day_task_002\", \"day_task_001\"]。\n"
        "示例5（LOW_ATTENTION）：只有低注意力任务需要时 include_low_attention=true，否则 false。\n"
        "示例6（生命周期防复活）：已 complete / skip_today / abandon 的任务不要放进 task_order。"
    )


def _review_system() -> str:
    return (
        "你是 CampusFlow 的“计划审查（review）”助手。\n"
        "程序已经完成全部硬约束校验（不超 remaining / capacity、不复活 completed / "
        "skipped_today / abandoned、不可拆任务不拆分等）。\n"
        "你不要重新做数学求解，不要声称“程序路线错了”，不要要求修改 capacity。\n"
        "你只审查：计划是否符合用户意图与常识。\n\n"
        "schema：" + REVIEW_SCHEMA_VERSION + "\n"
        "输出对象：{\"schema_version\": \"...\", \"decision\": \"accept|revise\", "
        "\"reason\": string|null, \"suggested_task_order\": [ref, ...]|null, "
        "\"include_low_attention\": bool|null}\n"
        "decision=revise 时必须给出明确理由，可附带 suggested_task_order / include_low_attention。"
    )


def _revision_system() -> str:
    return (
        "你是 CampusFlow 的“计划修订（revision）”助手。\n"
        "根据 Review 意见，输出一个新的 day-plan-intent JSON。\n"
        "只调整任务顺序与 include_low_attention 等意图字段，不输出具体 allocation。\n"
        "遵守与 Day Plan Agent 相同的规则：task_order 只能引用真实存在的 ref，"
        "不得重复，不得选择 completed / skipped_today / abandoned。\n"
        "schema：" + DAY_PLAN_INTENT_SCHEMA_VERSION + "\n"
        "只输出 JSON。"
    )


def _fmt_optional(value: Optional[int]) -> str:
    return "未知" if value is None else str(value)


def _fmt_optional_bool(value: Optional[bool]) -> str:
    if value is None:
        return "未知"
    return "是" if value else "否"

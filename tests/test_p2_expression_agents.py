"""P3e Qwen-heavy 多 Agent 计划表达层测试（mock，不调用真实 TJU API）。

覆盖：
A. 多 Agent 调用顺序与有限调用预算
B/C/D. Plan Critic / Improver：发现碎片、批准不调 Improver、revision 只调一次不循环
E. Lifestyle Reviewer
F/G/H. change_summary（只来自真实 state diff，initial 不生成）
I/K. gap_tip（仅真实空档、内部术语屏蔽、无 gap 强制 null）
L/M. Warm Companion 红线 / Final Copy Reviewer 只润色文案不改计划
N/O. 表达层与 critic/lifestyle 失败时主计划仍完整
P/Q/R. rerun 不重复调用 / feedback 变化才重新生成 / changed=False 不生成“已更新”
S/T. sanitizer 与页面一级栏目约束
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_companion_copy import (
    CLOSING_FALLBACK,
    CONTEXT_FEEDBACK,
    CONTEXT_INITIAL,
    OPENING_FALLBACK,
    CompanionCopy,
    companion_fallbacks,
)
from src.p2_expression_agents import (
    MAX_EXPRESSION_CALLS_PER_TURN,
    _final_facts_guard,
    build_copy_review_prompt,
    build_narrator_prompt,
    build_warm_prompt,
    extract_expression_facts,
    generate_expression_bundle,
    parse_plan_critic,
    run_narrator,
    run_warm_companion,
    validate_expression_facts,
    validate_expression_locations,
    parse_plan_improver,
    run_plan_critic,
    run_plan_improver,
)
from src.p2_main import (
    _COMMITMENT_NONE,
    _PLAN_EMPTY,
    _REVIEW_ACCEPT,
    _TASK_NONE,
    _TASK_PROGRESS_30,
    _TASK_SKIP,
    build_demo_state,
    render_page_text,
)
from src.p2_models import TaskState, TaskProgress
from src.p2_session import P2SessionController
from src.p3_map_schema import TransportMode
from src.p3_map_loader import load_campus_map_data
from src.p3_route_planner import MovementBlock
from src.p3_time_estimator import TimeEstimateMethod
from src.p2_window_derivation import derive_day_state


def _router_json(route):
    return '{"schema_version": "p2.feedback-router.v1", "route": "' + route + '", "reason": null}'


def _demo_route(user):
    if "又做了30分钟" in user or "不背单词" in user:
        return _router_json("task")
    if "晚了" in user or "推迟" in user or "组会" in user or "改到" in user:
        return _router_json("commitment")
    return _router_json("unclear")


def make_task(ref="day_task_001", title="计组实验", total=120, completed=50,
              state=TaskState.ACTIVE, splittable=True, min_slice=30):
    return TaskProgress(
        ref, title, total, completed,
        SourceKind.AI_EXTRACTED_FROM_USER_TEXT, state, splittable, min_slice,
    )


def make_commitment(ref, title, start, end, availability=AvailabilityLevel.UNAVAILABLE):
    return FixedCommitment(ref, title, title, start, end, None, availability, {}, ())


def make_block(window_ref, window_start, origin, destination, mode=TransportMode.BIKE,
               minutes=5, peak_bike=False, transition_minutes=5,
               origin_node_id=None, destination_node_id=None):
    return MovementBlock(
        window_ref=window_ref,
        window_start=window_start,
        origin_text=origin,
        destination_text=destination,
        origin_node_id=origin_node_id or ("node_" + origin),
        destination_node_id=destination_node_id or ("node_" + destination),
        origin_name=origin,
        destination_name=destination,
        mode=mode,
        distance_m=800,
        estimated_minutes=minutes,
        low_minutes=max(1, minutes - 1),
        high_minutes=minutes + 1,
        method=TimeEstimateMethod.PROGRAM_FALLBACK,
        approximate=False,
        destination_short_name=destination,
        transition_minutes=transition_minutes,
        peak_bike=peak_bike,
    )


def _narrator_json(opening):
    return json.dumps({
        "schema_version": "p3.plan-narrator.v1",
        "opening": opening,
    }, ensure_ascii=False)


def _warm_json(change_summary=None, closing="按自己的节奏来，祝你今天顺利。"):
    return json.dumps({
        "schema_version": "p3.warm-companion.v1",
        "change_summary": change_summary,
        "closing": closing,
    }, ensure_ascii=False)


def _critic_json(approved=True, revision_needed=False, issues=None):
    return json.dumps({
        "schema_version": "p3.plan-critic.v1",
        "approved": approved,
        "issues": issues if issues is not None else [],
        "revision_needed": revision_needed,
    }, ensure_ascii=False)


def _improver_json(titles, include_low=False, reason="调整顺序"):
    return json.dumps({
        "schema_version": "p3.plan-improver.v1",
        "task_order_titles": titles,
        "include_low_attention": include_low,
        "reason": reason,
    }, ensure_ascii=False)


def _lifestyle_json(notes, hint=None, gap_tip=None):
    return json.dumps({
        "schema_version": "p3.lifestyle-review.v1",
        "notes": notes,
        "user_facing_hint": hint,
        "gap_tip": gap_tip,
    }, ensure_ascii=False)


def _copy_review_json(approved=True, revised=None):
    return json.dumps({
        "schema_version": "p3.copy-review.v1",
        "approved": approved,
        "revised": revised,
        "reason": "review",
    }, ensure_ascii=False)


_CRITIC_APPROVE = _critic_json(True, False)
_CRITIC_REVISION = _critic_json(False, True, [
    {"type": "wasted_gap", "severity": "high", "message": "空闲碎片被浪费，短任务可先安排。"}
])
_IMPROVER_GOOD = _improver_json(["背单词", "计组实验3"])
_IMPROVER_BAD = _improver_json(["不存在的任务A", "不存在的任务B"])
_LIFESTYLE_EMPTY = _lifestyle_json([])
_LIFESTYLE_MULTI = _lifestyle_json(
    ["地点切换较多"], "今天地点切换较多，通勤记得留余量。"
)
_NARRATOR_DEFAULT = _narrator_json("这是为你整理好的当前安排：")
_NARRATOR_FULL = _narrator_json("同学你好，下午的安排已经整理好啦～")
_WARM_DEFAULT = _warm_json()
_WARM_CHANGE = _warm_json("实验进度增加30分钟，骑行安排保持不变。")
_COPY_REVIEW_APPROVE = _copy_review_json(True)
_COPY_REVIEW_REVISE = _copy_review_json(False, {
    "opening": "已经重新润色好的开场。",
    "change_summary": None,
    "gap_tip": None,
    "closing": "今天下午事情不少，不过已经帮你把收拾和通勤的时间留出来啦，不用卡着最后一分钟赶。实验能推进多少就推进多少，没做完的之后再接着来就好。路上注意安全，也别忘了喝点水～祝你上课顺利！",
})

_EXPRESSION_SCHEMAS = (
    "p3.plan-critic",
    "p3.lifestyle-review",
    "p3.plan-narrator",
    "p3.warm-companion",
    "p3.copy-review",
    "p3.plan-improver",
)


class ExpressionCaller:
    """表达层 mock：按 schema 返回可配置 JSON；可注入失败 Agent。"""

    def __init__(self, critic=_CRITIC_APPROVE, lifestyle=_LIFESTYLE_EMPTY,
                 narrator=_NARRATOR_DEFAULT,
                 warm=_WARM_DEFAULT, copy_review=_COPY_REVIEW_APPROVE,
                 improver=_IMPROVER_GOOD, fail_agents=()):
        self.responses = {
            "p3.plan-critic": critic,
            "p3.lifestyle-review": lifestyle,
            "p3.plan-narrator": narrator,
            "p3.warm-companion": warm,
            "p3.copy-review": copy_review,
            "p3.plan-improver": improver,
        }
        self.fail_agents = set(fail_agents)
        self.calls = []
        self.order = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        for schema in _EXPRESSION_SCHEMAS:
            if schema in system:
                self.order.append(schema)
                if schema in self.fail_agents:
                    raise RuntimeError("agent unavailable: " + schema)
                return self.responses[schema]
        raise AssertionError("unexpected expression system prompt: " + system[:80])


class SessionCaller:
    """会话级 mock：P2 管线返回固定 JSON，表达层委托 ExpressionCaller。"""

    def __init__(self, expression=None):
        self.expression = expression if expression is not None else ExpressionCaller()
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        for schema in _EXPRESSION_SCHEMAS:
            if schema in system:
                return self.expression(system, user)
        if "feedback-router" in system:
            return _demo_route(user)
        if "commitment-reconciliation" in system:
            return _COMMITMENT_NONE
        if "task-reconciliation" in system:
            if "又做了30分钟" in user:
                return _TASK_PROGRESS_30
            if "不背单词" in user:
                return _TASK_SKIP
            return _TASK_NONE
        if "day-plan-intent" in system:
            return _PLAN_EMPTY
        if "day-review" in system:
            return _REVIEW_ACCEPT
        return _TASK_NONE


def _make_session(expression, state=None, map_data=None):
    expr = expression if isinstance(expression, ExpressionCaller) else ExpressionCaller()
    caller = SessionCaller(expression=expr)
    controller = P2SessionController(
        {}, caller, companion_caller=caller, companion_enabled=True, map_data=map_data
    )
    turn = controller.start_day(state if state is not None else build_demo_state())
    return controller, caller, expr, turn



# ---------------------------------------------------------------------------
# A. 多 Agent 调用顺序与预算
# ---------------------------------------------------------------------------


def test_expression_call_order_and_budget():
    controller, caller, expr, turn = _make_session(ExpressionCaller())
    # 一次完整表达层：Critic -> Lifestyle -> Narrator -> Warm -> CopyReview
    assert expr.order == [
        "p3.plan-critic",
        "p3.lifestyle-review",
        "p3.plan-narrator",
        "p3.warm-companion",
        "p3.copy-review",
    ]
    assert len(expr.calls) == 5
    assert turn.companion_copy.copy_reviewed is True
    assert turn.companion_copy.critic_approved is True
    assert turn.companion_copy.improved is False
    # 调用预算有限且未耗尽
    assert len(expr.calls) <= MAX_EXPRESSION_CALLS_PER_TURN


def test_expression_agents_receive_only_plan_facts():
    controller, caller, expr, turn = _make_session(ExpressionCaller())
    for _, user in expr.calls:
        assert "schema_version" not in user
        assert "task_ref" not in user
        assert "allocation_ref" not in user


# ---------------------------------------------------------------------------
# B/C/D. Plan Critic / Improver
# ---------------------------------------------------------------------------


def test_critic_can_flag_wasted_gap_without_changing_hard_facts():
    # 直接单元：Critic 审查基于确定性计划事实
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    caller = ExpressionCaller(critic=_CRITIC_REVISION)
    result = run_plan_critic(facts, caller, budget=None)
    assert result.revision_needed is True
    assert result.issues[0].severity == "high"
    assert result.issues[0].message == "空闲碎片被浪费，短任务可先安排。"
    # 硬事实不被 Critic 修改：计划快照原样
    assert plan.total_planned_minutes == state.tasks[0].remaining_minutes + state.tasks[1].remaining_minutes


def test_critic_approved_skips_improver():
    controller, caller, expr, turn = _make_session(ExpressionCaller(critic=_CRITIC_APPROVE))
    assert "p3.plan-improver" not in expr.order
    assert turn.companion_copy.critic_approved is True
    assert turn.companion_copy.improved is False
    assert turn.result.revision_used is False


def test_critic_revision_runs_improver_once_and_reallocates():
    controller, caller, expr, turn = _make_session(ExpressionCaller(critic=_CRITIC_REVISION))
    # 只调用一次 Improver，不循环
    assert expr.order.count("p3.plan-improver") == 1
    assert turn.companion_copy.improved is True
    assert turn.companion_copy.critic_approved is False
    # 程序重新走了确定性 allocator / validation
    assert turn.result.revision_used is True
    assert turn.result.allocation_plan is not None
    # 背单词被提前（Improver 建议顺序生效）
    refs = [a.task_ref for a in turn.result.allocation_plan.allocations]
    assert refs[0] == "day_task_002"


def test_improver_inapplicable_keeps_original_plan():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(critic=_CRITIC_REVISION, improver=_IMPROVER_BAD)
    )
    assert expr.order.count("p3.plan-improver") == 1
    assert turn.companion_copy.improved is False
    assert turn.result.revision_used is False
    # 保留原安全计划
    assert turn.result.allocation_plan.total_planned_minutes > 0


def test_parse_critic_and_improver_reject_garbage():
    assert parse_plan_critic("不是 JSON") is None
    assert parse_plan_critic('{"schema_version": "p3.plan-critic.v1"}') is None
    assert parse_plan_improver("[]") is None
    assert parse_plan_improver('{"schema_version": "p3.plan-improver.v1", '
                               '"task_order_titles": "x", "include_low_attention": false}') is None


# ---------------------------------------------------------------------------
# E. Lifestyle Reviewer
# ---------------------------------------------------------------------------


def test_lifestyle_notes_and_hint_flow_into_copy():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(lifestyle=_LIFESTYLE_MULTI)
    )
    assert turn.companion_copy.lifestyle_hint == "今天地点切换较多，通勤记得留余量。"
    # notes 是内部审查信息，不新增一级栏目
    text = render_page_text(turn)
    assert text.count("# 当前方案") == 1
    assert "## 待确认问题" not in text


def test_lifestyle_failure_ignored_keeps_plan():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(fail_agents={"p3.lifestyle-review"})
    )
    assert turn.companion_copy.lifestyle_hint is None
    assert turn.result.allocation_plan.total_planned_minutes > 0



# ---------------------------------------------------------------------------
# F. 精简表达层：不再生成 plan_title / mainline / reasoning / rhythm
# ---------------------------------------------------------------------------


def test_removed_copy_fields_not_present():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(narrator=_NARRATOR_FULL)
    )
    assert not hasattr(turn.companion_copy, "plan_title")
    assert not hasattr(turn.companion_copy, "mainline")
    assert not hasattr(turn.companion_copy, "reasoning_note")
    assert not hasattr(turn.companion_copy, "rhythm_note")
    text = render_page_text(turn)
    for marker in (
        "下午实验推进版", "今日主线：", "为什么这么排：", "当前节奏：", "**当前安排**",
    ):
        assert marker not in text


def test_narrator_failure_uses_opening_fallback_without_title():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(fail_agents={"p3.plan-narrator"})
    )
    assert turn.companion_copy.opening == OPENING_FALLBACK
    assert "这是为你整理好的当前安排" not in OPENING_FALLBACK
    assert "下面的计划" in OPENING_FALLBACK
    text = render_page_text(turn)
    assert OPENING_FALLBACK in text
    assert "**当前安排**" not in text


# ---------------------------------------------------------------------------
# H. change_summary 只来自真实 state diff
# ---------------------------------------------------------------------------


def test_change_summary_only_from_real_state_diff():
    controller, caller, expr, turn0 = _make_session(ExpressionCaller(warm=_WARM_CHANGE))
    assert turn0.companion_copy.change_summary is None  # initial 不生成
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    assert turn.companion_copy.context_type == CONTEXT_FEEDBACK
    assert turn.companion_copy.change_summary == "实验进度增加30分钟，骑行安排保持不变。"
    text = render_page_text(turn)
    assert "本轮更新：实验进度增加30分钟" in text
    # 真实 progress 已应用且未被文案修改
    updated = {t.task_ref: t for t in turn.result.updated_state.tasks}["day_task_001"]
    assert updated.completed_minutes == 80


def test_no_fake_update_when_state_unchanged():
    controller, caller, expr, turn0 = _make_session(ExpressionCaller(warm=_WARM_CHANGE))
    before = len(expr.calls)
    turn = controller.apply_feedback("随便问问")
    assert len(expr.calls) == before + 5
    assert turn.companion_copy.change_summary is None
    assert "已为你更新计划" not in turn.companion_copy.opening
    text = render_page_text(turn)
    assert "本轮更新：" not in text


# ---------------------------------------------------------------------------
# I/K. gap_tip（仅真实空档）
# ---------------------------------------------------------------------------


def test_gap_tip_rendered_when_real_gap_exists():
    lifestyle = _lifestyle_json([], None, "还有8分钟空档，可以背一小会儿单词。")
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(lifestyle=lifestyle)
    )
    assert turn.companion_copy.gap_tip == "还有8分钟空档，可以背一小会儿单词。"
    text = render_page_text(turn)
    assert "小空档：还有8分钟空档" in text
    # reasoning / rhythm 已删除
    assert "为什么这么排：" not in text
    assert "当前节奏：" not in text


def test_gap_tip_forced_null_without_real_gaps():
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    assert facts["idle_gaps"]  # demo state 存在空档
    facts["idle_gaps"] = ()
    caller = ExpressionCaller(lifestyle=_lifestyle_json([], None, "还有8分钟空档，可以背一小会儿单词。"))
    copy = generate_expression_bundle(
        facts, caller, gap_tip="还有8分钟空档，可以背一小会儿单词。", budget=None
    )
    assert copy.gap_tip is None  # 程序事实红线：无真实空档禁止 gap_tip


def test_long_gap_never_renders_as_small_gap_with_machine_minutes():
    state = build_demo_state()
    facts = extract_expression_facts(state, allocate_tasks_across_windows(state))
    facts["idle_gaps"] = ({"start": state.now, "end": state.day_end, "minutes": 295},)
    caller = ExpressionCaller(lifestyle=_lifestyle_json([], None, "295分钟空闲，可以自由安排。"))
    copy = generate_expression_bundle(facts, caller, gap_tip="295分钟空闲，可以自由安排。", budget=None)
    assert copy.gap_tip is None


def test_end_time_supplement_rejects_false_start_time_change_summary():
    from src.p2_expression_agents import _change_summary_matches_diff

    changes = {"commitment_changes": ("固定安排「上课」已补充结束时间为17:00",)}
    assert _change_summary_matches_diff("已补充上课结束时间为17:00", changes)
    assert not _change_summary_matches_diff("上课改到15:00", changes)


def test_gap_tip_internal_terms_blocked_by_sanitizer():
    leak = _lifestyle_json([], None, "splittability 判断显示任务可拆分，peak_multiplier 生效。")
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(lifestyle=leak)
    )
    text = render_page_text(turn)
    for marker in ("splittability", "peak_multiplier", "minimum_slice", "transition_buffer"):
        assert marker not in text
    # 整段被丢弃，不显示内部占位符
    assert "可拆分" not in text


# ---------------------------------------------------------------------------
# L. Warm Companion 红线
# ---------------------------------------------------------------------------


def test_warm_prompt_contains_fact_redlines():
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    system, user = build_warm_prompt(facts, context_type=CONTEXT_FEEDBACK)
    assert "快到了/快迟到" in system
    assert "晚间可用时间" in system
    assert "编造" in system


def test_warm_closing_fallback_on_failure():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(fail_agents={"p3.warm-companion"})
    )
    assert turn.companion_copy.closing == CLOSING_FALLBACK
    assert turn.result.allocation_plan.total_planned_minutes > 0


# ---------------------------------------------------------------------------
# M. Final Copy Reviewer 只润色文案
# ---------------------------------------------------------------------------


def test_copy_reviewer_revises_copy_without_touching_plan():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(copy_review=_COPY_REVIEW_REVISE)
    )
    assert turn.companion_copy.copy_reviewed is True
    assert turn.companion_copy.opening == "已经重新润色好的开场。"
    # 结构化计划事实未被文案修改
    assert turn.result.allocation_plan.total_planned_minutes > 0
    text = render_page_text(turn)
    assert "已经重新润色好的开场。" in text


def test_copy_reviewer_failure_keeps_previous_copy():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(fail_agents={"p3.copy-review"})
    )
    assert turn.companion_copy.copy_reviewed is False
    assert turn.companion_copy.opening == "这是为你整理好的当前安排："



# ---------------------------------------------------------------------------
# N/O. 失败安全
# ---------------------------------------------------------------------------


def test_expression_failure_fallback_and_plan_intact():
    controller, caller, expr, turn = _make_session(ExpressionCaller(fail_agents={
        "p3.plan-critic", "p3.lifestyle-review", "p3.plan-narrator",
        "p3.warm-companion", "p3.copy-review",
    }))
    assert turn.companion_copy.generated is False
    assert turn.companion_copy.opening == OPENING_FALLBACK
    assert turn.companion_copy.closing == CLOSING_FALLBACK
    assert turn.companion_copy.critic_approved is True  # critic 失败默认批准保留原计划
    # 主计划完整
    assert turn.result.allocation_plan.total_planned_minutes > 0
    text = render_page_text(turn)
    assert OPENING_FALLBACK in text


def test_critic_failure_defaults_to_approved_plan():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(fail_agents={"p3.plan-critic"})
    )
    assert turn.companion_copy.critic_approved is True
    assert "p3.plan-improver" not in expr.order
    assert turn.result.allocation_plan.total_planned_minutes > 0


# ---------------------------------------------------------------------------
# P/Q. rerun 不重复调用 / 反馈变化才重新生成
# ---------------------------------------------------------------------------


def test_rerun_same_turn_no_extra_expression_calls():
    controller, caller, expr, turn0 = _make_session(ExpressionCaller())
    n0 = len(expr.calls)
    # 相同初始 state 再次 start_day：命中缓存
    turn1 = controller.start_day(build_demo_state())
    assert turn1 is turn0
    assert len(expr.calls) == n0
    # 相同反馈文本重复提交：命中缓存
    controller.apply_feedback("我刚又做了30分钟实验")
    n1 = len(expr.calls)
    turn2 = controller.apply_feedback("我刚又做了30分钟实验")
    assert turn2 is not None
    assert len(expr.calls) == n1


def test_feedback_real_change_regenerates_expression():
    controller, caller, expr, turn0 = _make_session(ExpressionCaller())
    n0 = len(expr.calls)
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    assert len(expr.calls) == n0 + 5
    assert turn.companion_copy.context_type == CONTEXT_FEEDBACK


# ---------------------------------------------------------------------------
# S/T. sanitizer 与页面栏目
# ---------------------------------------------------------------------------


def test_sanitizer_blocks_internal_words_in_new_fields():
    leak_narrator = _narrator_json("schema_version 泄漏到 opening")
    leak_warm = _warm_json("change_summary task_ref 泄漏")
    leak_lifestyle = _lifestyle_json([], None, "gap transition_buffer 泄漏")
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(narrator=leak_narrator, warm=leak_warm, lifestyle=leak_lifestyle)
    )
    text = render_page_text(turn)
    for marker in ("task_ref", "schema_version", "window_ref", "allocator",
                   "peak_multiplier", "transition_buffer", "allocation_ref"):
        assert marker not in text
    # 不安全字段被整体替换/跳过，不显示内部占位符
    assert "泄漏" not in text


def test_page_keeps_only_two_top_level_sections():
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(narrator=_NARRATOR_FULL)
    )
    text = render_page_text(turn)
    headers = [line for line in text.splitlines() if line.startswith("#")]
    assert headers == ["# 当前方案"]
    # 待确认问题只在存在问题时出现
    with_questions = render_page_text(turn, extra_questions=("请补充上课结束时间。",))
    headers2 = [line for line in with_questions.splitlines() if line.startswith("#")]
    assert headers2 == ["# 当前方案", "## 待确认问题"]


# ---------------------------------------------------------------------------
# 精简页面：实际计划为主体、顺序固定、每行独立
# ---------------------------------------------------------------------------


def test_plan_lines_keep_order_between_opening_and_closing():
    """实际计划行顺序与事实完全一致，且位于 opening 后、closing 前。"""
    plain_caller = SessionCaller(expression=ExpressionCaller())
    plain_controller = P2SessionController({}, plain_caller)
    plain_turn = plain_controller.start_day(build_demo_state())
    plain_text = render_page_text(plain_turn)
    assert plain_text.startswith("# 当前方案\n")
    plan_body = plain_text.splitlines()[1:]
    assert plan_body  # 存在真实计划行

    expr = ExpressionCaller(narrator=_NARRATOR_FULL)
    controller, caller, expr2, turn = _make_session(expr)
    text = render_page_text(turn)
    lines = text.splitlines()
    assert lines[0] == "# 当前方案"
    assert lines[1] == "同学你好，下午的安排已经整理好啦～"
    # 每一条计划独占一行，且与纯计划完全一致
    for i, plan_line in enumerate(plan_body):
        assert lines[2 + i] == plan_line
    closing_idx = 2 + len(plan_body)
    assert lines[closing_idx] == "按自己的节奏来，祝你今天顺利。"
    assert len(lines) == closing_idx + 1


def test_change_summary_sits_between_opening_and_plan():
    """feedback 真实变化时，本轮更新行位于 opening 后、计划行前，只占一行。"""
    expr = ExpressionCaller(narrator=_NARRATOR_FULL, warm=_WARM_CHANGE)
    controller, caller, expr2, turn0 = _make_session(expr)
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    text = render_page_text(turn)
    lines = text.splitlines()
    assert lines[0] == "# 当前方案"
    assert lines[1] == "同学你好，下午的安排已经整理好啦～"
    assert lines[2] == "本轮更新：实验进度增加30分钟，骑行安排保持不变。"
    assert lines[-1] == "按自己的节奏来，祝你今天顺利。"
    assert any("计组实验" in line for line in lines[3:-1])


# ---------------------------------------------------------------------------
# 文案 prompt 约束：opening 活泼短、closing 丰富、reviewer 不精简
# ---------------------------------------------------------------------------


def test_narrator_prompt_short_lively_and_feedback_continuous():
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    system, user = build_narrator_prompt(facts, context_type=CONTEXT_FEEDBACK)
    assert "1~2句" in system
    assert "活泼自然" in system or "活泼、亲切" in system
    assert "好嘞/收到/没问题" in system
    assert "不要重新说" in system
    assert "同学你好" in system  # 禁止项：不得重新问候
    assert "不要像客服" in system


def test_warm_prompt_covers_closing_scenarios():
    """closing 2~4 句，且覆盖临近上课/满/宽松/高峰/跳过任务五种真实场景。"""
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    system, user = build_warm_prompt(facts)
    for marker in (
        "2~4句话",
        "约50~120个中文字符",
        "快到了，先收拾好东西就出发吧",
        "不用为了赶计划把通勤弄得急匆匆",
        "不用把每一分钟都塞满",
        "路上别赶",
        "先放一放也没关系",
    ):
        assert marker in system


def test_copy_reviewer_prompt_keeps_closing_rich_and_blocks_boilerplate():
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    fields = {
        "opening": "安排好啦～",
        "change_summary": None,
        "gap_tip": None,
        "closing": "今天事情不少，不过已经帮你留好了收拾和通勤的时间～",
    }
    system, user = build_copy_review_prompt(fields, facts)
    for marker in (
        "closing 允许 2~4 句",
        "不要因为“精简”把它强行压成一句",
        "银行客服",
        "鸡汤",
        "不要因为“精简”",
    ):
        assert marker in system


def test_copy_reviewer_does_not_truncate_long_closing():
    """Final Copy Reviewer 的 revised closing 不会被程序截短成一句。"""
    long_closing = (
        "今天下午事情不少，不过已经给你把收拾和通勤的时间留出来啦，"
        "不用卡着最后一分钟赶。实验能推进多少就推进多少，没做完的之后再接着来就好。"
        "路上注意安全，也别忘了喝点水～祝你上课顺利！"
    )
    revised = {
        "opening": "安排好啦，照着下面的计划走就好～",
        "change_summary": None,
        "gap_tip": None,
        "closing": long_closing,
    }
    controller, caller, expr, turn = _make_session(
        ExpressionCaller(copy_review=_copy_review_json(False, revised))
    )
    assert turn.companion_copy.closing == long_closing
    assert len(long_closing) >= 50
    assert turn.result.allocation_plan.total_planned_minutes > 0


# ---------------------------------------------------------------------------
# extract_expression_facts 扩展事实
# ---------------------------------------------------------------------------


def test_expression_facts_extended_fields():
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    facts = extract_expression_facts(state, plan)
    assert facts["reference_datetime"] == "2026-09-01 09:00"
    assert facts["plan_span_minutes"] > 0
    assert facts["next_commitment"] == "10:00 上课"
    assert facts["minutes_to_next_commitment"] == 60
    assert facts["only_afternoon"] is False
    assert facts["movement_count"] == 0
    assert facts["peak_movement_count"] == 0
    assert facts["transition_buffer_count"] == 0
    assert facts["location_change_count"] == 0
    assert any(t["title"] == "计组实验3" for t in facts["tasks_detail"])
    assert facts["remaining_minutes"] > 0


def test_expression_facts_capture_movement_peak_buffer():
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    block = make_block(
        "day_window_001", datetime(2026, 9, 1, 9, 0), "9斋", "31教",
        mode=TransportMode.BIKE, minutes=5, peak_bike=True, transition_minutes=5,
    )
    facts = extract_expression_facts(state, plan, (block,), current_location="9斋")
    assert facts["movement_count"] == 1
    assert facts["peak_movement_count"] == 1
    assert facts["transition_buffer_count"] == 1
    assert facts["location_change_count"] == 1
    assert facts["current_location"] == "9斋"
    mv = facts["movement_facts"][0]
    assert mv["mode"] == "骑行"
    assert mv["destination"] == "31教"
    assert mv["peak_bike"] is True
    assert mv["buffer_minutes"] == 5


# ---------------------------------------------------------------------------
# P3e 地点事实安全：禁止表达层改写/拼接地图地点名称
# ---------------------------------------------------------------------------

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


class SequenceCaller:
    """按顺序返回输出（用于地点修复成功/失败场景）。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            return "{}"
        return self.outputs.pop(0)


def _facts_with_map(real_map, with_block=True):
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    blocks = ()
    if with_block:
        block = make_block(
            "day_window_001",
            datetime(2026, 9, 1, 9, 0),
            "9斋",
            "31教",
            mode=TransportMode.WALK,
            minutes=7,
            origin_node_id="zhengyuan_9zhai",
            destination_node_id="building_31",
        )
        blocks = (block,)
    return extract_expression_facts(
        state, plan, blocks, current_location="9斋", map_data=real_map
    )


def test_expression_facts_trusted_locations(real_map):
    facts = _facts_with_map(real_map)
    locs = {loc["display_name"]: loc for loc in facts["trusted_locations"]}
    assert "31教" in locs
    assert locs["31教"]["node_id"] == "building_31"
    assert "31教" in locs["31教"]["allowed_aliases"]
    assert "9斋" in locs
    # 全量词表包含齐园与31教，用于拦截“齐园31教学楼”拼接
    assert "齐园" in facts["location_lexicon"]
    assert "31教" in facts["location_lexicon"]
    mv = facts["movement_facts"][0]
    assert mv["destination"] == "31教"
    assert mv["origin"] == "9斋"


def test_validator_rejects_hallucinated_location(real_map):
    facts = _facts_with_map(real_map)
    for text in (
        "步行前往齐园31教学楼",
        "前往齐园31教改为步行",
        "齐园31楼集合",
        "31教东门集合",
    ):
        assert validate_expression_locations(text, facts) is False
    for text in (
        "步行前往31教",
        "到31教上课",
        "接下来去9斋收拾东西",
        "今天节奏比较从容，不用赶。",
    ):
        assert validate_expression_locations(text, facts) is True


def test_validator_no_lexicon_skips_check():
    facts = {"location_lexicon": (), "trusted_locations": ()}
    assert validate_expression_locations("今天安排比较从容，不用赶。", facts) is True


def test_narrator_location_hallucination_repaired(real_map):
    facts = _facts_with_map(real_map)
    caller = SequenceCaller([
        _narrator_json("步行前往齐园31教学楼，安心出发吧～"),
        _narrator_json("步行前往31教，安心出发吧～"),
    ])
    copy = run_narrator(facts, caller, repair_caller=caller)
    assert copy.opening == "步行前往31教，安心出发吧～"
    assert copy.generated is True
    assert "齐园31教学楼" not in copy.opening


def test_narrator_location_hallucination_falls_back(real_map):
    facts = _facts_with_map(real_map)
    bad = _narrator_json("步行前往齐园31教学楼，安心出发吧～")
    caller = SequenceCaller([bad, bad])
    copy = run_narrator(facts, caller, repair_caller=caller)
    assert copy.opening == OPENING_FALLBACK
    assert copy.generated is False


def test_warm_companion_location_hallucination_repaired(real_map):
    facts = _facts_with_map(real_map)
    caller = SequenceCaller([
        _warm_json(None, "接下来步行前往齐园31教学楼，路上注意安全。"),
        _warm_json(None, "接下来步行前往31教，路上注意安全。"),
    ])
    warm = run_warm_companion(facts, caller, repair_caller=caller)
    assert warm.closing == "接下来步行前往31教，路上注意安全。"
    assert "齐园31教学楼" not in warm.closing


def test_warm_companion_location_hallucination_falls_back(real_map):
    facts = _facts_with_map(real_map)
    bad = _warm_json(None, "接下来步行前往齐园31教学楼，路上注意安全。")
    caller = SequenceCaller([bad, bad])
    warm = run_warm_companion(facts, caller, repair_caller=caller)
    assert warm.closing == CLOSING_FALLBACK
    assert warm.change_summary is None


def test_copy_reviewer_cannot_rewrite_location(real_map):
    facts = _facts_with_map(real_map)
    caller = ExpressionCaller(
        narrator=_narrator_json("步行前往31教，安心出发吧～"),
        warm=_warm_json(None, "路上注意安全～"),
        copy_review=_copy_review_json(False, {
            "opening": "步行前往齐园31教学楼，安心出发吧～",
            "change_summary": None,
            "gap_tip": None,
            "closing": "路上注意安全～",
        }),
    )
    copy = generate_expression_bundle(facts, caller, budget=None)
    # reviewer 把受信地点改坏 -> 修订结果不被采用，保留原文案
    assert copy.opening == "步行前往31教，安心出发吧～"
    assert copy.copy_reviewed is False
    assert "齐园31教学楼" not in copy.opening


def test_change_summary_location_guard(real_map):
    facts = _facts_with_map(real_map)
    caller = ExpressionCaller(
        narrator=_narrator_json("好嘞，已经重新帮你排好啦～"),
        warm=_warm_json("前往齐园31教学楼的方式改为步行", "路上注意安全～"),
    )
    copy = generate_expression_bundle(
        facts, caller, context_type=CONTEXT_FEEDBACK, change_facts={"changed": True}, budget=None
    )
    # 含不受信地点的 change_summary 被整段丢弃
    assert copy.change_summary is None
    assert copy.closing == "路上注意安全～"


def test_session_copy_blocks_hallucinated_location(real_map):
    expr = ExpressionCaller(
        narrator=_narrator_json("步行前往齐园31教学楼，安心出发吧～"),
        warm=_warm_json(None, "接下来步行前往齐园31教学楼，路上注意安全。"),
    )
    controller, caller, expr2, turn = _make_session(expr, map_data=real_map)
    text = render_page_text(turn)
    assert "齐园31教学楼" not in text
    assert "齐园31教" not in text
    assert turn.companion_copy.opening == OPENING_FALLBACK
    assert turn.companion_copy.closing == CLOSING_FALLBACK
    # 主计划不受表达层影响
    assert turn.result.allocation_plan.total_planned_minutes > 0


# ---------------------------------------------------------------------------
# P3e 事实安全扩展：明确时间 / movement mode / travel duration
# ---------------------------------------------------------------------------


def _facts_afternoon(real_map):
    """walk 7分钟到31教 + 15:20 上课 的受信事实（不含 15:30 等干扰时间）。"""
    facts = dict(_facts_with_map(real_map))
    facts["next_commitment"] = "15:20 上课"
    facts["minutes_to_next_commitment"] = 60
    facts["commitments"] = ("上课 15:20-16:50",)
    facts["tasks"] = ()
    facts["plan_start"] = "13:00"
    facts["plan_end"] = "15:20"
    facts["idle_gaps"] = ()
    return facts


def _facts_peak_bike(real_map):
    """高峰骑行：effective=10 分钟（base=5）。"""
    state = build_demo_state()
    plan = allocate_tasks_across_windows(state)
    block = make_block(
        "day_window_001",
        datetime(2026, 9, 1, 9, 0),
        "9斋",
        "31教",
        mode=TransportMode.BIKE,
        minutes=10,
        peak_bike=True,
        transition_minutes=5,
        origin_node_id="zhengyuan_9zhai",
        destination_node_id="building_31",
    )
    return extract_expression_facts(
        state,
        plan,
        (block,),
        current_location="9斋",
        map_data=real_map,
    )


def test_validate_explicit_time_consistency(real_map):
    facts = _facts_afternoon(real_map)
    # 真实15:20，写15:30 -> 拒绝
    assert validate_expression_facts("15:30上课", facts) is False
    # 自然等价格式（下午3点20） -> 通过
    assert validate_expression_facts("下午3点20上课", facts) is True
    # 15点20分 等价写法 -> 通过
    assert validate_expression_facts("15点20分上课", facts) is True
    # 不提时间 -> 通过
    assert validate_expression_facts("先安心出发吧～", facts) is True


def test_validate_movement_mode_consistency(real_map):
    facts = _facts_with_map(real_map)  # 真实 walk 前往31教
    assert validate_expression_facts("骑车去31教", facts) is False
    assert validate_expression_facts("骑行去31教", facts) is False
    assert validate_expression_facts("走去31教", facts) is True
    assert validate_expression_facts("慢慢走过去就好～", facts) is True
    assert validate_expression_facts("到31教上课", facts) is True


def test_validate_travel_duration_consistency(real_map):
    facts = _facts_with_map(real_map)  # effective=7 分钟
    assert validate_expression_facts("约7分钟", facts) is True
    assert validate_expression_facts("差不多7分钟", facts) is True
    assert validate_expression_facts("5分钟就到", facts) is False
    assert validate_expression_facts("十几分钟就能到", facts) is False


def test_validate_peak_uses_effective_duration(real_map):
    facts = _facts_peak_bike(real_map)  # base=5 / effective=10
    assert validate_expression_facts("约5分钟", facts) is False
    assert validate_expression_facts("约10分钟", facts) is True


def test_validate_omits_claims_without_killing(real_map):
    facts = _facts_with_map(real_map)
    for text in (
        "好啦，接下来慢慢出发就行～",
        "今天的安排已经整理好了。",
        "路上注意安全，也别忘了喝点水～",
    ):
        assert validate_expression_facts(text, facts) is True


def test_validate_relative_time_claims(real_map):
    facts = dict(_facts_afternoon(real_map))
    facts["minutes_to_next_commitment"] = 60
    facts["idle_gaps"] = ()
    # 事实不支持“还有半小时” -> 拒绝
    assert validate_expression_facts("还有半小时上课", facts) is False
    # 存在30分钟真实空档 -> 允许
    facts2 = dict(facts)
    facts2["idle_gaps"] = ({"minutes": 30, "start": None, "end": None},)
    assert validate_expression_facts("还有半小时可以休息", facts2) is True


def test_reviewer_rewrites_hard_facts_not_adopted(real_map):
    facts = _facts_afternoon(real_map)
    correct_opening = "15:20上课，步行前往31教，约7分钟"
    caller = ExpressionCaller(
        narrator=_narrator_json(correct_opening),
        warm=_warm_json(None, "路上注意安全～"),
        copy_review=_copy_review_json(False, {
            "opening": "15:30上课，骑车去31教，约5分钟",
            "change_summary": None,
            "gap_tip": None,
            "closing": "路上注意安全～",
        }),
    )
    copy = generate_expression_bundle(facts, caller, budget=None)
    # Reviewer 改坏硬事实 -> 修订不被采用，保留正确原文案
    assert copy.opening == correct_opening
    assert copy.copy_reviewed is False


# ---------------------------------------------------------------------------
# P3e 真实验收回归：validator 误判边界 / 表达层局部降级
# ---------------------------------------------------------------------------


def _raising_validator(text, facts):
    raise RuntimeError("validator internal error")


def test_task_and_rest_durations_not_travel_duration(real_map):
    """任务剩余时长 / 休息 / 收拾等普通数字不得被误判为 movement travel duration。"""
    facts = _facts_with_map(real_map)  # 真实 movement effective=7 分钟
    for text in (
        "实验还有一个半小时。",
        "先做10分钟单词。",
        "提前5分钟收拾东西。",
        "休息5分钟也行。",
        "喝两口水，歇一小会儿。",
        "今天有两件事，一件件来就好。",
        "先做一会儿计组实验，后面再出发。",
    ):
        assert validate_expression_facts(text, facts) is True, text


def test_chinese_time_and_relative_boundaries(real_map):
    """中文时间格式边界：全角冒号 / 点式 / 范围 / 口语相对时间。"""
    facts = dict(_facts_afternoon(real_map))  # 真实 15:20 上课
    # 等价写法通过
    assert validate_expression_facts("15：20上课", facts) is True
    assert validate_expression_facts("下午3点20上课", facts) is True
    assert validate_expression_facts("15:20-16:50上课", facts) is True
    assert validate_expression_facts("15点20分上课", facts) is True
    # 明确写错时间拒绝
    assert validate_expression_facts("15:30上课", facts) is False
    # 不提时间不误杀
    assert validate_expression_facts("先安心出发吧～", facts) is True

    # 相对时间：只按真实 minutes_to_next / idle gap 校验
    rel = dict(facts)
    rel["minutes_to_next_commitment"] = 10
    rel["idle_gaps"] = ()
    assert validate_expression_facts("还有十分钟上课", rel) is True
    assert validate_expression_facts("十分钟后出发", rel) is True
    assert validate_expression_facts("还有半小时上课", rel) is False


def test_final_facts_guard_replaces_only_invalid_fields(real_map):
    """final guard 失败：单字段回退，不是整轮失败。"""
    facts = _facts_with_map(real_map)  # walk 7分钟 31教
    copy = CompanionCopy(
        opening="步行前往31教，安心出发吧～",
        closing="骑车去31教，5分钟就到。",
        change_summary=None,
        gap_tip=None,
        generated=True,
        context_type=CONTEXT_INITIAL,
    )
    fallback_opening, fallback_closing = companion_fallbacks(CONTEXT_INITIAL)
    guarded = _final_facts_guard(copy, facts, fallback_opening, fallback_closing)
    # 合法字段保留，非法字段整段回退
    assert guarded.opening == "步行前往31教，安心出发吧～"
    assert guarded.closing == fallback_closing


def test_validator_exception_degrades_to_fallback_plan_intact(real_map, monkeypatch):
    """factual validator 自身抛异常时：表达层整体降级为安全 fallback，主计划仍完整。"""
    import src.p2_expression_agents as expr_mod

    monkeypatch.setattr(expr_mod, "validate_expression_facts", _raising_validator)
    expr = ExpressionCaller(
        narrator=_narrator_json("步行前往31教，安心出发吧～"),
        warm=_warm_json(None, "路上注意安全～"),
    )
    controller, caller, expr2, turn = _make_session(expr, map_data=real_map)
    assert turn.companion_copy.opening == OPENING_FALLBACK
    assert turn.companion_copy.closing == CLOSING_FALLBACK
    assert turn.companion_copy.generated is False
    # 结构化主计划不受表达层失败影响
    assert turn.result.allocation_plan.total_planned_minutes > 0
    text = render_page_text(turn)
    assert OPENING_FALLBACK in text
    assert "当前方案" in text  # 主计划区块仍渲染


def test_narrator_parse_failure_then_repair_fallback(real_map):
    """Narrator 正常调用解析失败 -> repair 成功 -> 文案可用；repair 仍失败 -> fallback。"""
    facts = _facts_with_map(real_map)
    # repair 成功：第一次坏 JSON，第二次合法
    caller = SequenceCaller([
        "这不是 JSON",
        _narrator_json("步行前往31教，安心出发吧～"),
    ])
    copy = run_narrator(facts, caller, repair_caller=caller)
    assert copy.opening == "步行前往31教，安心出发吧～"
    # repair 也失败：fallback
    caller2 = SequenceCaller(["坏输出", "还是坏输出"])
    copy2 = run_narrator(facts, caller2, repair_caller=caller2)
    assert copy2.opening == OPENING_FALLBACK
    assert copy2.generated is False

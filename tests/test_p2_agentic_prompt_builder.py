from datetime import datetime

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel
from src.p2_agentic_models import (
    DAY_PLAN_INTENT_SCHEMA_VERSION,
    RECONCILIATION_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    DayPlanIntent,
    ReviewResult,
)
from src.p2_agentic_prompt_builder import (
    build_day_plan_prompt,
    build_reconciliation_prompt,
    build_repair_prompt,
    build_review_prompt,
    build_revision_prompt,
    format_history,
)
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_window_derivation import derive_active_window_ref


def dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def make_task():
    return TaskProgress(
        "day_task_001",
        "计组实验3",
        120,
        50,
        SourceKind.AI_EXTRACTED_FROM_USER_TEXT,
        TaskState.ACTIVE,
        True,
        30,
    )


def build_state(history=()):
    now = dt(9)
    window = DayWindow(
        "day_window_001",
        now,
        dt(10),
        AvailabilityLevel.FULLY_AVAILABLE,
        0,
        0,
        None,
        60,
    )
    active = derive_active_window_ref((window,), now)
    return DayPlanningState(
        now, now, dt(22), (), (make_task(),), (window,), active, (), tuple(history)
    )


def make_intent():
    return DayPlanIntent(
        DAY_PLAN_INTENT_SCHEMA_VERSION,
        ("day_task_001",),
        False,
        (),
        None,
    )


def make_review():
    return ReviewResult(REVIEW_SCHEMA_VERSION, "revise", "应优先 B", ("day_task_001",), False)


def make_plan(state):
    return allocate_tasks_across_windows(state)


def test_reconciliation_system_contains_required_examples():
    system, _ = build_reconciliation_prompt(build_state(), "我刚才又做了30分钟。")
    for marker in (
        "我刚才又做了30分钟",
        "今天先不背单词了",
        "这个任务以后都不做了",
        "我已经把实验报告做完了",
        "我想完成计组实验3",
        "实验我又做了30分钟",
        "另外今天还要整理操作系统实验",
        "又想背单词了",
        "还是背单词吧",
        "把背单词加回来",
        "我改变主意了，单词还是要背",
        "resume_today",
    ):
        assert marker in system


def test_reconciliation_user_contains_ledger_without_repr():
    _, user = build_reconciliation_prompt(build_state(), "我刚才又做了30分钟。")
    assert "day_task_001" in user
    assert "计组实验3" in user
    assert "TaskProgress(" not in user
    assert "object at" not in user
    assert "TaskState." not in user


def test_day_plan_system_contains_examples_and_rules():
    system, _ = build_day_plan_prompt(build_state(), "安排一下。")
    for marker in (
        "task_order",
        "include_low_attention",
        "estimated_total_minutes",
        "用户已经明确给出的总时长不得覆盖",
        "completed / skipped_today / abandoned",
    ):
        assert marker in system


def test_day_plan_user_contains_windows_and_capacity():
    _, user = build_day_plan_prompt(build_state(), "安排一下。")
    assert "day_window_001" in user
    assert "capacity=60" in user
    assert "09:00 - 10:00" in user


def test_review_system_respects_program_facts():
    state = build_state()
    system, _ = build_review_prompt(state, "安排一下。", make_intent(), make_plan(state))
    assert "不要重新做数学求解" in system
    assert "不要要求修改 capacity" in system
    assert "符合用户意图与常识" in system


def test_revision_prompt_contains_review_suggestions():
    state = build_state()
    plan = make_plan(state)
    system, user = build_revision_prompt(
        state, "安排一下。", make_intent(), plan, make_review()
    )
    assert "应优先 B" in user
    assert "suggested_task_order: day_task_001" in user


def test_repair_prompt_does_not_reinterpret():
    system, user = build_repair_prompt("reconciliation", "坏内容", RECONCILIATION_SCHEMA_VERSION)
    assert "不重新解释用户意图" in system
    assert "不添加新事实" in system
    assert "坏内容" in user


def test_format_history_limited_to_ten():
    history = tuple("entry-{}".format(i) for i in range(12))
    rendered = format_history(history)
    expected = tuple("- entry-{}".format(i) for i in range(2, 12))
    assert tuple(rendered.splitlines()) == expected


def test_all_prompts_are_non_empty_strings():
    state = build_state()
    plan = make_plan(state)
    builders = (
        build_reconciliation_prompt(state, "x"),
        build_day_plan_prompt(state, "x"),
        build_review_prompt(state, "x", make_intent(), plan),
        build_revision_prompt(state, "x", make_intent(), plan, make_review()),
        build_repair_prompt("day_plan", "x", DAY_PLAN_INTENT_SCHEMA_VERSION),
    )
    for system, user in builders:
        assert isinstance(system, str) and system.strip()
        assert isinstance(user, str) and user.strip()

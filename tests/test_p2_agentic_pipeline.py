from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_pipeline import run_p2_agentic_day_planning
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_window_derivation import derive_active_window_ref

RECONCILIATION_NONE = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": [], "questions": []}'
)

RECONCILIATION_PROGRESS_30 = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": 30, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "none", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)

RECONCILIATION_SKIP = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_002", "new_task_title": null, '
    '"progress_delta_minutes": null, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "skip_today", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)

RECONCILIATION_ABANDON = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": null, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "abandon", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)

RECONCILIATION_NEW_TASK = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": null, "new_task_title": "整理操作系统实验", '
    '"progress_delta_minutes": null, "set_total_minutes": 120, '
    '"set_total_source": "user_text", "lifecycle_action": "none", '
    '"is_splittable": true, "minimum_slice_minutes": 30}], "questions": []}'
)

RECONCILIATION_AMBIGUOUS = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": [], '
    '"questions": ["你说的是哪个实验？"]}'
)

PLAN_INTENT_A = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001"], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)

PLAN_INTENT_AB = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001", "day_task_002"], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)

PLAN_INTENT_BA = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_002", "day_task_001"], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)

PLAN_INTENT_WITH_SKIPPED = PLAN_INTENT_BA

PLAN_INTENT_WITH_NEW = (
    '{"schema_version": "p2.day-plan-intent.v1", '
    '"task_order": ["day_task_001", "day_task_002", "day_task_003"], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)

PLAN_INTENT_ESTIMATE = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001"], '
    '"include_low_attention": false, "task_estimates": ['
    '{"task_ref": "day_task_001", "estimated_total_minutes": 100, '
    '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
)

PLAN_INTENT_ESTIMATE_180 = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001"], '
    '"include_low_attention": false, "task_estimates": ['
    '{"task_ref": "day_task_001", "estimated_total_minutes": 180, '
    '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
)

REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)

REVIEW_REVISE_BA = (
    '{"schema_version": "p2.day-review.v1", "decision": "revise", '
    '"reason": "应优先 B", "suggested_task_order": ["day_task_002", "day_task_001"], '
    '"include_low_attention": null}'
)


def dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def make_task(
    ref="day_task_001",
    title="计组实验3",
    total=120,
    completed=0,
    state=TaskState.ACTIVE,
    splittable=True,
    min_slice=30,
    source=SourceKind.AI_ESTIMATED,
):
    return TaskProgress(ref, title, total, completed, source, state, splittable, min_slice)


def window(ref, start, end, capacity, availability=AvailabilityLevel.FULLY_AVAILABLE):
    return DayWindow(ref, start, end, availability, 0, 0, None, capacity)


def build_state(tasks, windows, now=None, history=()):
    now = now or dt(9)
    active = derive_active_window_ref(tuple(windows), now)
    return DayPlanningState(
        now, now, dt(22), (), tuple(tasks), tuple(windows), active, (), tuple(history)
    )


class ScriptedCaller:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def __call__(self, system, user):
        self.prompts.append((system, user))
        if not self.outputs:
            raise AssertionError("unexpected extra model call")
        return self.outputs.pop(0)


# ---------------------------------------------------------------------------
# 场景 A：用户报告部分完成
# ---------------------------------------------------------------------------


def test_scenario_a_partial_completion():
    state = build_state(
        (make_task(completed=50),),
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
    )
    result = run_p2_agentic_day_planning(
        state,
        "我刚才又做了30分钟。",
        ScriptedCaller([RECONCILIATION_PROGRESS_30, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    updated = result.updated_state.tasks[0]
    assert updated.completed_minutes == 80
    assert updated.remaining_minutes == 40
    assert result.allocation_plan.total_planned_minutes == 40
    assert result.call_count == 3
    assert result.warnings == ()


# ---------------------------------------------------------------------------
# 场景 B：今天跳过任务
# ---------------------------------------------------------------------------


def test_scenario_b_skip_today_no_allocation():
    tasks = (
        make_task(ref="day_task_001", title="计组实验3"),
        make_task(ref="day_task_002", title="背单词", total=30, splittable=False),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 200),))
    result = run_p2_agentic_day_planning(
        state,
        "今天先不背单词了。",
        ScriptedCaller([RECONCILIATION_SKIP, PLAN_INTENT_WITH_SKIPPED, REVIEW_ACCEPT]),
    )
    by_ref = {task.task_ref: task for task in result.updated_state.tasks}
    assert by_ref["day_task_002"].state == TaskState.SKIPPED_TODAY
    planned = result.allocation_plan.planned_minutes_by_task
    assert "day_task_002" not in planned


# ---------------------------------------------------------------------------
# 场景 C：永久放弃
# ---------------------------------------------------------------------------


def test_scenario_c_abandon_not_resurrected():
    state = build_state(
        (make_task(ref="day_task_001", title="A", total=30, splittable=False),),
        (window("w1", dt(9), dt(10), 100),),
    )
    first = run_p2_agentic_day_planning(
        state,
        "这个任务以后都不做了。",
        ScriptedCaller([RECONCILIATION_ABANDON, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert first.updated_state.tasks[0].state == TaskState.ABANDONED
    second = run_p2_agentic_day_planning(
        first.updated_state,
        "重新安排。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert second.allocation_plan.allocations == ()


# ---------------------------------------------------------------------------
# 场景 D：新任务稳定 ref
# ---------------------------------------------------------------------------


def test_scenario_d_new_task_stable_ref():
    tasks = (
        make_task(ref="day_task_001", title="计组实验3"),
        make_task(ref="day_task_002", title="实验报告", total=60, splittable=False),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 300),))

    def run():
        return run_p2_agentic_day_planning(
            state,
            "另外今天还要整理操作系统实验。",
            ScriptedCaller([RECONCILIATION_NEW_TASK, PLAN_INTENT_WITH_NEW, REVIEW_ACCEPT]),
        )

    first = run()
    second = run()
    assert first.reconciliation_result.updates[0].new_task_title == "整理操作系统实验"
    assert first.updated_state.tasks[-1].task_ref == "day_task_003"
    assert second.updated_state.tasks[-1].task_ref == "day_task_003"
    assert "day_task_003" in first.allocation_plan.planned_minutes_by_task


# ---------------------------------------------------------------------------
# 场景 E：ambiguous identity
# ---------------------------------------------------------------------------


def test_scenario_e_ambiguous_identity():
    tasks = (
        make_task(ref="day_task_001", title="计组实验3", completed=50),
        make_task(ref="day_task_002", title="物理实验报告", total=90, completed=20),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 300),))
    result = run_p2_agentic_day_planning(
        state,
        "实验我又做了30分钟。",
        ScriptedCaller([RECONCILIATION_AMBIGUOUS, PLAN_INTENT_AB, REVIEW_ACCEPT]),
    )
    assert result.questions == ("你说的是哪个实验？",)
    by_ref = {task.task_ref: task for task in result.updated_state.tasks}
    assert by_ref["day_task_001"].completed_minutes == 50
    assert by_ref["day_task_002"].completed_minutes == 20
    assert result.allocation_plan is not None


# ---------------------------------------------------------------------------
# 场景 F：未知 duration AI 暂估
# ---------------------------------------------------------------------------


def test_scenario_f_ai_estimate_unknown_duration():
    task = make_task(
        title="整理数据库实验", total=None, completed=20, splittable=None, min_slice=None, source=None
    )
    state = build_state((task,), (window("w1", dt(9), dt(10, 30), 90),))
    result = run_p2_agentic_day_planning(
        state,
        "帮我把数据库实验安排进今天。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_ESTIMATE, REVIEW_ACCEPT]),
    )
    updated = result.updated_state.tasks[0]
    assert updated.total_minutes == 100
    assert updated.total_source == SourceKind.AI_ESTIMATED
    assert updated.completed_minutes == 20
    assert updated.remaining_minutes == 80
    assert result.allocation_plan.total_planned_minutes == 80


# ---------------------------------------------------------------------------
# 场景 G：AI 不覆盖已有 total
# ---------------------------------------------------------------------------


def test_scenario_g_ai_does_not_override_known_total():
    task = make_task(total=120, completed=0, source=SourceKind.AI_EXTRACTED_FROM_USER_TEXT)
    state = build_state((task,), (window("w1", dt(9), dt(10, 30), 90),))
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_ESTIMATE_180, REVIEW_ACCEPT]),
    )
    updated = result.updated_state.tasks[0]
    assert updated.total_minutes == 120
    assert updated.total_source == SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert any("已忽略" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# 场景 H：Agent 决定任务顺序
# ---------------------------------------------------------------------------


def test_scenario_h_agent_task_order():
    tasks = (
        make_task(ref="day_task_001", title="A", total=30, splittable=False),
        make_task(ref="day_task_002", title="B", total=30, splittable=False),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 60),))
    result = run_p2_agentic_day_planning(
        state,
        "先做 B。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_BA, REVIEW_ACCEPT]),
    )
    assert result.allocation_plan.allocations[0].task_ref == "day_task_002"


# ---------------------------------------------------------------------------
# 场景 I：Review 要求修订（恰好一次 revision）
# ---------------------------------------------------------------------------


def test_scenario_i_review_revise_once():
    tasks = (
        make_task(ref="day_task_001", title="A", total=30, splittable=False),
        make_task(ref="day_task_002", title="B", total=30, splittable=False),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 60),))
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_REVISE_BA, PLAN_INTENT_BA]),
    )
    assert result.review_result.decision == "revise"
    assert result.revision_used is True
    assert result.call_count == 4
    assert result.allocation_plan.allocations[0].task_ref == "day_task_002"


# ---------------------------------------------------------------------------
# 场景 J：Review 错误不能破坏合法计划
# ---------------------------------------------------------------------------


def test_scenario_j_review_error_keeps_valid_plan():
    state = build_state(
        (make_task(total=30, splittable=False),),
        (window("w1", dt(9), dt(10), 60),),
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, "这不是JSON", "还是不行"]),
    )
    assert result.review_result is None
    assert any("审查暂未完成" in warning for warning in result.warnings)
    assert len(result.allocation_plan.allocations) == 1
    assert result.call_count == 4


# ---------------------------------------------------------------------------
# 场景 K：Reconciliation 格式失败后 Repair 成功
# ---------------------------------------------------------------------------


def test_scenario_k_reconciliation_repair_success():
    state = build_state(
        (make_task(completed=50),),
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
    )
    result = run_p2_agentic_day_planning(
        state,
        "我刚才又做了30分钟。",
        ScriptedCaller([RECONCILIATION_PROGRESS_30[:5], RECONCILIATION_PROGRESS_30, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert result.updated_state.tasks[0].completed_minutes == 80
    assert result.call_count == 4


# ---------------------------------------------------------------------------
# 场景 L：Reconciliation 连 repair 都失败
# ---------------------------------------------------------------------------


def test_scenario_l_reconciliation_repair_fails():
    state = build_state(
        (make_task(completed=50),),
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
    )
    result = run_p2_agentic_day_planning(
        state,
        "我刚才又做了30分钟。",
        ScriptedCaller(["坏格式", "还是坏", PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert result.updated_state.tasks[0].completed_minutes == 50
    assert any("暂未结构化应用" in warning for warning in result.warnings)
    assert result.allocation_plan.total_planned_minutes == 70
    assert result.call_count == 4


# ---------------------------------------------------------------------------
# 场景 M：调用硬上限
# ---------------------------------------------------------------------------


def test_scenario_m_hard_cap_with_small_budget():
    state = build_state((make_task(),), (window("w1", dt(9), dt(10), 60),))
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller(["坏"] * 20),
        max_calls_per_round=4,
    )
    assert result.call_count == 4
    assert result.allocation_plan is not None


def test_scenario_m_default_budget_never_exceeded():
    state = build_state((make_task(),), (window("w1", dt(9), dt(10), 60),))
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller(["坏"] * 20),
    )
    assert result.call_count <= 10
    assert result.call_count == 6


def test_max_calls_above_cap_rejected():
    state = build_state((make_task(),), (window("w1", dt(9), dt(10), 60),))
    with pytest.raises(ValueError):
        run_p2_agentic_day_planning(
            state,
            "安排一下。",
            ScriptedCaller([]),
            max_calls_per_round=25,
        )


# ---------------------------------------------------------------------------
# 场景 N：history
# ---------------------------------------------------------------------------


def test_scenario_n_history_capped():
    history = tuple("历史{}".format(i) for i in range(10))
    state = build_state(
        (make_task(total=30, splittable=False),),
        (window("w1", dt(9), dt(10), 60),),
        history=history,
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert len(result.updated_state.history) == 10
    assert "历史0" not in result.updated_state.history
    assert result.updated_state.history[-1] == "当前计划已重新生成。"


# ---------------------------------------------------------------------------
# 场景 O：重新规划不改 completed
# ---------------------------------------------------------------------------


def test_scenario_o_replan_keeps_completed():
    tasks = (
        make_task(ref="day_task_001", title="A", total=120, completed=50),
        make_task(ref="day_task_002", title="B", total=30, completed=0, splittable=False),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 90),))
    first = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    second = run_p2_agentic_day_planning(
        state,
        "再安排。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_BA, REVIEW_ACCEPT]),
    )
    assert state.tasks[0].completed_minutes == 50
    assert first.updated_state.tasks[0].completed_minutes == 50
    assert second.updated_state.tasks[0].completed_minutes == 50


# ---------------------------------------------------------------------------
# 其他护栏
# ---------------------------------------------------------------------------


def test_input_state_immutable():
    state = build_state(
        (make_task(completed=50),),
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
    )
    result = run_p2_agentic_day_planning(
        state,
        "我刚才又做了30分钟。",
        ScriptedCaller([RECONCILIATION_PROGRESS_30, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert result.original_state == state
    assert state.tasks[0].completed_minutes == 50


def test_summary_contains_no_internal_refs():
    state = build_state((make_task(),), (window("w1", dt(9), dt(10), 60),))
    result = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    lines = []
    if result.day_summary.current_action_line:
        lines.append(result.day_summary.current_action_line)
    lines.extend(result.day_summary.later_window_lines)
    lines.extend(result.day_summary.unallocated_lines)
    text = "\n".join(lines)
    assert "day_task" not in text
    assert "allocation_" not in text
    assert "day_window" not in text
    assert "TaskState" not in text
    assert "object at" not in text


def test_low_attention_excluded_by_default_and_included_when_asked():
    tasks = (make_task(total=60, splittable=True, min_slice=30),)
    windows = (
        window("w1", dt(9), dt(9, 30), 30),
        window("w2", dt(10), dt(11), 60, availability=AvailabilityLevel.LOW_ATTENTION),
    )
    state = build_state(tasks, windows)
    default = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert default.allocation_plan.total_planned_minutes == 30

    low_intent = (
        '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001"], '
        '"include_low_attention": true, "task_estimates": [], "rationale": null}'
    )
    enabled = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, low_intent, REVIEW_ACCEPT]),
    )
    assert enabled.allocation_plan.total_planned_minutes == 60
    assert any("低注意力" in warning for warning in enabled.allocation_plan.warnings)


def test_day_plan_unknown_ref_falls_back():
    state = build_state(
        (make_task(total=30, splittable=False),),
        (window("w1", dt(9), dt(10), 60),),
    )
    bad_intent = (
        '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_999"], '
        '"include_low_attention": false, "task_estimates": [], "rationale": null}'
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, bad_intent, "坏", REVIEW_ACCEPT]),
    )
    assert any("未通过校验" in warning for warning in result.warnings)
    assert len(result.allocation_plan.allocations) == 1


def test_day_plan_duplicate_ref_falls_back():
    state = build_state(
        (make_task(total=30, splittable=False),),
        (window("w1", dt(9), dt(10), 60),),
    )
    dup_intent = (
        '{"schema_version": "p2.day-plan-intent.v1", '
        '"task_order": ["day_task_001", "day_task_001"], '
        '"include_low_attention": false, "task_estimates": [], "rationale": null}'
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, dup_intent, "坏", REVIEW_ACCEPT]),
    )
    assert any("未通过校验" in warning for warning in result.warnings)


def test_day_plan_failure_uses_default_order():
    state = build_state(
        (make_task(total=30, splittable=False),),
        (window("w1", dt(9), dt(10), 60),),
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, "坏", "坏", REVIEW_ACCEPT]),
    )
    assert any("已使用默认任务顺序" in warning for warning in result.warnings)
    assert len(result.allocation_plan.allocations) == 1


def test_revision_invalid_keeps_original_plan():
    tasks = (
        make_task(ref="day_task_001", title="A", total=30, splittable=False),
        make_task(ref="day_task_002", title="B", total=30, splittable=False),
    )
    state = build_state(tasks, (window("w1", dt(9), dt(10), 60),))
    bad_revision = (
        '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_999"], '
        '"include_low_attention": false, "task_estimates": [], "rationale": null}'
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排。",
        ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_REVISE_BA, bad_revision]),
    )
    assert result.revision_used is False
    assert any("修订建议未被应用" in warning for warning in result.warnings)
    assert result.allocation_plan.allocations[0].task_ref == "day_task_001"


SPLITTABILITY_REVIEW_TRUE = (
    '{"schema_version": "p2.splittability-review.v1", "is_splittable": true, '
    '"minimum_slice_minutes": 20, "reason": "实验可以分段推进"}'
)
SPLITTABILITY_REVIEW_FALSE = (
    '{"schema_version": "p2.splittability-review.v1", "is_splittable": false, '
    '"minimum_slice_minutes": null, "reason": "必须一次完成"}'
)


def test_splittability_review_turns_partial_lab_into_scheduled():
    """真实场景：实验剩余90分钟、窗口约75分钟，首次估计 is_splittable=false。
    应触发一次复核，复核后安排部分实验，剩余继续。"""
    now = dt(13, 35)
    w1 = DayWindow(
        "w1", dt(13, 35), dt(14, 50),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 75,
    )
    commitments = (
        FixedCommitment("c1", "上课", "上课", dt(15), dt(16), None,
                        AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    task = TaskProgress(
        "day_task_001", "计组实验", 90, 0, SourceKind.USER_STATED,
        TaskState.ACTIVE, False, 90,
    )
    state = DayPlanningState(now, now, dt(22), commitments, (task,), (w1,), "w1", (), ())
    caller = ScriptedCaller(
        [RECONCILIATION_NONE, PLAN_INTENT_A, SPLITTABILITY_REVIEW_TRUE, REVIEW_ACCEPT]
    )
    result = run_p2_agentic_day_planning(state, "安排一下。", caller)
    updated = {t.task_ref: t for t in result.updated_state.tasks}["day_task_001"]
    assert updated.is_splittable is True
    assert updated.minimum_slice_minutes == 20
    assert result.allocation_plan.total_planned_minutes == 75
    assert result.allocation_plan.allocations[0].task_ref == "day_task_001"
    assert result.allocation_plan.allocations[0].is_partial is True
    assert result.allocation_plan.unallocated_task_refs == ("day_task_001",)
    # 只复核一次：plan/review 各一次 + 复核一次
    assert result.call_count == 4


def test_splittability_review_still_false_is_respected():
    """复核仍明确 false：尊重模型判断，不强排；不得生成“准备去上课”填充。"""
    now = dt(13, 35)
    w1 = DayWindow(
        "w1", dt(13, 35), dt(14, 50),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 75,
    )
    commitments = (
        FixedCommitment("c1", "上课", "上课", dt(15), dt(16), None,
                        AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    task = TaskProgress(
        "day_task_001", "计组实验", 90, 0, SourceKind.USER_STATED,
        TaskState.ACTIVE, False, 90,
    )
    state = DayPlanningState(now, now, dt(22), commitments, (task,), (w1,), "w1", (), ())
    caller = ScriptedCaller(
        [RECONCILIATION_NONE, PLAN_INTENT_A, SPLITTABILITY_REVIEW_FALSE, REVIEW_ACCEPT]
    )
    result = run_p2_agentic_day_planning(state, "安排一下。", caller)
    updated = {t.task_ref: t for t in result.updated_state.tasks}["day_task_001"]
    assert updated.is_splittable is False
    assert result.allocation_plan.allocations == ()
    assert result.allocation_plan.unallocated_task_refs == ("day_task_001",)
    lines = []
    if result.day_summary.current_action_line:
        lines.append(result.day_summary.current_action_line)
    lines.extend(result.day_summary.later_window_lines)
    lines.extend(result.day_summary.unallocated_lines)
    assert not any("准备去上课" in line for line in lines)


def test_original_minimum_slice_does_not_block_review():
    """第一次模型给的 minimum_slice=80 > 窗口75，不能阻止复核；
    is_splittable=false + remaining>最大窗口 => 必须触发一次 review。"""
    now = dt(13, 35)
    w1 = DayWindow(
        "w1", dt(13, 35), dt(14, 50),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 75,
    )
    commitments = (
        FixedCommitment("c1", "上课", "上课", dt(15), dt(16), None,
                        AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    task = TaskProgress(
        "day_task_001", "计组实验", 90, 0, SourceKind.USER_STATED,
        TaskState.ACTIVE, False, 80,
    )
    state = DayPlanningState(now, now, dt(22), commitments, (task,), (w1,), "w1", (), ())
    # 原 min_slice=80 不阻止复核；复核返回 splittable=true、min_slice=20 => 安排75分钟
    caller = ScriptedCaller(
        [RECONCILIATION_NONE, PLAN_INTENT_A, SPLITTABILITY_REVIEW_TRUE, REVIEW_ACCEPT]
    )
    result = run_p2_agentic_day_planning(state, "安排一下。", caller)
    updated = {t.task_ref: t for t in result.updated_state.tasks}["day_task_001"]
    assert updated.is_splittable is True
    assert updated.minimum_slice_minutes == 20
    assert result.allocation_plan.total_planned_minutes == 75
    assert result.call_count == 4


def test_window_below_meaningful_threshold_no_review():
    """窗口只有7分钟，低于程序固定 MIN_MEANINGFUL_SLICE_MINUTES=10，不触发复核。"""
    now = dt(13, 35)
    w1 = DayWindow(
        "w1", dt(13, 35), dt(13, 42),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 7,
    )
    commitments = (
        FixedCommitment("c1", "上课", "上课", dt(15), dt(16), None,
                        AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    task = TaskProgress(
        "day_task_001", "计组实验", 90, 0, SourceKind.USER_STATED,
        TaskState.ACTIVE, False, 80,
    )
    state = DayPlanningState(now, now, dt(22), commitments, (task,), (w1,), "w1", (), ())
    # 不应触发复核：plan/review 各一次（reconciliation 1 + plan 1 + review 1）
    caller = ScriptedCaller([RECONCILIATION_NONE, PLAN_INTENT_A, REVIEW_ACCEPT])
    result = run_p2_agentic_day_planning(state, "安排一下。", caller)
    assert result.call_count == 3
    assert result.allocation_plan.unallocated_task_refs == ("day_task_001",)


def test_empty_user_text_rejected():
    state = build_state((make_task(),), (window("w1", dt(9), dt(10), 60),))
    with pytest.raises(ValueError):
        run_p2_agentic_day_planning(state, "   ", ScriptedCaller([]))

def test_ai_estimate_below_completed_is_ignored():
    task = make_task(
        title="整理数据库实验", total=None, completed=80, splittable=None, min_slice=None, source=None
    )
    state = build_state((task,), (window("w1", dt(9), dt(10, 30), 120),))
    for estimated in (60, 80):
        intent = (
            '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001"], '
            '"include_low_attention": false, "task_estimates": ['
            '{"task_ref": "day_task_001", "estimated_total_minutes": ' + str(estimated) + ', '
            '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
        )
        result = run_p2_agentic_day_planning(
            state,
            "安排一下。",
            ScriptedCaller([RECONCILIATION_NONE, intent, REVIEW_ACCEPT]),
        )
        updated = result.updated_state.tasks[0]
        assert updated.state == TaskState.ACTIVE
        assert updated.total_minutes is None
        assert updated.completed_minutes == 80
        assert any("不高于已完成进度" in warning for warning in result.warnings)


def test_ai_estimate_above_completed_applies():
    task = make_task(
        title="整理数据库实验", total=None, completed=80, splittable=None, min_slice=None, source=None
    )
    state = build_state((task,), (window("w1", dt(9), dt(10, 30), 120),))
    intent = (
        '{"schema_version": "p2.day-plan-intent.v1", "task_order": ["day_task_001"], '
        '"include_low_attention": false, "task_estimates": ['
        '{"task_ref": "day_task_001", "estimated_total_minutes": 81, '
        '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
    )
    result = run_p2_agentic_day_planning(
        state,
        "安排一下。",
        ScriptedCaller([RECONCILIATION_NONE, intent, REVIEW_ACCEPT]),
    )
    updated = result.updated_state.tasks[0]
    assert updated.state == TaskState.ACTIVE
    assert updated.total_minutes == 81
    assert updated.remaining_minutes == 1
    assert result.allocation_plan.total_planned_minutes == 1


def test_duplicate_target_reconciliation_falls_back():
    dup = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": 30, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null},'
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": 30, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    state = build_state(
        (make_task(completed=50),),
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
    )
    result = run_p2_agentic_day_planning(
        state,
        "我刚才又做了30分钟。",
        ScriptedCaller([dup, "坏", PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert result.updated_state.tasks[0].completed_minutes == 50
    assert any("暂未结构化应用" in warning for warning in result.warnings)
    assert result.allocation_plan.total_planned_minutes == 70


def test_questions_with_update_do_not_change_task():
    rec = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": 30, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], '
        '"questions": ["你说的是哪个实验？"]}'
    )
    state = build_state(
        (make_task(completed=50),),
        (window("w1", dt(9), dt(10), 120),),
    )
    result = run_p2_agentic_day_planning(
        state,
        "实验我又做了30分钟。",
        ScriptedCaller([rec, PLAN_INTENT_A, REVIEW_ACCEPT]),
    )
    assert result.updated_state.tasks[0].completed_minutes == 50
    assert result.questions == ("你说的是哪个实验？",)
    assert len(result.allocation_plan.allocations) == 1

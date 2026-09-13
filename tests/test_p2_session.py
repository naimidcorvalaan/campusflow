"""P2d 会话状态层测试：commitment 反馈 -> 重派生窗口 -> P2c 重新规划。

验证点：
- 三个动作（开始 / 应用反馈 / 刷新）才触发 Agent 调用，普通 rerun 命中缓存；
- 场景 A~F 的端到端行为；
- task 进度 / lifecycle 跨 replanning 保留；
- 输入不可变、history 上限 10、P2c 10-call cap 不被绕过。
"""

from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_main import (
    _COMMITMENT_ADD,
    _COMMITMENT_CANCEL,
    _COMMITMENT_DELAY,
    _COMMITMENT_NONE,
    _PLAN_EMPTY,
    _REVIEW_ACCEPT,
    _TASK_NONE,
    _TASK_PROGRESS_30,
    _TASK_RESUME,
    _TASK_SKIP,
    _is_resume_phrase,
    build_demo_state,
)
from src.p2_models import MAX_HISTORY_LEN, TaskProgress, TaskState
from src.p2_session import START_TEXT, P2SessionController, make_cache_key
from src.p2_window_derivation import derive_day_state

def _router_json(route):
    return '{"schema_version": "p2.feedback-router.v1", "route": "' + route + '", "reason": null}'


def _demo_route(user):
    if "又做了30分钟" in user or "不背单词" in user or _is_resume_phrase(user):
        return _router_json("task")
    if "晚了" in user or "推迟" in user or "临时有个组会" in user or "取消了" in user:
        return _router_json("commitment")
    return _router_json("unclear")


COMMITMENT_AMBIGUOUS = (
    '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [], '
    '"questions": ["你说的是哪个组会？"]}'
)


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


def make_commitment(ref, title, start, end, availability=AvailabilityLevel.UNAVAILABLE):
    return FixedCommitment(ref, title, title, start, end, None, availability, {}, ())


def make_task(
    ref="day_task_001",
    title="计组实验3",
    total=120,
    completed=50,
    state=TaskState.ACTIVE,
    splittable=True,
    min_slice=30,
):
    return TaskProgress(
        ref, title, total, completed,
        SourceKind.AI_EXTRACTED_FROM_USER_TEXT, state, splittable, min_slice,
    )


class MockCaller:
    """按 system 提示分发的确定性 mock；可注入各角色输出并记录调用。"""

    def __init__(self, commitment=None, task=None, plan=None, review=None):
        self.commitment = commitment if commitment is not None else _COMMITMENT_NONE
        self.task = task if task is not None else _TASK_NONE
        self.plan = plan if plan is not None else _PLAN_EMPTY
        self.review = review if review is not None else _REVIEW_ACCEPT
        self.repair = None
        self.call_count = 0
        self.calls = []

    def __call__(self, system, user):
        self.call_count += 1
        self.calls.append((system, user))
        if "feedback-router" in system:
            return _demo_route(user)
        if "commitment-reconciliation" in system:
            return self.commitment
        if "task-reconciliation" in system:
            return self.task
        if "day-plan-intent" in system:
            return self.plan
        if "day-review" in system:
            return self.review
        if "格式修复" in system:
            return self.repair if self.repair is not None else self.commitment
        return _TASK_NONE


class ScenarioCaller(MockCaller):
    """文本感知 mock：覆盖 P2d 五个演示反馈（commitment + task）。

    计数与记录只在当前 __call__ 发生一次，不再委托 super().__call__，
    避免 day-plan / review 阶段被重复计数。
    """

    def __call__(self, system, user):
        self.call_count += 1
        self.calls.append((system, user))
        if "feedback-router" in system:
            return _demo_route(user)
        if "commitment-reconciliation" in system:
            if "晚了" in user or "推迟" in user:
                return _COMMITMENT_DELAY
            if "临时有个组会" in user:
                return _COMMITMENT_ADD
            if "取消了" in user:
                return _COMMITMENT_CANCEL
            return self.commitment
        if "task-reconciliation" in system:
            if "又做了30分钟" in user:
                return _TASK_PROGRESS_30
            if "不背单词" in user:
                return _TASK_SKIP
            if _is_resume_phrase(user):
                return _TASK_RESUME
            return self.task
        if "day-plan-intent" in system:
            return self.plan
        if "day-review" in system:
            return self.review
        if "格式修复" in system:
            return self.repair if self.repair is not None else self.task
        return self.task


def start_session(state=None, mock=None):
    mock = mock if mock is not None else ScenarioCaller()
    controller = P2SessionController({}, mock)
    controller.start_day(state if state is not None else build_demo_state())
    return controller, mock


# ---------------------------------------------------------------------------
# 初始规划与场景 A~F
# ---------------------------------------------------------------------------


def test_start_day_produces_plan():
    controller, mock = start_session()
    turn = controller.last_turn()
    assert turn.user_text == START_TEXT
    assert turn.result.call_count == 2  # Day Plan + Review（P2e 首次规划不送 reconciliation）
    assert turn.result.allocation_plan.total_planned_minutes == 100
    summary = turn.result.day_summary
    assert summary.current_action_line == "现在：计组实验3 50 分钟"
    assert "11:30 后：计组实验3 20 分钟" in summary.later_window_lines
    assert "11:30 后：背单词 30 分钟" in summary.later_window_lines


def test_scenario_a_partial_progress_replanned():
    controller, mock = start_session()
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    updated = turn.result.updated_state
    task1 = {t.task_ref: t for t in updated.tasks}["day_task_001"]
    assert task1.completed_minutes == 80
    assert task1.remaining_minutes == 40
    assert turn.result.allocation_plan.planned_minutes_by_task["day_task_001"] == 40
    assert turn.result.day_summary.current_action_line == "现在：计组实验3 40 分钟"


def test_scenario_b_delay_class_rebuilds_windows():
    controller, mock = start_session()
    turn = controller.apply_feedback("下课晚了20分钟。")
    updated = turn.result.updated_state
    commitments = {c.commitment_ref: c for c in updated.commitments}
    assert commitments["day_commitment_001"].ends_at == dt(11, 50)
    # 已过去/被占用的上课时段不重复进入窗口
    for w in updated.windows:
        assert not (w.starts_at < dt(11, 50) and w.ends_at > dt(10))
    later = turn.result.day_summary.later_window_lines
    assert any(line.startswith("11:50 后：") for line in later)
    # 任务进度跨 replanning 保留
    task1 = {t.task_ref: t for t in updated.tasks}["day_task_001"]
    assert task1.completed_minutes == 50


def test_scenario_c_add_meeting_blocks_windows():
    controller, mock = start_session()
    turn = controller.apply_feedback("14点临时有个组会，大概1小时。")
    updated = turn.result.updated_state
    by_ref = {c.commitment_ref: c for c in updated.commitments}
    assert "day_commitment_003" in by_ref
    assert by_ref["day_commitment_003"].title == "组会"
    assert by_ref["day_commitment_003"].starts_at == dt(14)
    assert by_ref["day_commitment_003"].ends_at == dt(15)
    for w in updated.windows:
        assert not (w.starts_at < dt(14, 30) < w.ends_at)


def test_scenario_d_cancel_meeting_frees_time():
    commitments = (
        make_commitment("day_commitment_001", "上课", dt(10), dt(11, 30)),
        make_commitment("day_commitment_002", "实验", dt(15, 30), dt(17)),
        make_commitment("day_commitment_003", "组会", dt(14), dt(15)),
    )
    state = derive_day_state(dt(9), dt(22), commitments, (make_task(),), 10, {}, (), dt(9))
    controller, mock = start_session(state=state)
    turn = controller.apply_feedback("下午的组会取消了。")
    updated = turn.result.updated_state
    refs = [c.commitment_ref for c in updated.commitments]
    assert "day_commitment_003" not in refs
    assert any(w.starts_at <= dt(14, 30) < w.ends_at for w in updated.windows)


def test_scenario_e_skip_today_and_no_resurrection():
    controller, mock = start_session()
    turn = controller.apply_feedback("今天先不背单词了。")
    updated = turn.result.updated_state
    task2 = {t.task_ref: t for t in updated.tasks}["day_task_002"]
    assert task2.state == TaskState.SKIPPED_TODAY
    assert "day_task_002" not in turn.result.allocation_plan.planned_minutes_by_task
    # 再次重新规划：背单词仍不复活
    turn2 = controller.apply_feedback("我刚又做了30分钟实验")
    updated2 = turn2.result.updated_state
    task2_again = {t.task_ref: t for t in updated2.tasks}["day_task_002"]
    assert task2_again.state == TaskState.SKIPPED_TODAY
    assert "day_task_002" not in turn2.result.allocation_plan.planned_minutes_by_task



def test_scenario_resume_today_after_skip():
    """先 skip 背单词 -> 又想背单词了 -> 恢复 ACTIVE -> 重新进入当天计划 -> 无待确认问题。"""
    controller, mock = start_session()
    turn1 = controller.apply_feedback("今天先不背单词了。")
    task_skipped = {t.task_ref: t for t in turn1.result.updated_state.tasks}["day_task_002"]
    assert task_skipped.state == TaskState.SKIPPED_TODAY
    assert "day_task_002" not in turn1.result.allocation_plan.planned_minutes_by_task

    turn2 = controller.apply_feedback("又想背单词了")
    updated2 = turn2.result.updated_state
    task_resumed = {t.task_ref: t for t in updated2.tasks}["day_task_002"]
    assert task_resumed.state == TaskState.ACTIVE
    # 不产生待确认问题
    assert turn2.all_questions == ()
    # 背单词可以重新进入当天计划
    assert "day_task_002" in turn2.result.allocation_plan.planned_minutes_by_task

def test_scenario_f_ambiguous_commitment_question_no_change():
    mock = MockCaller(commitment=COMMITMENT_AMBIGUOUS)
    controller, _ = start_session(mock=mock)
    before_refs = [c.commitment_ref for c in controller.current_state().commitments]
    turn = controller.apply_feedback("下午的组会取消了。")
    assert turn.all_questions == ("你说的是哪个组会？",)
    after_refs = [c.commitment_ref for c in controller.current_state().commitments]
    assert after_refs == before_refs
    # 仍基于原 state 继续规划
    assert turn.result.allocation_plan.total_planned_minutes > 0


# ---------------------------------------------------------------------------
# 缓存 / 触发语义 / 预算 / history
# ---------------------------------------------------------------------------


def test_rerun_with_same_input_hits_cache():
    controller, mock = start_session()
    calls_after_start = mock.call_count
    turn1 = controller.apply_feedback("我刚又做了30分钟实验")
    calls_after_feedback = mock.call_count
    assert calls_after_feedback == calls_after_start + 4
    turn2 = controller.apply_feedback("我刚又做了30分钟实验")
    assert mock.call_count == calls_after_feedback
    assert turn2 is turn1


def test_start_day_same_state_cached():
    state = build_demo_state()
    mock = MockCaller()
    controller = P2SessionController({}, mock)
    controller.start_day(state)
    calls = mock.call_count
    turn = controller.start_day(state)
    assert mock.call_count == calls
    assert turn.user_text == START_TEXT


def test_refresh_always_runs_pipeline():
    controller, mock = start_session()
    before = mock.call_count
    controller.refresh()
    step = mock.call_count - before
    assert step > 0
    controller.refresh()
    assert mock.call_count == before + 2 * step


def test_feedback_before_start_raises():
    controller = P2SessionController({}, MockCaller())
    with pytest.raises(RuntimeError):
        controller.apply_feedback("我刚又做了30分钟实验")


def test_commitment_format_repair_recovers():
    mock = MockCaller(commitment="这不是 JSON")
    mock.repair = _COMMITMENT_DELAY
    controller, _ = start_session(mock=mock)
    turn = controller.apply_feedback("下课晚了20分钟。")
    updated = turn.result.updated_state
    assert {c.commitment_ref: c for c in updated.commitments}["day_commitment_001"].ends_at == dt(11, 50)


def test_pipeline_call_cap_not_bypassed():
    controller, mock = start_session()
    for text in ("我刚又做了30分钟实验", "下课晚了20分钟。", "今天先不背单词了。"):
        turn = controller.apply_feedback(text)
        assert turn.result.call_count <= 10
    # 每轮真实调用有界：commitment<=2 + pipeline<=10
    assert mock.call_count <= 3 + 3 * 12


def test_history_stays_bounded():
    controller, mock = start_session()
    for _ in range(12):
        controller.apply_feedback("我刚又做了30分钟实验")
    assert len(controller.current_state().history) <= MAX_HISTORY_LEN


def test_input_state_immutable_across_feedback():
    state = build_demo_state()
    before = state
    controller, _ = start_session(state=state)
    controller.apply_feedback("下课晚了20分钟。")
    assert state is before
    assert {c.commitment_ref: c for c in state.commitments}["day_commitment_001"].ends_at == dt(11, 30)
    assert state.tasks[0].completed_minutes == 50


def test_cache_key_deterministic_and_sensitive():
    state = build_demo_state()
    assert make_cache_key("x", state) == make_cache_key("x", state)
    assert make_cache_key("x", state) != make_cache_key("y", state)
    task1 = state.tasks[0]
    changed_task = TaskProgress(
        task1.task_ref, task1.title, 120, 60, task1.total_source,
        task1.state, task1.is_splittable, task1.minimum_slice_minutes,
    )
    state2 = derive_day_state(
        state.now, state.day_end, state.commitments,
        (changed_task,) + state.tasks[1:], 10, {}, state.history, state.reference_datetime,
    )
    assert make_cache_key("x", state) != make_cache_key("x", state2)


# ---------------------------------------------------------------------------
# P2e：LLM Router 防串台
# ---------------------------------------------------------------------------


def test_router_task_feedback_skips_commitment_reconciler():
    mock = ScenarioCaller()
    controller, _ = start_session(mock=mock)
    calls_before = mock.call_count
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    step = mock.call_count - calls_before
    # 任务反馈：router(1) + task reconciliation(1) + plan(1) + review(1)，不调用 commitment
    assert step == 4
    stage_systems = [system for system, _ in mock.calls[calls_before:]]
    assert not any("commitment-reconciliation" in system for system in stage_systems)
    assert any("feedback-router" in system for system in stage_systems)
    updated = turn.result.updated_state
    assert {t.task_ref: t for t in updated.tasks}["day_task_001"].completed_minutes == 80


def test_router_commitment_feedback_skips_task_reconciler():
    mock = ScenarioCaller()
    controller, _ = start_session(mock=mock)
    calls_before = mock.call_count
    turn = controller.apply_feedback("下课晚了20分钟。")
    step = mock.call_count - calls_before
    # 固定安排反馈：router(1) + commitment(1) + plan(1) + review(1)，跳过任务 reconciliation
    assert step == 4
    stage_systems = [system for system, _ in mock.calls[calls_before:]]
    assert not any("task-reconciliation" in system for system in stage_systems)
    assert turn.result.reconciliation_result is None
    updated = turn.result.updated_state
    assert {c.commitment_ref: c for c in updated.commitments}["day_commitment_001"].ends_at == dt(11, 50)


def test_router_commitment_feedback_does_not_invent_task():
    # “组会推迟到3点”只走 commitment，任务 reconciliation 不被调用，不会把组会当任务
    mock = ScenarioCaller()
    controller, _ = start_session(mock=mock)
    before_tasks = tuple(controller.current_state().tasks)
    controller.apply_feedback("组会推迟到3点。")
    stage_systems = [system for system, _ in mock.calls]
    assert not any("task-reconciliation" in system for system in stage_systems)
    assert controller.current_state().tasks == before_tasks


def test_router_unclear_keeps_state_and_asks():
    mock = MockCaller()
    controller, _ = start_session(mock=mock)
    before = controller.current_state()
    turn = controller.apply_feedback("今天感觉不错。")
    assert turn.router_questions == ("未能判断这条反馈属于任务还是固定安排，请补充说明。",)
    assert turn.result.updated_state.tasks == before.tasks
    assert turn.result.updated_state.commitments == before.commitments


def test_router_fallback_both_on_parse_failure():
    class BrokenRouterCaller(ScenarioCaller):
        def __call__(self, system, user):
            self.call_count += 1
            self.calls.append((system, user))
            if "feedback-router" in system:
                return "不是 JSON"
            if "格式修复" in system:
                return "还是不是 JSON"
            return super().__call__(system, user)

    mock = BrokenRouterCaller()
    controller, _ = start_session(mock=mock)
    turn = controller.apply_feedback("我刚又做了30分钟实验")
    # 回退 both：commitment 与 task 都会尝试，但不丢失任务进度
    assert any("反馈路由判断暂未完成" in w for w in turn.all_warnings)
    updated = turn.result.updated_state
    assert {t.task_ref: t for t in updated.tasks}["day_task_001"].completed_minutes == 80

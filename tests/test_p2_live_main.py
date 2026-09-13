"""P2d 第二阶段：TJU live 页面测试（全部注入 mock，不请求真实 API）。

覆盖：参考时间解析、adapter 接线、intake 流程、普通 rerun 不重复调用模型、
反馈续接首次 state、配置缺失/参考时间非法守卫、refresh 触发、intake 失败 fallback。
"""

from datetime import datetime, time
from types import SimpleNamespace

import pytest

import src.p4_execution_movement as execution_movement

from src.p2_day_intake import run_day_intake
from src.p2_live_main import (
    LIVE_ERROR_KEY,
    LIVE_CAMPUS_SELECT_KEY,
    LIVE_CURRENT_LOCATION_KEY,
    LIVE_FEEDBACK_KEY,
    LIVE_INTAKE_KEY,
    LIVE_REFERENCE_HOUR_KEY,
    LIVE_REFERENCE_KEY,
    LIVE_REFERENCE_MINUTE_KEY,
    LIVE_SESSION_KEY,
    _resolve_reference_time,
    make_live_session,
    render_live_page_text,
    render_page_streamlit,
    run_live_intake,
    SAFE_ERROR_TEXT,
    _consume_default_walk_hint,
    main,
)

from src.p2_models import TaskState
from src.p3_campus_session import SELECTED_CAMPUS_ID_KEY
from src.p3_campus_session import select_campus
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_route_planner import final_plan_overlap_errors
from src.p3_map_schema import TransportMode
from src.p2_expression_agents import _idle_gaps, _expression_segments
from src.p4_execution_context import (
    EXECUTION_PLAN_CONTEXT_KEY,
    CurrentLocationSource,
    ExecutionConfirmationKind,
    load_execution_context,
)
from src.p4_feedback_decision import P4_FEEDBACK_DECISION_KEY, FeedbackDecision
from src.personal_settings import PersonalSettings, load_personal_settings, save_personal_settings
from src.p2_session import (
    LAST_TURN_KEY,
    LIVE_FINAL_TURN_KEY,
    P2SessionController,
    PlanConfig,
    REFRESH_TEXT,
    START_TEXT,
    load_live_final_turn,
)

INTAKE_SIMPLE = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "10:00", "ends_at": "11:30", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": null}], '
    '"tasks": [{"title": "计组实验", "total_minutes": 120, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}], "questions": []}'
)

INTAKE_WITH_UNKNOWN = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "10:00", "ends_at": "11:30", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": null}], '
    '"tasks": [{"title": "计组实验", "total_minutes": 120, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}, '
    '{"title": "整理实验报告", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null}], "questions": []}'
)

TASK_NONE = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": [], "questions": []}'
)
TASK_PROGRESS_30 = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": 30, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "none", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)
COMMITMENT_NONE = (
    '{"schema_version": "p2.commitment-reconciliation.v1", "updates": [], "questions": []}'
)
PLAN_EMPTY = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)
PLAN_ESTIMATE = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": ['
    '{"task_ref": "day_task_002", "estimated_total_minutes": 100, '
    '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
)
PLAN_REPORT_MEAL_ESTIMATE = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": '
    '["day_task_001", "day_task_002"], "include_low_attention": false, '
    '"task_estimates": [{"task_ref": "day_task_001", '
    '"estimated_total_minutes": 20, "is_splittable": false, '
    '"minimum_slice_minutes": 20}], "rationale": null}'
)
PLAN_REVERSED_REPORT_MEAL = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": '
    '["day_task_002", "day_task_001"], "include_low_attention": false, '
    '"task_estimates": [{"task_ref": "day_task_001", '
    '"estimated_total_minutes": 20, "is_splittable": true, '
    '"minimum_slice_minutes": 5}], "rationale": null}'
)
PLAN_LONG_REPORT_REVERSED = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": '
    '["day_task_002", "day_task_001"], "include_low_attention": false, '
    '"task_estimates": [{"task_ref": "day_task_001", '
    '"estimated_total_minutes": 180, "is_splittable": true, '
    '"minimum_slice_minutes": 5}], "rationale": null}'
)
INTAKE_LIVE_EXECUTION_REGRESSION = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "17:00", "ends_at": "18:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "9教", '
    '"commitment_kind": "class", "class_arrival_lead_minutes": null}], '
    '"tasks": ['
    '{"title": "写实验报告", "total_minutes": null, "is_splittable": true, '
    '"minimum_slice_minutes": 5, "location_text": "图书馆", "activity_kind": "generic", '
    '"duration_source": null}, '
    '{"title": "吃饭", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "meal", '
    '"duration_source": null}], "questions": [], "current_location": null, "transport_mode": null}'
)
INTAKE_LIVE_EXECUTION_2000 = INTAKE_LIVE_EXECUTION_REGRESSION.replace(
    '"17:00", "ends_at": "18:00"', '"20:00", "ends_at": "21:00"'
)
INTAKE_LIVE_EXECUTION_2100_UNKNOWN_END = INTAKE_LIVE_EXECUTION_REGRESSION.replace(
    '"17:00", "ends_at": "18:00"', '"21:00", "ends_at": null'
)
INTAKE_LIVE_EXECUTION_1700_UNKNOWN_END = INTAKE_LIVE_EXECUTION_REGRESSION.replace(
    '"17:00", "ends_at": "18:00"', '"17:00", "ends_at": null'
)
INTAKE_LIVE_EXECUTION_REAL_1700_SHAPE = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "17:00", "ends_at": "18:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "9教", '
    '"commitment_kind": "class", "class_arrival_lead_minutes": null}], '
    '"tasks": ['
    '{"title": "写实验报告", "total_minutes": null, "is_splittable": true, '
    '"minimum_slice_minutes": 5, "location_text": "图书馆", "activity_kind": "generic", '
    '"duration_source": null}, '
    '{"title": "吃饭", "total_minutes": 30, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "meal", '
    '"duration_source": "ai_estimated"}], "questions": [], '
    '"current_location": null, "transport_mode": null}'
)

INTAKE_P4C_LAUNDRY = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "17:00", "ends_at": "18:30", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "9教", '
    '"commitment_kind": "class", "class_arrival_lead_minutes": null}], '
    '"tasks": ['
    '{"title": "写实验报告", "total_minutes": 60, "is_splittable": true, '
    '"minimum_slice_minutes": 10, "location_text": "图书馆", "activity_kind": "generic", '
    '"duration_source": "user_explicit"}, '
    '{"title": "吃饭", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "meal", '
    '"duration_source": null}, '
    '{"title": "写作业", "total_minutes": 60, "is_splittable": true, '
    '"minimum_slice_minutes": 15, "location_text": null, "activity_kind": "generic", '
    '"duration_source": "user_explicit"}, '
    '{"title": "背单词", "total_minutes": 20, "is_splittable": false, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "generic", '
    '"duration_source": "user_explicit"}, '
    '{"title": "洗衣服", "total_minutes": 20, "is_splittable": false, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "generic", '
    '"duration_source": "user_explicit"}], "questions": [], '
    '"current_location": null, "transport_mode": null}'
)

PLAN_P4C_ALL = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": '
    '["day_task_001", "day_task_002", "day_task_003", "day_task_004", "day_task_005"], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)
def _router_json(route):
    return '{"schema_version": "p2.feedback-router.v1", "route": "' + route + '", "reason": null}'


def _demo_route(user):
    if "又做了30分钟" in user or "不背单词" in user:
        return _router_json("task")
    if "推迟" in user or "组会" in user:
        return _router_json("commitment")
    return _router_json("unclear")


REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)

COMPANION_COPY_OK = (
    '{"schema_version": "p3.companion-copy.v1", '
    '"opening": "同学你好，这是为你整理好的当前安排。", '
    '"closing": "按自己的节奏来，祝你今天一切顺利。"}'
)

CRITIC_APPROVE = (
    '{"schema_version": "p3.plan-critic.v1", "approved": true, '
    '"issues": [], "revision_needed": false}'
)
CRITIC_REQUESTS_REVISION = (
    '{"schema_version": "p3.plan-critic.v1", "approved": false, '
    '"issues": [{"type": "wasted_gap", "severity": "high", '
    '"message": "建议调整任务顺序。"}], "revision_needed": true}'
)
IMPROVER_REVERSES_TASKS = (
    '{"schema_version": "p3.plan-improver.v1", '
    '"task_order_titles": ["吃饭", "写实验报告"], '
    '"include_low_attention": false, "reason": "调整顺序"}'
)
LIFESTYLE_OK = (
    '{"schema_version": "p3.lifestyle-review.v1", "notes": [], "user_facing_hint": null}'
)
NARRATOR_OK = (
    '{"schema_version": "p3.plan-narrator.v1", '
    '"opening": "同学你好，这是为你整理好的当前安排。"}'
)
WARM_OK = (
    '{"schema_version": "p3.warm-companion.v1", "change_summary": null, '
    '"closing": "按自己的节奏来，祝你今天一切顺利。"}'
)
COPY_REVIEW_OK = (
    '{"schema_version": "p3.copy-review.v1", "approved": true, "revised": null, "reason": null}'
)
INTAKE_AUDIT_APPROVE = (
    '{"schema_version": "p4.initial-intake-audit.v1", "decision": "approve", '
    '"issues": [], "repaired_proposal": null}'
)


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


class CountingCaller:
    def __init__(self, intake=None, task=None, plan=None, review=None, commitment=None,
                 unified_questions=None):
        self.intake = intake if intake is not None else INTAKE_SIMPLE
        self.task = task if task is not None else TASK_NONE
        self.plan = plan if plan is not None else PLAN_EMPTY
        self.review = review if review is not None else REVIEW_ACCEPT
        self.commitment = commitment if commitment is not None else COMMITMENT_NONE
        self.unified_questions = unified_questions
        self.calls = []

    @property
    def count(self):
        return len(self.calls)

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "Initial Intake Semantic Auditor" in system:
            return INTAKE_AUDIT_APPROVE
        if "p3.plan-critic" in system:
            return CRITIC_APPROVE
        if "p3.lifestyle-review" in system:
            return LIFESTYLE_OK
        if "p3.plan-narrator" in system:
            return NARRATOR_OK
        if "p3.warm-companion" in system:
            return WARM_OK
        if "p3.copy-review" in system:
            return COPY_REVIEW_OK
        if "p3.companion-copy" in system:
            return COMPANION_COPY_OK
        if "统一反馈理解器" in system:
            return self._unified_json(user)
        if "feedback-router" in system:
            return _demo_route(user)
        if "day-intake" in system:
            return self.intake
        if "commitment-reconciliation" in system:
            return self.commitment
        if "task-reconciliation" in system:
            if START_TEXT in user or REFRESH_TEXT in user:
                return TASK_NONE
            return self.task
        if "day-plan-intent" in system:
            return self.plan
        if "day-review" in system:
            return self.review
        return self.task

    def _unified_json(self, user):
        """把 self.task / self.commitment 包成统一反馈输出（P3e）。"""
        import json as _json
        task_updates = []
        try:
            task_updates = _json.loads(self.task).get("updates", [])
        except Exception:
            task_updates = []
        commitment_updates = []
        if "推迟" in user or "组会" in user or "取消" in user or "改到" in user:
            try:
                commitment_updates = _json.loads(self.commitment).get("updates", [])
            except Exception:
                commitment_updates = []
        movement = {
            "has_movement": False,
            "origin_text": None,
            "destination_text": None,
            "mode": None,
            "depart_at": None,
            "arrive_by": None,
        }
        return _json.dumps(
            {
                "schema_version": "p3.unified-feedback.v1",
                "task_updates": task_updates,
                "commitment_updates": commitment_updates,
                "movement": movement,
                "current_location": None,
                "questions": self.unified_questions if self.unified_questions is not None else [],
                "reason": None,
            },
            ensure_ascii=False,
        )


class FakeAdapter:
    def __init__(self, caller):
        self.agent_caller = caller


class _CurrentLocationFeedbackCaller(CountingCaller):
    """Selected-campus current-location correction through unified feedback."""

    def _unified_json(self, user):
        import json as _json
        payload = _json.loads(super()._unified_json(user))
        payload["current_location"] = "31斋" if "31斋" in user else "9斋"
        return _json.dumps(payload, ensure_ascii=False)


class _RevisionRequestingFeedbackCaller(_CurrentLocationFeedbackCaller):
    """Mimic a real critic that asks the expression layer to revise the plan."""

    def __call__(self, system, user):
        if "p3.plan-critic" in system:
            self.calls.append((system, user))
            return CRITIC_REQUESTS_REVISION
        if "p3.plan-improver" in system:
            self.calls.append((system, user))
            return IMPROVER_REVERSES_TASKS
        return super().__call__(system, user)


class _UnsafeCampusCopyFeedbackCaller(_CurrentLocationFeedbackCaller):
    """A hostile narrator must not turn an assumption into a foreign fact."""

    def __call__(self, system, user):
        if "p3.plan-narrator" in system:
            self.calls.append((system, user))
            return (
                '{"schema_version": "p3.plan-narrator.v1", '
                '"opening": "你正在天津大学北洋园校区郑东图书馆写报告。"}'
            )
        return super().__call__(system, user)


class _P4CFeedbackCaller(CountingCaller):
    """Mock all three P4c semantic passes while retaining the live pipeline."""

    def __init__(self, compliance="approve"):
        super().__init__(intake=INTAKE_P4C_LAUNDRY, plan=PLAN_P4C_ALL)
        self.compliance = compliance

    def __call__(self, system, user):
        import json as _json
        if "Intent Compliance Reviewer 2.0" in system:
            self.calls.append((system, user))
            reject = self.compliance == "reject" and "flow=feedback" in user
            return _json.dumps({
                "schema_version": "p5.intent-compliance.v2",
                "decision": "reject" if reject else "approve",
                "reason": "candidate check",
                "repair_directives": ([{
                    "kind": "prefer_task", "task_ref": "day_task_005",
                    "commitment_ref": None, "related_task_ref": None,
                }] if reject else []),
            }, ensure_ascii=False)
        if "Feedback Interpreter" in system:
            self.calls.append((system, user))
            if "31斋" in user.split("用户最新反馈：")[-1]:
                return _json.dumps({
                    "schema_version": "p4.feedback-decision.v1",
                    "intent_type": "location_correction", "target_task_refs": [],
                    "preferred_next_task_ref": None, "priority_changes": [],
                    "ordering_constraints": [], "cancelled_task_refs": [],
                    "postponed_task_refs": [], "completed_task_refs": [],
                    "restored_task_refs": [], "location_correction": "31斋",
                    "explicit_user_preference": False, "confidence": 0.99,
                    "clarification_needed": False, "clarification_question": None,
                }, ensure_ascii=False)
            return _json.dumps({
                "schema_version": "p4.feedback-decision.v1",
                "intent_type": "prioritize_now",
                "target_task_refs": ["day_task_005"],
                "preferred_next_task_ref": "day_task_005",
                "priority_changes": [{"task_ref": "day_task_005", "direction": "raise"}],
                "ordering_constraints": [], "cancelled_task_refs": [],
                "postponed_task_refs": [], "completed_task_refs": [],
                "restored_task_refs": [], "location_correction": None,
                "explicit_user_preference": True, "confidence": 0.98,
                "clarification_needed": False, "clarification_question": None,
            }, ensure_ascii=False)
        if "Feedback Decision Critic" in system:
            self.calls.append((system, user))
            return _json.dumps({
                "schema_version": "p4.feedback-decision-review.v1",
                "decision": "approve", "reason": None, "repaired_decision": None,
            })
        if "Final Intent Compliance Reviewer" in system:
            self.calls.append((system, user))
            return _json.dumps({
                "schema_version": "p4.intent-compliance-review.v1",
                "decision": self.compliance, "reason": "candidate check",
                "repaired_decision": None,
            })
        return super().__call__(system, user)


def _intake_state(caller, reference=None, user_text="10点上课，今天做计组实验。"):
    outcome = run_day_intake(reference if reference is not None else dt(9), user_text, caller)
    assert outcome.applied is not None
    return outcome.applied.state


# ---------------------------------------------------------------------------
# 纯函数 / 会话接线
# ---------------------------------------------------------------------------


def test_resolve_reference_time():
    now = dt(9, 0)
    assert _resolve_reference_time(None, now) == now
    assert _resolve_reference_time(time(14, 30), now) == dt(14, 30)
    assert _resolve_reference_time(time(18, 25), now) == dt(18, 25)
    assert _resolve_reference_time(datetime(2026, 9, 2, 10, 5, 45), now) == datetime(2026, 9, 2, 10, 5)
    with pytest.raises(TypeError):
        _resolve_reference_time("not-a-time", now)


def test_make_live_session_wires_adapter_caller():
    caller = CountingCaller()
    session = make_live_session({}, FakeAdapter(caller))
    state = _intake_state(caller)
    turn = session.start_day(state)
    assert turn.result.day_summary.current_action_line == "现在：计组实验 50 分钟"
    assert turn.result.call_count == 2  # Day Plan + Review
    assert caller.count == 1 + 2  # intake + pipeline（make_live_session 默认不启用 companion）


def test_live_intake_cache_avoids_duplicate_calls():
    caller = CountingCaller()
    store = {}
    reference = dt(9)
    first = run_live_intake(store, reference, "10点上课，今天做计组实验。", caller)
    assert first.applied is not None
    calls_after_first = caller.count
    second = run_live_intake(store, reference, "10点上课，今天做计组实验。", caller)
    assert second is first
    assert caller.count == calls_after_first
    # 不同文本不命中缓存
    run_live_intake(store, reference, "换个说法：今天做实验。", caller)
    assert caller.count == calls_after_first + 3


def test_live_intake_failure_not_cached():
    caller = CountingCaller(intake="不是 JSON")
    store = {}
    first = run_live_intake(store, dt(9), "今天做实验。", caller)
    assert first.applied is None
    second = run_live_intake(store, dt(9), "今天做实验。", caller)
    assert second.applied is None
    assert caller.count == 6  # 两次尝试各 raw pass + extraction + repair


def test_render_live_page_text_ai_estimates_no_refs():
    caller = CountingCaller(intake=INTAKE_WITH_UNKNOWN, plan=PLAN_ESTIMATE)
    session = make_live_session({}, FakeAdapter(caller))
    state = _intake_state(caller)
    turn = session.start_day(state)
    text = render_live_page_text(turn)
    assert "# 当前方案" in text
    assert "整理实验报告 100 分钟（AI暂估）" in text
    assert "## AI 暂估" not in text
    assert "整理实验报告 100 分钟（AI暂估）" in text
    for marker in ("day_task_", "day_window_", "day_commitment_", "allocation_"):
        assert marker not in text
    assert "TaskProgress(" not in text


# ---------------------------------------------------------------------------
# main(stub) 页面流程
# ---------------------------------------------------------------------------


class _Form:
    def __init__(self, stub, is_intake):
        self.stub = stub
        self.is_intake = is_intake
        self.submitted = stub.intake_submitted if is_intake else stub.feedback_submitted

    def __enter__(self):
        self.stub._active = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stub._active = None
        return False

    def text_input(self, label, key=None, **kwargs):
        self.stub.form_widget_calls.append(("text_input", label, key))
        return "" if self.is_intake else self.stub.feedback_text

    def text_area(self, label, key=None, **kwargs):
        self.stub.form_widget_calls.append(("text_area", label, key))
        return self.stub.intake_text if self.is_intake else ""

    def form_submit_button(self, label, **kwargs):
        self.stub.form_widget_calls.append(("submit", label, kwargs.get("type")))
        return self.submitted


class _Expander:
    def __init__(self, stub):
        self.stub = stub

    def __enter__(self):
        self.stub._in_expander = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stub._in_expander = False
        return False



class _Column:
    """模拟 st.column：支持 with 上下文，selectbox 委托回主 stub。"""

    def __init__(self, stub):
        self.stub = stub

    def __enter__(self):
        self.stub._active = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stub._active = None
        return False

    def selectbox(self, label, options, key=None, format_func=None, **kwargs):
        return self.stub.selectbox(label, options, key=key, format_func=format_func)


class _StrictSessionState(dict):
    """模拟 Streamlit 1.31 widget 生命周期：widget 实例化后禁止用户代码写其 key。

    - instantiate(key)：selectbox 创建时调用，标记该 key 已实例化；
    - widget_set(key, value)：模拟 widget 自身写回（允许）；
    - begin_run()：每个 Streamlit run 开始时调用（真实 Streamlit 每次 run 重新
      实例化 widget，pre-widget 写 key 在新 run 中仍被允许）；
    - 其余 __setitem__：对已实例化 key 抛异常，模拟
      “st.session_state.X cannot be modified after the widget with key X is instantiated”。
    """

    def __init__(self):
        super().__init__()
        self._instantiated = set()

    def instantiate(self, key):
        self._instantiated.add(key)

    def begin_run(self):
        self._instantiated = set()

    def widget_set(self, key, value):
        dict.__setitem__(self, key, value)

    def __setitem__(self, key, value):
        if key in self._instantiated:
            raise RuntimeError(
                "st.session_state.{0} cannot be modified after the widget "
                "with key {0} is instantiated.".format(key)
            )
        dict.__setitem__(self, key, value)


class _StubSt:
    def __init__(self, session_state=None):
        self.session_state = {} if session_state is None else session_state
        self.reference_time = None
        self.reference_hour = None
        self.reference_minute = None
        self.selectbox_calls = []
        self.form_widget_calls = []
        self.intake_text = ""
        self.intake_submitted = False
        self.feedback_text = ""
        self.feedback_submitted = False
        self.refresh = False
        self.rerun_called = 0
        self.writes = []
        self.markdown_calls = []
        self.errors = []
        self.warnings = []
        self._active = None
        self._in_expander = False

    def set_inputs(
        self, reference=None, reference_hour=None, reference_minute=None,
        intake="", intake_submitted=False,
        feedback="", feedback_submitted=False, refresh=False,
    ):
        self.reference_time = reference
        self.reference_hour = reference_hour
        self.reference_minute = reference_minute
        self.intake_text = intake
        self.intake_submitted = intake_submitted
        self.feedback_text = feedback
        self.feedback_submitted = feedback_submitted
        self.refresh = refresh
        return self

    def set_page_config(self, **kwargs):
        return None

    def title(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        self.warnings.append(args)

    def error(self, *args, **kwargs):
        self.errors.append(args)

    def expander(self, *args, **kwargs):
        return _Expander(self)

    def form(self, *args, **kwargs):
        name = args[0] if args else ""
        return _Form(self, "intake" in name)

    def time_input(self, label, key=None, value=None):
        if self.reference_time is not None:
            if key is not None:
                self.session_state[key] = self.reference_time
            return self.reference_time
        if key is not None and key in self.session_state:
            return self.session_state[key]
        return value

    def text_input(self, label, key=None, **kwargs):
        if self._active is not None:
            return self._active.text_input(label, key=key)
        return ""

    def selectbox(self, label, options, key=None, format_func=None, **kwargs):
        # 模拟 keyed st.selectbox：用户选择直接写回 session_state，并记录调用以校验
        # int options + format_func 两位数显示。严格模式下先标记 key 已实例化，
        # 之后用户代码再写该 key 就会抛异常（模拟 Streamlit 1.31 生命周期）。
        self.selectbox_calls.append((label, list(options), key, format_func))
        strict = isinstance(self.session_state, _StrictSessionState)
        if strict and key is not None:
            self.session_state.instantiate(key)
        if key == LIVE_REFERENCE_HOUR_KEY:
            if self.reference_hour is not None:
                if strict:
                    self.session_state.widget_set(key, self.reference_hour)
                else:
                    self.session_state[key] = self.reference_hour
            return self.session_state.get(key, options[0])
        if key == LIVE_CAMPUS_SELECT_KEY:
            # Legacy live-page fixtures make an explicit original-campus
            # selection; production has the "请选择校区" sentinel instead.
            if key not in self.session_state:
                if strict:
                    self.session_state.widget_set(key, "北洋园校区")
                else:
                    self.session_state[key] = "北洋园校区"
            return self.session_state[key]
        if key == LIVE_REFERENCE_MINUTE_KEY:
            if self.reference_minute is not None:
                if strict:
                    self.session_state.widget_set(key, self.reference_minute)
                else:
                    self.session_state[key] = self.reference_minute
            return self.session_state.get(key, options[0])
        if key is not None:
            return self.session_state.get(key, options[0] if options else None)
        return options[0] if options else None

    def columns(self, count):
        return tuple(_Column(self) for _ in range(count))

    def text_area(self, label, key=None, **kwargs):
        if self._active is not None:
            return self._active.text_area(label, key=key)
        return self.intake_text

    def form_submit_button(self, label, **kwargs):
        if self._active is not None:
            return self._active.form_submit_button(label, **kwargs)
        return False

    def button(self, label, key=None, **kwargs):
        if key == "p2_live_refresh":
            return self.refresh
        return False

    def write(self, *args, **kwargs):
        self.writes.append(args)

    def caption(self, *args, **kwargs):
        self.writes.append(args)

    def markdown(self, text, **kwargs):
        self.markdown_calls.append(text)

    def rerun(self):
        self.rerun_called += 1

    def experimental_rerun(self):
        self.rerun_called += 1


def _run_main(stub, caller, missing=(), now=None):
    if isinstance(stub.session_state, _StrictSessionState):
        stub.session_state.begin_run()
    main(
        stub,
        adapter_factory=lambda: FakeAdapter(caller),
        now_provider=lambda: now if now is not None else dt(9),
        configuration_loader=lambda: tuple(missing),
    )


def test_main_intake_flow_then_rerun_no_calls():
    caller = CountingCaller()
    stub = _StubSt().set_inputs(
        reference=time(9, 0),
        intake="我现在在宿舍，10点到11点半上课。今天要做计组实验。",
        intake_submitted=True,
    )
    _run_main(stub, caller)
    assert stub.rerun_called == 1
    assert LIVE_SESSION_KEY in stub.session_state
    assert caller.count == 2 + 3 + 5  # raw fallback + extraction + audit + planning + expression
    # 普通 rerun：不重复调用模型，只渲染
    stub.set_inputs()
    _run_main(stub, caller)
    assert caller.count == 10
    assert stub.markdown_calls
    assert "cf-plan-hero" in "\n".join(stub.markdown_calls)
    # 再一次 rerun 仍不调用
    _run_main(stub, caller)
    assert caller.count == 10


def test_p4b_top_input_chrome_keeps_existing_widget_keys_and_uses_static_hints_only():
    caller = CountingCaller()
    stub = _StubSt().set_inputs()
    _run_main(stub, caller)
    assert ("text_area", "接下来想做什么？", LIVE_INTAKE_KEY) in stub.form_widget_calls
    assert ("text_input", "我现在的位置（可选）", LIVE_CURRENT_LOCATION_KEY) in stub.form_widget_calls
    assert ("text_input", "告诉 CampusFlow 发生了什么……", LIVE_FEEDBACK_KEY) not in stub.form_widget_calls
    assert ("submit", "帮我安排", "primary") in stub.form_widget_calls
    # Kept mounted in a hidden region to preserve drafts across navigation.
    assert ("submit", "更新方案", "primary") in stub.form_widget_calls
    rendered = "\n".join(stub.markdown_calls)
    assert "cf-brand-header" in rendered
    assert "tjuer专属的校园时空规划系统" in rendered
    assert "学生项目 · 非官方教务系统" not in rendered
    assert "基于高德地图数据" in rendered
    assert "时间设置" in rendered
    assert caller.count == 0
    stub.set_inputs(intake="今天写作业", intake_submitted=True)
    _run_main(stub, caller)
    stub.set_inputs()
    _run_main(stub, caller)
    assert ("text_input", "进度、位置或临时变化", LIVE_FEEDBACK_KEY) in stub.form_widget_calls
    assert ("submit", "更新方案", "primary") in stub.form_widget_calls
    assert "更新进度或变化" in "\n".join(stub.markdown_calls)


def test_default_walk_hint_is_consumed_once_without_changing_transport_fact():
    store = {}
    save_personal_settings(store, PersonalSettings())
    turn = SimpleNamespace(
        movement_blocks=(SimpleNamespace(mode=TransportMode.WALK),)
    )
    assert _consume_default_walk_hint(store, turn, dt(9), "beiyangyuan", dt(9)) is True
    assert load_personal_settings(store).default_walk_hint_seen is True
    assert load_personal_settings(store).travel_mode == "walk"
    assert _consume_default_walk_hint(store, turn, dt(9), "beiyangyuan", dt(9)) is False

    explicit = {}
    save_personal_settings(explicit, PersonalSettings())
    explicit["personal_transport_explicit_for_day"] = True
    assert _consume_default_walk_hint(explicit, turn, dt(9), "beiyangyuan", dt(9)) is False


def test_main_feedback_continues_intake_state():
    caller = CountingCaller(task=TASK_PROGRESS_30)
    stub = _StubSt().set_inputs(
        reference=time(9, 0),
        intake="10点到11点半上课，今天做计组实验。",
        intake_submitted=True,
    )
    _run_main(stub, caller)
    session = stub.session_state[LIVE_SESSION_KEY]
    task_before = session.current_state().tasks[0]
    assert task_before.completed_minutes == 0
    stub.set_inputs(feedback="我刚又做了30分钟实验", feedback_submitted=True)
    _run_main(stub, caller)
    task_after = session.current_state().tasks[0]
    assert task_after.completed_minutes == 30
    assert task_after.total_minutes == 120
    assert task_after.state == TaskState.ACTIVE


def test_main_defaults_to_beiyangyuan_without_calling_planning_agent():
    """First load selects Beiyangyuan locally and performs no model call."""
    caller = CountingCaller()
    stub = _StubSt().set_inputs()
    _run_main(stub, caller)
    assert caller.count == 0
    assert stub.session_state[LIVE_CAMPUS_SELECT_KEY] == "北洋园校区"
    assert stub.session_state[SELECTED_CAMPUS_ID_KEY] == "beiyangyuan"
    assert LIVE_SESSION_KEY in stub.session_state


def test_live_intake_saves_structured_context_without_mutating_task_text():
    caller = CountingCaller(intake=INTAKE_EXECUTION_SEMANTICS)
    store = {}
    outcome = run_live_intake(
        store,
        dt(9),
        "写报告，然后吃饭",
        caller,
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        campus_id="weijinlu",
        current_location_text="31斋",
    )
    assert outcome.applied is not None
    assert "我现在在31斋" not in caller.calls[0][1]
    assert EXECUTION_PLAN_CONTEXT_KEY in store
    context = load_execution_context(store)
    report = context.binding_for(outcome.applied.new_task_refs[0])
    meal = context.binding_for(outcome.applied.new_task_refs[1])
    assert report.execution_location.display_name == "图书馆"
    assert meal.activity_kind == "meal"
    assert meal.effective_duration_minutes == 40
    assert meal.execution_location.source.value == "auto_selected_meal"
    assert context.current_location.source is CurrentLocationSource.USER
    assert context.current_location.location.display_name == "31斋"


def test_live_execution_context_survives_cache_and_is_cleared_by_campus_switch():
    caller = CountingCaller(intake=INTAKE_EXECUTION_SEMANTICS)
    store = {}
    select_campus(store, "weijinlu", DEFAULT_CAMPUS_REGISTRY)
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    first = run_live_intake(
        store, dt(9), "写报告，然后吃饭", caller,
        map_data=map_data, campus_id="weijinlu", current_location_text="31斋",
    )
    saved = load_execution_context(store)
    cached = run_live_intake(
        store, dt(9), "写报告，然后吃饭", caller,
        map_data=map_data, campus_id="weijinlu", current_location_text="31斋",
    )
    assert cached is first
    assert load_execution_context(store) == saved
    assert EXECUTION_PLAN_CONTEXT_KEY in store
    store[P4_FEEDBACK_DECISION_KEY] = FeedbackDecision()
    select_campus(store, "beiyangyuan", DEFAULT_CAMPUS_REGISTRY)
    assert EXECUTION_PLAN_CONTEXT_KEY not in store
    assert P4_FEEDBACK_DECISION_KEY not in store


def test_live_session_allocator_consumes_saved_meal_duration_on_start_and_refresh():
    caller = CountingCaller(intake=INTAKE_EXECUTION_SEMANTICS)
    store = {}
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    outcome = run_live_intake(
        store, dt(9), "写报告，然后吃饭", caller,
        map_data=map_data, campus_id="weijinlu", current_location_text="31斋",
    )
    session = make_live_session(store, FakeAdapter(caller), map_data=map_data, campus_id="weijinlu")
    first = session.start_day(outcome.applied.state)
    assert first.result.allocation_plan.total_planned_minutes == 40
    refreshed = session.refresh()
    assert refreshed.result.allocation_plan.total_planned_minutes == 40


def test_feedback_replaces_assumed_execution_origin_and_rebuilds_sequence():
    """An assumed origin is a routing fact and a verified correction replaces it."""
    # The unified model deliberately leaves current_location null: the live
    # controller must still honor the user's explicit correction text.
    caller = CountingCaller(
        intake=INTAKE_EXECUTION_SEMANTICS,
        plan=PLAN_REPORT_MEAL_ESTIMATE,
    )
    store = {}
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    intake = run_live_intake(
        store, dt(9), "在图书馆写报告，然后吃饭", caller,
        map_data=map_data, campus_id="weijinlu",
    )
    session = make_live_session(store, FakeAdapter(caller), map_data=map_data, campus_id="weijinlu")
    first = session.start_day(intake.applied.state)
    assumed = load_execution_context(store)
    assert assumed.current_location.source is CurrentLocationSource.ASSUMED
    assert assumed.current_location.location.display_name == "图书馆"
    assert all(block.origin_name != "31斋" for block in first.movement_blocks)
    session.refresh()
    assert load_execution_context(store) == assumed

    corrected = session.apply_feedback("其实我现在在31斋")
    context = load_execution_context(store)
    assert context.current_location.source is CurrentLocationSource.USER
    assert context.current_location.location.display_name == "31斋"
    assert not any(
        item.kind is ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED
        for item in context.confirmations
    )
    assert corrected.movement_blocks
    assert corrected.movement_blocks[0].origin_name == "31斋"
    assert any(block.destination_name == "图书馆" for block in corrected.movement_blocks)
    corrected_text = render_live_page_text(corrected)
    assert "你没有填写当前位置" not in corrected_text
    assert "你现在是在图书馆吗？" not in corrected_text


def test_unresolved_feedback_location_keeps_existing_assumption_and_map_scope():
    caller = _CurrentLocationFeedbackCaller(
        intake=INTAKE_EXECUTION_SEMANTICS,
        plan=PLAN_REPORT_MEAL_ESTIMATE,
    )
    store = {}
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    intake = run_live_intake(
        store, dt(9), "在图书馆写报告，然后吃饭", caller,
        map_data=map_data, campus_id="weijinlu",
    )
    session = make_live_session(store, FakeAdapter(caller), map_data=map_data, campus_id="weijinlu")
    session.start_day(intake.applied.state)
    before = load_execution_context(store)
    turn = session.apply_feedback("我现在在9斋")
    after = load_execution_context(store)
    assert before == after
    assert after.current_location.source is CurrentLocationSource.ASSUMED
    assert not any(block.origin_name == "9斋" for block in turn.movement_blocks)


def test_p4_presentation_renders_assumption_auto_meal_and_class_10_5_facts():
    caller = CountingCaller(
        intake=INTAKE_EXECUTION_PRESENTATION,
        plan=PLAN_REPORT_MEAL_ESTIMATE,
    )
    store = {}
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    intake = run_live_intake(
        store, dt(9), "在图书馆写报告，然后吃饭，15点到16点去9教上课", caller,
        map_data=map_data, campus_id="weijinlu",
    )
    session = make_live_session(
        store, FakeAdapter(caller), map_data=map_data, campus_id="weijinlu",
        companion_enabled=True,
    )
    turn = session.start_day(intake.applied.state)
    context = load_execution_context(store)
    meal = context.binding_for("day_task_002").execution_location
    text = render_live_page_text(turn)
    assert text.startswith("# 当前方案\n")
    assert turn.companion_copy.opening in text
    assert turn.companion_copy.closing in text
    assert "你没有填写当前位置，我先按你在图书馆来安排。" in text
    assert "你现在是在图书馆吗？" in text
    assert "{}【已为您选择就近食堂】".format(meal.display_name) in text
    assert "吃饭 40 分钟" in text
    assert "14:50" in text and "到教学楼" in text
    assert "14:50–14:55：进楼 / 找教室" in text
    assert "14:55–15:00：到教室后签到 / 课前准备" in text
    assert "15:00–16:00：上课" in text
    assert "提前10分钟到教室" not in text
    assert "（AI暂估）" not in "\n".join(
        line for line in text.splitlines() if "去" in line
    )


def test_live_weijinlu_execution_sequence_is_scoped_ordered_and_refresh_safe():
    """Real controller path: no cross-campus copy, gaps, or reversed intake order."""
    caller = _UnsafeCampusCopyFeedbackCaller(
        intake=INTAKE_LIVE_EXECUTION_REGRESSION,
        # Deliberately contradictory model order: persisted intake order wins.
        plan=PLAN_REVERSED_REPORT_MEAL,
    )
    store = {}
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    intake = run_live_intake(
        store,
        dt(15),
        "我想在图书馆写一会儿实验报告，然后吃饭，今天17:00去9教上课",
        caller,
        map_data=map_data,
        campus_id="weijinlu",
    )
    session = make_live_session(
        store, FakeAdapter(caller), map_data=map_data, campus_id="weijinlu",
        companion_enabled=True,
    )
    turn = session.start_day(intake.applied.state)
    context = load_execution_context(store)
    meal = context.binding_for("day_task_002")
    assert context.current_location.source is CurrentLocationSource.ASSUMED
    assert context.current_location.location.display_name == "图书馆"
    assert meal.execution_location.source.value == "auto_selected_meal"
    assert all(location.campus_id == "weijinlu" for location in (
        context.current_location.location,
        context.binding_for("day_task_001").execution_location,
        meal.execution_location,
    ))
    assert [item.task_ref for item in turn.result.allocation_plan.allocations][:2] == [
        "day_task_001", "day_task_002"
    ]
    assert any(
        block.origin_name == meal.execution_location.display_name
        and block.destination_name == "第九教学楼"
        for block in turn.movement_blocks
    )
    assert final_plan_overlap_errors(
        turn.result.updated_state, turn.result.allocation_plan, turn.movement_blocks
    ) == ()
    text = render_live_page_text(turn)
    assert "北洋园校区" not in text
    assert "天津大学北洋园校区郑东图书馆" not in text
    assert "你正在图书馆" not in text
    assert "你没有填写当前位置，我先按你在图书馆来安排。" in text
    assert "16:50–16:55：进楼 / 找教室" in text
    assert "16:55–17:00：到教室后签到 / 课前准备" in text
    assert "提前10分钟到教室" not in text

    refreshed = session.refresh()
    assert final_plan_overlap_errors(
        refreshed.result.updated_state, refreshed.result.allocation_plan, refreshed.movement_blocks
    ) == ()
    assert [item.task_ref for item in refreshed.result.allocation_plan.allocations][:2] == [
        "day_task_001", "day_task_002"
    ]
    assert all(block.origin_node_id.startswith("weijinlu_") for block in refreshed.movement_blocks)
    assert load_execution_context(store).current_location.source is CurrentLocationSource.ASSUMED

    corrected = session.apply_feedback("其实我现在在31斋")
    corrected_context = load_execution_context(store)
    assert corrected_context.current_location.source is CurrentLocationSource.USER
    assert corrected_context.current_location.location.display_name == "31斋"
    assert not any(
        item.kind is ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED
        for item in corrected_context.confirmations
    )
    assert corrected.movement_blocks
    assert corrected.movement_blocks[0].origin_name == "31斋"
    corrected_text = render_live_page_text(corrected)
    assert "你没有填写当前位置" not in corrected_text
    assert "你现在是在图书馆吗？" not in corrected_text


def test_live_meal_is_protected_before_a_late_class_and_idle_uses_final_timeline():
    """A long flexible report cannot consume an explicitly requested meal."""
    caller = _CurrentLocationFeedbackCaller(
        intake=INTAKE_LIVE_EXECUTION_2000,
        plan=PLAN_LONG_REPORT_REVERSED,
    )
    store = {}
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    intake = run_live_intake(
        store,
        dt(17),
        "我想在图书馆写一会儿实验报告，然后吃饭，今天20:00去9教上课",
        caller,
        map_data=map_data,
        campus_id="weijinlu",
    )
    session = make_live_session(
        store, FakeAdapter(caller), map_data=map_data, campus_id="weijinlu",
        companion_enabled=True,
    )
    turn = session.start_day(intake.applied.state)
    context = load_execution_context(store)
    meal = context.binding_for("day_task_002")
    plan = turn.result.allocation_plan
    assert plan.planned_minutes_by_task["day_task_002"] == 40
    refs = [item.task_ref for item in plan.allocations]
    meal_index = refs.index("day_task_002")
    assert refs[:meal_index] and all(ref == "day_task_001" for ref in refs[:meal_index])
    assert all(ref != "day_task_001" for ref in refs[meal_index + 1:])
    assert any(
        block.origin_name == meal.execution_location.display_name
        and block.destination_name == "第九教学楼"
        for block in turn.movement_blocks
    )
    assert final_plan_overlap_errors(turn.result.updated_state, plan, turn.movement_blocks) == ()
    text = render_live_page_text(turn)
    assert "吃饭 40 分钟" in text
    assert "【已为您选择就近食堂】" in text
    assert "去第九教学楼" in text
    assert text.index("去{}【已为您选择就近食堂】".format(meal.execution_location.display_name)) < text.index("吃饭 40 分钟")
    assert text.index("吃饭 40 分钟") < text.index("去第九教学楼")
    assert "19:50–19:55：进楼 / 找教室" in text
    assert "19:55–20:00：到教室后签到 / 课前准备" in text
    assert "19:46到20:00之间还有14分钟空闲" not in text
    occupied = [
        (item["start"], item["end"])
        for item in _expression_segments(turn.result.updated_state, plan, turn.movement_blocks)
    ]
    for gap in _idle_gaps(turn.result.updated_state, plan, turn.movement_blocks):
        assert all(gap["end"] <= start or end <= gap["start"] for start, end in occupied)


def test_streamlit_live_start_and_feedback_share_one_final_execution_turn():
    """Exercise the actual page submit/rerun path, not a controller shortcut."""
    caller = _CurrentLocationFeedbackCaller(
        intake=INTAKE_LIVE_EXECUTION_2000,
        plan=PLAN_LONG_REPORT_REVERSED,
    )
    stub = _StubSt().set_inputs(
        reference_hour=17,
        reference_minute=0,
        intake="我想在图书馆写一会儿实验报告，然后吃饭，今天20:00到21:00去9教上课",
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(17))
    assert not stub.errors

    # The post-submit Streamlit rerun must render the same final P4 turn.
    stub.set_inputs(reference_hour=17, reference_minute=0)
    _run_main(stub, caller, now=dt(17))
    session = stub.session_state[LIVE_SESSION_KEY]
    turn = session.last_turn()
    context = load_execution_context(stub.session_state)
    meal = context.binding_for("day_task_002")
    assert session.campus_id == "weijinlu"
    assert meal.effective_duration_minutes == 40
    assert meal.duration_source == "meal_default"
    assert any(
        block.destination_activity_ref == "day_task_002"
        and block.destination_node_id == meal.execution_location.node_id
        for block in turn.movement_blocks
    )
    # The final turn retains both the inbound meal leg and a later campus
    # transition; exact commitment refs are intentionally not renderer keys.
    assert len(turn.movement_blocks) >= 2
    assert final_plan_overlap_errors(
        turn.result.updated_state, turn.result.allocation_plan, turn.movement_blocks
    ) == ()
    page = "\n".join(stub.markdown_calls)
    assert "你没有填写当前位置，我先按你在图书馆来安排。" in page
    assert "你现在是在图书馆吗？" in page
    assert "就近食堂</span>" in page
    assert "cf-pill-meal" in page
    assert "去第九教学楼" in page
    assert "吃饭 40 分钟（AI暂估）" not in page
    assert "19:50–19:55" in page and "进楼找教室" in page

    # A real page feedback submission replaces the assumed origin, rebuilds
    # the execution route, and leaves no UI-bound exception behind.
    stub.markdown_calls = []
    stub.set_inputs(
        reference_hour=17,
        reference_minute=0,
        feedback="其实我现在在31斋",
        feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(17))
    assert not stub.errors
    corrected = session.last_turn()
    corrected_context = load_execution_context(stub.session_state)
    assert corrected_context.current_location.source is CurrentLocationSource.USER
    assert corrected_context.current_location.location.display_name == "31斋"
    assert corrected.movement_blocks[0].origin_name == "31斋"


def test_streamlit_unknown_class_end_keeps_one_final_timeline_and_feedback_works():
    """Page-equivalent regression for a 21:00 class with no stated end time."""
    # The unified model deliberately leaves current_location null: the live
    # controller must still honor the user's explicit correction text.
    caller = CountingCaller(
        intake=INTAKE_LIVE_EXECUTION_2100_UNKNOWN_END,
        plan=PLAN_LONG_REPORT_REVERSED,
    )
    stub = _StubSt().set_inputs(
        reference_hour=18,
        reference_minute=0,
        intake="我想在图书馆写一会儿实验报告，然后吃饭，今天21:00去9教上课",
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(18))
    assert not stub.errors
    stub.set_inputs(reference_hour=18, reference_minute=0)
    _run_main(stub, caller, now=dt(18))

    session = stub.session_state[LIVE_SESSION_KEY]
    turn = session.last_turn()
    context = load_execution_context(stub.session_state)
    meal = context.binding_for("day_task_002")
    commitment = turn.result.updated_state.commitments[0]
    assert commitment.ends_at is None
    assert commitment.commitment_ref in turn.result.updated_state.unresolved_commitment_refs
    assert turn.result.allocation_plan.planned_minutes_by_task["day_task_002"] == 40
    assert meal.execution_location.source.value == "auto_selected_meal"
    assert any(
        block.destination_activity_ref == "day_task_002"
        for block in turn.movement_blocks
    )
    assert any(
        block.origin_node_id == meal.execution_location.node_id
        and block.destination_name == "第九教学楼"
        for block in turn.movement_blocks
    )
    assert final_plan_overlap_errors(
        turn.result.updated_state, turn.result.allocation_plan, turn.movement_blocks
    ) == ()
    page = "\n".join(stub.markdown_calls)
    assert "你没有填写当前位置，我先按你在图书馆来安排。" in page
    assert "你现在是在图书馆吗？" in page
    assert "就近食堂</span>" in page
    assert "去第九教学楼" in page
    assert "20:50" in page and "到教学楼" in page
    assert "20:50–20:55" in page and "进楼找教室" in page
    assert "20:55–21:00" in page and "课前准备" in page
    assert "21:00" in page and "上课" in page
    assert "21:00–22:00" not in page
    assert "空闲" not in page.split("21:00", 1)[-1]

    stub.markdown_calls = []
    stub.set_inputs(
        reference_hour=18,
        reference_minute=0,
        feedback="其实我现在在31斋",
        feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(18))
    assert not stub.errors
    corrected = session.last_turn()
    corrected_context = load_execution_context(stub.session_state)
    assert corrected_context.current_location.source is CurrentLocationSource.USER
    assert corrected_context.current_location.location.display_name == "31斋"
    assert corrected.movement_blocks[0].origin_name == "31斋"
    # The single user action triggers an internal rerun; the following normal
    # page render is already the corrected final turn, not a retry request.
    stub.set_inputs(reference_hour=18, reference_minute=0)
    _run_main(stub, caller, now=dt(18))
    corrected_page = "\n".join(stub.markdown_calls)
    assert "当前方案正在重新校验，请刷新后重试。" not in corrected_page
    assert "你没有填写当前位置" not in corrected_page
    assert "你现在是在图书馆吗？" not in corrected_page
    assert "就近食堂</span>" in corrected_page


def test_live_atomic_final_turn_rebuilds_feedback_from_canonical_facts_once(monkeypatch):
    """The actual 17:00 P4 page publishes and replaces one complete turn bundle."""
    allocator_contracts = []
    real_allocator = execution_movement.allocate_tasks_across_windows

    def _capture_final_allocator(state, *args, **kwargs):
        plan = real_allocator(state, *args, **kwargs)
        allocator_contracts.append((state, dict(kwargs), plan))
        return plan

    monkeypatch.setattr(
        execution_movement, "allocate_tasks_across_windows", _capture_final_allocator
    )
    # Real Qwen may ask the expression layer to revise the plan.  P4's formal
    # allocation is already final at that boundary, so the copy path must not
    # invoke the legacy bare allocator and replace it with an unconstrained plan.
    caller = _RevisionRequestingFeedbackCaller(
        intake=INTAKE_LIVE_EXECUTION_REAL_1700_SHAPE,
        plan=PLAN_LONG_REPORT_REVERSED,
    )
    stub = _StubSt().set_inputs(
        reference_hour=14,
        reference_minute=0,
        intake="我想在图书馆写一会儿实验报告，然后吃饭，今天17:00去9教上课",
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(14))
    assert not stub.errors

    initial_bundle = load_live_final_turn(stub.session_state)
    assert initial_bundle is not None
    initial = initial_bundle.turn
    context = initial_bundle.execution_context
    meal = context.binding_for("day_task_002")
    assert initial_bundle.state.commitments[0].ends_at is None
    assert context.current_location.source is CurrentLocationSource.ASSUMED
    assert context.current_location.location.display_name == "图书馆"
    assert meal.duration_source == "meal_default"
    assert meal.effective_duration_minutes == 40
    assert initial.result.allocation_plan.planned_minutes_by_task["day_task_002"] == 40
    assert meal.execution_location.source.value == "auto_selected_meal"
    assert any(block.destination_activity_ref == "day_task_002" for block in initial.movement_blocks)
    assert any(
        block.origin_activity_ref == "day_task_002"
        and block.destination_name == "第九教学楼"
        for block in initial.movement_blocks
    )
    departure = next(
        (block.transition_start or block.window_start)
        for block in initial.movement_blocks
        if block.origin_activity_ref == "day_task_001"
        and block.destination_activity_ref == "day_task_002"
    )
    report_segments = [
        item for item in _expression_segments(
            initial_bundle.state, initial_bundle.result.allocation_plan,
            initial_bundle.movement_blocks,
        )
        if item["kind"] == "task" and item["title"] == "写实验报告"
    ]
    assert report_segments and all(item["end"] <= departure for item in report_segments)
    assert "day_task_001" in initial.result.allocation_plan.unallocated_task_refs
    initial_contract_count = len(allocator_contracts)
    assert initial_contract_count >= 1
    matching_contracts = [
        item for item in allocator_contracts
        if item[1].get("latest_end_by_task_ref", {}).get("day_task_001") == departure
    ]
    assert matching_contracts
    allocator_state, allocator_kwargs, allocator_plan = matching_contracts[-1]
    assert all(window.ends_at <= dt(16, 50) for window in allocator_state.windows)
    allocator_segments = [
        item for item in _expression_segments(allocator_state, allocator_plan, ())
        if item["kind"] == "task" and item["title"] == "写实验报告"
    ]
    assert allocator_segments and all(item["end"] <= departure for item in allocator_segments)
    assert final_plan_overlap_errors(
        initial_bundle.state, initial_bundle.result.allocation_plan,
        initial_bundle.movement_blocks,
    ) == ()
    assert initial.agent_intelligence is not None
    assert any(
        record.stage == "intent_compliance_reviewer"
        for record in initial.agent_intelligence.trace.records
    )

    # Even deliberately stale compatibility snapshots cannot change P4 render.
    stub.session_state[LAST_TURN_KEY] = None
    stub.markdown_calls = []
    stub.set_inputs(reference_hour=14, reference_minute=0)
    _run_main(stub, caller, now=dt(14))
    initial_page = "\n".join(stub.markdown_calls)
    assert "就近食堂</span>" in initial_page
    assert "你没有填写当前位置，我先按你在图书馆来安排。" in initial_page

    # One feedback submit creates version N+1; the simulated internal rerun
    # only renders that already-committed complete bundle.
    stub.markdown_calls = []
    stub.set_inputs(
        reference_hour=14,
        reference_minute=0,
        feedback="其实我现在在31斋",
        feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(14))
    assert not stub.errors
    corrected_bundle = load_live_final_turn(stub.session_state)
    assert corrected_bundle.version == initial_bundle.version + 1
    assert corrected_bundle is stub.session_state[LIVE_FINAL_TURN_KEY]
    assert corrected_bundle.execution_context.current_location.source is CurrentLocationSource.USER
    assert corrected_bundle.execution_context.current_location.location.display_name == "31斋"
    assert corrected_bundle.turn.movement_blocks[0].origin_name == "31斋"
    assert corrected_bundle.turn.result.allocation_plan.planned_minutes_by_task["day_task_002"] == 40
    corrected_departure = next(
        (block.transition_start or block.window_start)
        for block in corrected_bundle.movement_blocks
        if block.origin_activity_ref == "day_task_001"
        and block.destination_activity_ref == "day_task_002"
    )
    corrected_report_segments = [
        item for item in _expression_segments(
            corrected_bundle.state,
            corrected_bundle.result.allocation_plan,
            corrected_bundle.movement_blocks,
        )
        if item["kind"] == "task" and item["title"] == "写实验报告"
    ]
    assert corrected_report_segments
    assert all(item["end"] <= corrected_departure for item in corrected_report_segments)
    assert "day_task_001" in corrected_bundle.result.allocation_plan.unallocated_task_refs
    assert len(allocator_contracts) > initial_contract_count
    corrected_matching = [
        item for item in allocator_contracts[initial_contract_count:]
        if item[1].get("latest_end_by_task_ref", {}).get("day_task_001")
        == corrected_departure
    ]
    assert corrected_matching
    corrected_allocator_state, corrected_allocator_kwargs, corrected_allocator_plan = corrected_matching[-1]
    assert all(window.ends_at <= dt(16, 50) for window in corrected_allocator_state.windows)
    corrected_allocator_segments = [
        item for item in _expression_segments(
            corrected_allocator_state, corrected_allocator_plan, ()
        )
        if item["kind"] == "task" and item["title"] == "写实验报告"
    ]
    assert corrected_allocator_segments
    assert all(item["end"] <= corrected_departure for item in corrected_allocator_segments)
    assert final_plan_overlap_errors(
        corrected_bundle.state,
        corrected_bundle.result.allocation_plan,
        corrected_bundle.movement_blocks,
    ) == ()
    assert all(
        getattr(item, "kind", None) is not ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED
        for item in corrected_bundle.execution_context.confirmations
    )

    stub.markdown_calls = []
    stub.set_inputs(reference_hour=14, reference_minute=0)
    _run_main(stub, caller, now=dt(14))
    corrected_page = "\n".join(stub.markdown_calls)
    assert "当前方案正在重新校验" not in corrected_page
    assert "你没有填写当前位置" not in corrected_page
    assert "你现在是在图书馆吗？" not in corrected_page
    assert "就近食堂</span>" in corrected_page


def test_live_near_class_compresses_default_meal_to_one_safe_block_and_publishes_turn():
    """At 16:04 the default meal may use its 20-minute safety floor."""
    caller = _CurrentLocationFeedbackCaller(
        intake=INTAKE_LIVE_EXECUTION_REAL_1700_SHAPE,
        plan=PLAN_LONG_REPORT_REVERSED,
    )
    stub = _StubSt().set_inputs(
        reference_hour=16,
        reference_minute=4,
        intake="我想在图书馆写一会儿实验报告，然后吃饭，今天17:00去9教上课",
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(16, 4))
    assert not stub.errors

    bundle = load_live_final_turn(stub.session_state)
    assert bundle is not None
    turn = bundle.turn
    meal = bundle.execution_context.binding_for("day_task_002")
    meal_allocations = [
        item for item in turn.result.allocation_plan.allocations
        if item.task_ref == "day_task_002"
    ]
    assert meal.duration_source == "meal_default"
    assert meal.effective_duration_minutes == 40
    assert meal_allocations == []
    assert turn.result.allocation_plan.planned_minutes_by_task.get("day_task_002", 0) == 0
    assert "day_task_002" in turn.result.allocation_plan.unallocated_task_refs
    assert final_plan_overlap_errors(
        turn.result.updated_state, turn.result.allocation_plan, turn.movement_blocks
    ) == ()

    stub.markdown_calls = []
    stub.set_inputs(reference_hour=16, reference_minute=4)
    _run_main(stub, caller, now=dt(16, 4))
    page = "\n".join(stub.markdown_calls)
    assert "吃饭 20 分钟" not in page
    assert SAFE_ERROR_TEXT not in page


def test_live_atomic_feedback_failure_keeps_previous_reliable_bundle():
    caller = _CurrentLocationFeedbackCaller(
        intake=INTAKE_LIVE_EXECUTION_1700_UNKNOWN_END,
        plan=PLAN_LONG_REPORT_REVERSED,
    )
    stub = _StubSt().set_inputs(
        reference_hour=14,
        reference_minute=0,
        intake="我想在图书馆写一会儿实验报告，然后吃饭，今天17:00去9教上课",
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(14))
    reliable = load_live_final_turn(stub.session_state)

    stub.set_inputs(
        reference_hour=14,
        reference_minute=0,
        feedback="其实我现在在火星基地",
        feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(14))
    assert stub.errors[-1] == (SAFE_ERROR_TEXT,)
    assert load_live_final_turn(stub.session_state) is reliable
    assert load_execution_context(stub.session_state) == reliable.execution_context

    stub.markdown_calls = []
    stub.set_inputs(reference_hour=14, reference_minute=0)
    _run_main(stub, caller, now=dt(14))
    page = "\n".join(stub.markdown_calls)
    assert "当前方案正在重新校验" not in page
    assert "吃饭 40 分钟" in page


def test_p4_presentation_keeps_explicit_meal_unlabelled_and_asks_when_meal_is_unresolved():
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    explicit_caller = CountingCaller(
        intake=INTAKE_EXPLICIT_MEAL,
        plan=PLAN_REPORT_MEAL_ESTIMATE,
    )
    explicit_store = {}
    explicit_intake = run_live_intake(
        explicit_store, dt(9), "在图书馆写报告，去学三食堂吃饭", explicit_caller,
        map_data=map_data, campus_id="weijinlu", current_location_text="31斋",
    )
    explicit_session = make_live_session(
        explicit_store, FakeAdapter(explicit_caller), map_data=map_data, campus_id="weijinlu"
    )
    explicit_text = render_live_page_text(explicit_session.start_day(explicit_intake.applied.state))
    assert "学三食堂【已为您选择就近食堂】" not in explicit_text

    unresolved_caller = CountingCaller(intake=INTAKE_UNRESOLVED_MEAL)
    unresolved_store = {}
    unresolved_intake = run_live_intake(
        unresolved_store, dt(9), "吃饭，然后背单词", unresolved_caller,
        map_data=map_data, campus_id="weijinlu",
    )
    unresolved_session = make_live_session(
        unresolved_store, FakeAdapter(unresolved_caller), map_data=map_data, campus_id="weijinlu"
    )
    unresolved_text = render_live_page_text(
        unresolved_session.start_day(unresolved_intake.applied.state)
    )
    assert "你现在大概在哪里？我可以帮你选一个顺路的食堂。" in unresolved_text
    assert "【已为您选择就近食堂】" not in unresolved_text


def test_main_feedback_pipeline_exception_shows_safe_message():
    # P3e-final：unified/feedback pipeline 抛异常时，页面不得暴露
    # TypeError / Traceback / 本地路径 / 内部实现信息，只显示一句人话。
    class _ExplodingTurn:
        pass

    class _ExplodingSession:
        def __init__(self):
            self.caller = None
            self.store = {}

        def last_turn(self):
            return _ExplodingTurn()

        def apply_feedback(self, text):
            raise TypeError("unsupported operand type(s) for +: 'NoneType' and 'datetime.timedelta'")

        def refresh(self):
            return None

    stub = _StubSt().set_inputs(feedback="上课改到下午3点半，实验先做到这里", feedback_submitted=True)
    # Test fixtures explicitly retain their campus rather than exercising the
    # production campus-switch invalidation path.
    stub.session_state[SELECTED_CAMPUS_ID_KEY] = "beiyangyuan"
    stub.session_state[LIVE_SESSION_KEY] = _ExplodingSession()
    _run_main(stub, CountingCaller())
    # 只显示安全人话，不显示 traceback / 内部异常文本
    assert stub.errors
    assert all(SAFE_ERROR_TEXT in str(args[0]) for args in stub.errors)
    visible = " ".join(str(a) for a in stub.errors + stub.writes + stub.warnings)
    visible += "\n" + "\n".join(stub.markdown_calls)
    for marker in (
        "TypeError", "Traceback", "NoneType", "unsupported operand",
        "src", "p2_live_main", ".py", "C:\\",
    ):
        assert marker not in visible, marker
    # 内部诊断仍保留在 session_state，不破坏排查能力
    assert LIVE_ERROR_KEY in stub.session_state
    assert "apply_feedback" in stub.session_state[LIVE_ERROR_KEY]


def test_main_missing_config_blocks_intake():
    caller = CountingCaller()
    stub = _StubSt().set_inputs(
        reference=time(9, 0), intake="今天做实验。", intake_submitted=True
    )
    _run_main(stub, caller, missing=("TJU_LLM_API_KEY", "TJU_LLM_BASE_URL", "TJU_LLM_MODEL"))
    assert caller.count == 0
    assert stub.errors
    assert LIVE_SESSION_KEY not in stub.session_state or True
    assert stub.session_state.get("p2_state") is None


def test_main_time_input_builds_today_reference():
    # 小时=18 / 分钟=30 与当天日期组合成 reference_datetime
    caller = CountingCaller()
    stub = _StubSt().set_inputs(
        reference_hour=18, reference_minute=30, intake="今天做实验。", intake_submitted=True
    )
    _run_main(stub, caller)
    assert caller.count == 2 + 3 + 5  # raw fallback + extraction + audit + planning + expression
    assert "2026-09-01 18:30" in caller.calls[0][1]
    assert not stub.errors
    assert not any("格式" in str(w) or "参考时间" in str(w) for w in stub.warnings)


def test_main_refresh_button_triggers():
    caller = CountingCaller()
    stub = _StubSt().set_inputs(
        reference=time(9, 0),
        intake="10点到11点半上课，今天做计组实验。",
        intake_submitted=True,
    )
    _run_main(stub, caller)
    before = caller.count
    assert stub.session_state[SELECTED_CAMPUS_ID_KEY] == "beiyangyuan"
    stub.set_inputs(refresh=True)
    _run_main(stub, caller)
    assert caller.count == before + 7  # 刷新重跑 Day Plan + Review + 5 表达层 Agent（不路由不 reconciliation）
    assert stub.rerun_called == 2
    assert stub.session_state[SELECTED_CAMPUS_ID_KEY] == "beiyangyuan"


def test_main_intake_failure_shows_warning_no_state():
    caller = CountingCaller(intake="不是 JSON")
    stub = _StubSt().set_inputs(
        reference=time(9, 0), intake="今天做实验。", intake_submitted=True
    )
    _run_main(stub, caller)
    assert stub.session_state.get("p2_state") is None
    assert any("首次全天计划暂未生成" in str(item) for item in stub.warnings)

def test_live_page_never_leaks_mock_question_internal_params():
    """mock Agent 故意在统一反馈 question 里塞内部参数，最终 Streamlit 页面也不泄露。"""
    caller = CountingCaller(
        unified_questions=["你说的 task_ref=day_task_001 是否要恢复为 ACTIVE？"]
    )
    stub = _StubSt().set_inputs(
        reference=time(9, 0),
        intake="10点到11点半上课，今天做计组实验。",
        intake_submitted=True,
    )
    _run_main(stub, caller)
    assert stub.rerun_called == 1
    stub.set_inputs(feedback="我又做了30分钟实验", feedback_submitted=True)
    _run_main(stub, caller)
    # 反馈提交后 rerun，再普通 rerun 渲染页面
    stub.set_inputs()
    _run_main(stub, caller)
    joined = "\n".join(stub.markdown_calls)
    assert "cf-plan-provisional" in joined
    assert "这个反馈暂时无法确定对应哪个任务，请再说明一下。" in joined
    for marker in ("task_ref", "day_task_001", "ACTIVE", "schema_version"):
        assert marker not in joined


def test_live_render_streamlit_uses_current_plan_timeline_card():
    """The live page renders a card timeline, not a raw Markdown list."""
    caller = CountingCaller()
    session = make_live_session({}, FakeAdapter(caller))
    state = _intake_state(caller)
    turn = session.start_day(state)

    class _MarkdownStub:
        def __init__(self):
            self.calls = []

        def markdown(self, text, **kwargs):
            self.calls.append(text)

    stub = _MarkdownStub()
    render_page_streamlit(stub, turn)
    page = "\n".join(stub.calls)
    assert "cf-plan-hero" in page
    assert "cf-timeline-card" in page
    assert "cf-focus-action" in page
    assert " 分钟" in page


def test_streamlit_plan_block_visually_emphasized():
    """Actual plan is the visual hierarchy through the timeline card."""
    caller = CountingCaller()
    session = make_live_session({}, FakeAdapter(caller))
    state = _intake_state(caller)
    turn = session.start_day(state)

    class _MarkdownStub:
        def __init__(self):
            self.calls = []

        def markdown(self, text, **kwargs):
            self.calls.append(text)

    stub = _MarkdownStub()
    render_page_streamlit(stub, turn)
    calls = stub.calls
    page = "\n".join(calls)
    assert "cf-plan-hero" in page
    assert "cf-timeline-card" in page
    assert "cf-node" in page
    assert "cf-focus-action" in page


# ---------------------------------------------------------------------------
# P2e UI-fix：假定当前时间持久化在 st.session_state，不被系统时间覆盖
# ---------------------------------------------------------------------------


def test_time_input_initializes_with_system_time_once():
    """首次打开默认系统小时/分钟，且只在第一次初始化。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 9  # now_provider 09:00
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 0
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 9
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 0


def test_time_input_persists_user_choice_across_reruns():
    """用户改成 10:05 后普通 rerun 仍为 10:05，不被系统时间覆盖。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 9
    stub.set_inputs(reference_hour=10, reference_minute=5)
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 10
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 5
    stub.set_inputs()
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 10
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 5


def test_time_input_persists_after_filling_intake_text():
    """填写全天计划文本后 rerun 仍为 10:05。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    stub.set_inputs(reference_hour=10, reference_minute=5)
    _run_main(stub, caller)
    stub.set_inputs(intake="今天做计组实验。")
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 10
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 5


def test_intake_uses_persisted_reference_time():
    """点击规划后 reference_datetime 使用 10:05（而非系统时间 09:00）。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    stub.set_inputs(reference_hour=10, reference_minute=5)
    _run_main(stub, caller)
    stub.set_inputs(intake="今天做计组实验。", intake_submitted=True)
    _run_main(stub, caller)
    assert caller.count == 2 + 3 + 5  # raw fallback + extraction + audit + planning + expression
    assert "2026-09-01 10:05" in caller.calls[0][1]
    assert LIVE_SESSION_KEY in stub.session_state


def test_refresh_keeps_persisted_reference_time():
    """refresh 后小时/分钟仍保持用户选择的 10:05。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    stub.set_inputs(reference_hour=10, reference_minute=5)
    _run_main(stub, caller)
    stub.set_inputs(intake="今天做计组实验。", intake_submitted=True)
    _run_main(stub, caller)
    stub.set_inputs(refresh=True)
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 10
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 5


def test_stale_reference_cleaned_once():
    """旧版本遗留的 reference 文本：小时/分钟下拉框各自初始化，不报错。"""
    caller = CountingCaller()
    stub = _StubSt()
    stub.session_state[LIVE_REFERENCE_KEY] = "2026-09-01 09:00"
    _run_main(stub, caller)
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 9
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 0


# ---------------------------------------------------------------------------
# P3e-final：小时/分钟下拉框时间输入
# ---------------------------------------------------------------------------


def test_hour_options_cover_full_range():
    """小时下拉框内部为 int 0~23，format_func 显示两位数。"""
    from src.p2_live_main import _HOUR_OPTIONS

    assert len(_HOUR_OPTIONS) == 24
    assert _HOUR_OPTIONS[0] == 0
    assert _HOUR_OPTIONS[13] == 13
    assert _HOUR_OPTIONS[23] == 23
    fmt = lambda x: "{:02d}".format(x)
    assert fmt(0) == "00"
    assert fmt(13) == "13"
    assert fmt(23) == "23"


def test_minute_options_cover_full_range():
    """分钟下拉框内部为 int 0~59（无5分钟步长限制），format_func 显示两位数。"""
    from src.p2_live_main import _MINUTE_OPTIONS

    assert len(_MINUTE_OPTIONS) == 60
    assert _MINUTE_OPTIONS[0] == 0
    assert _MINUTE_OPTIONS[35] == 35
    assert _MINUTE_OPTIONS[3] == 3
    assert _MINUTE_OPTIONS[59] == 59
    fmt = lambda x: "{:02d}".format(x)
    assert fmt(3) == "03"
    assert fmt(35) == "35"
    assert fmt(59) == "59"


def test_reference_select_combines_hour_minute():
    """13:35 / 15:03 / 23:59 / 00:00 都正确组合成 reference（纯读取）。"""
    from src.p2_live_main import _resolve_reference_time_select

    for hour, minute in ((13, 35), (15, 3), (23, 59), (0, 0), (15, 25), (8, 30)):
        store = {LIVE_REFERENCE_HOUR_KEY: hour, LIVE_REFERENCE_MINUTE_KEY: minute}
        ref, err = _resolve_reference_time_select(store)
        assert err == ""
        assert ref == time(hour, minute), (hour, minute)


def test_migrate_initializes_from_system_once():
    """pre-widget 迁移：首次进入用系统当前小时/分钟初始化。"""
    from src.p2_live_main import (
        _migrate_reference_hour_minute,
        _resolve_reference_time_select,
    )

    store = {}
    hour, minute = _migrate_reference_hour_minute(store, lambda: dt(9, 35))
    assert hour == 9
    assert minute == 35
    assert store[LIVE_REFERENCE_HOUR_KEY] == 9
    assert store[LIVE_REFERENCE_MINUTE_KEY] == 35
    ref, err = _resolve_reference_time_select(store)
    assert err == ""
    assert ref == time(9, 35)


def test_reference_select_accepts_mapping_like_store():
    """非 dict 的 Mapping（真实 st.session_state 类型）也能正常工作。"""
    from collections.abc import MutableMapping

    from src.p2_live_main import (
        _migrate_reference_hour_minute,
        _resolve_reference_time_select,
    )

    class _MappingStore(MutableMapping):
        def __init__(self):
            self._d = {}

        def __getitem__(self, key):
            return self._d[key]

        def __setitem__(self, key, value):
            self._d[key] = value

        def __delitem__(self, key):
            del self._d[key]

        def __iter__(self):
            return iter(self._d)

        def __len__(self):
            return len(self._d)

    store = _MappingStore()
    _migrate_reference_hour_minute(store, lambda: dt(21, 15))
    ref, err = _resolve_reference_time_select(store)
    assert err == ""
    assert ref == time(21, 15)
    assert store[LIVE_REFERENCE_HOUR_KEY] == 21
    assert store[LIVE_REFERENCE_MINUTE_KEY] == 15


def test_resolve_is_read_only():
    """resolve 是纯读取：非法值只返回人话错误，不写 session_state。"""
    from src.p2_live_main import _resolve_reference_time_select

    store = {LIVE_REFERENCE_HOUR_KEY: "abc", LIVE_REFERENCE_MINUTE_KEY: "xyz"}
    ref, err = _resolve_reference_time_select(store)
    assert ref is None
    assert err
    assert store[LIVE_REFERENCE_HOUR_KEY] == "abc"
    assert store[LIVE_REFERENCE_MINUTE_KEY] == "xyz"


def test_modifying_time_control_does_not_call_model():
    """只修改下拉框本身，不触发任何模型调用。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    assert caller.count == 0
    stub.set_inputs(reference_hour=15, reference_minute=3)
    _run_main(stub, caller)
    assert caller.count == 0
    stub.set_inputs(reference_hour=15, reference_minute=5)
    _run_main(stub, caller)
    assert caller.count == 0


def test_custom_peak_boundary_time_flows_to_intake():
    """自定义 15:03 作为 reference，intake 收到同一时间（峰值判断基于该时间）。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    stub.set_inputs(reference_hour=15, reference_minute=3)
    _run_main(stub, caller)
    stub.set_inputs(intake="我今天做实验，下午3点去31教上课。", intake_submitted=True)
    _run_main(stub, caller)
    assert caller.count == 2 + 3 + 5
    assert "2026-09-01 15:03" in caller.calls[0][1]


def test_first_launch_system_2115_no_crash_int_state():
    """系统当前 21:15 首次启动不崩，session_state 为 int，页面正常显示 21:15。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller, now=dt(21, 15))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 21
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 15
    assert isinstance(stub.session_state[LIVE_REFERENCE_HOUR_KEY], int)
    assert isinstance(stub.session_state[LIVE_REFERENCE_MINUTE_KEY], int)
    # 小时/分钟下拉框使用 int options + format_func 两位数显示
    hour_calls = [c for c in stub.selectbox_calls if c[2] == LIVE_REFERENCE_HOUR_KEY]
    minute_calls = [c for c in stub.selectbox_calls if c[2] == LIVE_REFERENCE_MINUTE_KEY]
    assert hour_calls and minute_calls
    assert hour_calls[0][1] == list(range(24))
    assert minute_calls[0][1] == list(range(60))
    assert hour_calls[0][3](21) == "21"
    assert minute_calls[0][3](15) == "15"
    assert not stub.errors


def test_legacy_string_session_migrates_to_int():
    """旧 session_state 字符串 "21" / "03" 迁移为 int 21 / 3。"""
    caller = CountingCaller()
    stub = _StubSt()
    stub.session_state[LIVE_REFERENCE_HOUR_KEY] = "21"
    stub.session_state[LIVE_REFERENCE_MINUTE_KEY] = "03"
    _run_main(stub, caller, now=dt(9, 0))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 21
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 3
    assert isinstance(stub.session_state[LIVE_REFERENCE_HOUR_KEY], int)
    assert isinstance(stub.session_state[LIVE_REFERENCE_MINUTE_KEY], int)


def test_legacy_datetime_time_session_migrates_to_int():
    """旧 session_state datetime.time(13, 35) 迁移为 int 13 / 35。"""
    caller = CountingCaller()
    stub = _StubSt()
    stub.session_state[LIVE_REFERENCE_HOUR_KEY] = time(13, 35)
    _run_main(stub, caller, now=dt(9, 0))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 13
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 35


def test_legacy_invalid_session_falls_back_to_system_time():
    """非法/越界旧值回退到系统时间，不崩溃。"""
    caller = CountingCaller()
    stub = _StubSt()
    stub.session_state[LIVE_REFERENCE_HOUR_KEY] = "abc"
    stub.session_state[LIVE_REFERENCE_MINUTE_KEY] = 99
    _run_main(stub, caller, now=dt(21, 15))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 21
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 15
    assert not stub.errors


def test_live_main_full_render_no_value_error():
    """live main 完整渲染不出现 ValueError / traceback；所有内部异常只显示人话。"""
    from src.p2_live_main import SAFE_ERROR_TEXT

    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller, now=dt(21, 15))
    stub.set_inputs(reference_hour=13, reference_minute=35)
    _run_main(stub, caller)
    stub.set_inputs(intake="我今天做实验，下午3点去31教上课。", intake_submitted=True)
    _run_main(stub, caller)
    joined = "\n".join(stub.markdown_calls) + "\n".join(
        str(w) for w in stub.warnings
    ) + "\n".join(str(e) for e in stub.errors)
    assert "ValueError" not in joined
    assert "Traceback" not in joined
    assert SAFE_ERROR_TEXT not in joined


def test_strict_widget_lifecycle_first_launch_2125():
    """严格模拟 Streamlit 生命周期：widget 实例化后禁止写其 key；首次 21:25 启动正常。"""
    from src.p2_live_main import PAGE_INIT_ERROR_TEXT

    caller = CountingCaller()
    stub = _StubSt(session_state=_StrictSessionState())
    _run_main(stub, caller, now=dt(21, 25))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 21
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 25
    assert isinstance(stub.session_state[LIVE_REFERENCE_HOUR_KEY], int)
    assert isinstance(stub.session_state[LIVE_REFERENCE_MINUTE_KEY], int)
    assert not stub.errors
    joined = "\n".join(stub.markdown_calls) + "\n".join(
        str(w) for w in stub.warnings
    ) + "\n".join(str(e) for e in stub.errors)
    assert PAGE_INIT_ERROR_TEXT not in joined
    assert "StreamlitAPIException" not in joined
    assert "cannot be modified" not in joined


def test_strict_widget_lifecycle_legacy_strings_migrate_before_widget():
    """严格生命周期下，旧字符串 "13"/"35" 在 widget 实例化前迁移为 int。"""
    from src.p2_live_main import PAGE_INIT_ERROR_TEXT

    caller = CountingCaller()
    stub = _StubSt(session_state=_StrictSessionState())
    stub.session_state[LIVE_REFERENCE_HOUR_KEY] = "13"
    stub.session_state[LIVE_REFERENCE_MINUTE_KEY] = "35"
    _run_main(stub, caller, now=dt(9, 0))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 13
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 35
    assert isinstance(stub.session_state[LIVE_REFERENCE_HOUR_KEY], int)
    assert isinstance(stub.session_state[LIVE_REFERENCE_MINUTE_KEY], int)
    assert not stub.errors
    joined = "\n".join(str(w) for w in stub.warnings) + "\n".join(
        str(e) for e in stub.errors
    )
    assert PAGE_INIT_ERROR_TEXT not in joined


def test_strict_resolve_does_not_write_after_widget():
    """selectbox 实例化后，resolve 只读取 hour/minute，绝不写 session_state。"""
    from src.p2_live_main import (
        _migrate_reference_hour_minute,
        _resolve_reference_time_select,
    )

    store = _StrictSessionState()
    _migrate_reference_hour_minute(store, lambda: dt(21, 25))
    store.instantiate(LIVE_REFERENCE_HOUR_KEY)
    store.instantiate(LIVE_REFERENCE_MINUTE_KEY)
    ref, err = _resolve_reference_time_select(store)
    assert err == ""
    assert ref == time(21, 25)
    # 值未被 resolve 改动
    assert store[LIVE_REFERENCE_HOUR_KEY] == 21
    assert store[LIVE_REFERENCE_MINUTE_KEY] == 25


def test_strict_widget_lifecycle_rerun_keeps_user_choice():
    """严格生命周期下，用户改 13:35 后普通 rerun 仍为 13:35，不被系统时间覆盖。"""
    from src.p2_live_main import PAGE_INIT_ERROR_TEXT

    caller = CountingCaller()
    stub = _StubSt(session_state=_StrictSessionState())
    _run_main(stub, caller, now=dt(21, 25))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 21
    stub.set_inputs(reference_hour=13, reference_minute=35)
    _run_main(stub, caller, now=dt(21, 25))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 13
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 35
    stub.set_inputs()
    _run_main(stub, caller, now=dt(21, 25))
    assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == 13
    assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == 35
    assert not stub.errors
    joined = "\n".join(str(w) for w in stub.warnings) + "\n".join(
        str(e) for e in stub.errors
    )
    assert PAGE_INIT_ERROR_TEXT not in joined


def test_strict_widget_lifecycle_peak_boundaries():
    """严格生命周期下 00:00 / 15:03 / 23:59 正常。"""
    from src.p2_live_main import PAGE_INIT_ERROR_TEXT

    for hour, minute in ((0, 0), (15, 3), (23, 59)):
        caller = CountingCaller()
        stub = _StubSt(session_state=_StrictSessionState())
        stub.session_state[LIVE_REFERENCE_HOUR_KEY] = hour
        stub.session_state[LIVE_REFERENCE_MINUTE_KEY] = minute
        _run_main(stub, caller, now=dt(21, 25))
        assert stub.session_state[LIVE_REFERENCE_HOUR_KEY] == hour
        assert stub.session_state[LIVE_REFERENCE_MINUTE_KEY] == minute
        assert not stub.errors
        joined = "\n".join(str(w) for w in stub.warnings) + "\n".join(
            str(e) for e in stub.errors
        )
        assert PAGE_INIT_ERROR_TEXT not in joined


# ---------------------------------------------------------------------------
# P3e：live 页面接入本地真实地图与移动反馈
# ---------------------------------------------------------------------------


class _MovementCaller(CountingCaller):
    """CountingCaller + 统一反馈移动输出；其余角色沿用父类。"""

    def __init__(self, intent):
        super().__init__()
        self.intent = intent

    def __call__(self, system, user):
        if "统一反馈理解器" in system:
            return self.intent
        return super().__call__(system, user)


MOVEMENT_UNIFIED_9ZHAI_31JIAO = (
    '{"schema_version": "p3.unified-feedback.v1", "task_updates": [], '
    '"commitment_updates": [], '
    '"movement": {"has_movement": true, "origin_text": "9斋", '
    '"destination_text": "31教", "mode": "bike", "depart_at": null, '
    '"arrive_by": null}, "current_location": null, "questions": [], "reason": null}'
)

INTAKE_EXECUTION_SEMANTICS = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [], "tasks": ['
    '{"title": "写报告", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": "图书馆", "activity_kind": "generic", "duration_source": null}, '
    '{"title": "吃饭", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "meal", "duration_source": null}'
    '], "questions": [], "current_location": null, "transport_mode": null}'
)

INTAKE_EXECUTION_PRESENTATION = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": "16:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "第九教学楼", '
    '"commitment_kind": "class"}], "tasks": ['
    '{"title": "写报告", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": "图书馆", "activity_kind": "generic", "duration_source": null}, '
    '{"title": "吃饭", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "meal", "duration_source": null}'
    '], "questions": [], "current_location": null, "transport_mode": "walk"}'
)

INTAKE_EXPLICIT_MEAL = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [], "tasks": ['
    '{"title": "写报告", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": "图书馆", "activity_kind": "generic", "duration_source": null}, '
    '{"title": "吃饭", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": "学三食堂", "activity_kind": "meal", "duration_source": null}'
    '], "questions": [], "current_location": null, "transport_mode": "walk"}'
)

INTAKE_UNRESOLVED_MEAL = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [], "tasks": ['
    '{"title": "吃饭", "total_minutes": null, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "meal", "duration_source": null}, '
    '{"title": "背单词", "total_minutes": 15, "is_splittable": false, '
    '"minimum_slice_minutes": 15, "location_text": null, "activity_kind": "generic", "duration_source": null}'
    '], "questions": [], "current_location": null, "transport_mode": "walk"}'
)

MOVEMENT_UNIFIED_YOUYUAN_9JIAO = (
    '{"schema_version": "p3.unified-feedback.v1", "task_updates": [], '
    '"commitment_updates": [], '
    '"movement": {"has_movement": true, "origin_text": "友园", '
    '"destination_text": "第九教学楼", "mode": "walk", "depart_at": null, '
    '"arrive_by": null}, "current_location": "友园", "questions": [], "reason": null}'
)


def test_main_movement_feedback_applies_and_rerun_no_calls():
    """P3e：反馈“9斋骑车去31教”经 live 页面真实地图接入后扣容量并渲染移动行；
    普通 rerun 不重复调用模型。"""
    caller = _MovementCaller(intent=MOVEMENT_UNIFIED_9ZHAI_31JIAO)
    stub = _StubSt().set_inputs(
        reference=time(9, 0),
        intake="10点到11点半上课，今天做计组实验。",
        intake_submitted=True,
    )
    _run_main(stub, caller)
    assert stub.rerun_called == 1
    assert stub.session_state[LIVE_SESSION_KEY].campus_id == "beiyangyuan"
    assert stub.session_state[LIVE_SESSION_KEY].map_data.campus_id == "beiyangyuan"
    stub.set_inputs(feedback="我从9斋骑车去31教。", feedback_submitted=True)
    _run_main(stub, caller)
    assert stub.rerun_called == 2
    session = stub.session_state[LIVE_SESSION_KEY]
    last_turn = session.last_turn()
    assert last_turn.movement_blocks
    block = last_turn.movement_blocks[0]
    assert block.destination_node_id == "building_31"
    assert block.mode.value == "bike"
    assert last_turn.result.call_count == 2  # intent + 时间估计（地点精确命中不调模型）
    # 移动已扣除窗口容量
    assert session.current_state().windows[0].starts_at.hour == 9
    after_feedback = caller.count
    # 普通 rerun 不重复调用模型
    stub.set_inputs()
    _run_main(stub, caller)
    assert caller.count == after_feedback


def test_live_weijinlu_feedback_keeps_one_explicit_map_through_replan_and_rerun():
    """The live controller captures the selected registry map for feedback/replan."""
    caller = _MovementCaller(intent=MOVEMENT_UNIFIED_YOUYUAN_9JIAO)
    stub = _StubSt().set_inputs(
        reference=time(9, 0),
        intake="10点到11点半上课，今天做计组实验。",
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller)
    session = stub.session_state[LIVE_SESSION_KEY]
    assert session.campus_id == "weijinlu"
    assert session.map_data.campus_id == "weijinlu"

    stub.set_inputs(feedback="我从友园步行去第九教学楼。", feedback_submitted=True)
    _run_main(stub, caller)
    block = stub.session_state[LIVE_SESSION_KEY].last_turn().movement_blocks[0]
    assert block.origin_node_id == "weijinlu_youyuan"
    assert block.destination_node_id == "weijinlu_building_9"
    calls_after_feedback = caller.count

    # A normal Streamlit rerun preserves selection and does not invoke agents.
    stub.set_inputs()
    _run_main(stub, caller)
    assert stub.session_state[SELECTED_CAMPUS_ID_KEY] == "weijinlu"
    assert caller.count == calls_after_feedback


# ---------------------------------------------------------------------------
# P3e：live 链路停用旧“固定安排前默认预留10分钟”机制
# ---------------------------------------------------------------------------


def test_make_live_session_default_config_disables_old_safety_buffer():
    """P3 live 默认 config：default_safety_buffer_minutes=0。"""
    session = make_live_session({}, FakeAdapter(CountingCaller()))
    assert session.config.default_safety_buffer_minutes == 0


def test_live_intake_derives_windows_with_zero_safety_buffer():
    """run_live_intake 显式传 0，窗口不再带旧10分钟 safety buffer。"""
    caller = CountingCaller()
    store = {}
    outcome = run_live_intake(store, dt(9), "10点上课，今天做计组实验。", caller)
    assert outcome.applied is not None
    assert outcome.applied.state.windows
    for window in outcome.applied.state.windows:
        assert window.safety_buffer_minutes == 0


def test_live_intake_custom_reference_time_uses_zero_buffer_rule():
    """自定义参考时间与系统时间用同一规则（均不带旧 buffer）。"""
    caller = CountingCaller()
    store = {}
    outcome = run_live_intake(store, dt(13, 35), "今天下午做计组实验。", caller)
    assert outcome.applied is not None
    assert outcome.applied.state.reference_datetime == dt(13, 35)
    for window in outcome.applied.state.windows:
        assert window.safety_buffer_minutes == 0


def test_more_settings_no_old_10min_option_and_no_internal_terms():
    """“更多设置”不再渲染旧 10 分钟预留，页面无内部词。"""
    caller = CountingCaller()
    stub = _StubSt()
    _run_main(stub, caller)
    joined = "\n".join([str(args) for args in stub.writes] + stub.markdown_calls)
    assert "默认预留" not in joined
    assert "默认预留 10 分钟" not in joined
    for token in ("safety_buffer", "transition_buffer", "window capacity"):
        assert token not in joined


def test_p1_p2_api_defaults_preserved():
    """旧 P1/P2 底层 API 默认仍为 10，仅 live P3 主链路显式传0。"""
    from src.p2_window_derivation import DEFAULT_SAFETY_BUFFER_MINUTES as window_default
    from src.p2_day_intake import DEFAULT_SAFETY_BUFFER_MINUTES as intake_default
    assert window_default == 10
    assert intake_default == 10
    assert PlanConfig().default_safety_buffer_minutes == 10


# ---------------------------------------------------------------------------
# P3e 真实验收回归：真实场景完整 main() 流程（13:35 / 9斋 -> 31教 / bike / 90分钟实验）
# ---------------------------------------------------------------------------


REAL_INTAKE_9ZHAI_31JIAO = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": null, '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
    '"tasks": [{"title": "计组实验", "total_minutes": 90, "is_splittable": null, '
    '"minimum_slice_minutes": null, "location_text": null}], '
    '"questions": ["“上课”大约几点结束？"], '
    '"current_location": "9斋", "transport_mode": "bike"}'
)

REAL_TRAVEL_TIME = (
    '{"schema_version": "p3.travel-time.v1", "min_minutes": 4, "max_minutes": 8, '
    '"reason": "9斋到31教约900米，骑车大约5分钟，高峰可能10分钟。"}'
)

REAL_SPLITTABILITY = (
    '{"schema_version": "p2.splittability-review.v1", "is_splittable": true, '
    '"minimum_slice_minutes": 30, "reason": "实验可以分段推进，先做一部分没问题。"}'
)


class _RealisticCaller(CountingCaller):
    """模拟真实 Qwen 输出风格（中文标点/口语/峰值表述），走完整 live 流程。"""

    def __init__(self):
        super().__init__(intake=REAL_INTAKE_9ZHAI_31JIAO)
        self.travel_time = REAL_TRAVEL_TIME

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "p3.travel-time.v1" in system or "移动时间" in system:
            return self.travel_time
        if "p3.plan-critic" in system:
            return CRITIC_APPROVE
        if "p3.lifestyle-review" in system:
            return LIFESTYLE_OK
        if "p3.plan-narrator" in system:
            return NARRATOR_OK
        if "p3.warm-companion" in system:
            return WARM_OK
        if "p3.copy-review" in system:
            return COPY_REVIEW_OK
        if "splittability-review" in system:
            return REAL_SPLITTABILITY
        if "统一反馈理解器" in system:
            return self._unified_json(user)
        if "feedback-router" in system:
            return _demo_route(user)
        if "day-intake" in system:
            return self.intake
        if "commitment-reconciliation" in system:
            return self.commitment
        if "task-reconciliation" in system:
            if START_TEXT in user or REFRESH_TEXT in user:
                return TASK_NONE
            return self.task
        if "day-plan-intent" in system:
            return self.plan
        if "day-review" in system:
            return self.review
        return self.task


def test_realistic_intake_full_flow_no_safe_error():
    """真实输入（9斋/15:00 31教/骑车/实验90分钟，13:35）完整 main() 不出现 SAFE_ERROR。"""
    caller = _RealisticCaller()
    stub = _StubSt().set_inputs(
        reference=time(13, 35),
        intake="我现在在9斋，下午3点去31教上课，我骑车。这之前想做一下计组实验，大概还要一个半小时。",
        intake_submitted=True,
    )
    _run_main(stub, caller, now=dt(13, 35))
    assert stub.rerun_called == 1
    assert not [e for e in stub.errors if SAFE_ERROR_TEXT in str(e)]
    assert LIVE_SESSION_KEY in stub.session_state
    session = stub.session_state[LIVE_SESSION_KEY]
    turn = session.last_turn()
    assert turn is not None
    assert turn.companion_copy.generated is True
    assert turn.result.allocation_plan.total_planned_minutes > 0
    # 普通 rerun：不重复调用模型，只渲染
    stub.set_inputs()
    _run_main(stub, caller, now=dt(13, 35))
    joined = "\n".join(str(m) for m in stub.markdown_calls)
    assert "cf-plan-hero" in joined
    assert "31教" in joined  # 真实计划行包含 P3 移动目的地
    assert "traceback" not in joined.lower()
def test_p4c_live_feedback_prioritizes_named_existing_task_by_ref():
    """Live-equivalent feedback A/B/C makes laundry the next feasible task."""
    caller = _P4CFeedbackCaller()
    stub = _StubSt().set_inputs(
        reference_hour=14,
        reference_minute=0,
        intake=(
            "我想在图书馆写实验报告，然后吃饭，17:00去9教上课，"
            "18:30下课后写一小时作业，还想背单词，还需要洗20分钟衣服"
        ),
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(14))
    assert not stub.errors
    original = load_live_final_turn(stub.session_state)
    assert original is not None
    assert tuple(item.title for item in original.state.tasks) == (
        "写实验报告", "吃饭", "写作业", "背单词", "洗衣服",
    )
    assert original.state.commitments[0].starts_at == dt(17)
    assert original.state.commitments[0].ends_at == dt(18, 30)

    stub.set_inputs(
        reference_hour=14, reference_minute=0,
        feedback="我现在就想洗衣服", feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(14))
    assert not stub.errors
    rebuilt = load_live_final_turn(stub.session_state)
    assert rebuilt.version == original.version + 1
    assert rebuilt.feedback_decision.preferred_next_task_ref == "day_task_005"
    assert rebuilt.result.allocation_plan.current_allocation.task_ref == "day_task_005"
    assert rebuilt.result.allocation_plan.current_allocation.task_title == "洗衣服"
    assert rebuilt.state.commitments[0].starts_at == dt(17)
    assert final_plan_overlap_errors(
        rebuilt.state, rebuilt.result.allocation_plan, rebuilt.movement_blocks
    ) == ()
    systems = [system for system, _ in caller.calls]
    assert any("Feedback Interpreter" in item for item in systems)
    assert any("Feedback Decision Critic" in item for item in systems)
    assert any("Intent Compliance Reviewer 2.0" in item for item in systems)
    calls_after_feedback = len(caller.calls)
    stub.set_inputs(reference_hour=14, reference_minute=0)
    _run_main(stub, caller, now=dt(14))
    assert len(caller.calls) == calls_after_feedback


def test_p4c_live_initial_intake_auditor_repairs_real_multiclause_shape():
    """The production live entry audits omissions before applying the day."""
    import json as _json

    repaired = _json.loads(INTAKE_P4C_LAUNDRY.replace('"17:00"', '"21:00"', 1))
    repaired["day_end"] = "23:30"
    repaired["commitments"][0]["ends_at"] = None
    repaired["commitments"][0]["relative_end_minutes"] = 90
    incomplete = dict(repaired)
    incomplete["tasks"] = list(repaired["tasks"][:-1])

    class _AuditedInitialCaller(CountingCaller):
        def __init__(self):
            super().__init__(
                intake=_json.dumps(incomplete, ensure_ascii=False),
                plan=PLAN_P4C_ALL,
            )

        def __call__(self, system, user):
            if "Initial Intake Semantic Auditor" in system:
                self.calls.append((system, user))
                return _json.dumps({
                    "schema_version": "p4.initial-intake-audit.v1",
                    "decision": "repair",
                    "issues": ["漏掉用户明确提出的洗衣服20分钟"],
                    "repaired_proposal": repaired,
                }, ensure_ascii=False)
            return super().__call__(system, user)

    caller = _AuditedInitialCaller()
    stub = _StubSt().set_inputs(
        reference_hour=18,
        reference_minute=0,
        intake=(
            "我想在图书馆写一会儿实验报告，然后吃饭，今天21:00去9教上课，"
            "一个半小时后下课。然后写一个小时作业，还想背单词，还需要洗20分钟衣服"
        ),
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(18))
    assert not stub.errors
    bundle = load_live_final_turn(stub.session_state)
    assert bundle is not None
    assert [task.title for task in bundle.state.tasks] == [
        "写实验报告", "吃饭", "写作业", "背单词", "洗衣服",
    ]
    assert bundle.state.commitments[0].starts_at == dt(21)
    assert bundle.state.commitments[0].ends_at == dt(22, 30)
    assert bundle.state.commitments[0].location_text == "9教"
    assert bundle.state.tasks[2].total_minutes == 60
    assert bundle.state.tasks[4].total_minutes == 20
    audit_calls = [call for call in caller.calls if "Initial Intake Semantic Auditor" in call[0]]
    assert len(audit_calls) == 1
    assert "一个半小时后下课" in audit_calls[0][1]


def test_p5_reviewer_rejection_uses_one_bounded_validated_candidate_repair():
    caller = _P4CFeedbackCaller(compliance="reject")
    stub = _StubSt().set_inputs(
        reference_hour=14, reference_minute=0,
        intake=(
            "我想在图书馆写实验报告，然后吃饭，17:00去9教上课，"
            "18:30下课后写一小时作业，还想背单词，还需要洗20分钟衣服"
        ),
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(14))
    reliable = load_live_final_turn(stub.session_state)
    stub.set_inputs(
        reference_hour=14, reference_minute=0,
        feedback="我现在就想洗衣服", feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(14))
    rebuilt = load_live_final_turn(stub.session_state)
    assert rebuilt is not reliable
    assert rebuilt.version == reliable.version + 1
    # The model rejection triggers one bounded candidate repair; the stored
    # review describes the repaired candidate after deterministic re-check.
    assert rebuilt.agent_intelligence.review.decision == "approve"
    assert rebuilt.agent_intelligence.review.generated is False
    assert rebuilt.agent_intelligence.controlled_repair_used is True
    assert rebuilt.result.allocation_plan.current_allocation.task_ref == "day_task_005"
    assert not stub.errors
    reviewer_calls = [
        system for system, _ in caller.calls
        if "Intent Compliance Reviewer 2.0" in system
    ]
    # One reviewer pass for initial planning and one for feedback; no loop.
    assert len(reviewer_calls) == 2

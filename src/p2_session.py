"""P2d 全天规划会话状态层（Python 3.8 兼容）。

轻量 controller：保存当前 DayPlanningState、最新 P2AgenticDayResult、
交互问题/提示与缓存 key。普通 Streamlit rerun 不重新调用 Agent；
只有“开始全天计划 / 应用反馈并重新规划 / 刷新方案”三个动作触发新规划。
本模块不依赖 Streamlit，store 为可注入的 dict-like（测试用普通 dict）。

反馈入口（P3e）：
- 注入 unified_handler 时，一条 feedback 由统一理解 Agent 一次解析
  task / commitment / movement，程序分别执行后统一重规划；
- 未注入时保留旧路径（movement_handler -> Router -> 相关 reconciler）。
"""

import hashlib
import logging
import traceback
import re
from dataclasses import dataclass, field, replace
from typing import Mapping, MutableMapping, Optional, Tuple

from src.p2_agentic_models import (
    DAY_PLAN_INTENT_SCHEMA_VERSION,
    DayPlanIntent,
    P2AgenticDayResult,
)
from src.p2_agentic_parser import AgenticParseError
from src.p2_agentic_pipeline import run_p2_agentic_day_planning
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import summarize_day_plan
from src.p2_agentic_prompt_builder import build_repair_prompt
from src.p2_commitment_reconciler import (
    COMMITMENT_SCHEMA_VERSION,
    apply_commitment_reconciliation,
    build_commitment_reconciliation_prompt,
    parse_commitment_reconciliation,
)
from src.p2_feedback_router import (
    ROUTE_BOTH,
    ROUTE_COMMITMENT,
    ROUTE_TASK,
    ROUTE_UNKNOWN,
    route_feedback,
)
from src.p1_models import SourceKind
from src.p2_models import DayPlanningState, append_history
from src.p2_state_reconciler import append_structured_task, confirm_unknown_task_duration
from src.p2_window_derivation import derive_day_state
from src.p2_companion_copy import (
    CONTEXT_FEEDBACK,
    CONTEXT_INITIAL,
    CONTEXT_REFRESH,
    CompanionCopy,
    companion_fallbacks,
    extract_change_facts,
)
from src.p2_expression_agents import generate_expression_copy
from src.p3_route_planner import final_plan_overlap_errors, reapply_movement_blocks
from src.p4_execution_context import (
    EXECUTION_PLAN_CONTEXT_KEY,
    CurrentLocationSource,
    ExecutionPlanContext,
    ExecutionLocation,
    ExecutionLocationSource,
    ExecutableTaskBinding,
    TaskExecutionProfile,
    load_execution_context,
    save_execution_context,
)
from src.task_estimation import (
    TASK_ESTIMATE_ADDED_DRAFTS_KEY,
    ConfirmedEstimatedTask,
)
from src.p4_execution_enrichment import (
    earliest_start_overrides,
    latest_end_overrides,
    preferred_start_overrides,
    effective_duration_overrides,
    preferred_chunk_overrides,
    protected_meal_duration_overrides,
    reconcile_execution_context,
)
from src.p4_execution_movement import (
    apply_execution_sequence_movements,
    assume_current_location_for_timeline,
    execution_timeline,
    replace_assumed_current_location,
)
from src.p4_concurrency import (
    ConcurrentAllocation,
    concurrent_minutes_by_task,
    concurrency_validation_errors,
    plan_explicit_concurrency,
)
from src.p3_location_resolver import resolve_location
from src.p4_feedback_decision import (
    P4_FEEDBACK_DECISION_KEY,
    FeedbackDecision,
    ConcurrencyDecision,
    TaskOrderingConstraint,
    apply_feedback_decision_to_state,
    apply_feedback_task_order,
    candidate_complies_with_decision,
    candidate_plan_summary,
    decide_feedback,
    decision_clarification_questions,
    hard_facts_summary,
    load_feedback_decision,
    merge_feedback_decisions,
    review_intent_compliance,
)
from src.p5_concurrency_semantics import apply_concurrency_feedback
from src.p5_agent_context import build_agent_decision_context
from src.p5_agent_pipeline import (
    AGENT_CALL_TRACE_KEY,
    AGENT_INTELLIGENCE_KEY,
    LATEST_FEEDBACK_TEXT_KEY,
    LATEST_USER_TEXT_KEY,
    AgentIntelligenceResult,
    run_agent_intelligence,
    save_agent_intelligence,
)
from src.p5_agent_runtime import AgentCallTrace
from src.p5_candidate import summarize_candidate
from src.p5_copy_guard import GroundedNarrative, observable_plan_note
from src.p5_day_preferences import (
    DAY_PREFERENCE_KEY,
    extract_day_preferences,
    load_day_preferences,
    save_day_preferences,
)
from src.p5_what_if import (
    WHAT_IF_PREVIEW_KEY,
    WhatIfIntent,
    WhatIfApplyError,
    WhatIfInfeasibleError,
    apply_what_if_preview,
    build_what_if_preview,
    clear_what_if_preview,
    fingerprint_context,
    interpret_what_if,
    load_what_if_preview,
    replace_hypothetical_preferences,
    save_what_if_preview,
)
from src.personal_settings import (
    PERSONAL_OTHER_CAMPUS_COURSES_KEY,
    PERSONAL_SETTINGS_KEY,
    PERSONAL_TRANSPORT_EXPLICIT_KEY,
    apply_personal_defaults_to_context,
    capture_occurrence_overrides,
    inject_timetable,
    load_personal_settings,
    personal_location_alias,
)

logger = logging.getLogger("campusflow.session")

STATE_KEY = "p2_state"
RESULT_KEY = "p2_result"
LAST_TURN_KEY = "p2_last_turn"
LAST_CACHE_KEY = "p2_last_cache_key"
MOVEMENT_DATA_KEY = "p2_movement_data"
LIVE_FINAL_TURN_KEY = "p4_live_final_turn"
LIVE_FINAL_TURN_SEQUENCE_KEY = "p4_live_final_turn_sequence"

START_TEXT = "开始全天计划"
REFRESH_TEXT = "刷新方案"


def _without_unroutable_default_meals(plan, context):
    """Fail safely when a meal's required route reservation could not exist.

    A 20-minute floor applies only after real route/class reservations have
    been accepted.  If the execution sequence cannot be built at all, keeping
    a bare allocator meal would falsely claim the user can eat in place.
    """
    meal_refs = {
        item.task_ref for item in context.bindings
        if item.activity_kind == "meal" and item.duration_source == "meal_default"
    }
    if not meal_refs:
        return plan
    allocations = tuple(item for item in plan.allocations if item.task_ref not in meal_refs)
    removed = len(allocations) != len(plan.allocations)
    if not removed:
        return plan
    unallocated = tuple(dict.fromkeys(tuple(plan.unallocated_task_refs) + tuple(sorted(meal_refs))))
    current = next((item for item in allocations if item.window_ref == plan.current_window_ref
                    and item.occupies_attention), None)
    later = tuple(item for item in allocations if item.window_ref != plan.current_window_ref)
    return replace(
        plan,
        allocations=allocations,
        unallocated_task_refs=unallocated,
        current_allocation_ref=current.allocation_ref if current else None,
        later_allocations=later,
        total_planned_minutes=sum(item.planned_minutes for item in allocations),
    )


@dataclass(frozen=True)
class PlanConfig:
    """规划参数：重新派生窗口时保持与初始规划一致的 buffer / travel 输入。"""

    default_safety_buffer_minutes: int = 10
    travel_minutes_by_commitment: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class P2SessionTurn:
    """一轮交互的只读快照；questions/warnings 为 commitment 侧 + 任务侧合并视图。"""

    user_text: str
    result: P2AgenticDayResult
    commitment_questions: Tuple[str, ...]
    commitment_warnings: Tuple[str, ...]
    cache_key: str
    router_questions: Tuple[str, ...] = ()
    router_warnings: Tuple[str, ...] = ()
    movement_plan: Optional[object] = None
    movement_blocks: Tuple[object, ...] = ()
    movement_questions: Tuple[str, ...] = ()
    movement_warnings: Tuple[str, ...] = ()
    companion_copy: Optional[object] = None
    # A read-only snapshot for presentation.  The execution context remains
    # owned by session persistence; this merely prevents the UI from guessing
    # location provenance from task titles or rendered strings.
    execution_context: Optional[object] = None
    # Explicit user-authorized work inside fixed commitments.  It is kept
    # outside DayAllocationPlan because it deliberately does not consume an
    # ordinary free window.
    concurrent_allocations: Tuple[ConcurrentAllocation, ...] = ()
    # Complete P5 read-only intelligence result for this exact final turn.
    # The renderer must never combine it with another turn's plan snapshots.
    agent_intelligence: Optional[AgentIntelligenceResult] = None

    @property
    def all_questions(self) -> Tuple[str, ...]:
        return (
            self.commitment_questions
            + self.result.questions
            + self.router_questions
            + self.movement_questions
        )

    @property
    def all_warnings(self) -> Tuple[str, ...]:
        return (
            self.commitment_warnings
            + self.result.warnings
            + self.router_warnings
            + self.movement_warnings
        )


@dataclass(frozen=True)
class LiveFinalTurn:
    """One validated, renderable P4 turn committed as a single session value.

    The renderer may read this object, but must not reconstruct a P4 page from
    ``p2_result`` / ``p2_last_turn`` / movement or execution-context keys.
    Those keys remain compatibility state for the older P2/P3 controller.
    """

    version: int
    turn: P2SessionTurn
    state: DayPlanningState
    result: P2AgenticDayResult
    execution_context: ExecutionPlanContext
    movement_blocks: Tuple[object, ...]
    extra_questions: Tuple[str, ...] = ()
    feedback_decision: Optional[FeedbackDecision] = None
    concurrent_allocations: Tuple[ConcurrentAllocation, ...] = ()
    agent_intelligence: Optional[AgentIntelligenceResult] = None

    def __post_init__(self):
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("live final turn version must be a positive integer")
        if self.turn.result is not self.result:
            raise ValueError("live final turn result snapshot mismatch")
        if self.result.updated_state is not self.state:
            raise ValueError("live final turn state snapshot mismatch")
        if self.turn.execution_context != self.execution_context:
            raise ValueError("live final turn execution-context snapshot mismatch")
        if tuple(self.turn.movement_blocks) != self.movement_blocks:
            raise ValueError("live final turn movement snapshot mismatch")
        if self.feedback_decision is not None and not isinstance(
            self.feedback_decision, FeedbackDecision
        ):
            raise ValueError("live final turn feedback-decision snapshot mismatch")
        if tuple(self.turn.concurrent_allocations) != self.concurrent_allocations:
            raise ValueError("live final turn concurrent-allocation snapshot mismatch")
        if self.turn.agent_intelligence is not self.agent_intelligence:
            raise ValueError("live final turn agent-intelligence snapshot mismatch")


@dataclass(frozen=True)
class EstimatedTaskAddOutcome:
    """Atomic result of adding one confirmed estimate to today's plan."""

    bundle: LiveFinalTurn
    task_ref: str
    duplicate: bool = False


def make_cache_key(user_text: str, state: DayPlanningState) -> str:
    """确定性缓存 key：相同输入（反馈文本 + 当前 state 快照）得到相同 key。"""
    if not isinstance(user_text, str):
        raise TypeError("user_text must be a string")
    _require_state(state)
    payload = user_text + "|" + _state_fingerprint(state)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_live_final_turn(
    turn, map_data, version=1, extra_questions=(), feedback_decision=None
):
    """Validate and freeze every fact consumed by the P4 live renderer."""
    if not isinstance(turn, P2SessionTurn):
        raise TypeError("turn must be a P2SessionTurn")
    context = turn.execution_context
    if not isinstance(context, ExecutionPlanContext):
        raise ValueError("P4 live final turn requires an ExecutionPlanContext snapshot")
    state = turn.result.updated_state
    plan = turn.result.allocation_plan
    p4_execution = any(
        binding.activity_kind in ("generic", "meal")
        or getattr(binding, "before_commitment_ref", None) is not None
        for binding in context.bindings
    ) or bool(context.concurrency_authorizations)
    from src.task_dependencies import dependency_errors
    from src.p4_execution_enrichment import protected_meal_duration_overrides
    errors = list(dependency_errors(state, plan, effective_duration_overrides(context, state),
                                   protected_meal_duration_overrides(context, state)))
    from src.task_dependencies import departure_dependency_errors
    errors.extend(departure_dependency_errors(state, plan, turn.movement_blocks))
    if any(task.attention_mode == 'background' for task in state.tasks):
        from src.task_attention import attention_validation_errors
        errors.extend(attention_validation_errors(state, plan, context))
    if p4_execution:
        errors.extend(final_plan_overlap_errors(state, plan, turn.movement_blocks))
        errors.extend(
            _live_turn_execution_errors(
                state, plan, turn.movement_blocks, context, map_data,
                turn.concurrent_allocations,
            )
        )
    if errors:
        raise ValueError("invalid live final turn: {}".format("; ".join(errors)))
    intelligence = turn.agent_intelligence
    questions = list(extra_questions or ()) + list(turn.result.questions)
    clarification = getattr(intelligence, "clarification", None)
    if clarification is not None and clarification.should_ask:
        questions.append(clarification.question)
    return LiveFinalTurn(
        version=version,
        turn=turn,
        state=state,
        result=turn.result,
        execution_context=context,
        movement_blocks=tuple(turn.movement_blocks),
        extra_questions=tuple(dict.fromkeys(questions)),
        feedback_decision=feedback_decision,
        concurrent_allocations=tuple(turn.concurrent_allocations),
        agent_intelligence=intelligence,
    )


def commit_live_final_turn(store, turn, map_data, extra_questions=()):
    """Publish a validated P4 turn with one final assignment."""
    previous = store.get(LIVE_FINAL_TURN_KEY)
    previous_version = previous.version if isinstance(previous, LiveFinalTurn) else 0
    version = max(previous_version, int(store.get(LIVE_FINAL_TURN_SEQUENCE_KEY, 0) or 0)) + 1
    bundle = build_live_final_turn(
        turn, map_data, version, extra_questions,
        feedback_decision=load_feedback_decision(store),
    )
    store[LIVE_FINAL_TURN_SEQUENCE_KEY] = version
    store[LIVE_FINAL_TURN_KEY] = bundle
    return bundle


def load_live_final_turn(store):
    value = store.get(LIVE_FINAL_TURN_KEY)
    return value if isinstance(value, LiveFinalTurn) else None


def _live_turn_execution_errors(state, plan, blocks, context, map_data, concurrent_allocations=()):
    """P4 invariants not covered by the generic interval overlap audit."""
    errors = []
    campus_id = getattr(map_data, "campus_id", None)
    if not campus_id:
        errors.append("selected campus map is missing")
    locations = [item.execution_location for item in context.bindings if item.execution_location]
    if context.current_location.location is not None:
        locations.append(context.current_location.location)
    if campus_id and any(item.campus_id != campus_id for item in locations):
        errors.append("execution location does not belong to selected campus")
    if campus_id and not errors:
        from src.p4_execution_movement import execution_route_coverage_errors
        errors.extend(execution_route_coverage_errors(state, plan, blocks, context, map_data))

    state_task_refs = {task.task_ref for task in state.tasks}
    state_commitment_refs = {item.commitment_ref for item in state.commitments}
    known_activity_refs = state_task_refs | state_commitment_refs
    for block in blocks:
        for ref in (
            getattr(block, "origin_activity_ref", None),
            getattr(block, "destination_activity_ref", None),
        ):
            if ref is not None and ref not in known_activity_refs:
                errors.append("movement activity ref is stale: {}".format(ref))

    planned = getattr(plan, "planned_minutes_by_task", {})
    for binding in context.bindings:
        if binding.activity_kind != "meal" or binding.duration_source != "meal_default":
            continue
        if binding.effective_duration_minutes != 40:
            errors.append("meal_default duration must be 40 minutes: {}".format(binding.task_ref))
        planned_minutes = planned.get(binding.task_ref, 0)
        meal_chunks = _allocation_intervals(state, plan).get(binding.task_ref, ())
        if planned_minutes == 0:
            continue
        if not (20 <= planned_minutes <= 40 and len(meal_chunks) == 1):
            errors.append("meal_default allocation must be one 20--40 minute block or unplanned: {}".format(binding.task_ref))

    task_intervals = _allocation_intervals(state, plan)
    earliest = earliest_start_overrides(context, state)
    latest = latest_end_overrides(context, state)
    for ref, intervals in task_intervals.items():
        if ref in earliest and any(start < earliest[ref] for start, _ in intervals):
            errors.append("task starts before explicit boundary: {}".format(ref))
        if ref in latest and any(end > latest[ref] for _, end in intervals):
            errors.append("task ends after explicit boundary: {}".format(ref))
    for block in blocks:
        origin_ref = getattr(block, "origin_activity_ref", None)
        binding = context.binding_for(origin_ref) if origin_ref else None
        if binding is None or binding.execution_location is None:
            continue
        if getattr(block, "origin_node_id", None) != binding.execution_location.node_id:
            continue
        if getattr(block, "destination_node_id", None) == binding.execution_location.node_id:
            continue
        departure = getattr(block, "transition_start", None) or block.window_start
        if any(
            end > departure and not any(
                back.destination_node_id == binding.execution_location.node_id
                and departure < back.end_time <= start
                for back in blocks
            )
            for start, end in task_intervals.get(origin_ref, ())
        ):
            errors.append("location-bound task continues after departure: {}".format(origin_ref))
    errors.extend(
        concurrency_validation_errors(
            state,
            context,
            concurrent_allocations,
            map_data=map_data,
            ordinary_plan=plan,
        )
    )
    return tuple(dict.fromkeys(errors))


def _allocation_intervals(state, plan):
    from src.task_attention import allocation_spans
    result = {}
    for allocation, start, end in allocation_spans(state, plan):
        result.setdefault(allocation.task_ref, []).append((start, end))
    return {key: tuple(value) for key, value in result.items()}


class P2SessionController:
    """会话控制器：Agent 调用只发生在三个明确动作中，其余 rerun 全部命中缓存。"""

    def __init__(
        self,
        store: MutableMapping,
        caller,
        commitment_caller=None,
        repair_caller=None,
        config=None,
        plan_caller=None,
        review_caller=None,
        revision_caller=None,
        reconciliation_caller=None,
        movement_handler=None,
        unified_handler=None,
        companion_caller=None,
        companion_enabled=False,
        agent_intelligence_enabled=False,
        map_data=None,
        campus_id=None,
    ):
        if not callable(caller):
            raise TypeError("caller must be callable")
        if movement_handler is not None and not callable(movement_handler):
            raise TypeError("movement_handler must be callable or None")
        if unified_handler is not None and not callable(unified_handler):
            raise TypeError("unified_handler must be callable or None")
        self.store = store
        self.caller = caller
        self.commitment_caller = commitment_caller if commitment_caller is not None else caller
        self.repair_caller = repair_caller if repair_caller is not None else caller
        self.config = config if config is not None else PlanConfig()
        self.movement_handler = movement_handler
        self.unified_handler = unified_handler
        self.companion_enabled = bool(companion_enabled)
        self.agent_intelligence_enabled = bool(agent_intelligence_enabled)
        if campus_id is not None:
            from src.p3_campus_scope import require_campus_map
            require_campus_map(campus_id, map_data)
        self.campus_id = campus_id
        self.map_data = map_data
        self.companion_caller = companion_caller if companion_caller is not None else caller
        self._pipeline_kwargs = {
            "plan_caller": plan_caller,
            "review_caller": review_caller,
            "revision_caller": revision_caller,
            "reconciliation_caller": reconciliation_caller,
        }

    def _apply_personal_settings(self, state):
        """Project saved defaults into canonical facts before formal planning."""
        settings = load_personal_settings(self.store)
        context = apply_personal_defaults_to_context(
            load_execution_context(self.store),
            settings,
            transport_is_explicit=bool(self.store.get(PERSONAL_TRANSPORT_EXPLICIT_KEY)),
        )
        save_execution_context(self.store, context)
        if self.campus_id is None:
            return state
        applied = inject_timetable(
            state, settings, self.campus_id,
            default_safety_buffer_minutes=self.config.default_safety_buffer_minutes,
            travel_minutes_by_commitment=self.config.travel_minutes_by_commitment,
        )
        self.store[PERSONAL_OTHER_CAMPUS_COURSES_KEY] = applied.other_campus_commitment_refs
        return applied.state

    def _personal_settings(self):
        return load_personal_settings(self.store)

    def _personal_location_text(self, text):
        alias = personal_location_alias(text, self.campus_id, self._personal_settings())
        return alias.display_name if alias is not None else text

    def _make_companion(
        self,
        state,
        result,
        movement_blocks=(),
        context_type=CONTEXT_INITIAL,
        before_state=None,
        before_blocks=(),
        after_blocks=None,
        before_location=None,
        after_location=None,
        before_allocation_plan=None,
    ):
        """完整表达层：Plan Critic ->（可选）Improver -> Lifestyle + 四个文案 Agent。

        返回 (CompanionCopy, revised_result_or_None)；未启用时返回 (None, None)。
        feedback 场景：基于程序已应用的真实 state diff 生成 change summary；
        若反馈没有产生有效状态变化，则退回普通概览（refresh），不假装“已更新计划”。
        """
        if not self.companion_enabled:
            return None, None
        effective_context = context_type
        change_facts = None
        try:
            if context_type == CONTEXT_FEEDBACK and before_state is not None:
                change_facts = extract_change_facts(
                    before_state,
                    state,
                    before_blocks=tuple(before_blocks or ()),
                    after_blocks=tuple(
                        after_blocks if after_blocks is not None else movement_blocks or ()
                    ),
                    before_location=before_location,
                    after_location=after_location,
                    before_allocation_plan=before_allocation_plan,
                    after_allocation_plan=result.allocation_plan,
                )
                if not change_facts["changed"]:
                    effective_context = CONTEXT_REFRESH
                    change_facts = None
            resolved_location = after_location
            if resolved_location is None:
                resolved_location = self.store.get(MOVEMENT_DATA_KEY, {}).get(
                    "current_location_text"
                )
            execution_context = load_execution_context(self.store)
            assumed_location = None
            if (
                execution_context.current_location.source.value == "assumed"
                and execution_context.current_location.location is not None
            ):
                assumed_location = execution_context.current_location.location.display_name
            return generate_expression_copy(
                state,
                result,
                tuple(movement_blocks or ()),
                context_type=effective_context,
                change_facts=change_facts,
                current_location=resolved_location,
                assumed_current_location=assumed_location,
                map_data=self.map_data,
                caller=self.companion_caller,
                repair_caller=self.companion_caller,
                # P4's route reservation + bounded formal reallocation is the
                # final planning authority.  A copy-layer Improver must not
                # run a bare allocator afterwards and discard meal duration,
                # class-prep capacity, or per-task location boundaries.
                allow_plan_revision=not self._has_execution_semantics(),
            )
        except Exception as exc:  # noqa: BLE001 - 表达层失败只降级，不影响主计划
            logger.error(
                "[CampusFlow][companion] degraded context=%s %s: %r\n%s",
                effective_context,
                type(exc).__name__,
                exc,
                traceback.format_exc(),
            )
            opening, closing = companion_fallbacks(effective_context)
            return (
                CompanionCopy(
                    opening=opening,
                    closing=closing,
                    generated=False,
                    context_type=effective_context,
                ),
                None,
            )

    def _companion_from_intelligence(self, intelligence):
        """Project P5 grounded copy into the existing UI contract."""
        narrative = intelligence.narrative
        notes = [
            value for value in (
                narrative.why_this_plan,
                narrative.proactive_suggestion,
            )
            if value
        ]
        return CompanionCopy(
            # A material unmet preference/risk must reach the visible summary;
            # optional architectural/lifestyle explanations remain secondary.
            opening=" ".join(dict.fromkeys(value for value in
                (narrative.opening, narrative.risk_note) if value)),
            # The old lifestyle panel is no longer rendered. Keep a validated
            # decision explanation in the existing closing slot rather than
            # silently discarding it or adding another UI section.
            closing=narrative.why_this_plan or narrative.closing,
            generated=narrative.generated,
            context_type=(
                CONTEXT_FEEDBACK
                if intelligence.context.latest_feedback_text
                else CONTEXT_INITIAL
            ),
            lifestyle_hint=" ".join(notes) if notes else None,
            copy_reviewed=True,
        )

    def _turn_with_applied_what_if_copy(self, turn, preview):
        """Attach deterministic adoption copy without touching plan facts.

        ``preview.candidate`` is the exact hard-validated turn the user saw.
        The preview path purposefully does not spend normal narrator calls, so
        adoption creates a small structured explanation from its ref-bound
        differences instead of reinterpreting or rebuilding the plan.
        """
        intelligence = getattr(turn, "agent_intelligence", None)
        if intelligence is None:
            return turn
        narrative = _applied_what_if_narrative(
            intelligence.narrative, preview, turn.result.updated_state,
            intelligence.context,
        )
        updated = replace(intelligence, narrative=narrative)
        return replace(
            turn,
            agent_intelligence=updated,
            companion_copy=self._companion_from_intelligence(updated),
        )

    def _run_p5_intelligence(self, result, movement_blocks, base_state, latest_text, flow):
        """Run P5 over immutable facts and choose one hard-valid candidate."""
        context = load_execution_context(self.store)
        decision = load_feedback_decision(self.store)
        preferences = load_day_preferences(self.store)
        decision_context = build_agent_decision_context(
            result.updated_state,
            context,
            self.campus_id,
            allocation_plan=result.allocation_plan,
            movement_blocks=movement_blocks,
            feedback_decision=decision,
            latest_user_text=self.store.get(LATEST_USER_TEXT_KEY),
            latest_feedback_text=self.store.get(LATEST_FEEDBACK_TEXT_KEY),
            day_preferences=preferences,
            personal_settings=self._personal_settings(),
        )
        baseline_blocks = tuple(movement_blocks or ())
        baseline_concurrent = self._concurrent_execution(result.updated_state).allocations

        def _timeline_facts(candidate_result, blocks, concurrent):
            from src.p3_route_planner import render_movement_lines
            # Give every language pass the exact deterministic final timeline
            # shown by the renderer, rather than a lossy list of refs that can
            # accidentally associate a destination with the following task.
            return tuple(render_movement_lines(
                tuple(blocks),
                candidate_result.allocation_plan,
                candidate_result.updated_state,
                execution_context=load_execution_context(self.store),
                concurrent_allocations=tuple(concurrent),
            ))

        def _build(strategy, candidate_id):
            if candidate_id == "candidate_01":
                candidate_result = result
                blocks = baseline_blocks
                concurrent = baseline_concurrent
            else:
                order = apply_feedback_task_order(base_state, strategy.priority_order, decision)
                intent = result.day_plan_intent
                include_low = intent.include_low_attention if intent is not None else False
                overrides = self._effective_duration_overrides(base_state)
                preferred_chunks = self._strategy_chunk_overrides(
                    base_state, strategy.fragmentation_level
                )
                provisional_plan = allocate_tasks_across_windows(
                    base_state,
                    task_order=order,
                    include_low_attention=include_low,
                    effective_duration_by_task_ref=overrides,
                    protected_duration_by_task_ref=self._protected_meal_durations(base_state),
                    earliest_start_by_task_ref=self._execution_earliest_starts(base_state),
                    latest_end_by_task_ref=self._execution_latest_ends(base_state),
                    preferred_chunk_by_task_ref=preferred_chunks,
                    concurrent_minutes_by_task_ref=self._concurrent_minutes(base_state),
                )
                provisional = replace(
                    result,
                    original_state=base_state,
                    updated_state=base_state,
                    allocation_plan=provisional_plan,
                    day_summary=summarize_day_plan(provisional_plan, base_state),
                )
                sequence = self._apply_execution_sequence(
                    provisional,
                    overrides,
                    task_order_override=order,
                    deterministic_facts=True,
                )
                # No movement reservation is also the correct result for a
                # same-place plan. The shared final execution validator below
                # rejects an actually missing route, rather than treating the
                # presence of any location as proof that travel is required.
                if sequence.applied:
                    candidate_result = replace(
                        provisional,
                        updated_state=sequence.state,
                        allocation_plan=sequence.allocation_plan,
                        day_summary=summarize_day_plan(sequence.allocation_plan, sequence.state),
                        warnings=provisional.warnings + sequence.warnings,
                    )
                    blocks = tuple(sequence.blocks)
                else:
                    candidate_result = provisional
                    blocks = ()
                concurrent = self._concurrent_execution(candidate_result.updated_state).allocations
            summary = summarize_candidate(
                candidate_id, strategy, candidate_result.updated_state,
                candidate_result, blocks, concurrent,
                timeline=_timeline_facts(candidate_result, blocks, concurrent),
                unresolved_questions=result.questions,
                preferred_next_task_ref=(
                    decision.preferred_next_task_ref if decision is not None else None
                ),
                meal_task_refs=decision_context.meals and tuple(
                    item.task_ref for item in decision_context.meals
                ),
                effective_duration_by_task_ref=self._effective_duration_overrides(candidate_result.updated_state),
                protected_duration_by_task_ref=self._protected_meal_durations(candidate_result.updated_state),
            )
            return {
                "result": candidate_result,
                "state": candidate_result.updated_state,
                "movement_blocks": blocks,
                "concurrent_allocations": concurrent,
                "summary": summary,
            }

        def _validate(candidate):
            candidate_result = candidate.result
            errors = list(final_plan_overlap_errors(
                candidate_result.updated_state,
                candidate_result.allocation_plan,
                candidate.movement_blocks,
            ))
            errors.extend(_live_turn_execution_errors(
                candidate_result.updated_state,
                candidate_result.allocation_plan,
                candidate.movement_blocks,
                load_execution_context(self.store),
                self.map_data,
                candidate.concurrent_allocations,
            ))
            unique_sequence = []
            for task_ref in candidate.summary.task_sequence:
                if task_ref not in unique_sequence:
                    unique_sequence.append(task_ref)
            positions = {ref: index for index, ref in enumerate(unique_sequence)}
            if any(
                left in positions and right in positions
                and positions[left] > positions[right]
                for left, right in decision_context.ordering_constraints
            ):
                errors.append("candidate violates canonical narrative order")
            if decision is not None and not candidate_complies_with_decision(
                candidate_result.updated_state,
                candidate_result.allocation_plan,
                decision,
            ):
                errors.append("candidate violates latest explicit feedback")
            return tuple(errors)

        def _expression_context(candidate):
            """Rebuild the read-only copy context from the selected final facts.

            Strategy evaluation starts from a baseline context, but the
            explanation, suggestion and confirmation card must all refer to
            the exact candidate whose timeline may be published.
            """
            return build_agent_decision_context(
                candidate.result.updated_state,
                load_execution_context(self.store),
                self.campus_id,
                allocation_plan=candidate.result.allocation_plan,
                movement_blocks=candidate.movement_blocks,
                feedback_decision=decision,
                latest_user_text=self.store.get(LATEST_USER_TEXT_KEY),
                latest_feedback_text=self.store.get(LATEST_FEEDBACK_TEXT_KEY),
                day_preferences=preferences,
                personal_settings=self._personal_settings(),
            )

        intelligence = run_agent_intelligence(
            decision_context,
            self.caller,
            _build,
            candidate_validator=_validate,
            repair_caller=self.repair_caller,
            latest_user_text=latest_text,
            extracted_semantics=_p5_review_semantic_facts(
                decision_context, decision, flow
            ),
            deterministic_facts={
                "campus_id": self.campus_id,
                "fixed_commitments": [item.commitment_ref for item in result.updated_state.commitments],
                "movement_count": len(tuple(movement_blocks or ())),
                "concurrency_pairs": [
                    (item.task_ref, item.commitment_ref)
                    for item in decision_context.concurrency_authorizations
                ],
                "class_prep": [
                    {
                        "commitment_ref": item.commitment_ref,
                        "building_arrival": item.class_arrival_deadline,
                        "classroom_arrival": item.classroom_arrival_time,
                    }
                    for item in decision_context.fixed_commitments
                    if item.commitment_kind == "class"
                ],
                "hard_validated": True,
            },
            flow=flow,
            expression_context_builder=_expression_context,
        )
        save_agent_intelligence(self.store, intelligence)
        chosen = intelligence.selected_candidate
        return chosen.result, tuple(chosen.movement_blocks), tuple(chosen.concurrent_allocations), intelligence

    # -- 三个允许触发新规划的入口 --

    def start_day(
        self, initial_state: DayPlanningState, movement_blocks=(),
        force: bool = False, agent_flow=None, seed_day_plan_intent=None,
    ) -> P2SessionTurn:
        """“开始全天计划”：对初始结构化状态执行首次规划。

        movement_blocks 为 P3 空间 intake 生成的移动块（可空），
        随首个 turn 一并缓存，供“当前方案”逐行渲染与后续 refresh 继承。
        """
        _require_state(initial_state)
        initial_state = self._apply_personal_settings(initial_state)
        key = make_cache_key(START_TEXT, initial_state)
        cached = self._cached(key)
        if cached is not None and not force:
            return cached
        overrides = self._effective_duration_overrides(initial_state)
        from src.low_input_context import initial_planning_text
        planning_text = initial_planning_text(
            self.store.get(LATEST_USER_TEXT_KEY), self._personal_settings(), START_TEXT
        )
        result = run_p2_agentic_day_planning(
            initial_state,
            planning_text,
            self.caller,
            skip_reconciliation=True,
            seed_day_plan_intent=seed_day_plan_intent,
            skip_legacy_review=(
                self.agent_intelligence_enabled and self._has_execution_semantics()
            ),
            effective_duration_by_task_ref=overrides,
            **self._pipeline_kwargs
        )
        result = self._with_execution_task_order(result)
        execution_base_state = result.updated_state
        deterministic_preview = self.agent_intelligence_enabled or agent_flow == "what_if"
        sequence = self._apply_execution_sequence(
            result, overrides, deterministic_facts=deterministic_preview
        )
        if not sequence.applied and sequence.warnings:
            # Drop only meals whose own route chain could not be reserved,
            # then still build the reliable route to the fixed commitment.
            safe_result = replace(
                result,
                allocation_plan=_without_unroutable_default_meals(
                    result.allocation_plan, load_execution_context(self.store)
                ),
            )
            if safe_result.allocation_plan is not result.allocation_plan:
                retried = self._apply_execution_sequence(
                    safe_result,
                    overrides,
                    deterministic_facts=deterministic_preview,
                    deferred_task_refs=set(result.allocation_plan.planned_minutes_by_task) -
                    set(safe_result.allocation_plan.planned_minutes_by_task),
                )
                if retried.applied:
                    result = safe_result
                    sequence = retried
        all_blocks = tuple(movement_blocks) + tuple(sequence.blocks)
        if sequence.applied:
            result = replace(
                result,
                updated_state=sequence.state,
                allocation_plan=sequence.allocation_plan,
                day_summary=summarize_day_plan(sequence.allocation_plan, sequence.state),
                warnings=result.warnings + sequence.warnings,
            )
        elif sequence.warnings:
            result = replace(
                result,
                allocation_plan=_without_unroutable_default_meals(
                    result.allocation_plan, load_execution_context(self.store)
                ),
                warnings=result.warnings + sequence.warnings,
            )
        concurrent = self._concurrent_execution(result.updated_state).allocations
        intelligence = None
        if self.agent_intelligence_enabled and self._has_execution_semantics():
            try:
                flow = agent_flow or (
                    "feedback"
                    if self.store.get(LATEST_FEEDBACK_TEXT_KEY)
                    else "initial"
                )
                if flow not in ("initial", "feedback", "concurrency", "what_if"):
                    raise ValueError("agent_flow invalid")
                result, all_blocks, concurrent, intelligence = self._run_p5_intelligence(
                    result, all_blocks, execution_base_state,
                    self.store.get(LATEST_FEEDBACK_TEXT_KEY) or self.store.get(LATEST_USER_TEXT_KEY) or START_TEXT,
                    flow,
                )
            except Exception as exc:  # intelligence is non-critical
                logger.warning("P5 intelligence degraded to deterministic baseline: %s", type(exc).__name__)
                self.store.pop(AGENT_INTELLIGENCE_KEY, None)
                self.store.pop(AGENT_CALL_TRACE_KEY, None)
        if intelligence is not None:
            companion = self._companion_from_intelligence(intelligence)
        else:
            companion, revised = self._make_companion(
                result.updated_state, result, all_blocks
            )
            if revised is not None:
                result = revised
        turn = P2SessionTurn(
            START_TEXT, result, (), (), key,
            movement_blocks=all_blocks,
            companion_copy=companion,
            execution_context=load_execution_context(self.store),
            concurrent_allocations=tuple(concurrent),
            agent_intelligence=intelligence,
        )
        self._commit(key, turn)
        if all_blocks:
            movement_data = self.store.setdefault(MOVEMENT_DATA_KEY, {})
            movement_data["active_blocks"] = all_blocks
            if sequence.applied:
                # The pre-reservation snapshot is needed if a later feedback
                # corrects an assumed current location.
                movement_data["p4_execution_base_state"] = execution_base_state
        return turn

    def add_confirmed_estimated_task_atomic(
        self, confirmed_task, reference_datetime=None, existing_task_ref=None
    ):
        """Add one confirmed estimate through the canonical ledger and planner.

        The estimator's structured scope and adopted workload are already
        confirmed facts.  They therefore bypass Day Intake language parsing
        and estimation, but still enter the ordinary TaskProgress ledger,
        ExecutionPlanContext, movement/allocation pipeline and LiveFinalTurn
        validation.  Nothing in the real store is changed until the candidate
        bundle is complete.
        """
        from datetime import datetime, timedelta

        if not isinstance(confirmed_task, ConfirmedEstimatedTask):
            raise TypeError("confirmed_task must be ConfirmedEstimatedTask")
        if self.map_data is None or self.campus_id is None:
            raise RuntimeError("adding an estimated task requires a selected campus")

        added = self.store.get(TASK_ESTIMATE_ADDED_DRAFTS_KEY, {})
        if isinstance(added, Mapping):
            prior_ref = added.get(confirmed_task.draft_id)
            current_bundle = load_live_final_turn(self.store)
            if (
                prior_ref
                and current_bundle is not None
                and any(task.task_ref == prior_ref for task in current_bundle.state.tasks)
            ):
                return EstimatedTaskAddOutcome(current_bundle, prior_ref, duplicate=True)
            if prior_ref:
                raise ValueError("this estimate draft has already been consumed")

        previous_bundle = load_live_final_turn(self.store)
        movement_data = self.store.get(MOVEMENT_DATA_KEY, {})
        base_state = (
            movement_data.get("p4_execution_base_state")
            if isinstance(movement_data, Mapping) else None
        )
        if not isinstance(base_state, DayPlanningState):
            base_state = (
                previous_bundle.result.original_state
                if previous_bundle is not None else self.store.get(STATE_KEY)
            )
        if not isinstance(base_state, DayPlanningState):
            reference = reference_datetime or datetime.now()
            if not isinstance(reference, datetime):
                raise TypeError("reference_datetime must be datetime or None")
            day_end = reference.replace(hour=23, minute=59, second=0, microsecond=0)
            if day_end <= reference:
                day_end = reference + timedelta(hours=2)
            base_state = derive_day_state(
                reference,
                day_end,
                (),
                (),
                default_safety_buffer_minutes=self.config.default_safety_buffer_minutes,
                reference_datetime=reference,
            )

        source = (
            SourceKind.AI_ESTIMATED
            if confirmed_task.duration_source == "ai_estimated"
            else SourceKind.USER_CONFIRMED
        )
        splittable = confirmed_task.is_splittable
        if confirmed_task.requires_single_session is True:
            splittable = False
        minimum_slice = confirmed_task.minimum_chunk_minutes
        if existing_task_ref is not None:
            # The user's explicit selection, never title matching, binds the
            # estimate to the original task. Current canonical facts win over
            # an older movement base snapshot when checking stale selection.
            if previous_bundle is None:
                raise ValueError("待补时长的方案已变化，请重新选择。")
            confirm_unknown_task_duration(previous_bundle.state, existing_task_ref,
                                          confirmed_task.total_minutes, source)
            applied = confirm_unknown_task_duration(base_state, existing_task_ref,
                                                     confirmed_task.total_minutes, source)
            task_ref = existing_task_ref
        else:
            applied = append_structured_task(
                base_state, confirmed_task.task_name, confirmed_task.total_minutes,
                source, is_splittable=splittable, minimum_slice_minutes=minimum_slice,
            )
            task_ref = applied.new_task_refs[0]

        previous_context = (
            previous_bundle.execution_context
            if previous_bundle is not None else load_execution_context(self.store)
        )
        candidate_context = reconcile_execution_context(previous_context, applied.state)
        location = None
        if confirmed_task.location_text:
            resolution = resolve_location(
                self.map_data,
                self._personal_location_text(confirmed_task.location_text),
                self.caller,
                self.repair_caller,
            )
            if not resolution.usable or resolution.campus_id != self.campus_id:
                raise ValueError("estimated task location was not resolved in selected campus")
            location = ExecutionLocation.from_resolution(
                resolution, ExecutionLocationSource.EXPLICIT_TASK_LOCATION
            )

        profile = None
        if (
            splittable is not None
            or confirmed_task.requires_single_session is not None
            or confirmed_task.minimum_chunk_minutes is not None
            or confirmed_task.preferred_chunk_minutes is not None
        ):
            profile_splittable = bool(splittable)
            minimum_chunk = confirmed_task.minimum_chunk_minutes or (
                confirmed_task.total_minutes if not profile_splittable else 15
            )
            preferred_chunk = confirmed_task.preferred_chunk_minutes or (
                confirmed_task.total_minutes if not profile_splittable else max(minimum_chunk, 30)
            )
            minimum_chunk = min(minimum_chunk, confirmed_task.total_minutes)
            preferred_chunk = min(
                max(preferred_chunk, minimum_chunk), confirmed_task.total_minutes
            )
            profile = TaskExecutionProfile(
                task_ref,
                profile_splittable,
                minimum_chunk,
                preferred_chunk,
                requires_single_session=bool(confirmed_task.requires_single_session),
                source="user_explicit" if confirmed_task.requires_single_session is not None else "qwen_semantic",
            )
        binding = ExecutableTaskBinding(
            task_ref=task_ref,
            execution_location=location,
            activity_kind="generic",
            effective_duration_minutes=confirmed_task.total_minutes,
            duration_source=(
                "ai_estimated"
                if confirmed_task.duration_source == "ai_estimated"
                else "user_explicit"
            ),
            execution_profile=profile,
            scope_summary=confirmed_task.scope_summary,
            completion_criteria=confirmed_task.completion_criteria,
            latest_end_time=confirmed_task.deadline_time,
        )
        if existing_task_ref is not None:
            old_binding = previous_context.binding_for(existing_task_ref)
            if old_binding is not None:
                if location is not None and location != old_binding.execution_location:
                    raise ValueError("估算地点与原任务不同，请在调整计划中确认地点变化。")
                binding = replace(old_binding,
                    effective_duration_minutes=confirmed_task.total_minutes,
                    duration_source=binding.duration_source,
                    scope_summary=confirmed_task.scope_summary,
                    completion_criteria=confirmed_task.completion_criteria,
                )
        candidate_context = candidate_context.upsert(binding)
        campus_locations = [
            item.execution_location for item in candidate_context.bindings
            if item.execution_location is not None
        ]
        if candidate_context.current_location.location is not None:
            campus_locations.append(candidate_context.current_location.location)
        if any(item.campus_id != self.campus_id for item in campus_locations):
            raise ValueError("estimated task context does not belong to selected campus")

        isolated_store = dict(self.store)
        if isinstance(movement_data, Mapping):
            isolated_store[MOVEMENT_DATA_KEY] = dict(movement_data)
        save_execution_context(isolated_store, candidate_context)
        isolated_store[LATEST_FEEDBACK_TEXT_KEY] = "加入今日计划：{}".format(
            confirmed_task.task_name
        )
        isolated = self._fork_with_store(isolated_store)
        prior_intent = (
            previous_bundle.result.day_plan_intent
            if previous_bundle is not None else None
        )
        task_order = tuple(task.task_ref for task in applied.state.tasks)
        if prior_intent is not None:
            seed_intent = replace(prior_intent, task_order=task_order)
        else:
            seed_intent = DayPlanIntent(
                DAY_PLAN_INTENT_SCHEMA_VERSION,
                task_order,
                False,
                (),
                None,
            )
        candidate_turn = isolated.start_day(
            applied.state,
            force=True,
            agent_flow="feedback",
            seed_day_plan_intent=seed_intent,
        )
        candidate_turn = replace(
            candidate_turn,
            user_text="加入今日计划：{}".format(confirmed_task.task_name),
            execution_context=load_execution_context(isolated_store),
        )
        previous_version = previous_bundle.version if previous_bundle is not None else 0
        version = max(
            previous_version,
            int(self.store.get(LIVE_FINAL_TURN_SEQUENCE_KEY, 0) or 0),
        ) + 1
        candidate_bundle = build_live_final_turn(
            candidate_turn,
            self.map_data,
            version=version,
            extra_questions=(
                previous_bundle.extra_questions if previous_bundle is not None else ()
            ),
            feedback_decision=load_feedback_decision(isolated_store),
        )

        for key in (
            STATE_KEY, RESULT_KEY, LAST_TURN_KEY, LAST_CACHE_KEY,
            MOVEMENT_DATA_KEY, EXECUTION_PLAN_CONTEXT_KEY,
            P4_FEEDBACK_DECISION_KEY, DAY_PREFERENCE_KEY,
            AGENT_INTELLIGENCE_KEY, AGENT_CALL_TRACE_KEY,
            LATEST_USER_TEXT_KEY, LATEST_FEEDBACK_TEXT_KEY,
        ):
            if key in isolated_store:
                self.store[key] = isolated_store[key]
            else:
                self.store.pop(key, None)
        self.store[LIVE_FINAL_TURN_SEQUENCE_KEY] = version
        self.store[LIVE_FINAL_TURN_KEY] = candidate_bundle
        updated_added = dict(added) if isinstance(added, Mapping) else {}
        updated_added[confirmed_task.draft_id] = task_ref
        self.store[TASK_ESTIMATE_ADDED_DRAFTS_KEY] = updated_added
        clear_what_if_preview(self.store)
        return EstimatedTaskAddOutcome(candidate_bundle, task_ref, duplicate=False)

    def apply_feedback(self, user_text: str, reference_datetime=None) -> P2SessionTurn:
        """“应用反馈并重新规划”：commitment 反馈 -> 重派生窗口 -> P2c 重新规划。"""
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("user_text must be a non-empty string")
        current = self._feedback_state_at_reference(self._current_state(), reference_datetime)
        key = make_cache_key(user_text, current)
        cached = self._cached(key)
        if cached is not None:
            return cached
        turn = self._run_feedback_turn(user_text, current, key)
        # 同一反馈在同一结果状态下重复提交视为重复动作，命中缓存不重复调用 Agent
        self._commit(key, turn, dedup_key=make_cache_key(user_text, turn.result.updated_state))
        return turn

    def rebuild_feedback_atomic(
        self, user_text: str, accepted_decision=None, use_unified=True,
        agent_flow=None, reference_datetime=None,
        confirmed_task_location=None,
    ):
        """Apply feedback to canonical facts, then publish one complete new P4 turn.

        The unified feedback handler is allowed to understand and update task /
        commitment facts inside an isolated store.  Its allocation and route
        snapshots are deliberately discarded.  The candidate state and the
        updated execution context then pass through ``start_day`` once, which
        is the same formal enrichment/movement/allocation path used initially.
        Nothing in the real session is changed until the candidate satisfies
        every final-turn invariant.
        """
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("user_text must be a non-empty string")
        if self.map_data is None or self.campus_id is None:
            raise RuntimeError("atomic P4 feedback rebuild requires a selected campus map")
        previous_bundle = load_live_final_turn(self.store)
        previous_context = load_execution_context(self.store)
        real_movement_data = self.store.get(MOVEMENT_DATA_KEY, {})
        base_state = real_movement_data.get("p4_execution_base_state")
        if not isinstance(base_state, DayPlanningState):
            base_state = self._current_state()
        base_state = self._feedback_state_at_reference(base_state, reference_datetime)

        previous_decision = load_feedback_decision(self.store)
        previous_summary = (
            candidate_plan_summary(previous_bundle.state, previous_bundle.result.allocation_plan)
            if previous_bundle is not None else ""
        )
        if accepted_decision is None:
            semantic_outcome = decide_feedback(
                base_state,
                previous_context,
                user_text,
                previous_summary,
                self.caller,
                self.repair_caller,
                existing_decision=previous_decision,
            )
        else:
            if not isinstance(accepted_decision, FeedbackDecision):
                raise TypeError("accepted_decision must be FeedbackDecision or None")
            from src.p4_feedback_decision import FeedbackDecisionOutcome
            semantic_outcome = FeedbackDecisionOutcome(
                accepted_decision, 0, 0, used_fallback=False
            )
        if accepted_decision is None and semantic_outcome.decision.intent_type == "what_if":
            return self.preview_what_if_atomic(user_text)
        feedback_decision = merge_feedback_decisions(
            previous_decision, semantic_outcome.decision, base_state
        )

        # The feedback interpreter receives a private movement snapshot.  It
        # can update canonical facts, but cannot mutate the page's reliable
        # current turn while the candidate is incomplete.
        candidate_movement_data = {}
        if previous_context.current_location.location is not None:
            candidate_movement_data["current_location_text"] = (
                previous_context.current_location.location.display_name
            )
        outcome = None
        planning_state = base_state
        if (
            use_unified
            and self.unified_handler is not None
            and _feedback_requires_unified_interpreter(feedback_decision)
        ):
            outcome = self.unified_handler(user_text, base_state, candidate_movement_data)
            interpreted_result = getattr(outcome, "result", None)
            if interpreted_result is not None:
                planning_state = interpreted_result.updated_state
        interpreted_state = planning_state
        planning_state = apply_feedback_decision_to_state(
            planning_state, feedback_decision
        )
        candidate_personal_settings = capture_occurrence_overrides(
            self._personal_settings(), base_state, planning_state
        )

        candidate_context = reconcile_execution_context(previous_context, planning_state)
        if confirmed_task_location is not None:
            from src.p4_execution_context import ExecutionConfirmationKind
            target_ref, location = confirmed_task_location
            target = previous_context.binding_for(target_ref)
            pending = any(c.task_ref == target_ref and c.kind is ExecutionConfirmationKind.TASK_LOCATION_REQUIRED
                          for c in previous_context.confirmations)
            if (not pending or target is None or target.execution_location is not None
                    or not isinstance(location, ExecutionLocation) or location.campus_id != self.campus_id):
                raise ValueError('task location answer has no matching unresolved field')
            candidate_context = candidate_context.upsert(replace(target, execution_location=location))
            candidate_context = candidate_context.with_confirmations(tuple(
                c for c in candidate_context.confirmations
                if not (c.task_ref == target_ref and c.kind is ExecutionConfirmationKind.TASK_LOCATION_REQUIRED)))
        explicit_current = _explicit_current_location_feedback(user_text)
        interpreted_current = getattr(outcome, "current_location_text", None) if outcome else None
        current_text = (
            explicit_current or interpreted_current
            or feedback_decision.location_correction
        )
        if current_text:
            resolution = resolve_location(
                self.map_data, self._personal_location_text(current_text),
                self.caller, self.repair_caller
            )
            if not resolution.usable or resolution.campus_id != self.campus_id:
                raise ValueError("current location was not resolved in selected campus")
            current_location = ExecutionLocation.from_resolution(
                resolution, ExecutionLocationSource.USER_CURRENT_LOCATION
            )
            candidate_context = replace_assumed_current_location(
                candidate_context, current_location
            )
        # Qwen decides whether the user explicitly authorized/revoked a
        # particular task/commitment pair.  Only the deterministic adapter
        # below turns that semantic decision into a concurrency fact.
        candidate_context = apply_concurrency_feedback(
            candidate_context, feedback_decision, planning_state
        )

        candidate_preferences = load_day_preferences(self.store)
        if (
            self.agent_intelligence_enabled
            and feedback_decision.day_preference_candidate
        ):
            preference_context = build_agent_decision_context(
                planning_state, candidate_context, self.campus_id,
                allocation_plan=(previous_bundle.result.allocation_plan if previous_bundle is not None else None),
                movement_blocks=(previous_bundle.movement_blocks if previous_bundle is not None else ()),
                feedback_decision=feedback_decision,
                latest_user_text=self.store.get(LATEST_USER_TEXT_KEY),
                latest_feedback_text=user_text,
                day_preferences=candidate_preferences,
                personal_settings=self._personal_settings(),
            )
            extracted, _ = extract_day_preferences(
                preference_context, user_text, self.caller, self.repair_caller,
                existing=candidate_preferences,
                turn_version=(previous_bundle.version + 1 if previous_bundle is not None else 1),
            )
            candidate_preferences = extracted.profile

        def _build_candidate(decision, state_for_candidate):
            local_store = {
                EXECUTION_PLAN_CONTEXT_KEY: candidate_context,
                P4_FEEDBACK_DECISION_KEY: decision,
                DAY_PREFERENCE_KEY: candidate_preferences,
                LATEST_USER_TEXT_KEY: self.store.get(LATEST_USER_TEXT_KEY),
                LATEST_FEEDBACK_TEXT_KEY: user_text,
                PERSONAL_SETTINGS_KEY: candidate_personal_settings,
                PERSONAL_TRANSPORT_EXPLICIT_KEY: self.store.get(PERSONAL_TRANSPORT_EXPLICIT_KEY),
            }
            local = self._fork_with_store(local_store)
            turn = local.start_day(
                state_for_candidate,
                force=True,
                agent_flow=(agent_flow or "feedback"),
                seed_day_plan_intent=(
                    previous_bundle.result.day_plan_intent
                    if previous_bundle is not None else None
                ),
            )
            turn = replace(
                turn,
                user_text=user_text.strip(),
                cache_key=make_cache_key(user_text, turn.result.updated_state),
                execution_context=load_execution_context(local_store),
            )
            local_store[LAST_TURN_KEY] = turn
            local_store[LAST_CACHE_KEY] = turn.cache_key
            return local_store, turn

        candidate_store, candidate_turn = _build_candidate(
            feedback_decision, planning_state
        )
        if not candidate_complies_with_decision(
            candidate_turn.result.updated_state,
            candidate_turn.result.allocation_plan,
            feedback_decision,
        ):
            raise ValueError("candidate plan violates latest feedback decision")

        # Reviewer C never edits time or route facts.  A rejection can trigger
        # one fresh rebuild from canonical facts, never an in-place turn patch.
        should_review = (
            not self.agent_intelligence_enabled
            and
            not feedback_decision.clarification_needed
            and (
                feedback_decision.intent_type != "no_change"
                or feedback_decision.explicit_user_preference
                or feedback_decision.has_planning_constraints
            )
        )
        if should_review:
            compliance, _ = review_intent_compliance(
                planning_state,
                user_text,
                feedback_decision,
                candidate_plan_summary(
                    candidate_turn.result.updated_state,
                    candidate_turn.result.allocation_plan,
                ),
                hard_facts_summary(candidate_turn.result.updated_state),
                self.caller,
                self.repair_caller,
            )
            if compliance is not None and compliance.decision in ("replan", "reject"):
                repaired_decision = compliance.repaired_decision or feedback_decision
                repaired_decision = merge_feedback_decisions(
                    previous_decision, repaired_decision, interpreted_state
                )
                repaired_state = apply_feedback_decision_to_state(
                    interpreted_state, repaired_decision
                )
                repaired_store, repaired_turn = _build_candidate(
                    repaired_decision, repaired_state
                )
                if not candidate_complies_with_decision(
                    repaired_turn.result.updated_state,
                    repaired_turn.result.allocation_plan,
                    repaired_decision,
                ):
                    raise ValueError("candidate plan violates latest feedback decision")
                if compliance.decision == "reject" and compliance.repaired_decision is None:
                    raise ValueError("intent reviewer rejected candidate without a repair")
                feedback_decision = repaired_decision
                planning_state = repaired_state
                candidate_store, candidate_turn = repaired_store, repaired_turn

        previous_version = previous_bundle.version if previous_bundle is not None else 0
        version = max(
            previous_version,
            int(self.store.get(LIVE_FINAL_TURN_SEQUENCE_KEY, 0) or 0),
        ) + 1
        # The interpreter may defer an update pending a specific answer. Its
        # questions belong to this result, not to a discarded interim plan.
        # Preserve them in the final bundle, and replace (rather than inherit)
        # the previous turn's interpreter questions when the user responds.
        interpreted_questions = tuple(
            getattr(getattr(outcome, "result", None), "questions",
                    previous_bundle.result.questions if previous_bundle is not None else ())
        )
        candidate_turn = replace(candidate_turn, result=replace(
            candidate_turn.result,
            questions=tuple(dict.fromkeys(
                candidate_turn.result.questions + interpreted_questions
            )),
        ))
        candidate_store[LAST_TURN_KEY] = candidate_turn
        candidate_store[RESULT_KEY] = candidate_turn.result
        prior_questions = previous_bundle.extra_questions if previous_bundle is not None else ()
        if previous_bundle is not None and getattr(outcome, "result", None) is not None:
            prior_questions = tuple(
                question for question in prior_questions
                if question not in previous_bundle.result.questions
            )
        if previous_bundle is not None:
            prior_clarification = getattr(
                getattr(previous_bundle, "agent_intelligence", None),
                "clarification", None,
            )
            if prior_clarification is not None and prior_clarification.should_ask:
                prior_questions = tuple(
                    item for item in prior_questions
                    if item != prior_clarification.question
                )
        if previous_bundle is not None and previous_decision is not None:
            stale_decision_questions = set(decision_clarification_questions(
                previous_decision,
                previous_bundle.state,
                previous_bundle.result.allocation_plan,
            ))
            prior_questions = tuple(
                item for item in prior_questions if item not in stale_decision_questions
            )
        extra_questions = prior_questions + decision_clarification_questions(
            feedback_decision,
            candidate_turn.result.updated_state,
            candidate_turn.result.allocation_plan,
        )
        candidate_bundle = build_live_final_turn(
            candidate_turn,
            self.map_data,
            version=version,
            extra_questions=extra_questions,
            feedback_decision=feedback_decision,
        )

        # Compatibility keys are updated only after validation.  The renderer
        # switches turns with the final bundle assignment below, so it cannot
        # observe a partially copied controller snapshot.
        for key in (
            STATE_KEY,
            RESULT_KEY,
            LAST_TURN_KEY,
            LAST_CACHE_KEY,
            MOVEMENT_DATA_KEY,
            EXECUTION_PLAN_CONTEXT_KEY,
            P4_FEEDBACK_DECISION_KEY,
            DAY_PREFERENCE_KEY,
            AGENT_INTELLIGENCE_KEY,
            AGENT_CALL_TRACE_KEY,
            LATEST_USER_TEXT_KEY,
            LATEST_FEEDBACK_TEXT_KEY,
            PERSONAL_SETTINGS_KEY,
        ):
            if key in candidate_store:
                self.store[key] = candidate_store[key]
            else:
                self.store.pop(key, None)
        self.store[LIVE_FINAL_TURN_SEQUENCE_KEY] = version
        self.store[LIVE_FINAL_TURN_KEY] = candidate_bundle
        clear_what_if_preview(self.store)
        return candidate_bundle

    def preview_what_if_atomic(self, user_text):
        """Build a complete hypothetical turn in an isolated session store.

        The only real-session mutation is the final preview value.  No task,
        movement, execution-context, preference, or live-turn key is copied
        back until the user explicitly applies the preview.
        """
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("user_text must be a non-empty string")
        baseline = load_live_final_turn(self.store)
        if baseline is None:
            raise RuntimeError("what-if preview requires a reliable live final turn")
        context = build_agent_decision_context(
            baseline.state,
            baseline.execution_context,
            self.campus_id,
            allocation_plan=baseline.result.allocation_plan,
            movement_blocks=baseline.movement_blocks,
            feedback_decision=baseline.feedback_decision,
            latest_user_text=self.store.get(LATEST_USER_TEXT_KEY),
            # The hypothetical sentence is interpreter input, not a canonical
            # recent-intent fact until the user explicitly applies it.
            latest_feedback_text=self.store.get(LATEST_FEEDBACK_TEXT_KEY),
            day_preferences=load_day_preferences(self.store),
            personal_settings=self._personal_settings(),
        )
        intent, trace = interpret_what_if(
            context, user_text, self.caller, self.repair_caller
        )
        failure_status = "failed" if intent is None else None
        candidate_bundle = None
        if intent is not None and not intent.clarification_needed:
            isolated_store = dict(self.store)
            # ``dict(store)`` is only a shallow copy.  Movement compatibility
            # state is itself mutable and start_day writes its active blocks;
            # sharing it would let a hypothetical preview contaminate the
            # canonical turn even though the top-level bundle stayed intact.
            movement_snapshot = isolated_store.get(MOVEMENT_DATA_KEY)
            if isinstance(movement_snapshot, Mapping):
                isolated_store[MOVEMENT_DATA_KEY] = dict(movement_snapshot)
            isolated_store.pop(WHAT_IF_PREVIEW_KEY, None)
            isolated = self._fork_with_store(isolated_store)
            stage = "constraint_replacement"
            try:
                decision = replace_hypothetical_preferences(
                    baseline.feedback_decision, _feedback_decision_from_what_if(intent)
                )
                stage = "candidate_build"
                candidate_bundle = isolated.rebuild_feedback_atomic(
                    user_text, accepted_decision=decision, use_unified=False,
                    agent_flow="what_if",
                )
                stage = "candidate_validation"
                _validate_what_if_candidate(intent, candidate_bundle)
            except WhatIfInfeasibleError as exc:
                failure_status = "infeasible"
                candidate_bundle = None
                logger.info("what_if stage=%s status=infeasible exception=%s", stage, type(exc).__name__)
            except Exception as exc:
                failure_status = "failed"
                candidate_bundle = None
                logger.warning("what_if stage=%s status=failed exception=%s", stage, type(exc).__name__)
        preview = build_what_if_preview(
            context,
            intent,
            candidate_bundle,
            baseline_fingerprint=fingerprint_context(context),
            failure_status=failure_status,
        )
        preview = replace(preview, source_user_text=user_text.strip(), baseline_version=baseline.version)
        logger.info("what_if stage=preview status=%s candidate=%s baseline_version=%s",
                    preview.status, candidate_bundle is not None, baseline.version)
        save_what_if_preview(self.store, preview)
        # Preserve a single diagnostic trace for the whole hypothetical pass,
        # while keeping prompts and raw responses out of session state.
        candidate_trace = (
            candidate_bundle.agent_intelligence.trace
            if candidate_bundle is not None
            and candidate_bundle.agent_intelligence is not None
            else AgentCallTrace()
        )
        self.store[AGENT_CALL_TRACE_KEY] = AgentCallTrace(
            trace.records + candidate_trace.records
        )
        return preview

    def apply_what_if_preview_atomic(self):
        """Atomically publish a still-current, hard-validated preview."""
        preview = load_what_if_preview(self.store)
        baseline = load_live_final_turn(self.store)
        if preview is None or baseline is None:
            raise WhatIfApplyError("没有可采用的预览，当前方案没有改变。")
        if not preview.can_apply:
            raise WhatIfApplyError("这份预览尚不能采用，当前方案没有改变。")
        if preview.baseline_version is None or preview.baseline_version != baseline.version:
            logger.info("what_if stage=apply status=stale version_match=false")
            raise WhatIfApplyError("这份预览已过期，请基于当前方案重新预览。")
        current_context = build_agent_decision_context(
            baseline.state,
            baseline.execution_context,
            self.campus_id,
            allocation_plan=baseline.result.allocation_plan,
            movement_blocks=baseline.movement_blocks,
            feedback_decision=baseline.feedback_decision,
            latest_user_text=self.store.get(LATEST_USER_TEXT_KEY),
            latest_feedback_text=self.store.get(LATEST_FEEDBACK_TEXT_KEY),
            day_preferences=load_day_preferences(self.store),
            personal_settings=self._personal_settings(),
        )
        logger.info("what_if stage=apply candidate=%s fingerprint_match=%s",
                    preview.candidate is not None,
                    preview.baseline_fingerprint == fingerprint_context(current_context))
        candidate_bundle = apply_what_if_preview(
            preview, current_context, lambda _intent: preview.candidate
        )
        if not isinstance(candidate_bundle, LiveFinalTurn):
            raise WhatIfApplyError("这份预览尚未完成校验，当前方案没有改变。")
        _validate_what_if_candidate(preview.intent, candidate_bundle)
        # Revalidate immediately before publishing; a preview is never a
        # waiver for stale refs, route facts, or overlap invariants.
        # Preview construction intentionally avoids normal narration calls.
        # At adoption, derive concise copy from the already validated preview
        # differences.  This changes no planning fact, makes the published
        # adjustment explicit, and never invokes a model or replans.
        published_turn = self._turn_with_applied_what_if_copy(
            candidate_bundle.turn, preview
        )
        checked = build_live_final_turn(
            published_turn,
            self.map_data,
            version=max(
                baseline.version,
                int(self.store.get(LIVE_FINAL_TURN_SEQUENCE_KEY, 0) or 0),
            ) + 1,
            extra_questions=candidate_bundle.extra_questions,
            feedback_decision=candidate_bundle.feedback_decision,
        )
        self.store[STATE_KEY] = checked.state
        self.store[RESULT_KEY] = checked.result
        self.store[LAST_TURN_KEY] = checked.turn
        self.store[LAST_CACHE_KEY] = checked.turn.cache_key
        self.store[EXECUTION_PLAN_CONTEXT_KEY] = checked.execution_context
        self.store[P4_FEEDBACK_DECISION_KEY] = checked.feedback_decision
        self.store[MOVEMENT_DATA_KEY] = {
            "active_blocks": checked.movement_blocks,
            "p4_execution_base_state": checked.result.original_state,
        }
        if checked.agent_intelligence is not None:
            save_agent_intelligence(self.store, checked.agent_intelligence)
        self.store[LATEST_FEEDBACK_TEXT_KEY] = (
            preview.source_user_text or "what-if applied"
        )
        self.store[LIVE_FINAL_TURN_SEQUENCE_KEY] = checked.version
        self.store[LIVE_FINAL_TURN_KEY] = checked
        clear_what_if_preview(self.store)
        logger.info("what_if stage=apply status=committed version=%s", checked.version)
        return checked

    def refresh(self) -> P2SessionTurn:
        """“刷新方案”：基于当前 state 无条件重新生成计划快照。

        P2e：刷新不是用户反馈，不路由、不调用 commitment / task reconciler，
        只重新执行 Day Plan -> allocator -> Review。
        """
        current = self._apply_personal_settings(self._current_state())
        key = make_cache_key(REFRESH_TEXT, current)
        movement_data = self.store.setdefault(MOVEMENT_DATA_KEY, {})
        if not self._has_execution_semantics():
            # Legacy P3 turns own their retained movement blocks.  They do
            # not have execution-location bindings, so rebuilding from a P4
            # base would incorrectly discard their confirmed route.
            result = run_p2_agentic_day_planning(
                current,
                REFRESH_TEXT,
                self.caller,
                skip_reconciliation=True,
                effective_duration_by_task_ref=self._effective_duration_overrides(current),
                **self._pipeline_kwargs
            )
            movement_blocks = tuple(movement_data.get("active_blocks", ()) or ())
            companion, revised = self._make_companion(
                result.updated_state, result, movement_blocks, context_type=CONTEXT_REFRESH
            )
            if revised is not None:
                result = revised
            turn = P2SessionTurn(
                REFRESH_TEXT, result, (), (), key, movement_blocks=movement_blocks,
                companion_copy=companion,
                execution_context=load_execution_context(self.store),
            )
            self._commit(key, turn, dedup_key=make_cache_key(REFRESH_TEXT, turn.result.updated_state))
            return turn
        # The visible state has movement reservations split into its windows.
        # Rebuild a refresh from the saved pre-reservation state so the same
        # execution sequence can be reserved exactly once again.
        base_state = movement_data.get("p4_execution_base_state")
        planning_input = (
            self._apply_personal_settings(base_state)
            if isinstance(base_state, DayPlanningState) else current
        )
        result = run_p2_agentic_day_planning(
            planning_input,
            REFRESH_TEXT,
            self.caller,
            skip_reconciliation=True,
            seed_day_plan_intent=(
                self.current_result().day_plan_intent
                if self.current_result() is not None else None
            ),
            skip_legacy_review=self.agent_intelligence_enabled,
            effective_duration_by_task_ref=self._effective_duration_overrides(planning_input),
            **self._pipeline_kwargs
        )
        result = self._with_execution_task_order(result)
        execution_base_state = result.updated_state
        sequence = self._apply_execution_sequence(
            result,
            self._effective_duration_overrides(execution_base_state),
            deterministic_facts=self.agent_intelligence_enabled,
        )
        if not sequence.applied and sequence.warnings:
            safe_result = replace(
                result,
                allocation_plan=_without_unroutable_default_meals(
                    result.allocation_plan, load_execution_context(self.store)
                ),
            )
            if safe_result.allocation_plan is not result.allocation_plan:
                retried = self._apply_execution_sequence(
                    safe_result,
                    self._effective_duration_overrides(execution_base_state),
                    deterministic_facts=self.agent_intelligence_enabled,
                    deferred_task_refs=set(result.allocation_plan.planned_minutes_by_task) -
                    set(safe_result.allocation_plan.planned_minutes_by_task),
                )
                if retried.applied:
                    result = safe_result
                    sequence = retried
        movement_blocks = tuple(sequence.blocks)
        if sequence.applied:
            result = replace(
                result,
                updated_state=sequence.state,
                allocation_plan=sequence.allocation_plan,
                day_summary=summarize_day_plan(sequence.allocation_plan, sequence.state),
                warnings=result.warnings + sequence.warnings,
            )
            movement_data["active_blocks"] = movement_blocks
            movement_data["p4_execution_base_state"] = execution_base_state
        elif sequence.warnings:
            result = replace(
                result,
                allocation_plan=_without_unroutable_default_meals(
                    result.allocation_plan, load_execution_context(self.store)
                ),
                warnings=result.warnings + sequence.warnings,
            )
            movement_blocks = tuple(movement_data.get("active_blocks", ()) or ())
        concurrent = self._concurrent_execution(result.updated_state).allocations
        intelligence = None
        if self.agent_intelligence_enabled:
            try:
                result, movement_blocks, concurrent, intelligence = self._run_p5_intelligence(
                    result, movement_blocks, execution_base_state,
                    REFRESH_TEXT, "initial",
                )
            except Exception as exc:  # intelligence is non-critical
                logger.warning("P5 refresh intelligence degraded: %s", type(exc).__name__)
                self.store.pop(AGENT_INTELLIGENCE_KEY, None)
                self.store.pop(AGENT_CALL_TRACE_KEY, None)
        if intelligence is not None:
            companion = self._companion_from_intelligence(intelligence)
        else:
            companion, revised = self._make_companion(
                result.updated_state, result, movement_blocks, context_type=CONTEXT_REFRESH
            )
            if revised is not None:
                result = revised
        turn = P2SessionTurn(
            REFRESH_TEXT, result, (), (), key, movement_blocks=movement_blocks,
            companion_copy=companion,
            execution_context=load_execution_context(self.store),
            concurrent_allocations=tuple(concurrent),
            agent_intelligence=intelligence,
        )
        self._commit(key, turn, dedup_key=make_cache_key(REFRESH_TEXT, turn.result.updated_state))
        return turn

    # -- 只读访问 --

    def current_state(self) -> DayPlanningState:
        return self._current_state()

    def current_result(self) -> Optional[P2AgenticDayResult]:
        return self.store.get(RESULT_KEY)

    def last_turn(self) -> Optional[P2SessionTurn]:
        return self.store.get(LAST_TURN_KEY)

    def live_final_turn(self) -> Optional[LiveFinalTurn]:
        return load_live_final_turn(self.store)

    def _fork_with_store(self, store):
        """Clone controller dependencies without sharing mutable turn state."""
        return P2SessionController(
            store,
            self.caller,
            commitment_caller=self.commitment_caller,
            repair_caller=self.repair_caller,
            config=self.config,
            plan_caller=self._pipeline_kwargs.get("plan_caller"),
            review_caller=self._pipeline_kwargs.get("review_caller"),
            revision_caller=self._pipeline_kwargs.get("revision_caller"),
            reconciliation_caller=self._pipeline_kwargs.get("reconciliation_caller"),
            movement_handler=self.movement_handler,
            unified_handler=self.unified_handler,
            companion_caller=self.companion_caller,
            companion_enabled=self.companion_enabled,
            agent_intelligence_enabled=self.agent_intelligence_enabled,
            map_data=self.map_data,
            campus_id=self.campus_id,
        )

    def _run_feedback_turn(self, user_text: str, state: DayPlanningState, key: str) -> P2SessionTurn:
        """P2e/P3e：移动意图 -> 任务/固定安排 Router -> 相关 reconciler -> P2c 重规划。

        先由可注入的 movement_handler（Qwen 移动意图）判断是否包含移动需求；
        包含时本轮只处理移动（不路由任务/固定安排，避免串台），
        否则按 P2e 流程路由任务/固定安排并调用相关 reconciler。
        """
        commitment_questions = ()
        commitment_warnings = ()
        router_questions = ()
        router_warnings = ()
        movement_questions = ()
        movement_warnings = ()
        movement_plan = None
        movement_blocks = self.store.get(MOVEMENT_DATA_KEY, {}).get("active_blocks", ())
        planning_state = state
        movement_data = self.store.setdefault(MOVEMENT_DATA_KEY, {})
        before_blocks = tuple(movement_data.get("active_blocks", ()) or ())
        before_location = movement_data.get("current_location_text")
        previous_result = self.store.get(RESULT_KEY)
        before_allocation_plan = (
            getattr(previous_result, "allocation_plan", None)
            if previous_result is not None else None
        )

        def _companion_for(result, blocks):
            return self._make_companion(
                result.updated_state,
                result,
                blocks,
                context_type=CONTEXT_FEEDBACK,
                before_state=state,
                before_blocks=before_blocks,
                after_blocks=tuple(blocks or ()),
                before_location=before_location,
                after_location=movement_data.get("current_location_text"),
                before_allocation_plan=before_allocation_plan,
            )

        if self.unified_handler is not None:
            # P3e：统一反馈理解（task + commitment + movement 一次解析、分别执行）
            movement_data = self.store.setdefault(MOVEMENT_DATA_KEY, {})
            outcome = self.unified_handler(user_text, state, movement_data)
            # A direct current-location correction is an explicit user fact.
            # Do not lose it merely because the unified model classified the
            # rest of the feedback but omitted its optional current_location
            # field.  Resolution remains strictly selected-campus scoped.
            explicit_current = _explicit_current_location_feedback(user_text)
            if (
                explicit_current
                and not getattr(outcome, "current_location_text", None)
            ):
                outcome = replace(outcome, current_location_text=explicit_current)
            result = getattr(outcome, "result", None)
            if result is None:
                result = run_p2_agentic_day_planning(
                    state,
                    user_text,
                    self.caller,
                    skip_reconciliation=True,
                    effective_duration_by_task_ref=self._effective_duration_overrides(state),
                    **self._pipeline_kwargs,
                )
            outcome_blocks = getattr(outcome, "movement_blocks", ()) or ()
            if not isinstance(outcome_blocks, tuple):
                outcome_blocks = tuple(outcome_blocks)
            result, outcome_blocks = self._apply_feedback_current_location_correction(
                outcome, result, movement_data, outcome_blocks
            )
            companion, revised = _companion_for(result, outcome_blocks)
            if revised is not None:
                result = revised
            return P2SessionTurn(
                user_text,
                result,
                tuple(getattr(outcome, "commitment_questions", ()) or ()),
                tuple(getattr(outcome, "commitment_warnings", ()) or ()),
                key,
                (),
                (),
                movement_plan=getattr(outcome, "movement_plan", None),
                movement_blocks=outcome_blocks,
                movement_questions=tuple(getattr(outcome, "movement_questions", ()) or ()),
                movement_warnings=tuple(getattr(outcome, "movement_warnings", ()) or ()),
                companion_copy=companion,
                execution_context=load_execution_context(self.store),
            )

        if self.movement_handler is not None:
            movement_data = self.store.setdefault(MOVEMENT_DATA_KEY, {})
            outcome = self.movement_handler(user_text, state, movement_data)
            if outcome is not None:
                movement_questions = tuple(getattr(outcome, "questions", ()) or ())
                movement_warnings = tuple(getattr(outcome, "warnings", ()) or ())
                outcome_blocks = getattr(outcome, "blocks", ()) or ()
                if isinstance(outcome_blocks, tuple):
                    movement_blocks = outcome_blocks
                if getattr(outcome, "applied", False):
                    movement_result = getattr(outcome, "result", None)
                    if movement_result is not None:
                        movement_plan = getattr(outcome, "plan", None)
                        companion, revised = _companion_for(movement_result, movement_blocks)
                        if revised is not None:
                            movement_result = revised
                        return P2SessionTurn(
                            user_text,
                            movement_result,
                            (),
                            (),
                            key,
                            (),
                            (),
                            movement_plan=movement_plan,
                            movement_blocks=movement_blocks,
                            movement_questions=movement_questions,
                            movement_warnings=movement_warnings,
                            companion_copy=companion,
                        )
                if getattr(outcome, "intent_present", False):
                    # 已识别移动意图但未应用（地点不确定等）：不路由任务/固定安排，
                    # 只基于原 state 重新生成一份合法计划快照并透传待确认问题。
                    result = run_p2_agentic_day_planning(
                        state,
                        user_text,
                        self.caller,
                        skip_reconciliation=True,
                        effective_duration_by_task_ref=self._effective_duration_overrides(state),
                        **self._pipeline_kwargs,
                    )
                    companion, revised = _companion_for(result, movement_blocks)
                    if revised is not None:
                        result = revised
                    return P2SessionTurn(
                        user_text,
                        result,
                        (),
                        (),
                        key,
                        (),
                        (),
                        movement_blocks=movement_blocks,
                        movement_questions=movement_questions,
                        movement_warnings=movement_warnings,
                        companion_copy=companion,
                    )

        route, router_warning = route_feedback(user_text, state, self.caller, self.repair_caller)
        if router_warning is not None:
            router_warnings = (router_warning,)
        if route == ROUTE_UNKNOWN:
            router_questions = ("未能判断这条反馈属于任务还是固定安排，请补充说明。",)
        if route in (ROUTE_COMMITMENT, ROUTE_BOTH):
            proposal = self._call_commitment_proposal(state, user_text)
            if proposal is not None:
                applied = apply_commitment_reconciliation(
                    state,
                    proposal,
                    self.config.default_safety_buffer_minutes,
                    self.config.travel_minutes_by_commitment,
                    state.history,
                )
                commitment_questions = applied.questions
                commitment_warnings = applied.warnings
                if applied.applied_entries:
                    entry = "固定安排更新：{}。".format(";".join(applied.applied_entries))
                    planning_state = _with_history(applied.state, append_history(applied.state.history, entry))
                elif applied.state is not state:
                    planning_state = applied.state
            else:
                commitment_warnings = ("本轮固定安排反馈暂未结构化应用",)
        skip_task_reconciliation = route in (ROUTE_COMMITMENT, ROUTE_UNKNOWN)
        # P2 fallback routing can still receive a state whose windows were just
        # re-derived by commitment reconciliation.  Existing P3 blocks are hard
        # occupancy and must be replayed before the allocator sees that state.
        if movement_blocks:
            planning_state = reapply_movement_blocks(planning_state, movement_blocks)
        result = run_p2_agentic_day_planning(
            planning_state,
            user_text,
            self.caller,
            skip_reconciliation=skip_task_reconciliation,
            effective_duration_by_task_ref=self._effective_duration_overrides(planning_state),
            **self._pipeline_kwargs,
        )
        companion, revised = _companion_for(result, movement_blocks)
        if revised is not None:
            result = revised
        return P2SessionTurn(
            user_text, result, commitment_questions, commitment_warnings, key,
            router_questions, router_warnings,
            movement_blocks=movement_blocks,
            movement_questions=movement_questions,
            movement_warnings=movement_warnings,
            companion_copy=companion,
        )

    def _call_commitment_proposal(self, state: DayPlanningState, user_text: str):
        """Commitment Agent 最多 1 次调用 + 1 次格式 repair；失败返回 None。"""
        system, user = build_commitment_reconciliation_prompt(state, user_text)
        text = self.commitment_caller(system, user)
        try:
            return parse_commitment_reconciliation(text)
        except AgenticParseError:
            pass
        repair_system, repair_user = build_repair_prompt(
            "commitment_reconciliation", text, COMMITMENT_SCHEMA_VERSION
        )
        repaired = self.repair_caller(repair_system, repair_user)
        try:
            return parse_commitment_reconciliation(repaired)
        except AgenticParseError:
            return None

    def _cached(self, key: str) -> Optional[P2SessionTurn]:
        if self.store.get(LAST_CACHE_KEY) == key and LAST_TURN_KEY in self.store:
            return self.store[LAST_TURN_KEY]
        return None

    def _commit(self, key: str, turn: P2SessionTurn, dedup_key: Optional[str] = None) -> None:
        self.store[STATE_KEY] = turn.result.updated_state
        self.store[RESULT_KEY] = turn.result
        self.store[LAST_TURN_KEY] = turn
        self.store[LAST_CACHE_KEY] = dedup_key if dedup_key is not None else key

    def _current_state(self) -> DayPlanningState:
        if STATE_KEY not in self.store:
            raise RuntimeError("session has not started: call start_day first")
        return self.store[STATE_KEY]

    def _effective_duration_overrides(self, state):
        """Keep execution context at the session boundary, not inside P2b."""
        return effective_duration_overrides(load_execution_context(self.store), state)

    def _concurrent_execution(self, state):
        """Derive only user-authorized commitment-time work for this round."""
        return plan_explicit_concurrency(
            state,
            load_execution_context(self.store),
            map_data=self.map_data,
            effective_duration_by_task_ref=self._effective_duration_overrides(state),
        )

    def _concurrent_minutes(self, state):
        return concurrent_minutes_by_task(self._concurrent_execution(state).allocations)

    def _feedback_state_at_reference(self, state, reference_datetime):
        """Advance future capacity from the UI clock without inventing progress.

        Build privately; callers publish only after the normal final guards.
        A material/task-reported time later than the UI clock is not rewound.
        """
        from datetime import datetime
        if reference_datetime is None:
            return state
        if not isinstance(reference_datetime, datetime):
            raise TypeError("reference_datetime must be datetime or None")
        reference_datetime = reference_datetime.replace(second=0, microsecond=0)
        if reference_datetime <= state.now:
            return state
        if reference_datetime >= state.day_end:
            raise ValueError("当前时间已超出原方案的时间范围，请重新生成当天计划。")
        return derive_day_state(
            reference_datetime, state.day_end, state.commitments, state.tasks,
            default_safety_buffer_minutes=self.config.default_safety_buffer_minutes,
            travel_minutes_by_commitment=self.config.travel_minutes_by_commitment,
            history=state.history, reference_datetime=state.reference_datetime,
        )

    def _preferred_chunk_overrides(self, state):
        return preferred_chunk_overrides(load_execution_context(self.store), state)

    def _strategy_chunk_overrides(self, state, fragmentation_level):
        """Project a high-level strategy into bounded allocator preferences.

        This does not change task totals, minimum chunks, single-session
        semantics, or any hard interval.  It only gives deterministic
        candidates a real, reviewable difference in how much work they prefer
        to keep together.
        """
        values = dict(self._preferred_chunk_overrides(state))
        if fragmentation_level == "medium":
            return values
        context = load_execution_context(self.store)
        tasks = {item.task_ref: item for item in state.tasks}
        for binding in context.bindings:
            profile = binding.execution_profile
            task = tasks.get(binding.task_ref)
            if profile is None or task is None or not profile.splittable or not task.is_splittable:
                continue
            minimum = max(profile.minimum_chunk_minutes, task.minimum_slice_minutes or 1)
            if fragmentation_level == "high":
                values[binding.task_ref] = minimum
            elif fragmentation_level == "low":
                remaining = task.remaining_minutes
                if remaining is not None and remaining > 0:
                    values[binding.task_ref] = max(
                        minimum, remaining
                    )
        return values

    def _has_execution_semantics(self):
        context = load_execution_context(self.store)
        return any(
            item.activity_kind in ("generic", "meal")
            for item in context.bindings
        ) or bool(context.concurrency_authorizations)

    def _protected_meal_durations(self, state):
        return protected_meal_duration_overrides(load_execution_context(self.store), state)

    def _execution_earliest_starts(self, state):
        return earliest_start_overrides(load_execution_context(self.store), state)

    def _execution_latest_ends(self, state):
        return latest_end_overrides(load_execution_context(self.store), state)

    def _execution_preferred_starts(self, state):
        return preferred_start_overrides(load_execution_context(self.store), state)

    def _execution_task_order(self, state, fallback_order=None):
        """Prefer the persisted Day Intake order for executable activities.

        Bindings are created in the proposal's user order.  Once an execution
        context exists, that order is the authoritative sequence fact; an
        expression agent may still choose estimates but must not reverse an
        explicit “然后” chain to fill capacity.
        """
        context = load_execution_context(self.store)
        state_refs = [task.task_ref for task in state.tasks]
        bound = [item.task_ref for item in context.bindings if item.task_ref in state_refs]
        if bound:
            seen = set(bound)
            tail = [ref for ref in state_refs if ref not in seen]
            base_order = tuple(bound + tail)
        else:
            base_order = fallback_order or tuple(state_refs)
        return apply_feedback_task_order(state, base_order, load_feedback_decision(self.store))

    def _with_execution_task_order(self, result):
        """Replace only the deterministic allocation ordering at the boundary."""
        state = result.updated_state
        from src.p4_execution_movement import _split_windows_at_earliest_starts
        earliest = self._execution_earliest_starts(state)
        latest = self._execution_latest_ends(state)
        state = _split_windows_at_earliest_starts(state, earliest)
        fallback = result.day_plan_intent.task_order if result.day_plan_intent is not None else None
        order = self._execution_task_order(state, fallback)
        if order is None and not earliest and not latest:
            return result
        include_low = (
            result.day_plan_intent.include_low_attention
            if result.day_plan_intent is not None else False
        )
        plan = allocate_tasks_across_windows(
            state,
            task_order=order,
            include_low_attention=include_low,
            effective_duration_by_task_ref=self._effective_duration_overrides(state),
            protected_duration_by_task_ref=self._protected_meal_durations(state),
            earliest_start_by_task_ref=earliest,
            latest_end_by_task_ref=latest,
            preferred_chunk_by_task_ref=self._preferred_chunk_overrides(state),
            concurrent_minutes_by_task_ref=self._concurrent_minutes(state),
        )
        return replace(result, updated_state=state, allocation_plan=plan, day_summary=summarize_day_plan(plan, state))

    def _apply_feedback_current_location_correction(self, outcome, result, movement_data, outcome_blocks):
        """Confirm an unknown/legacy assumed origin after campus-scoped resolution.

        Unified feedback validates the text first. For an unconfirmed origin we
        rebuild from the saved pre-movement execution base, so old route
        occupancy cannot leak into the corrected plan.
        """
        text = getattr(outcome, "current_location_text", None)
        context = load_execution_context(self.store)
        if (
            not text
            or self.map_data is None
            or context.current_location.source.value not in ("unknown", "assumed")
        ):
            return result, outcome_blocks
        resolution = resolve_location(
            self.map_data, self._personal_location_text(text),
            self.caller, self.repair_caller,
        )
        if not resolution.usable or resolution.campus_id != self.campus_id:
            return result, outcome_blocks
        location = ExecutionLocation.from_resolution(
            resolution, ExecutionLocationSource.USER_CURRENT_LOCATION
        )
        corrected = replace_assumed_current_location(context, location)
        base_state = movement_data.get("p4_execution_base_state")
        if not isinstance(base_state, DayPlanningState):
            return result, outcome_blocks
        save_execution_context(self.store, corrected)
        overrides = effective_duration_overrides(corrected, base_state)
        result = self._with_execution_task_order(result)
        intent = result.day_plan_intent
        provisional_plan = allocate_tasks_across_windows(
            base_state,
            task_order=self._execution_task_order(base_state, intent.task_order if intent is not None else None),
            include_low_attention=intent.include_low_attention if intent is not None else False,
            effective_duration_by_task_ref=overrides,
            protected_duration_by_task_ref=self._protected_meal_durations(base_state),
            earliest_start_by_task_ref=self._execution_earliest_starts(base_state),
            preferred_chunk_by_task_ref=self._preferred_chunk_overrides(base_state),
            concurrent_minutes_by_task_ref=self._concurrent_minutes(base_state),
        )
        provisional = replace(
            result,
            original_state=base_state,
            updated_state=base_state,
            allocation_plan=provisional_plan,
            day_summary=summarize_day_plan(provisional_plan, base_state),
        )
        sequence = self._apply_execution_sequence(provisional, overrides)
        if not sequence.applied:
            # A resolved correction must never publish an old assumed route.
            save_execution_context(self.store, context)
            return result, outcome_blocks
        refreshed = replace(
            provisional,
            updated_state=sequence.state,
            allocation_plan=sequence.allocation_plan,
            day_summary=summarize_day_plan(sequence.allocation_plan, sequence.state),
            warnings=provisional.warnings + sequence.warnings,
        )
        movement_data["active_blocks"] = tuple(sequence.blocks)
        movement_data["p4_execution_base_state"] = base_state
        movement_data["current_location_text"] = text
        return refreshed, tuple(sequence.blocks)

    def _apply_execution_sequence(
        self, provisional_result, duration_overrides, task_order_override=None,
        deterministic_facts=False, deferred_task_refs=(),
    ):
        """P4a-b3: one provisional result, one formal deterministic allocation."""
        from src.p4_execution_movement import ExecutionMovementOutcome
        if self.map_data is None or self.campus_id is None:
            return ExecutionMovementOutcome(
                provisional_result.updated_state,
                provisional_result.allocation_plan,
                (), (), False,
            )
        context = load_execution_context(self.store)
        fact_caller = None if deterministic_facts else self.caller
        fact_repair_caller = None if deterministic_facts else self.repair_caller
        events = execution_timeline(
            provisional_result.updated_state,
            provisional_result.allocation_plan,
            context,
            self.map_data,
            fact_caller,
            fact_repair_caller,
        )
        assumed = assume_current_location_for_timeline(context, events)
        if assumed != context:
            save_execution_context(self.store, assumed)
            context = assumed
        intent = provisional_result.day_plan_intent
        earliest_starts = self._execution_earliest_starts(provisional_result.updated_state)
        protected_durations = self._protected_meal_durations(provisional_result.updated_state)
        # A route retry must not reintroduce the default meal just deferred
        # for lack of a feasible route. Keep its canonical task untouched,
        # but give this allocation candidate no eligible window for it.
        if deferred_task_refs:
            earliest_starts = dict(earliest_starts)
            protected_durations = dict(protected_durations)
            for ref in deferred_task_refs:
                earliest_starts[ref] = provisional_result.updated_state.day_end
                protected_durations.pop(ref, None)
        return apply_execution_sequence_movements(
            provisional_result.updated_state,
            provisional_result.allocation_plan,
            context,
            self.map_data,
            effective_duration_by_task_ref=duration_overrides,
            protected_duration_by_task_ref=protected_durations,
            earliest_start_by_task_ref=earliest_starts,
            preferred_start_by_task_ref=self._execution_preferred_starts(
                provisional_result.updated_state
            ),
            latest_end_by_task_ref=self._execution_latest_ends(
                provisional_result.updated_state
            ),
            preferred_chunk_by_task_ref=self._preferred_chunk_overrides(
                provisional_result.updated_state
            ),
            concurrent_minutes_by_task_ref=self._concurrent_minutes(
                provisional_result.updated_state
            ),
            task_order=(
                tuple(task_order_override)
                if task_order_override is not None
                else self._execution_task_order(
                    provisional_result.updated_state,
                    intent.task_order if intent is not None else None,
                )
            ),
            include_low_attention=intent.include_low_attention if intent is not None else False,
            # P5 strategy candidates must be reproducible program facts.
            # Existing baseline planning retains its historical bounded Qwen
            # travel estimate, while alternatives use the map-distance fallback
            # and exact selected-campus resolver only.
            time_caller=fact_caller,
            location_caller=fact_caller,
            location_repair_caller=fact_repair_caller,
        )


def _p5_review_semantic_facts(context, decision, flow):
    """Compact ref-bound semantics for Reviewer 2.0, without a second state."""
    return {
        "flow": flow,
        "tasks": [
            {
                "task_ref": item.task_ref,
                "activity_kind": item.activity_kind,
                "after_commitment_ref": item.after_commitment_ref,
                "before_commitment_ref": item.before_commitment_ref,
                "requires_single_session": item.requires_single_session,
            }
            for item in context.active_tasks
        ],
        "meals": [
            {
                "task_ref": item.task_ref,
                "period": item.period,
                "before_commitment_ref": item.before_commitment_ref,
                "after_commitment_ref": item.after_commitment_ref,
                "explicit_time": item.explicit_time,
            }
            for item in context.meals
        ],
        "commitments": [
            {
                "commitment_ref": item.commitment_ref,
                "starts_at": item.starts_at,
                "ends_at": item.ends_at,
            }
            for item in context.fixed_commitments
        ],
        "feedback_decision": (
            {
                "intent_type": decision.intent_type,
                "target_task_refs": list(decision.target_task_refs),
                "preferred_next_task_ref": decision.preferred_next_task_ref,
                "cancelled_task_refs": list(decision.cancelled_task_refs),
                "postponed_task_refs": list(decision.postponed_task_refs),
                "completed_task_refs": list(decision.completed_task_refs),
                "restored_task_refs": list(decision.restored_task_refs),
            }
            if decision is not None else None
        ),
    }


def _feedback_requires_unified_interpreter(decision):
    """Keep the legacy broad mutation pass only for semantics P5 cannot own.

    Ref-bound priority, ordering, lifecycle, location and concurrency decisions
    already have deterministic canonical adapters below.  Re-running the broad
    P3 interpreter for those intents duplicated semantic work and triggered a
    second Day Plan/Review inside its temporary controller.  ``mixed`` and
    ``no_change`` remain on the compatibility path because they may contain a
    commitment edit or a newly introduced task not representable by the
    lightweight FeedbackDecision schema.
    """
    if not isinstance(decision, FeedbackDecision):
        raise TypeError("decision must be FeedbackDecision")
    return decision.intent_type in ("mixed", "no_change")


def _state_fingerprint(state: DayPlanningState) -> str:
    commitments = "|".join(
        "{}~{}~{}~{}~{}".format(
            c.commitment_ref,
            c.title,
            _iso(c.starts_at),
            _iso(c.ends_at),
            c.availability_during.value,
        )
        for c in state.commitments
    )
    tasks = "|".join(
        "{}~{}~{}~{}~{}~{}~{}~{}".format(
            t.task_ref,
            t.title,
            _fmt(t.total_minutes),
            t.completed_minutes,
            t.state.value,
            _fmt(t.is_splittable),
            _fmt(t.minimum_slice_minutes),
            t.total_source.value if t.total_source is not None else "None",
        )
        for t in state.tasks
    )
    windows = "|".join(w.window_ref for w in state.windows)
    history = "|".join(state.history)
    return "#".join(
        (
            _iso(state.reference_datetime),
            _iso(state.now),
            _iso(state.day_end),
            commitments,
            tasks,
            windows,
            state.active_window_ref if state.active_window_ref is not None else "None",
            "|".join(state.unresolved_commitment_refs),
            history,
        )
    )


def _with_history(state: DayPlanningState, history: Tuple[str, ...]) -> DayPlanningState:
    return DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=state.tasks,
        windows=state.windows,
        active_window_ref=state.active_window_ref,
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=history,
    )


def _iso(value) -> str:
    return "" if value is None else value.isoformat()


def _explicit_current_location_feedback(user_text: str) -> Optional[str]:
    """Extract only unambiguous user assertions such as “其实我现在在31斋”."""
    if not isinstance(user_text, str):
        return None
    match = re.search(
        r"(?:其实\s*)?我(?:现在)?(?:已经)?\s*(?:在|到)\s*([^，。！？!?；;,\n\"{}]+)",
        user_text.strip(),
    )
    if match is None:
        return None
    candidate = match.group(1).strip()
    return candidate or None


def _validate_what_if_candidate(intent, bundle):
    """A legal but unchanged/omitted target is not a successful simulation."""
    intervals = _allocation_intervals(bundle.state, bundle.result.allocation_plan)
    for change in intent.changes:
        if change.kind == "ordering":
            left = intervals.get(change.task_ref, ())
            right = intervals.get(change.related_task_ref, ())
            if not left or not right or max(end for _, end in left) > min(start for start, _ in right):
                raise WhatIfInfeasibleError("requested ordering unavailable")
        elif change.kind == "prefer_task":
            if change.task_ref not in intervals:
                raise WhatIfInfeasibleError("preferred task unavailable")
        elif change.kind == "authorize_concurrency":
            if not any(x.task_ref == change.task_ref and x.commitment_ref == change.commitment_ref
                       for x in bundle.concurrent_allocations):
                raise WhatIfInfeasibleError("requested concurrency unavailable")


def _applied_what_if_narrative(previous, preview, state, context):
    """Describe an adopted hypothetical using only its verified ref changes."""
    if not isinstance(previous, GroundedNarrative):
        raise TypeError("what-if candidate requires grounded narrative")
    tasks = {item.task_ref: item.title for item in state.tasks}
    messages = []
    for change in preview.intent.changes:
        title = tasks.get(change.task_ref, "这项任务")
        related = tasks.get(change.related_task_ref, "另一项任务")
        if change.kind == "ordering":
            messages.append("已按你的调整，把{}排在{}之前。".format(title, related))
        elif change.kind == "prefer_task":
            messages.append("已按你的调整，优先安排{}。".format(title))
        elif change.kind == "cancel_task":
            messages.append("已按你的调整，本轮不再安排{}。".format(title))
        elif change.kind == "postpone_task":
            messages.append("已按你的调整，把{}留到之后再安排。".format(title))
        elif change.kind == "authorize_concurrency":
            messages.append("已按你的调整，保留{}的并行安排。".format(title))
        elif change.kind == "revoke_concurrency":
            messages.append("已按你的调整，取消{}的并行安排。".format(title))
    opening_parts = [messages[0] if messages else "已采用刚才预览的调整。"]
    details = messages[1:]
    # ``important_impact`` is calculated by comparing the preview candidate
    # against the baseline commitment facts, not by a language model.
    if preview.important_impact:
        opening_parts.append(preview.important_impact + "。")
    opening = " ".join(opening_parts)
    # The right card keeps the candidate's already checked explanation and at
    # most one approved suggestion.  Commitment stability belongs in the top
    # change receipt, not as the sole explanation of the current plan.
    why = previous.why_this_plan
    if why is None or not any(
        item.title in why for item in context.active_tasks if item.state == "active"
    ):
        why = observable_plan_note(context)
    if details:
        why = " ".join(details + ([why] if why else []))
    return GroundedNarrative(
        opening, why, previous.closing,
        proactive_suggestion=previous.proactive_suggestion,
        risk_note=None, generated=False,
    )


def _feedback_decision_from_what_if(intent):
    """Translate Qwen's ref-bound hypothetical into ordinary constraints.

    This adapter performs no language understanding and no feasibility
    judgement; those belong to the interpreter and the cloned planner.
    """
    if not isinstance(intent, WhatIfIntent):
        raise TypeError("intent must be WhatIfIntent")
    preferred = None
    cancelled = []
    postponed = []
    ordering = []
    concurrency = []
    location = None
    targets = []
    for change in intent.changes:
        if change.task_ref is not None and change.task_ref not in targets:
            targets.append(change.task_ref)
        if change.related_task_ref is not None and change.related_task_ref not in targets:
            targets.append(change.related_task_ref)
        if change.kind == "prefer_task":
            preferred = change.task_ref
        elif change.kind == "cancel_task":
            cancelled.append(change.task_ref)
        elif change.kind == "postpone_task":
            postponed.append(change.task_ref)
        elif change.kind == "ordering":
            if change.task_ref is None or change.related_task_ref is None:
                raise ValueError("what-if ordering requires two task refs")
            ordering.append(TaskOrderingConstraint(
                change.task_ref, change.related_task_ref
            ))
        elif change.kind == "location_change":
            if change.location_text is None:
                raise ValueError("what-if location change requires location_text")
            location = change.location_text
        elif change.kind in ("authorize_concurrency", "revoke_concurrency"):
            if change.task_ref is None or change.commitment_ref is None:
                raise ValueError("what-if concurrency requires task/commitment refs")
            if change.kind == "authorize_concurrency" and change.requested_minutes is None:
                raise ValueError("what-if concurrency authorization requires duration")
            concurrency.append(ConcurrencyDecision(
                action="authorize" if change.kind == "authorize_concurrency" else "revoke",
                task_ref=change.task_ref,
                commitment_ref=change.commitment_ref,
                requested_minutes=(
                    change.requested_minutes
                    if change.kind == "authorize_concurrency" else None
                ),
                source="explicit_user_request",
            ))
    return FeedbackDecision(
        intent_type="mixed" if len(intent.changes) > 1 else "what_if",
        target_task_refs=tuple(targets),
        preferred_next_task_ref=preferred,
        ordering_constraints=tuple(ordering),
        cancelled_task_refs=tuple(ref for ref in cancelled if ref is not None),
        postponed_task_refs=tuple(ref for ref in postponed if ref is not None),
        concurrency_changes=tuple(concurrency),
        location_correction=location,
        explicit_user_preference=True,
        confidence=1.0,
        clarification_needed=intent.clarification_needed,
        clarification_question=intent.clarification_question,
    )
def _fmt(value) -> str:
    return "None" if value is None else str(value)


def _require_state(state: DayPlanningState) -> None:
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")

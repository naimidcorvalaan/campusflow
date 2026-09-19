"""P5 intelligence orchestration over the existing deterministic planner."""

from dataclasses import dataclass
from typing import Optional, Tuple

from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import AgentCallTrace
from src.p5_candidate import (
    CandidateGenerationResult,
    DeterministicPlanCandidate,
    generate_deterministic_candidates,
)
from src.p5_copy_guard import (
    GroundedNarrative,
    generate_grounded_narrative,
    grounded_copy_fallback,
)
from src.p5_intent_reviewer import (
    IntentComplianceReviewV2,
    deterministic_intent_review,
    review_candidate_intent,
)
from src.p5_plan_judge import PlanJudgeDecision, deterministic_judge, judge_candidates
from src.p5_plan_strategy import (
    PlanStrategy,
    baseline_plan_strategy,
    generate_plan_strategies,
)
from src.p5_proactive_suggestion import ProactiveSuggestion, propose_suggestion
from src.p5_situation_analyst import SituationAnalysis, analyze_situation
from src.p5_smart_clarification import SmartClarification, choose_smart_clarification

AGENT_INTELLIGENCE_KEY = "p5_agent_intelligence"
AGENT_CALL_TRACE_KEY = "p5_agent_call_trace"
LATEST_USER_TEXT_KEY = "p5_latest_user_text"
LATEST_FEEDBACK_TEXT_KEY = "p5_latest_feedback_text"


@dataclass(frozen=True)
class AgentIntelligenceResult:
    context: AgentDecisionContext
    situation: SituationAnalysis
    strategies: Tuple[PlanStrategy, ...]
    candidates: Tuple[DeterministicPlanCandidate, ...]
    judge: PlanJudgeDecision
    review: IntentComplianceReviewV2
    selected_candidate: DeterministicPlanCandidate
    clarification: SmartClarification
    suggestion: ProactiveSuggestion
    narrative: GroundedNarrative
    trace: AgentCallTrace
    controlled_repair_used: bool = False

    def __post_init__(self):
        ids = {item.candidate_id for item in self.candidates}
        if self.selected_candidate.candidate_id not in ids:
            raise ValueError("selected candidate is not in candidate set")
        if self.judge.selected_candidate_id != self.selected_candidate.candidate_id and not self.controlled_repair_used:
            raise ValueError("judge/candidate mismatch without controlled repair")


def run_agent_intelligence(
    context,
    caller,
    candidate_builder,
    candidate_validator=None,
    repair_caller=None,
    latest_user_text=None,
    extracted_semantics=None,
    deterministic_facts=None,
    flow="initial",
    candidate_repairer=None,
    suggestion_feasibility_checker=None,
    expression_context_builder=None,
):
    if not isinstance(context, AgentDecisionContext):
        raise TypeError("context invalid")
    trace = AgentCallTrace()
    situation, trace = analyze_situation(context, caller, repair_caller, trace)
    strategies, trace = generate_plan_strategies(
        context, situation, caller, repair_caller, trace
    )
    # Candidate 01 is always the current hard-valid deterministic plan.  This
    # makes graceful degradation explicit and prevents a Qwen strategy label
    # from being attached to a plan that was not generated from that strategy.
    baseline = baseline_plan_strategy(context)
    strategies = (baseline,) + tuple(
        item for item in strategies if item.strategy_id != baseline.strategy_id
    )[:3]
    generated = generate_deterministic_candidates(
        strategies, candidate_builder, candidate_validator
    )
    if not generated.candidates:
        # The existing deterministic plan must be supplied as a viable
        # strategy candidate by the caller.  No model can waive this guard.
        raise ValueError("P5 produced no deterministic feasible candidate")
    summaries = tuple(item.summary for item in generated.candidates)
    judge, trace = judge_candidates(
        context, situation, summaries, caller, repair_caller, trace
    )
    selected = next(
        item for item in generated.candidates
        if item.candidate_id == judge.selected_candidate_id
    )
    _require_candidate_valid(selected, candidate_validator)
    review, trace = review_candidate_intent(
        context, latest_user_text, extracted_semantics, situation,
        selected.summary, deterministic_facts or {}, caller, repair_caller,
        trace, flow=flow,
    )
    repaired = False
    if review.decision == "reject":
        repaired_candidate = None
        if callable(candidate_repairer):
            repaired_candidate = candidate_repairer(
                selected, review.repair_directives
            )
        if repaired_candidate is None:
            # Bounded deterministic repair: select another already-validated
            # candidate that satisfies objective user-preference metadata.
            repaired_candidate = next(
                (
                    item for item in generated.candidates
                    if item.candidate_id != selected.candidate_id
                    and item.summary.user_preference_satisfied
                ),
                None,
            )
        if repaired_candidate is not None:
            selected = repaired_candidate
            repaired = True
            _require_candidate_valid(selected, candidate_validator)
            # A bounded repair is still not a waiver.  Re-check the actual
            # replacement candidate against ref-bound deterministic intent
            # facts before it can become the published selection.
            review = deterministic_intent_review(context, selected.summary)
            if review.decision != "approve":
                raise ValueError(
                    "intent repair did not produce a compliant candidate"
                )
        else:
            # A model may reject while asking for a condition the selected
            # hard-valid plan already satisfies (for example, prefer X when X
            # is already first).  Treat the deterministic re-check as a
            # bounded no-op repair.  A genuinely unmet objective still stays
            # rejected and is surfaced through clarification/fallback rather
            # than causing an unbounded model loop.
            objective_review = deterministic_intent_review(
                context, selected.summary
            )
            if objective_review.decision == "approve":
                repaired = True
                review = objective_review
            else:
                raise ValueError(
                    "intent reviewer rejected every hard-valid candidate"
                )

    # Expression is deliberately grounded in the *selected* candidate, not
    # the baseline supplied to the strategy/judge stages.  This matters when
    # a strategy changes ordering: a suggestion or question must describe the
    # same timeline that will eventually be published.
    expression_context = context
    if callable(expression_context_builder):
        built_context = expression_context_builder(selected)
        if not isinstance(built_context, AgentDecisionContext):
            raise TypeError("expression_context_builder must return AgentDecisionContext")
        expression_context = built_context

    # What-if expression is prepared while its candidate is still isolated.
    # Adoption can then publish that exact checked copy with zero model calls
    # and without re-running strategy selection or allocation.
    if expression_context.unresolved_confirmations:
        clarification, trace = choose_smart_clarification(
            expression_context, situation, caller, repair_caller, trace
        )
    else:
        clarification = SmartClarification(False, generated=False)

    if _suggestion_may_have_value(expression_context, selected):
        suggestion, trace = propose_suggestion(
            expression_context, situation, selected.summary, caller, repair_caller,
            trace, feasibility_checker=suggestion_feasibility_checker,
        )
    else:
        suggestion = ProactiveSuggestion(False, generated=False)
    narrative, trace = generate_grounded_narrative(
        expression_context, selected.summary, latest_user_text, caller, repair_caller,
        trace,
        approved_suggestion=(
            suggestion.text if suggestion.should_show else None
        ),
        actual_tasks=getattr(getattr(selected.result, "updated_state", None), "tasks", ()),
    )
    return AgentIntelligenceResult(
        expression_context, situation, tuple(strategies), tuple(generated.candidates),
        judge, review, selected, clarification, suggestion, narrative, trace,
        controlled_repair_used=repaired,
    )


def load_agent_intelligence(store):
    value = store.get(AGENT_INTELLIGENCE_KEY)
    return value if isinstance(value, AgentIntelligenceResult) else None


def save_agent_intelligence(store, value):
    if not isinstance(value, AgentIntelligenceResult):
        raise TypeError("value must be AgentIntelligenceResult")
    store[AGENT_INTELLIGENCE_KEY] = value
    store[AGENT_CALL_TRACE_KEY] = value.trace


def _suggestion_may_have_value(context, candidate):
    if not context.active_tasks:
        return False
    if any(key == "concurrency_suggestion_preference" and value == "suppress" for key, value in context.day_preferences):
        return False
    return bool(
        any(minutes > 0 for _, minutes in candidate.summary.remaining_work)
        or (context.fixed_commitments and context.active_tasks)
    )


def _require_candidate_valid(candidate, validator):
    """Revalidate the exact post-Judge/post-repair object before publishing."""
    if validator is None:
        return
    errors = tuple(validator(candidate) or ())
    if errors:
        raise ValueError("selected P5 candidate failed deterministic revalidation")

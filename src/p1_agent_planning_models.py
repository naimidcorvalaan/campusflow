"""Small, tolerant models for the P1 Agent fallback channel.

These models are deliberately separate from the strict P1a-P1h domain models.
They contain only information needed to render a useful tentative suggestion.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p1_view_models import P1ResultView
from src.p1_planning_diagnostics import SafeDiagnostic


class AgentPlanStatus(str, Enum):
    PROPOSED = "proposed"
    NEEDS_INFORMATION = "needs_information"
    NO_SUGGESTION = "no_suggestion"
    FAILED = "failed"


class ReviewDecision(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"


@dataclass(frozen=True)
class AgentPlanStep:
    title: str
    minutes: int
    location_text: Optional[str] = None
    timing_note: Optional[str] = None


@dataclass(frozen=True)
class AgentPlanDraft:
    status: AgentPlanStatus
    headline: Optional[str]
    plan_text: Optional[str]
    steps: Tuple[AgentPlanStep, ...]
    estimated_walking_minutes_min: Optional[int]
    estimated_walking_minutes_max: Optional[int]
    assumptions: Tuple[str, ...]
    warnings: Tuple[str, ...]
    question: Optional[str]
    quick_options: Tuple[str, ...]


def has_actionable_plan(draft):
    """A title, notice or question alone is never an executable plan."""
    return bool(draft is not None and (draft.steps or draft.plan_text))


@dataclass(frozen=True)
class AgentReview:
    decision: ReviewDecision
    feedback: Optional[str]


@dataclass(frozen=True)
class P1AgentPlanningResult:
    status: AgentPlanStatus
    draft: Optional[AgentPlanDraft]
    view: Optional[P1ResultView]
    call_count: int
    revised: bool
    safe_summary: str
    diagnostics: Tuple[SafeDiagnostic, ...] = ()
    review_status: Optional[ReviewDecision] = None
    review_feedback: Optional[str] = None
    is_tentative: bool = True
    route_estimate_notice: Optional[str] = None


@dataclass(frozen=True)
class P1DualPlanningResult:
    view: P1ResultView
    structured_result: object
    agent_result: Optional[P1AgentPlanningResult]
    used_agent_fallback: bool
    structured_call_count: int
    total_model_calls: int
    diagnostics: Tuple[SafeDiagnostic, ...] = ()

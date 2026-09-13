"""P1h 完整离线规划控制器的统一结果模型（Python 3.8 兼容）。"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p1_candidate_models import ProposedCandidate
from src.p1_context_bundle import P1ContextBundle, SourcedField
from src.p1_intake_pipeline import P1IntakeResult
from src.p1_recommendation_models import P1RecommendationResult, QuickAction
from src.p1_route_models import CandidateRouteValidation


class PlanningStatus(str, Enum):
    FINAL_CANDIDATE = "final_candidate"
    TENTATIVE_CANDIDATE = "tentative_candidate"
    PARTIAL_EXTRACTION = "partial_extraction"
    EXTRACTION_FAILED = "extraction_failed"
    NO_EXECUTABLE_TASKS = "no_executable_tasks"
    CANDIDATE_FAILED = "candidate_failed"
    CANDIDATES_INFEASIBLE = "candidates_infeasible"
    NEEDS_INFORMATION = "needs_information"


@dataclass(frozen=True)
class PlanningCallSummary:
    task_attempts: int
    window_attempts: int
    candidate_calls: int
    candidate_regenerated: bool


@dataclass(frozen=True)
class P1PlanningResult:
    status: PlanningStatus
    intake_result: P1IntakeResult
    context_bundle: P1ContextBundle
    recommendation_result: Optional[P1RecommendationResult]
    final_candidate: Optional[ProposedCandidate]
    final_route_validation: Optional[CandidateRouteValidation]
    tentative_candidate: Optional[ProposedCandidate]
    is_tentative: bool
    primary_question: Optional[str]
    other_questions: Tuple[str, ...]
    ai_estimated_fields: Tuple[SourcedField, ...]
    call_summary: PlanningCallSummary
    safe_summary: str
    quick_actions: Tuple[QuickAction, ...]

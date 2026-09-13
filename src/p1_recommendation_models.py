"""P1g 离线候选编排结果模型，兼容 Python 3.8。"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p1_candidate_extraction_models import P1CandidateParseResult
from src.p1_candidate_models import ProposedCandidate
from src.p1_route_models import CandidateRouteValidation, CandidateSelectionResult


class RecommendationStatus(str, Enum):
    SELECTED_PRIMARY = "selected_primary"
    SELECTED_ALTERNATIVE = "selected_alternative"
    REGENERATED_SUCCESS = "regenerated_success"
    NEEDS_INFORMATION = "needs_information"
    NO_EXECUTABLE_TASKS = "no_executable_tasks"
    BOTH_CANDIDATES_INFEASIBLE = "both_candidates_infeasible"
    CANDIDATE_PARSE_FAILED = "candidate_parse_failed"
    CALL_FAILED = "call_failed"


class SecondCallReason(str, Enum):
    SCHEMA_REPAIR = "schema_repair"
    ROUTE_REGENERATION = "route_regeneration"


class QuickAction(str, Enum):
    EXTEND_TIME = "extend_time"
    CHANGE_TASK = "change_task"
    UPDATE_LOCATION = "update_location"


@dataclass(frozen=True)
class P1RecommendationResult:
    status: RecommendationStatus
    first_candidate_result: P1CandidateParseResult
    first_route_selection: Optional[CandidateSelectionResult]
    second_candidate_result: Optional[P1CandidateParseResult]
    second_route_selection: Optional[CandidateSelectionResult]
    final_candidate: Optional[ProposedCandidate]
    final_route_validation: Optional[CandidateRouteValidation]
    call_count: int
    regenerated: bool
    second_call_reason: Optional[SecondCallReason]
    safe_summary: str
    quick_actions: Tuple[QuickAction, ...]
    supplement_question: Optional[str] = None

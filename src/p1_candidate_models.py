"""P1e 候选方案领域模型；路线验证仍留在后续切片。"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p1_context_bundle import SourcedField


CANDIDATE_SCHEMA_VERSION = "p1.candidate.v1"
MAX_CANDIDATE_STEPS = 8


class CandidateDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    MISSING_INFORMATION = "missing_information"
    NO_EXECUTABLE_TASKS = "no_executable_tasks"


class ExecutionContext(str, Enum):
    FREE_WINDOW = "free_window"
    COMMITMENT = "commitment"


@dataclass(frozen=True)
class CandidateStep:
    task_ref: str
    planned_minutes: int
    execution_context: ExecutionContext
    commitment_ref: Optional[str]


@dataclass(frozen=True)
class ProposedCandidate:
    candidate_ref: str
    steps: Tuple[CandidateStep, ...]
    rationale: str
    assumptions: Tuple[str, ...]
    warnings: Tuple[str, ...]
    uses_ai_estimates: bool
    requires_route_validation: bool
    is_tentative: bool
    ai_estimate_fields: Tuple[SourcedField, ...]


@dataclass(frozen=True)
class CandidateDocument:
    decision_status: CandidateDecisionStatus
    primary_candidate: Optional[ProposedCandidate]
    alternative_candidate: Optional[ProposedCandidate]
    safe_summary: str

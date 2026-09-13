"""P1f 的确定性路线验证结果模型（Python 3.8 兼容）。"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from src.p1_candidate_models import ProposedCandidate


class RouteDataTrust(str, Enum):
    SYNTHETIC_TEST = "synthetic_test"
    ESTIMATED_UNVERIFIED = "estimated_unverified"
    REAL_VERIFIED = "real_verified"


class LocationResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    DATA_INSUFFICIENT = "data_insufficient"


class ProviderRouteStatus(str, Enum):
    COMPUTED = "computed"
    UNREACHABLE = "unreachable"
    DATA_INSUFFICIENT = "data_insufficient"


class CandidateValidationStatus(str, Enum):
    FEASIBLE = "feasible"
    TIME_INFEASIBLE = "time_infeasible"
    LOCATION_UNRESOLVED = "location_unresolved"
    ROUTE_UNREACHABLE = "route_unreachable"
    ROUTE_DATA_INSUFFICIENT = "route_data_insufficient"
    INSUFFICIENT_TIME_BOUNDARY = "insufficient_time_boundary"


@dataclass(frozen=True)
class LocationResolution:
    status: LocationResolutionStatus
    location_id: Optional[str]
    display_name: Optional[str]


@dataclass(frozen=True)
class ProviderRoute:
    status: ProviderRouteStatus
    walking_minutes: Optional[int]
    path: Tuple[str, ...]


@dataclass(frozen=True)
class RouteSegment:
    start_location: str
    end_location: str
    walking_minutes: int
    path: Tuple[str, ...]


@dataclass(frozen=True)
class CandidateRouteValidation:
    candidate: ProposedCandidate
    status: CandidateValidationStatus
    route_data_trust: RouteDataTrust
    total_walking_minutes: Optional[int]
    total_candidate_task_minutes: int
    free_window_task_minutes: int
    expected_finish_at: Optional[datetime]
    segments: Tuple[RouteSegment, ...]
    safe_reason: str
    supplement_target_ref: Optional[str] = None
    supplement_field_name: Optional[str] = None
    supplement_target_label: Optional[str] = None

    @property
    def is_feasible(self) -> bool:
        return self.status is CandidateValidationStatus.FEASIBLE


@dataclass(frozen=True)
class CandidateSelectionResult:
    primary_validation: Optional[CandidateRouteValidation]
    alternative_validation: Optional[CandidateRouteValidation]
    selected_candidate: Optional[ProposedCandidate]
    selected_validation: Optional[CandidateRouteValidation]
    needs_regeneration: bool
    supplement_question: Optional[str]
    safe_summary: str

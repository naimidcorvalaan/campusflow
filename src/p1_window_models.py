"""P1b 单窗口与固定安排的隔离领域模型（Python 3.8 兼容）。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Optional, Tuple

from src.p1_models import FieldEvidence, SourceKind


WINDOW_SCHEMA_VERSION = "p1.window-context.v1"
MAX_COMMITMENTS = 12
MAX_WINDOW_QUESTIONS = 10
MAX_WINDOW_TEXT_LENGTH = 4000
MAX_WINDOW_OUTPUT_LENGTH = 50000
MAX_WINDOW_REF_LENGTH = 64
MAX_WINDOW_TITLE_LENGTH = 100
MAX_WINDOW_FRAGMENT_LENGTH = 500
MAX_WINDOW_LOCATION_LENGTH = 200
MAX_WINDOW_EXPLANATION_LENGTH = 200
MAX_WINDOW_MINUTES = 7 * 24 * 60
DEFAULT_SAFETY_BUFFER_MINUTES = 10


class AvailabilityLevel(str, Enum):
    UNAVAILABLE = "unavailable"
    LOW_ATTENTION = "low_attention"
    FULLY_AVAILABLE = "fully_available"


class BoundaryKind(str, Enum):
    NEXT_COMMITMENT = "next_commitment"
    FREE_DURATION = "free_duration"
    ENDS_AT = "ends_at"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CurrentContext:
    reference_datetime: datetime
    current_datetime: datetime
    current_location_text: Optional[str]
    field_evidence: Dict[str, FieldEvidence]
    needs_confirmation: Tuple[str, ...]


@dataclass(frozen=True)
class FixedCommitment:
    commitment_ref: str
    title: str
    original_text: str
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    location_text: Optional[str]
    availability_during: AvailabilityLevel
    field_evidence: Dict[str, FieldEvidence]
    needs_confirmation: Tuple[str, ...]
    # P3e campus-course semantic; None means use deterministic default when
    # this commitment is classified as a class.  Kept separate from buffers.
    commitment_kind: Optional[str] = None
    class_arrival_lead_minutes: Optional[int] = None


@dataclass(frozen=True)
class WindowConstraints:
    free_duration_minutes: Optional[int]
    ends_at: Optional[datetime]
    safety_buffer_minutes: int
    field_evidence: Dict[str, FieldEvidence]
    needs_confirmation: Tuple[str, ...]


@dataclass(frozen=True)
class WindowClarificationQuestion:
    question_id: str
    target_ref: str
    field_name: str
    question: str
    blocking: bool
    quick_options: Tuple[str, ...]


@dataclass(frozen=True)
class WindowContextDocument:
    schema_version: str
    original_user_input: str
    current_context: CurrentContext
    commitments: Tuple[FixedCommitment, ...]
    constraints: WindowConstraints
    clarification_questions: Tuple[WindowClarificationQuestion, ...]

    @property
    def earliest_known_commitment(self) -> Optional[FixedCommitment]:
        candidates = [item for item in self.commitments if item.starts_at is not None]
        return min(candidates, key=lambda item: item.starts_at) if candidates else None

    @property
    def has_unresolved_commitment_start(self) -> bool:
        return any(item.starts_at is None for item in self.commitments)

    @property
    def next_commitment_is_confirmed(self) -> bool:
        return self.earliest_known_commitment is not None and not self.has_unresolved_commitment_start

    @property
    def boundary_kinds(self) -> Tuple[BoundaryKind, ...]:
        kinds = []
        if self.earliest_known_commitment is not None:
            kinds.append(BoundaryKind.NEXT_COMMITMENT)
        if self.constraints.free_duration_minutes is not None:
            kinds.append(BoundaryKind.FREE_DURATION)
        if self.constraints.ends_at is not None:
            kinds.append(BoundaryKind.ENDS_AT)
        return tuple(kinds) if kinds else (BoundaryKind.UNKNOWN,)

    @property
    def nominal_window_end(self) -> Optional[datetime]:
        ends = []
        if self.earliest_known_commitment is not None:
            ends.append(self.earliest_known_commitment.starts_at)
        if self.constraints.free_duration_minutes is not None:
            ends.append(self.current_context.current_datetime + timedelta(minutes=self.constraints.free_duration_minutes))
        if self.constraints.ends_at is not None:
            ends.append(self.constraints.ends_at)
        return min(ends) if ends else None

    @property
    def arrival_deadline(self) -> Optional[datetime]:
        if not self.next_commitment_is_confirmed:
            return None
        return self.earliest_known_commitment.starts_at - timedelta(minutes=self.constraints.safety_buffer_minutes)

    @property
    def has_time_boundary(self) -> bool:
        return self.nominal_window_end is not None

    @property
    def effective_time_constraint(self) -> Optional[datetime]:
        ends = []
        if self.constraints.free_duration_minutes is not None:
            ends.append(self.current_context.current_datetime + timedelta(minutes=self.constraints.free_duration_minutes))
        if self.constraints.ends_at is not None:
            ends.append(self.constraints.ends_at)
        if self.arrival_deadline is not None:
            ends.append(self.arrival_deadline)
        return min(ends) if ends else None

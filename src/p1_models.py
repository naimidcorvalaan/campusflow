"""P1 首个切片的任务理解领域模型。

本模块只描述任意任务的通用特征、字段来源和待确认问题，不包含路线、
时间窗口、候选方案、任务进度或生命周期更新。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple


SCHEMA_VERSION = "p1.task-understanding.v1"

MAX_TASKS = 8
MAX_QUESTIONS = 8
MAX_REQUIREMENTS_PER_FIELD = 8
MAX_QUICK_OPTIONS = 5
MAX_USER_TEXT_LENGTH = 4000
MAX_MODEL_OUTPUT_LENGTH = 50000
MAX_TASK_REF_LENGTH = 64
MAX_TITLE_LENGTH = 100
MAX_ORIGINAL_FRAGMENT_LENGTH = 500
MAX_REQUIREMENT_TEXT_LENGTH = 100
MAX_LOCATION_TEXT_LENGTH = 200
MAX_EXPLANATION_LENGTH = 200
MAX_QUESTION_LENGTH = 200
MAX_QUICK_OPTION_LENGTH = 80
MAX_DURATION_MINUTES = 7 * 24 * 60


class SourceKind(str, Enum):
    """决策关键字段的来源。"""

    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    SYSTEM_DEFAULT = "system_default"
    SYSTEM_VERIFIED = "system_verified"
    AI_EXTRACTED_FROM_USER_TEXT = "ai_extracted_from_user_text"
    AI_ESTIMATED = "ai_estimated"


class LocationRequirement(str, Enum):
    """任务对特定地点的通用要求，不是现实任务类型。"""

    NO_SPECIFIC_LOCATION = "no_specific_location"
    SPECIFIC_LOCATION = "specific_location"
    LOCATION_REQUIREMENT_UNKNOWN = "location_requirement_unknown"


class AttentionLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class FieldEvidence:
    source: SourceKind
    explanation: Optional[str] = None


@dataclass(frozen=True)
class TaskUnderstandingFeatures:
    location_requirement: LocationRequirement
    location_text: Optional[str]
    environment_requirements: Tuple[str, ...]
    equipment_requirements: Tuple[str, ...]
    estimated_total_minutes: Optional[int]
    is_splittable: Optional[bool]
    minimum_slice_minutes: Optional[int]
    attention_required: Optional[AttentionLevel]
    interruption_allowed: Optional[bool]
    may_have_open_hours: Optional[bool]


@dataclass(frozen=True)
class TaskUnderstanding:
    """一张可展示、可在后续切片中确认或修正的任务理解结果。"""

    task_ref: str
    title: str
    original_text: str
    features: TaskUnderstandingFeatures
    field_evidence: Dict[str, FieldEvidence]
    needs_confirmation: Tuple[str, ...]


@dataclass(frozen=True)
class ClarificationQuestion:
    question_id: str
    task_ref: str
    field_name: str
    question: str
    blocking_for_task_understanding: bool
    quick_options: Tuple[str, ...]


@dataclass(frozen=True)
class TaskUnderstandingDocument:
    schema_version: str
    original_user_input: str
    tasks: Tuple[TaskUnderstanding, ...]
    clarification_questions: Tuple[ClarificationQuestion, ...]

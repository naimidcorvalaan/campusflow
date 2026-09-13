"""P1i 的只读结构化中文展示模型（Python 3.8 兼容）。"""
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p1_planning_diagnostics import SafeDiagnostic


@dataclass(frozen=True)
class QuickOptionView:
    label: str
    action: str
    value: Optional[str] = None
    target_ref: Optional[str] = None
    field_name: Optional[str] = None
    target_label: Optional[str] = None


@dataclass(frozen=True)
class QuestionView:
    text: str
    quick_options: Tuple[QuickOptionView, ...]


@dataclass(frozen=True)
class NoticeView:
    message: str
    level: str


@dataclass(frozen=True)
class DiagnosticView:
    stage_label: str
    status_label: str
    attempts: int
    call_count: int
    error_category_label: Optional[str] = None


@dataclass(frozen=True)
class TaskStepView:
    title: str
    planned_minutes: int
    environment_label: str
    timing_note: Optional[str] = None


@dataclass(frozen=True)
class RouteSegmentView:
    start_name: str
    end_name: str
    walking_minutes: int


@dataclass(frozen=True)
class PlanCardView:
    title: str
    steps: Tuple[TaskStepView, ...]
    total_task_minutes: Optional[int]
    free_window_task_minutes: Optional[int]
    total_walking_minutes: Optional[int]
    expected_finish_text: Optional[str]
    route_segments: Tuple[RouteSegmentView, ...]
    rationale: Optional[str]
    assumptions: Tuple[str, ...]
    warnings: Tuple[str, ...]
    is_tentative: bool


@dataclass(frozen=True)
class P1ResultView:
    status_title: str
    headline: str
    primary_card: Optional[PlanCardView]
    alternative_summary: Optional[str]
    selection_notice: Optional[NoticeView]
    tentative_notice: Optional[NoticeView]
    route_data_notice: Optional[NoticeView]
    primary_question: Optional[QuestionView]
    other_questions: Tuple[str, ...]
    quick_actions: Tuple[QuickOptionView, ...]
    source_notes: Tuple[str, ...]
    show_details: bool
    diagnostics: Tuple[DiagnosticView, ...] = ()
    model_call_count: Optional[int] = None

from dataclasses import dataclass, field
from typing import List, Optional

from src.models import PlanningRequest


@dataclass
class ExtractedTask:
    """允许缺失字段的任务草稿，用于模拟大模型返回的半结构化内容。"""
    location: Optional[str] = None
    description: Optional[str] = None
    is_mandatory: Optional[bool] = None
    estimated_duration_minutes: Optional[int] = None
    deadline: Optional[str] = None


@dataclass
class ExtractedPlanningRequest:
    """允许缺失字段的规划请求草稿。"""
    current_location: Optional[str] = None
    destination: Optional[str] = None
    current_time: Optional[str] = None
    tasks: List[ExtractedTask] = field(default_factory=list)


@dataclass
class ParseResult:
    """结构化解析结果。"""
    status: str
    error_type: Optional[str]
    message: str
    missing_fields: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    planning_request: Optional[PlanningRequest] = None

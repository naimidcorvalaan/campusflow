"""
数据模型定义模块

定义路线规划所需的数据结构。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Task:
    """
    校园任务数据模型

    属性：
        location: 任务发生地点（如"图书馆"、"食堂"）
        description: 任务描述内容（如"借书"、"吃饭"）
        is_mandatory: 是否为必须完成的任务
        estimated_duration_minutes: 预计停留时间（分钟数，必须>=0）
        deadline: 该任务的截止时间，格式为"HH:MM"（可选）
    """
    location: str
    description: str
    is_mandatory: bool
    estimated_duration_minutes: int
    deadline: Optional[str] = None


@dataclass
class PlanningRequest:
    """
    路线规划请求数据模型

    属性：
        current_location: 用户当前位置（如"宿舍"、"图书馆"）
        destination: 最终目的地（用户最后要去的地方）
        current_time: 当前时间，格式为"HH:MM"（如"09:30"）
        tasks: 需要完成的任务列表
    """
    current_location: str
    destination: str
    current_time: str
    tasks: List[Task] = field(default_factory=list)

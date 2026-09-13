"""
数据模型测试模块

验证Task和PlanningRequest的创建和字段。
"""

import pytest
from src.models import Task, PlanningRequest


class TestTask:
    """Task类的单元测试"""

    def test_task_creation_basic(self):
        """测试Task基本创建"""
        task = Task(
            location="图书馆",
            description="借书",
            is_mandatory=True,
            estimated_duration_minutes=30
        )
        assert task.location == "图书馆"
        assert task.description == "借书"
        assert task.is_mandatory is True
        assert task.estimated_duration_minutes == 30
        assert task.deadline is None

    def test_task_creation_with_deadline(self):
        """测试Task创建时包含截止时间"""
        task = Task(
            location="食堂",
            description="吃饭",
            is_mandatory=False,
            estimated_duration_minutes=45,
            deadline="12:00"
        )
        assert task.location == "食堂"
        assert task.description == "吃饭"
        assert task.is_mandatory is False
        assert task.estimated_duration_minutes == 45
        assert task.deadline == "12:00"

    def test_task_with_zero_duration(self):
        """测试Task的停留时间为0分钟"""
        task = Task(
            location="操场",
            description="打卡",
            is_mandatory=True,
            estimated_duration_minutes=0
        )
        assert task.estimated_duration_minutes == 0


class TestPlanningRequest:
    """PlanningRequest类的单元测试"""

    def test_planning_request_creation_basic(self):
        """测试PlanningRequest基本创建"""
        task1 = Task(
            location="食堂",
            description="吃饭",
            is_mandatory=True,
            estimated_duration_minutes=45
        )
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[task1]
        )
        assert request.current_location == "宿舍"
        assert request.destination == "图书馆"
        assert request.current_time == "09:00"
        assert len(request.tasks) == 1
        assert request.tasks[0].location == "食堂"

    def test_planning_request_creation_multiple_tasks(self):
        """测试PlanningRequest包含多个任务"""
        task1 = Task(
            location="食堂",
            description="吃饭",
            is_mandatory=True,
            estimated_duration_minutes=45
        )
        task2 = Task(
            location="图书馆",
            description="借书",
            is_mandatory=False,
            estimated_duration_minutes=30,
            deadline="15:00"
        )
        request = PlanningRequest(
            current_location="宿舍",
            destination="操场",
            current_time="09:00",
            tasks=[task1, task2]
        )
        assert len(request.tasks) == 2
        assert request.tasks[0].is_mandatory is True
        assert request.tasks[1].deadline == "15:00"

    def test_planning_request_empty_tasks_default(self):
        """测试PlanningRequest任务列表默认为空列表"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00"
        )
        assert request.tasks == []

"""
输入校验器测试模块

验证InputValidator的所有校验规则。
"""

import pytest
from src.models import Task, PlanningRequest
from src.input_validator import InputValidator


class TestInputValidatorCurrentLocation:
    """当前位置校验的测试"""

    def test_valid_current_location(self):
        """测试有效的当前位置"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True

    def test_empty_current_location(self):
        """测试当前位置为空字符串"""
        request = PlanningRequest(
            current_location="",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "当前位置" in message

    def test_whitespace_current_location(self):
        """测试当前位置仅为空格"""
        request = PlanningRequest(
            current_location="   ",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "当前位置" in message


class TestInputValidatorDestination:
    """最终目的地校验的测试"""

    def test_valid_destination(self):
        """测试有效的目的地"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True

    def test_empty_destination(self):
        """测试目的地为空字符串"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "最终目的地" in message

    def test_whitespace_destination(self):
        """测试目的地仅为空格"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="   ",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "最终目的地" in message


class TestInputValidatorCurrentTime:
    """当前时间校验的测试"""

    def test_valid_time_format(self):
        """测试有效的时间格式"""
        for valid_time in ["09:00", "23:59", "00:00", "12:30"]:
            request = PlanningRequest(
                current_location="宿舍",
                destination="图书馆",
                current_time=valid_time,
                tasks=[Task("食堂", "吃饭", True, 30)]
            )
            is_valid, message = InputValidator.validate(request)
            assert is_valid is True, f"时间 {valid_time} 应该有效"

    def test_invalid_time_format(self):
        """测试无效的时间格式"""
        invalid_times = [
            "9:00",      # 缺少前导零
            "09:0",      # 缺少分钟的个位数
            "9:30",      # 缺少前导零
            "25:00",     # 小时超过23
            "09:60",     # 分钟超过59
            "09-00",     # 使用短横线而不是冒号
            "09:00:00",  # 包含秒
            "0900",      # 没有冒号
            "invalid"    # 完全无效
        ]
        for invalid_time in invalid_times:
            request = PlanningRequest(
                current_location="宿舍",
                destination="图书馆",
                current_time=invalid_time,
                tasks=[Task("食堂", "吃饭", True, 30)]
            )
            is_valid, message = InputValidator.validate(request)
            assert is_valid is False, f"时间 {invalid_time} 应该无效"
            assert "时间格式" in message

    def test_empty_current_time(self):
        """测试当前时间为空"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "当前时间" in message


class TestInputValidatorTaskList:
    """任务列表校验的测试"""

    def test_valid_single_task(self):
        """测试单个有效任务"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True

    def test_valid_multiple_tasks(self):
        """测试多个有效任务"""
        tasks = [
            Task("食堂", "吃饭", True, 45),
            Task("图书馆", "借书", False, 30, "15:00"),
            Task("操场", "打卡", True, 10)
        ]
        request = PlanningRequest(
            current_location="宿舍",
            destination="宿舍",
            current_time="09:00",
            tasks=tasks
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True

    def test_empty_task_list(self):
        """测试空的任务列表"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "任务列表" in message


class TestInputValidatorTaskFields:
    """单个任务字段校验的测试"""

    def test_task_empty_location(self):
        """测试任务地点为空"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "地点" in message

    def test_task_whitespace_location(self):
        """测试任务地点仅为空格"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("   ", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "地点" in message

    def test_task_empty_description(self):
        """测试任务描述为空"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "描述" in message

    def test_task_whitespace_description(self):
        """测试任务描述仅为空格"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "   ", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "描述" in message

    def test_task_non_integer_duration(self):
        """测试停留时间不是整数"""
        task = Task("食堂", "吃饭", True, 30)
        # 通过直接修改来测试非整数情况
        # 由于dataclass的类型检查在运行时不会触发，我们需要创建一个特殊的对象
        # 但在Python中，我们可以通过直接设置来测试
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[task]
        )
        # 修改任务的duration为非整数
        request.tasks[0].estimated_duration_minutes = "30"
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "整数" in message

    def test_task_negative_duration(self):
        """测试停留时间为负数"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, -10)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "大于等于0" in message

    def test_task_zero_duration(self):
        """测试停留时间为0（应该有效）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 0)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True


class TestInputValidatorTaskDeadline:
    """任务截止时间校验的测试"""

    def test_task_valid_deadline(self):
        """测试有效的截止时间"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30, "12:00")]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True

    def test_task_none_deadline(self):
        """测试截止时间为None（应该有效）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30, None)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True

    def test_task_empty_deadline(self):
        """测试截止时间为空字符串（应该返回错误）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30, "")]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "截止时间" in message

    def test_task_whitespace_deadline(self):
        """测试截止时间为纯空格字符串（应该返回错误）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30, "   ")]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "截止时间" in message

    def test_task_invalid_deadline_format(self):
        """测试无效的截止时间格式"""
        invalid_deadlines = ["09:60", "25:00", "9:00", "12-30"]
        for invalid_deadline in invalid_deadlines:
            request = PlanningRequest(
                current_location="宿舍",
                destination="图书馆",
                current_time="09:00",
                tasks=[Task("食堂", "吃饭", True, 30, invalid_deadline)]
            )
            is_valid, message = InputValidator.validate(request)
            assert is_valid is False, f"截止时间 {invalid_deadline} 应该无效"
            assert "截止时间" in message


class TestInputValidatorMultipleTasks:
    """多任务场景的校验测试"""

    def test_multiple_tasks_first_task_invalid_location(self):
        """测试多个任务中第一个任务地点无效"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[
                Task("", "吃饭", True, 30),
                Task("图书馆", "借书", False, 30)
            ]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "第1个任务" in message

    def test_multiple_tasks_second_task_invalid_duration(self):
        """测试多个任务中第二个任务停留时间无效"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[
                Task("食堂", "吃饭", True, 30),
                Task("图书馆", "借书", False, -5)
            ]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "第2个任务" in message

    def test_multiple_tasks_third_task_invalid_deadline(self):
        """测试多个任务中第三个任务截止时间无效"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[
                Task("食堂", "吃饭", True, 30),
                Task("图书馆", "借书", False, 30),
                Task("操场", "打卡", True, 10, "invalid_time")
            ]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "第3个任务" in message


class TestInputValidatorComplexScenarios:
    """复杂场景的综合测试"""

    def test_valid_complete_request(self):
        """测试完整有效的请求"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="操场",
            current_time="09:00",
            tasks=[
                Task("食堂", "吃早饭", True, 45, "10:00"),
                Task("图书馆", "借书", False, 30, "12:00"),
                Task("宿舍", "放东西", True, 15),
            ]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is True
        assert message == "校验通过"

    def test_invalid_request_multiple_errors_prioritized(self):
        """测试多个错误时返回第一个错误"""
        # 按校验顺序，应该先检查当前位置
        request = PlanningRequest(
            current_location="",
            destination="",
            current_time="",
            tasks=[]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        # 应该返回第一个检测到的错误
        assert "当前位置" in message


class TestInputValidatorTypeErrors:
    """严格类型检查的测试（修复边界输入问题）"""

    def test_current_location_is_integer(self):
        """测试当前位置为整数（不应抛异常）"""
        request = PlanningRequest(
            current_location=123,
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_current_location_is_none(self):
        """测试当前位置为None"""
        request = PlanningRequest(
            current_location=None,
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_current_location_is_list(self):
        """测试当前位置为列表"""
        request = PlanningRequest(
            current_location=["宿舍"],
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_destination_is_integer(self):
        """测试目的地为整数"""
        request = PlanningRequest(
            current_location="宿舍",
            destination=123,
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_destination_is_none(self):
        """测试目的地为None"""
        request = PlanningRequest(
            current_location="宿舍",
            destination=None,
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_current_time_is_integer(self):
        """测试当前时间为整数（不应抛异常）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time=900,
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_current_time_is_none(self):
        """测试当前时间为None"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time=None,
            tasks=[Task("食堂", "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_tasks_is_none(self):
        """测试任务列表为None"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=None
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "列表" in message

    def test_tasks_is_string(self):
        """测试任务列表为字符串（不应抛异常）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks="任务"
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "列表" in message

    def test_tasks_is_integer(self):
        """测试任务列表为整数"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=123
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "列表" in message

    def test_tasks_contains_none(self):
        """测试任务列表包含None（不应抛异常）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[None]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "格式无效" in message

    def test_tasks_contains_string(self):
        """测试任务列表包含字符串"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=["任务"]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "格式无效" in message

    def test_task_location_is_integer(self):
        """测试任务地点为整数（不应抛异常）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task(123, "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_task_location_is_none(self):
        """测试任务地点为None"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task(None, "吃饭", True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_task_description_is_integer(self):
        """测试任务描述为整数（不应抛异常）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", 123, True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_task_description_is_none(self):
        """测试任务描述为None"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", None, True, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_estimated_duration_minutes_is_bool_true(self):
        """测试停留时间为True（应拒绝bool）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, True)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "整数" in message

    def test_estimated_duration_minutes_is_bool_false(self):
        """测试停留时间为False（应拒绝bool）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, False)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "整数" in message

    def test_estimated_duration_minutes_is_float(self):
        """测试停留时间为浮点数"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 1.5)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "整数" in message

    def test_estimated_duration_minutes_is_string(self):
        """测试停留时间为字符串"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, "10")]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "整数" in message

    def test_estimated_duration_minutes_is_none(self):
        """测试停留时间为None"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, None)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "整数" in message

    def test_is_mandatory_is_string(self):
        """测试是否必须为字符串（应拒绝）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", "是", 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "布尔值" in message

    def test_is_mandatory_is_integer_one(self):
        """测试是否必须为整数1（应拒绝）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", 1, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "布尔值" in message

    def test_is_mandatory_is_integer_zero(self):
        """测试是否必须为整数0（应拒绝）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", 0, 30)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "布尔值" in message

    def test_deadline_is_integer(self):
        """测试截止时间为整数（不应抛异常）"""
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[Task("食堂", "吃饭", True, 30, 1500)]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

    def test_deadline_is_invalid_integer_time(self):
        """测试截止时间为无效的时间格式整数"""
        task = Task("食堂", "吃饭", True, 30, None)
        task.deadline = 1500  # 直接设置为整数
        request = PlanningRequest(
            current_location="宿舍",
            destination="图书馆",
            current_time="09:00",
            tasks=[task]
        )
        is_valid, message = InputValidator.validate(request)
        assert is_valid is False
        assert "字符串" in message

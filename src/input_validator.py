"""
输入校验模块

对PlanningRequest进行数据校验，确保数据完整性和格式正确。
"""

import re
from typing import Tuple

from src.models import PlanningRequest, Task


class InputValidator:
    """
    输入数据校验器

    提供对路线规划请求的全面校验，包括字段有效性、格式检查等。
    采用严格的类型检查，确保不会因类型错误而抛异常。
    """

    # 时间格式的正则表达式：HH:MM（00:00 到 23:59）
    TIME_PATTERN = re.compile(r"^([0-1][0-9]|2[0-3]):([0-5][0-9])$")

    @staticmethod
    def _is_valid_time_string(value):
        """
        检查值是否是有效的时间字符串

        参数：
            value: 待检查的值

        返回：
            bool: 如果是有效的HH:MM格式字符串则返回True
        """
        if not isinstance(value, str):
            return False
        return InputValidator.TIME_PATTERN.match(value) is not None

    @staticmethod
    def _is_valid_non_empty_string(value):
        """
        检查值是否是非空字符串

        参数：
            value: 待检查的值

        返回：
            bool: 如果是非空字符串则返回True
        """
        return isinstance(value, str) and len(value.strip()) > 0

    @staticmethod
    def validate(request: PlanningRequest) -> Tuple[bool, str]:
        """
        校验PlanningRequest对象

        参数：
            request: 待校验的PlanningRequest对象

        返回：
            (is_valid, message)的元组
            - is_valid: True表示校验通过，False表示校验失败
            - message: 如果校验失败，返回中文错误信息；如果通过，返回"校验通过"
        """

        # 1. 检查当前位置（必须是非空字符串）
        if not isinstance(request.current_location, str):
            return False, "错误：当前位置必须是字符串"
        if not InputValidator._is_valid_non_empty_string(request.current_location):
            return False, "错误：当前位置不能为空"

        # 2. 检查最终目的地（必须是非空字符串）
        if not isinstance(request.destination, str):
            return False, "错误：最终目的地必须是字符串"
        if not InputValidator._is_valid_non_empty_string(request.destination):
            return False, "错误：最终目的地不能为空"

        # 3. 检查当前时间格式（必须是字符串且符合HH:MM）
        if not isinstance(request.current_time, str):
            return False, "错误：当前时间必须是字符串"
        if not InputValidator._is_valid_time_string(request.current_time):
            return False, "错误：当前时间格式必须为HH:MM（例如：09:30）"

        # 4. 检查任务列表（必须是list且非空）
        if not isinstance(request.tasks, list):
            return False, "错误：任务列表必须是列表类型"
        if len(request.tasks) == 0:
            return False, "错误：任务列表不能为空"

        # 5. 检查每个任务
        for idx, task in enumerate(request.tasks, start=1):
            # 检查task是否是Task对象
            if not isinstance(task, Task):
                return False, f"错误：第{idx}个任务格式无效（必须是Task对象）"

            # 检查地点（必须是非空字符串）
            if not isinstance(task.location, str):
                return False, f"错误：第{idx}个任务的地点必须是字符串"
            if not InputValidator._is_valid_non_empty_string(task.location):
                return False, f"错误：第{idx}个任务的地点不能为空"

            # 检查任务描述（必须是非空字符串）
            if not isinstance(task.description, str):
                return False, f"错误：第{idx}个任务的描述必须是字符串"
            if not InputValidator._is_valid_non_empty_string(task.description):
                return False, f"错误：第{idx}个任务的描述不能为空"

            # 检查是否必须（必须是bool，排除True/False的数值表示）
            if not isinstance(task.is_mandatory, bool):
                return False, f"错误：第{idx}个任务的\"是否必须\"必须是布尔值"

            # 检查预计停留时间（必须是真正的int，排除bool）
            if type(task.estimated_duration_minutes) is not int:
                return False, f"错误：第{idx}个任务的预计停留时间必须是整数"
            if task.estimated_duration_minutes < 0:
                return False, f"错误：第{idx}个任务的预计停留时间必须大于等于0"

            # 检查截止时间格式（必须是None或有效的HH:MM格式字符串）
            if task.deadline is not None:
                if not isinstance(task.deadline, str):
                    return False, f"错误：第{idx}个任务的截止时间必须是字符串"
                # deadline不是None，就必须是非空的有效时间字符串
                if len(task.deadline.strip()) == 0:
                    return False, f"错误：第{idx}个任务的截止时间不能为空"
                if not InputValidator._is_valid_time_string(task.deadline):
                    return False, f"错误：第{idx}个任务的截止时间格式必须为HH:MM（例如：15:30）"

        return True, "校验通过"

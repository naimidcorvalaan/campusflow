"""构造 P1 任务理解的 system/user 分离提示词。"""

from typing import Tuple

from src.p1_models import MAX_USER_TEXT_LENGTH


def _validate_user_text(user_text: object) -> str:
    if not isinstance(user_text, str):
        raise TypeError("user_text 必须是字符串。")
    if not user_text.strip():
        raise ValueError("user_text 不能为空。")
    if len(user_text) > MAX_USER_TEXT_LENGTH:
        raise ValueError("user_text 过长。")
    return user_text


def build_p1_system_prompt() -> str:
    """返回 compact v2 固定规则；程序随后规范化并交给严格 v1 解析器。"""
    return """
你是 CampusFlow 的任意任务理解器。任务名称没有封闭枚举；一句话可拆为多个独立任务。

只返回单个纯 JSON 对象，严格使用 schema_version "p1.task-extraction.v2"。顶层字段必须且只能是 schema_version、tasks。任务数量为 0 到 8；空数组表示没有识别到可执行待办，不是错误。
固定安排不是待办：例如“11:30去46楼上课”“下午开会”“明天考试”不能放入 tasks；“预习课程”“准备会议材料”“复习明天的考试”才是待办。
每个任务必须包含 title、original_text、features；还可以包含 extracted_fields、estimated_fields、confirmation_fields，这三个数组允许省略。task_ref、来源解释和确认问题由程序生成，不要输出。
title 是简洁自然的中文标题；original_text 必须是用户原始文本中的连续片段，不得改写或虚构。
features 只能含有 location_requirement、location_text、environment_requirements、equipment_requirements、estimated_total_minutes、is_splittable、minimum_slice_minutes、attention_required、interruption_allowed、may_have_open_hours。location_requirement 必填；其他缺失字段由程序补为 null 或空数组。
location_requirement 只能是 no_specific_location、specific_location、location_requirement_unknown。地点要求不确定时必须使用 location_requirement_unknown，不能静默当作地点不限。不要输出地图节点 ID，不要虚构天津大学地点。
environment_requirements 和 equipment_requirements 是自由文本数组，不是任务类型枚举。estimated_total_minutes 和 minimum_slice_minutes 为 1 到 10080 的整数分钟或 null；不得输出 completed_minutes、remaining_minutes、planned_minutes。
用户未明确给出属性时，可合理暂估时长、地点要求、环境、设备、注意力、可拆分性、最小有效片段、可中断性和开放时间风险。

extracted_fields 表示从用户原话直接识别；estimated_fields 表示用户未明确提供、由你主动暂估。能准确判断时请列出；不确定或遗漏时程序会保守派生来源，因此三个数组都可以省略。若输出这些数组，不得重复、冲突或引用空字段。无法证明来自用户原话时，宁可作为 AI 暂估。confirmation_fields 只列真正值得优先确认的字段，可以省略。
no_specific_location 表示任务可在普通环境完成，即使它是暂估也不要仅因此要求确认；location_requirement_unknown 必须确认；specific_location 缺少 location_text 时必须确认。例如“背单词”“看资料”“写报告”通常可使用 no_specific_location，但这只是通用语义示例，不是封闭任务枚举。
可以保存进度的实验、报告、复习、阅读等通常可拆分。“完成计组实验3”即使完整任务估计 120 分钟，也可标记 is_splittable=true、minimum_slice_minutes=30，以便先安排 30～90 分钟片段；不要因为“完成”二字就默认不可拆分。只有确实必须一次完成时才使用 is_splittable=false。
若 is_splittable 为 false：已知 estimated_total_minutes 时 minimum_slice_minutes 必须等于它；未知总时长时 minimum_slice_minutes 必须为 null。若 is_splittable 为 true，最小片段不得超过总时长。
不得生成路线、路径、地图 ID、步行时间、ETA、可行性、固定安排、时间窗口、首选方案、备选方案、任务进度、生命周期或用户状态更新。

固定结构示例：
{
  "schema_version": "p1.task-extraction.v2",
  "tasks": [
    {
      "title": "背单词",
      "original_text": "背30分钟单词",
      "features": {
        "location_requirement": "no_specific_location",
        "estimated_total_minutes": 30,
        "is_splittable": true,
        "minimum_slice_minutes": 15,
        "attention_required": "low",
        "interruption_allowed": true,
        "may_have_open_hours": false
      },
      "extracted_fields": ["estimated_total_minutes"],
      "estimated_fields": ["location_requirement", "is_splittable", "minimum_slice_minutes", "attention_required", "interruption_allowed", "may_have_open_hours"],
      "confirmation_fields": []
    }
  ]
}
""".strip()


def build_p1_user_prompt(user_text: str) -> str:
    validated = _validate_user_text(user_text)
    return "请理解下面的用户原始文本，并按 system prompt 的固定 schema 输出。\n\n用户原始文本：\n" + validated


def build_p1_prompt(user_text: str) -> Tuple[str, str]:
    return build_p1_system_prompt(), build_p1_user_prompt(user_text)


build_system_prompt = build_p1_system_prompt
build_user_prompt = build_p1_user_prompt
build_prompt = build_p1_prompt

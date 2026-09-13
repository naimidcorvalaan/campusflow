"""P1b 窗口上下文的 system/user 分离提示词。"""
from datetime import datetime
from typing import Tuple

from src.p1_window_models import MAX_WINDOW_TEXT_LENGTH


def _validate(user_text: object, reference_datetime: object) -> Tuple[str, datetime]:
    if not isinstance(user_text, str):
        raise TypeError("user_text 必须是字符串。")
    if not user_text.strip() or len(user_text) > MAX_WINDOW_TEXT_LENGTH:
        raise ValueError("user_text 无效。")
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime 必须是 datetime。")
    return user_text, reference_datetime


def build_p1_window_system_prompt() -> str:
    return """
你是 CampusFlow 的单窗口上下文提取器。只返回一个纯 JSON 对象，schema_version 必须是 "p1.window-extraction.v2"。
从用户原话提取当前位置、用户明确假定的当前时间、所有未来固定安排、空闲时长、空闲截止时间和用户明确缓冲。不要猜测用户没有提供的课程时间、地点、空闲时长或假定时间。
顶层字段必须是 schema_version、current_location、commitments、free_duration_minutes、ends_at、user_buffer_minutes；只有用户明确给出假定当前时间时，才可额外输出 assumed_current_datetime。
current_location 为 null，或只含 text、fragment；fragment 必须是用户原话连续片段。
assumed_current_datetime 为 null，或只含 value、fragment；value 必须是完整 ISO 8601 datetime，fragment 必须来自用户原话。
每项 commitment 必须且只能含 title、original_text、starts_at、ends_at、location_text、availability_during。保留全部安排，不要决定哪个最近；original_text 必须是用户原话连续片段。时间为完整 ISO 8601 datetime 或 null。availability_during 为 unavailable、low_attention、fully_available 或 null；未说明时返回 null，由程序默认 unavailable。
free_duration_minutes、user_buffer_minutes 为正整数分钟或 null；ends_at 为完整 ISO 8601 datetime 或 null。
commitment_ref、字段来源、中文证据解释、待确认字段和确认问题都由程序确定性生成，不要输出。

必须使用 user prompt 中程序提供的可信 reference_datetime 解析相对时间：
- “90分钟后”是在 reference_datetime 上增加 90 分钟；
- “2小时后”是在 reference_datetime 上增加 2 小时；
- “半小时后”是在 reference_datetime 上增加 30 分钟。
固定安排 starts_at 本身就是当前窗口的时间边界。识别到“90分钟后上课”后，不要再要求用户补充空闲时长。所有 starts_at 必须晚于可信 reference_datetime，并使用完整日期，跨午夜时必须进入次日。课程、会议、考试等固定安排不能作为任务输出。

示例一（reference_datetime 为 2026-08-16T10:00:00；用户说“我在宿舍，90分钟后去教学楼上课”）：
{"schema_version":"p1.window-extraction.v2","current_location":{"text":"宿舍","fragment":"宿舍"},"commitments":[{"title":"上课","original_text":"90分钟后去教学楼上课","starts_at":"2026-08-16T11:30:00","ends_at":null,"location_text":"教学楼","availability_during":null}],"free_duration_minutes":null,"ends_at":null,"user_buffer_minutes":null}

示例二（reference_datetime 为 2026-08-16T10:00:00；用户说“我在图书馆，2小时后去教学楼上课”）：
{"schema_version":"p1.window-extraction.v2","current_location":{"text":"图书馆","fragment":"图书馆"},"commitments":[{"title":"上课","original_text":"2小时后去教学楼上课","starts_at":"2026-08-16T12:00:00","ends_at":null,"location_text":"教学楼","availability_during":null}],"free_duration_minutes":null,"ends_at":null,"user_buffer_minutes":null}

示例三（reference_datetime 为 2026-08-16T23:30:00；用户说“我在宿舍，90分钟后去教学楼”）：
{"schema_version":"p1.window-extraction.v2","current_location":{"text":"宿舍","fragment":"宿舍"},"commitments":[{"title":"前往教学楼","original_text":"90分钟后去教学楼","starts_at":"2026-08-17T01:00:00","ends_at":null,"location_text":"教学楼","availability_during":null}],"free_duration_minutes":null,"ends_at":null,"user_buffer_minutes":null}

不得输出路线、地图 ID、ETA、步行时间、推荐、任务、进度、生命周期或状态更新。
""".strip()


def build_p1_window_user_prompt(user_text: str, reference_datetime: datetime) -> str:
    text, reference = _validate(user_text, reference_datetime)
    return "可信参考时间（程序提供，不可改写）：%s\n\n用户原始文本：\n%s" % (reference.isoformat(), text)


def build_p1_window_prompt(user_text: str, reference_datetime: datetime) -> Tuple[str, str]:
    _validate(user_text, reference_datetime)
    return build_p1_window_system_prompt(), build_p1_window_user_prompt(user_text, reference_datetime)

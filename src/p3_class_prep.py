"""Deterministic campus-course arrival preparation; separate from safety buffers."""

from datetime import timedelta


CLASS_ARRIVAL_LEAD_MINUTES = 10
CLASS_PREP_DISPLAY = "签到 / 找教室"
CLASS_BUILDING_PREP_DISPLAY = "进楼 / 找教室"
CLASS_CLASSROOM_PREP_DISPLAY = "到教室后签到 / 课前准备"
CLASSROOM_LEAD_MINUTES = 5

CLASS_ARRIVAL_FEEDBACK_SEMANTICS = (
    "class_arrival_lead_minutes 表示课程开始前必须预留的分钟数，不是当前时刻的分钟部分，"
    "也不是用户实际提前到达的时差。用户报告已经到某地点只更新当前位置；"
    "除非明确要求改变课程的提前到达规则，否则不生成该字段的更新，保留台账原有规则。"
)


def is_class_commitment(commitment):
    """Minimal fallback until the intake schema carries an explicit kind."""
    return getattr(commitment, "commitment_kind", None) == "class"


def class_arrival_lead_minutes(commitment):
    if not is_class_commitment(commitment):
        return 0
    override = getattr(commitment, "class_arrival_lead_minutes", None)
    if isinstance(override, int) and not isinstance(override, bool) and override >= 0:
        return override
    return CLASS_ARRIVAL_LEAD_MINUTES


def class_prep_interval(commitment):
    if getattr(commitment, "starts_at", None) is None:
        return None
    lead = class_arrival_lead_minutes(commitment)
    if lead <= 0:
        return None
    return (commitment.starts_at - timedelta(minutes=lead), commitment.starts_at)


def class_arrival_deadline(commitment):
    interval = class_prep_interval(commitment)
    return interval[0] if interval is not None else getattr(commitment, "starts_at", None)


def class_prep_display_segments(commitment):
    """Split the deterministic class buffer into building and classroom facts.

    Capacity remains the single existing prep interval; this helper only makes
    the established T-10 building arrival / T-5 classroom arrival visible.
    """
    interval = class_prep_interval(commitment)
    if interval is None:
        return ()
    start, end = interval
    classroom_arrival = end - timedelta(minutes=CLASSROOM_LEAD_MINUTES)
    if classroom_arrival <= start:
        return ((start, end, CLASS_CLASSROOM_PREP_DISPLAY),)
    return (
        (start, classroom_arrival, CLASS_BUILDING_PREP_DISPLAY),
        (classroom_arrival, end, CLASS_CLASSROOM_PREP_DISPLAY),
    )

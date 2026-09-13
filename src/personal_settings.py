"""Versioned single-user preferences, saved places and manual timetable facts.

These values are long-lived defaults, not a second planner.  Helpers in this
module only validate, resolve explicit saved anchors, and project defaults
into existing CampusFlow facts.  A per-request fact always wins.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_models import DayPlanningState
from src.p2_window_derivation import derive_day_state


PERSONAL_SETTINGS_KEY = "personal_settings"
PERSONAL_SETTINGS_REVISION_KEY = "personal_settings_revision"
PERSONAL_SETTINGS_PLAN_STALE_KEY = "personal_settings_plan_stale"
PERSONAL_SETTINGS_PANEL_OPEN_KEY = "personal_settings_panel_open"
PERSONAL_SETTINGS_DRAFT_REVISION_KEY = "personal_settings_draft_revision"
PERSONAL_TRANSPORT_EXPLICIT_KEY = "personal_transport_explicit_for_day"
PERSONAL_OTHER_CAMPUS_COURSES_KEY = "personal_other_campus_courses"
PERSONAL_SETTINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SavedCampusPlace:
    campus_id: str
    role: str
    raw_text: str
    node_id: Optional[str] = None
    display_name: Optional[str] = None

    def __post_init__(self):
        if self.campus_id not in ("weijinlu", "beiyangyuan"):
            raise ValueError("saved place campus invalid")
        if self.role not in ("dormitory", "study"):
            raise ValueError("saved place role invalid")
        if not isinstance(self.raw_text, str) or not self.raw_text.strip():
            raise ValueError("saved place text required")
        if (self.node_id is None) != (self.display_name is None):
            raise ValueError("saved place anchor identity incomplete")

    @property
    def matched(self):
        return self.node_id is not None


@dataclass(frozen=True)
class CourseTemplate:
    template_ref: str
    title: str
    weekday: int
    start_time: str
    end_time: Optional[str]
    start_week: int
    end_week: int
    week_pattern: str
    campus_id: str
    location_text: Optional[str]
    location_node_id: Optional[str] = None
    location_display_name: Optional[str] = None

    def __post_init__(self):
        _required(self.template_ref, "course template ref")
        _required(self.title, "course title")
        if isinstance(self.weekday, bool) or not 1 <= self.weekday <= 7:
            raise ValueError("课程星期必须为1到7")
        _parse_clock(self.start_time, "课程开始时间")
        if self.end_time is not None:
            if _parse_clock(self.end_time, "课程结束时间") <= _parse_clock(self.start_time, "课程开始时间"):
                raise ValueError("课程结束时间必须晚于开始时间")
        if isinstance(self.start_week, bool) or isinstance(self.end_week, bool):
            raise ValueError("课程周次必须为整数")
        if self.start_week < 1 or self.end_week < self.start_week:
            raise ValueError("课程周次范围无效")
        if self.week_pattern not in ("every", "odd", "even"):
            raise ValueError("课程周次模式无效")
        if self.campus_id not in ("weijinlu", "beiyangyuan"):
            raise ValueError("课程校区无效")
        if self.location_text is not None and not self.location_text.strip():
            raise ValueError("课程地点不能是空字符串")
        if (self.location_node_id is None) != (self.location_display_name is None):
            raise ValueError("课程地点锚点不完整")


@dataclass(frozen=True)
class CourseOccurrenceOverride:
    occurrence_ref: str
    action: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    location_text: Optional[str] = None

    def __post_init__(self):
        _required(self.occurrence_ref, "course occurrence ref")
        if self.action not in ("cancel", "replace"):
            raise ValueError("课程单次调整类型无效")
        if self.action == "replace" and self.starts_at is None:
            raise ValueError("改课必须提供新的开始时间")
        if self.ends_at is not None and self.starts_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("单次课程结束时间必须晚于开始时间")


@dataclass(frozen=True)
class PersonalSettings:
    schema_version: int = PERSONAL_SETTINGS_SCHEMA_VERSION
    revision: int = 0
    travel_mode: Optional[str] = "walk"
    learning_rhythm: Optional[str] = "rest_breaks"
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None
    dinner_start: Optional[str] = None
    dinner_end: Optional[str] = None
    default_note: str = ""
    saved_places: Tuple[SavedCampusPlace, ...] = ()
    semester_first_monday: Optional[str] = None
    semester_end_date: Optional[str] = None
    courses: Tuple[CourseTemplate, ...] = ()
    occurrence_overrides: Tuple[CourseOccurrenceOverride, ...] = ()
    default_walk_hint_seen: bool = False

    def __post_init__(self):
        if self.schema_version != PERSONAL_SETTINGS_SCHEMA_VERSION:
            raise ValueError("个人设置版本不兼容")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("个人设置 revision 无效")
        if self.travel_mode not in (None, "walk", "bike"):
            raise ValueError("常用出行方式无效")
        if self.learning_rhythm not in (None, "fewer_switches", "rest_breaks"):
            raise ValueError("学习节奏无效")
        if not isinstance(self.default_walk_hint_seen, bool):
            raise ValueError("步行默认提示状态无效")
        if not isinstance(self.default_note, str) or len(self.default_note) > 1200:
            raise ValueError("默认补充最多1200字")
        _validate_optional_window(self.lunch_start, self.lunch_end, "午饭")
        _validate_optional_window(self.dinner_start, self.dinner_end, "晚饭")
        if (self.semester_first_monday is None) != (self.semester_end_date is None):
            raise ValueError("学期起止日期必须同时填写或同时留空")
        if self.semester_first_monday is not None:
            first = _parse_date(self.semester_first_monday, "第一教学周周一")
            end = _parse_date(self.semester_end_date, "学期结束日期")
            if first.weekday() != 0:
                raise ValueError("第一教学周日期必须是周一")
            if end < first:
                raise ValueError("学期结束日期不能早于第一教学周")
        if any(not isinstance(item, SavedCampusPlace) for item in self.saved_places):
            raise ValueError("常用地点记录无效")
        if any(not isinstance(item, CourseTemplate) for item in self.courses):
            raise ValueError("课程模板记录无效")
        if any(not isinstance(item, CourseOccurrenceOverride) for item in self.occurrence_overrides):
            raise ValueError("课程单次调整记录无效")
        place_keys = [(item.campus_id, item.role) for item in self.saved_places]
        if len(place_keys) != len(set(place_keys)):
            raise ValueError("同一校区的常用地点角色不能重复")
        refs = [item.template_ref for item in self.courses]
        if len(refs) != len(set(refs)):
            raise ValueError("课程模板ref不能重复")
        override_refs = [item.occurrence_ref for item in self.occurrence_overrides]
        if len(override_refs) != len(set(override_refs)):
            raise ValueError("课程单次调整不能重复")
        _validate_course_conflicts(self)

    def place_for(self, campus_id, role):
        return next((item for item in self.saved_places if item.campus_id == campus_id and item.role == role), None)

    def estimation_context(self):
        values = []
        learning_rhythm = self.learning_rhythm or "rest_breaks"
        if learning_rhythm == "fewer_switches":
            values.append("默认学习节奏：希望少切换任务")
        elif learning_rhythm == "rest_breaks":
            values.append("默认学习节奏：希望适当穿插休息")
        if self.default_note.strip():
            values.append("用户保存的默认参考：" + self.default_note.strip())
        return "；".join(values)

    def planning_context_items(self):
        values = []
        values.append(("default_transport_mode", self.travel_mode or "walk"))
        values.append(("default_learning_rhythm", self.learning_rhythm or "rest_breaks"))
        if self.lunch_start:
            values.append(("default_lunch_window", "{}-{}".format(self.lunch_start, self.lunch_end)))
        if self.dinner_start:
            values.append(("default_dinner_window", "{}-{}".format(self.dinner_start, self.dinner_end)))
        if self.default_note.strip():
            values.append(("user_saved_default_note", self.default_note.strip()))
        return tuple(values)


@dataclass(frozen=True)
class TimetableApplication:
    state: DayPlanningState
    added_commitment_refs: Tuple[str, ...] = ()
    other_campus_commitment_refs: Tuple[str, ...] = ()


def load_personal_settings(store):
    value = store.get(PERSONAL_SETTINGS_KEY)
    return value if isinstance(value, PersonalSettings) else PersonalSettings()


def save_personal_settings(store, settings):
    if not isinstance(settings, PersonalSettings):
        raise TypeError("settings must be PersonalSettings")
    store[PERSONAL_SETTINGS_KEY] = settings
    store[PERSONAL_SETTINGS_REVISION_KEY] = settings.revision


def resolve_saved_place(raw_text, campus_id, role, map_data, anchor_node_id=None):
    """Resolve a saved place without Qwen; an explicit anchor may disambiguate."""
    text = str(raw_text or "").strip()
    if not text:
        return None
    if getattr(map_data, "campus_id", None) != campus_id:
        raise ValueError("常用地点地图与校区不一致")
    node_id = anchor_node_id or map_data.resolve_node_id(text)
    node = next((item for item in map_data.nodes if item.id == node_id and item.node_kind == "poi"), None)
    if node is None:
        return SavedCampusPlace(campus_id, role, text)
    return SavedCampusPlace(campus_id, role, text, node.id, node.name)


def personal_location_alias(text, campus_id, settings):
    """Return a saved stable anchor for the two explicit personal place roles."""
    if not isinstance(settings, PersonalSettings) or not isinstance(text, str):
        return None
    normalized = text.strip().replace(" ", "")
    role = None
    if normalized in ("宿舍", "回宿舍", "去宿舍", "到宿舍"):
        role = "dormitory"
    elif normalized in ("常用学习地点", "去常用学习地点", "到常用学习地点"):
        role = "study"
    if role is None:
        return None
    value = settings.place_for(campus_id, role)
    return value if value is not None and value.matched else None


def apply_personal_defaults_to_context(context, settings, transport_is_explicit=False):
    """Project only absent/default execution facts; explicit intake remains authoritative."""
    from src.p4_execution_context import ExecutionPlanContext
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context invalid")
    if not isinstance(settings, PersonalSettings):
        return context
    updated = context
    if not transport_is_explicit:
        updated = updated.with_transport_mode(settings.travel_mode or "walk")
    for binding in tuple(updated.bindings):
        if binding.activity_kind != "meal" or binding.meal_explicit_time is not None:
            continue
        window = None
        if binding.meal_period == "lunch" and settings.lunch_start:
            window = (_clock_minutes(settings.lunch_start), _clock_minutes(settings.lunch_end))
        elif binding.meal_period == "dinner" and settings.dinner_start:
            window = (_clock_minutes(settings.dinner_start), _clock_minutes(settings.dinner_end))
        elif binding.meal_period in (None, "unspecified"):
            existing = (binding.meal_window_start_minutes, binding.meal_window_end_minutes)
            if existing == (11 * 60, 13 * 60) and settings.lunch_start:
                window = (_clock_minutes(settings.lunch_start), _clock_minutes(settings.lunch_end))
            elif existing == (17 * 60, 19 * 60) and settings.dinner_start:
                window = (_clock_minutes(settings.dinner_start), _clock_minutes(settings.dinner_end))
        if window is not None:
            updated = updated.upsert(replace(
                binding,
                meal_window_start_minutes=window[0],
                meal_window_end_minutes=window[1],
            ))
    return updated


def saved_place_options(map_data, role):
    """Return campus-scoped POIs suitable for one personal place role.

    Categories are map facts.  Titles and aliases are deliberately not used
    to guess whether a POI is a dormitory or a study destination.
    """
    if role == "dormitory":
        categories = ("dormitory",)
    elif role == "study":
        categories = ("teaching", "teaching_building", "library")
    else:
        raise ValueError("saved place role invalid")
    return tuple(
        node for node in map_data.nodes
        if node.node_kind == "poi" and node.category in categories
    )


def expand_timetable(settings, planning_date, selected_campus_id):
    """Expand only one planning date into stable course occurrences."""
    if not isinstance(settings, PersonalSettings) or settings.semester_first_monday is None:
        return (), ()
    if isinstance(planning_date, datetime):
        planning_date = planning_date.date()
    if not isinstance(planning_date, date):
        raise TypeError("planning_date invalid")
    first = _parse_date(settings.semester_first_monday, "第一教学周周一")
    end = _parse_date(settings.semester_end_date, "学期结束日期")
    if planning_date < first or planning_date > end:
        return (), ()
    week = ((planning_date - first).days // 7) + 1
    if week < 1:
        return (), ()
    override_map = {item.occurrence_ref: item for item in settings.occurrence_overrides}
    selected = []
    other = []
    for course in settings.courses:
        if course.weekday != planning_date.isoweekday() or not course.start_week <= week <= course.end_week:
            continue
        if course.week_pattern == "odd" and week % 2 == 0:
            continue
        if course.week_pattern == "even" and week % 2 == 1:
            continue
        ref = occurrence_ref(course.template_ref, planning_date)
        override = override_map.get(ref)
        if override is not None and override.action == "cancel":
            continue
        start = datetime.combine(planning_date, _parse_clock(course.start_time, "课程开始时间"))
        finish = datetime.combine(planning_date, _parse_clock(course.end_time, "课程结束时间")) if course.end_time else None
        location = course.location_display_name or course.location_text
        if override is not None and override.action == "replace":
            start, finish = override.starts_at, override.ends_at
            if override.location_text is not None:
                location = override.location_text
        item = FixedCommitment(
            commitment_ref=ref,
            title=course.title,
            original_text="个人课表：{}".format(course.title),
            starts_at=start,
            ends_at=finish,
            location_text=location,
            availability_during=AvailabilityLevel.UNAVAILABLE,
            field_evidence={},
            needs_confirmation=("请确认{}大约几点结束。".format(course.title),) if finish is None else (),
            commitment_kind="class",
            class_arrival_lead_minutes=None,
        )
        (selected if course.campus_id == selected_campus_id else other).append(item)
    return tuple(selected), tuple(other)


def inject_timetable(
    state, settings, selected_campus_id,
    default_safety_buffer_minutes=0, travel_minutes_by_commitment=None,
):
    """Reconcile today's selected-campus occurrences into a planning state.

    ``schedule_`` commitments are derived facts.  Replacing or deleting a
    template therefore replaces/removes its old occurrence on the next
    explicit plan update.  User-entered commitments remain untouched.
    """
    occurrences, other = expand_timetable(settings, state.reference_datetime.date(), selected_campus_id)
    previous_by_ref = {
        item.commitment_ref: item for item in state.commitments
        if item.commitment_ref.startswith("schedule_")
    }
    existing = [
        item for item in state.commitments
        if not item.commitment_ref.startswith("schedule_")
    ]
    added = []
    for item in occurrences:
        if any(_same_occurrence(item, value) or _same_named_course_slot(item, value) for value in existing):
            continue
        existing.append(item)
        if item.commitment_ref not in previous_by_ref:
            added.append(item.commitment_ref)
    if tuple(existing) == tuple(state.commitments):
        return TimetableApplication(state, (), tuple(item.commitment_ref for item in other))
    known_ends = [item.ends_at or item.starts_at for item in existing if item.starts_at is not None]
    day_end = max((state.day_end,) + tuple(value for value in known_ends if value is not None))
    if day_end <= state.now:
        day_end = state.now + timedelta(hours=2)
    updated = derive_day_state(
        state.now, day_end, tuple(existing), state.tasks,
        default_safety_buffer_minutes=default_safety_buffer_minutes,
        travel_minutes_by_commitment=travel_minutes_by_commitment,
        history=state.history,
        reference_datetime=state.reference_datetime,
    )
    return TimetableApplication(updated, tuple(added), tuple(item.commitment_ref for item in other))


def occurrence_ref(template_ref, planning_date):
    return "schedule_{}_{}".format(template_ref, planning_date.strftime("%Y%m%d"))


def with_next_revision(settings):
    return replace(settings, revision=settings.revision + 1)


def capture_occurrence_overrides(settings, before_state, after_state):
    """Persist only date-specific edits to generated schedule occurrences."""
    if not isinstance(settings, PersonalSettings):
        return settings
    before = {
        item.commitment_ref: item for item in before_state.commitments
        if item.commitment_ref.startswith("schedule_")
    }
    after = {item.commitment_ref: item for item in after_state.commitments}
    overrides = {item.occurrence_ref: item for item in settings.occurrence_overrides}
    changed = False
    for ref, old in before.items():
        new = after.get(ref)
        if new is None:
            value = CourseOccurrenceOverride(ref, "cancel")
        elif (
            new.starts_at != old.starts_at or new.ends_at != old.ends_at
            or _normalize(new.location_text) != _normalize(old.location_text)
        ):
            value = CourseOccurrenceOverride(
                ref, "replace", new.starts_at, new.ends_at, new.location_text
            )
        else:
            continue
        if overrides.get(ref) != value:
            overrides[ref] = value
            changed = True
    if not changed:
        return settings
    return replace(
        settings,
        revision=settings.revision + 1,
        occurrence_overrides=tuple(sorted(overrides.values(), key=lambda item: item.occurrence_ref)),
    )


def _same_occurrence(left, right):
    return (
        left.starts_at == right.starts_at
        and left.ends_at == right.ends_at
        and _normalize(left.location_text) == _normalize(right.location_text)
    )


def _same_named_course_slot(left, right):
    """Dedupe the same lesson, never merely a matching course title."""
    return (
        left.starts_at == right.starts_at
        and left.ends_at == right.ends_at
        and _normalize(left.title) == _normalize(right.title)
    )


def _validate_course_conflicts(settings):
    courses = tuple(settings.courses)
    for index, left in enumerate(courses):
        for right in courses[index + 1:]:
            if left.weekday != right.weekday:
                continue
            if not _week_ranges_overlap(left, right):
                continue
            if left.end_time is None or right.end_time is None:
                continue
            if _parse_clock(left.start_time, "start") < _parse_clock(right.end_time, "end") and _parse_clock(right.start_time, "start") < _parse_clock(left.end_time, "end"):
                raise ValueError("课程“{}”与“{}”时间冲突".format(left.title, right.title))


def _week_ranges_overlap(left, right):
    start, end = max(left.start_week, right.start_week), min(left.end_week, right.end_week)
    if start > end:
        return False
    return any(
        (left.week_pattern == "every" or (week % 2 == 1) == (left.week_pattern == "odd"))
        and (right.week_pattern == "every" or (week % 2 == 1) == (right.week_pattern == "odd"))
        for week in range(start, end + 1)
    )


def _validate_optional_window(start, end, label):
    if (start is None) != (end is None):
        raise ValueError("{}偏好时段必须同时填写开始和结束".format(label))
    if start is not None and _parse_clock(end, label + "结束") <= _parse_clock(start, label + "开始"):
        raise ValueError("{}偏好结束时间必须晚于开始时间".format(label))


def _parse_clock(value, label):
    if not isinstance(value, str):
        raise ValueError("{}格式无效".format(label))
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise ValueError("{}必须为HH:MM".format(label))


def _clock_minutes(value):
    parsed = _parse_clock(value, "时间")
    return parsed.hour * 60 + parsed.minute


def _parse_date(value, label):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError("{}必须为YYYY-MM-DD".format(label))


def _required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{}不能为空".format(label))


def _normalize(value):
    return str(value or "").strip().casefold()

import json

import pytest

from src.personal_settings import CourseTemplate, PersonalSettings, SavedCampusPlace
from src.timetable_text_import import (
    TimetableImportError,
    TimetableImportRow,
    apply_timetable_import_default_campus,
    edit_timetable_import_row,
    merge_timetable_import,
    preview_timetable_import,
    run_timetable_image_import,
    run_timetable_text_import,
)


def settings(courses=()):
    return PersonalSettings(
        revision=2,
        semester_first_monday="2026-09-07",
        semester_end_date="2026-12-31",
        courses=tuple(courses),
    )


def payload(courses):
    return json.dumps({
        "schema_version": "campusflow.timetable-import.v1",
        "courses": courses,
    }, ensure_ascii=False)


def course(**changes):
    value = {
        "title": "高等数学",
        "weekday": 1,
        "start_time": "10:00",
        "end_time": "11:30",
        "start_week": 1,
        "end_week": 16,
        "week_pattern": "every",
        "campus_id": "weijinlu",
        "location_text": "第九教学楼",
    }
    value.update(changes)
    return value


class QueueCaller:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return self.responses.pop(0)


def test_text_recognition_builds_editable_preview_and_atomic_stable_import():
    caller = QueueCaller(payload([
        course(),
        course(title="大学物理", weekday=3, start_time="14:00", end_time="15:40", location_text="23教"),
    ]))
    draft = run_timetable_text_import("周一高数；周三物理", settings(), caller)
    assert draft.status == "ready"
    assert len(draft.rows) == 2
    assert len(caller.calls) == 1
    assert "不可信材料" in caller.calls[0][0]

    merged = merge_timetable_import(settings(), draft.rows)
    assert len(merged.added_template_refs) == 2
    assert merged.settings.revision == 3
    assert all(ref.startswith("course_import_") for ref in merged.added_template_refs)

    repeated = merge_timetable_import(merged.settings, draft.rows)
    assert repeated.settings is merged.settings
    assert repeated.added_template_refs == ()
    assert len(repeated.duplicate_template_refs) == 2


def test_period_only_row_is_kept_visible_until_user_adds_real_clock_times():
    caller = QueueCaller(payload([course(start_time=None, end_time=None)]))
    draft = run_timetable_text_import("周一第3—4节高等数学", settings(), caller)
    assert draft.status == "needs_correction"
    assert len(draft.rows) == 1
    assert any("实际开始时间" in item for item in draft.rows[0].errors)
    assert not preview_timetable_import(settings(), draft.rows).ready_to_import

    fixed = edit_timetable_import_row(
        draft.rows[0], start_time="10:00", end_time="11:30"
    )
    assert fixed.ready
    assert preview_timetable_import(settings(), (fixed,)).ready_to_import


def test_unknown_or_invalid_rows_are_not_silently_dropped_and_repair_is_bounded():
    caller = QueueCaller("not json", payload([
        course(title="", weekday=None, campus_id=None),
    ]))
    draft = run_timetable_text_import("一行信息不完整的课", settings(), caller)
    assert len(caller.calls) == 2
    assert len(draft.rows) == 1
    assert draft.status == "needs_correction"
    assert {"缺少课程名", "缺少有效星期", "缺少明确校区"}.issubset(set(draft.rows[0].errors))


def test_selected_conflict_fails_atomically_and_preserves_existing_settings():
    existing = CourseTemplate(
        template_ref="existing", title="线性代数", weekday=1,
        start_time="10:30", end_time="12:00", start_week=1, end_week=16,
        week_pattern="every", campus_id="weijinlu", location_text="23教",
    )
    original = settings((existing,))
    draft = run_timetable_text_import(
        "周一高数", original, QueueCaller(payload([course()])),
    )
    checked = preview_timetable_import(original, draft.rows)
    assert not checked.ready_to_import
    assert any("时间冲突" in item for item in checked.errors)
    with pytest.raises(ValueError, match="时间冲突"):
        merge_timetable_import(original, draft.rows)
    assert original.courses == (existing,)


def test_missing_semester_or_model_failure_has_safe_distinct_error():
    with pytest.raises(TimetableImportError, match="第1教学周"):
        run_timetable_text_import("周一高数", PersonalSettings(), QueueCaller(""))

    class FailedCaller:
        def __call__(self, system, user):
            raise RuntimeError("private service detail")

    with pytest.raises(TimetableImportError, match="服务暂时没有完成") as captured:
        run_timetable_text_import("周一高数", settings(), FailedCaller())
    assert "private" not in str(captured.value)


def test_recognized_preview_import_round_trip_and_reopen_do_not_duplicate(tmp_path):
    from src.local_persistence import LocalProfileStore

    caller = QueueCaller(payload([course()]))
    draft = run_timetable_text_import("周一高等数学", settings(), caller)
    # Recognition alone is isolated from the official value.
    original = settings()
    assert original.courses == ()
    merged = merge_timetable_import(original, draft.rows)
    database = LocalProfileStore(tmp_path / "campusflow.sqlite3")
    database.save(
        0, estimate_draft=None, added_estimate_drafts={},
        personal_settings=merged.settings,
    )
    reopened = LocalProfileStore(tmp_path / "campusflow.sqlite3").load()
    assert reopened.personal_settings.courses == merged.settings.courses
    repeated = merge_timetable_import(reopened.personal_settings, draft.rows)
    assert repeated.added_template_refs == ()
    assert len(repeated.settings.courses) == 1
    assert len(caller.calls) == 1


def test_image_timetable_reuses_preview_and_requires_one_class_from_ambiguous_group():
    image = b'\x89PNG\r\n\x1a\nsynthetic-timetable'
    candidates = [
        course(title='体育C（教师甲）',weekday=1,start_time='13:30',end_time='15:05',
            location_text='体育场',choice_group='monday-pe-c'),
        course(title='体育C（教师乙）',weekday=1,start_time='13:30',end_time='15:05',
            location_text='体育场',choice_group='monday-pe-c'),
    ]
    calls = []
    def image_caller(system,user,image_mime,image_bytes):
        calls.append((system,user,image_mime,image_bytes))
        return payload(candidates)
    draft = run_timetable_image_import('timetable.png','image/png',image,settings(),image_caller)
    assert draft.source_type == 'image' and len(calls) == 1
    assert all(not row.selected and row.choice_group == 'monday-pe-c' for row in draft.rows)
    assert not preview_timetable_import(settings(),draft.rows).ready_to_import
    selected = (edit_timetable_import_row(draft.rows[0],selected=True),draft.rows[1])
    checked = preview_timetable_import(settings(),selected)
    assert checked.ready_to_import
    merged = merge_timetable_import(settings(),selected)
    assert [item.title for item in merged.settings.courses] == ['体育C（教师甲）']
    assert '实际显示' in calls[0][0] and 'candidate_group' in calls[0][1]


def test_choice_group_is_enforced_by_merge_not_only_preview():
    current_settings = settings()
    first = TimetableImportRow('a','体育C（于楠）',1,'13:30','15:05',4,19,'every',
        'beiyangyuan','体育场',True,(),(),choice_group='monday-pe-c')
    second = TimetableImportRow('b','体育C（薛可）',1,'13:30','15:05',4,19,'every',
        'beiyangyuan','体育场',True,(),(),choice_group='monday-pe-c')
    with pytest.raises(TimetableImportError, match='只能选择一个'):
        merge_timetable_import(current_settings,(first,second))


def test_realistic_image_shape_accepts_fence_aliases_sections_and_optional_omissions():
    image = b'\x89PNG\r\n\x1a\nrealistic-shape'
    raw = """```json
    {
      "result": {
        "items": [
          {
            "course_name": "数据结构",
            "teacher": "张怡",
            "weekday": "星期三",
            "start_section": "1",
            "end_section": 2,
            "start_time": "08:30",
            "end_time": "10:05",
            "week_start": "12",
            "week_end": 19,
            "parity": "odd",
            "campus": "北洋园校区",
            "location": "33楼205"
          },
          {
            "course_name": "概率论与数理统计",
            "weekday": 4,
            "start_section": 3,
            "end_section": 4,
            "start_time": "10:25",
            "end_time": "12:00",
            "week_start": 4,
            "week_end": 17,
            "parity": "双周"
          }
        ]
      }
    }
    ```"""

    draft = run_timetable_image_import(
        'timetable.png', 'image/png', image, settings(),
        lambda *_: raw,
    )

    assert len(draft.rows) == 2
    assert draft.rows[0].title == "数据结构"
    assert draft.rows[0].teacher == "张怡"
    assert (draft.rows[0].start_section, draft.rows[0].end_section) == (1, 2)
    assert draft.rows[0].week_pattern == "odd"
    assert draft.rows[0].campus_id == "beiyangyuan"
    # Omitted optional campus is retained for user correction, not rejected as a bad payload.
    assert draft.rows[1].week_pattern == "even"
    assert "缺少明确校区" in draft.rows[1].errors
    assert draft.diagnostic.parse_status == "parsed_needs_correction"


def test_image_repair_failure_exposes_value_free_structural_diagnostic():
    image = b'\x89PNG\r\n\x1a\ninvalid-shape'
    calls = []

    def image_caller(*_):
        calls.append("image")
        return "模型确实返回了文字，但不是课表JSON secret-course-name"

    def repair_caller(*_):
        calls.append("repair")
        return '{"courses":[{"weekday":99}]}'

    with pytest.raises(TimetableImportError) as captured:
        run_timetable_image_import(
            'timetable.png', 'image/png', image, settings(), image_caller,
            repair_caller=repair_caller,
        )

    diagnostic = captured.value.diagnostic
    assert calls == ["image", "repair"]
    assert diagnostic.response_text_received is True
    assert diagnostic.repair_used is True
    assert diagnostic.repair_response_text_received is True
    assert diagnostic.parse_status == "repair_parse_failed"
    assert diagnostic.exception_type == "TimetableParseError"
    assert diagnostic.failed_field == "weekday"
    assert "secret-course-name" not in repr(diagnostic)


def test_image_format_repair_is_bounded_and_can_recover_once():
    image = b'\x89PNG\r\n\x1a\nrepairable'
    calls = []

    def image_caller(*_):
        calls.append("image")
        return "课程内容已看清，但输出遗漏了JSON包装"

    def repair_caller(system, user):
        calls.append("repair")
        assert "parse_error" in user and "invalid_output" in user
        return payload([course()])

    draft = run_timetable_image_import(
        'timetable.png', 'image/png', image, settings(), image_caller,
        repair_caller=repair_caller,
    )

    assert calls == ["image", "repair"]
    assert draft.model_calls == 2
    assert draft.diagnostic.repair_used is True
    assert draft.diagnostic.repair_response_text_received is True
    assert draft.diagnostic.parse_status == "repair_succeeded"
    assert len(draft.rows) == 1


def test_same_slot_same_course_with_multiple_teachers_becomes_an_ambiguity_group():
    image = b'\x89PNG\r\n\x1a\nmultiple-teachers'
    candidates = [
        dict(course(title=None), course_name="体育C", teacher="于楠"),
        dict(course(title=None), course_name="体育C", teacher="薛可"),
        dict(course(title=None), course_name="体育C", teacher="郭萱"),
    ]
    draft = run_timetable_image_import(
        'timetable.png', 'image/png', image, settings(),
        lambda *_: json.dumps({"items": candidates}, ensure_ascii=False),
    )

    assert len({row.choice_group for row in draft.rows}) == 1
    assert all(row.choice_group and not row.selected for row in draft.rows)
    assert draft.diagnostic.ambiguity_count == 1
    assert not preview_timetable_import(settings(), draft.rows).ready_to_import
    chosen = tuple(
        edit_timetable_import_row(row, selected=(row.teacher == "薛可"))
        for row in draft.rows
    )
    merged = merge_timetable_import(settings(), chosen)
    assert [item.title for item in merged.settings.courses] == ["体育C（薛可）"]


def test_import_default_campus_fills_only_missing_rows_and_keeps_explicit_material_facts():
    missing = run_timetable_image_import(
        "timetable.png", "image/png", b"\x89PNG\r\n\x1a\nmissing-campus",
        settings(), lambda *_: payload([
            course(title="高等数学", campus_id=None),
            course(title="数据结构", weekday=3, campus_id=None),
        ]),
    ).rows
    inherited = apply_timetable_import_default_campus(missing, "beiyangyuan")
    assert all(row.campus_id == "beiyangyuan" and row.ready for row in inherited)

    mixed = run_timetable_image_import(
        "timetable.png", "image/png", b"\x89PNG\r\n\x1a\nmixed-campus",
        settings(), lambda *_: payload([
            course(title="卫津路课程", campus_id="weijinlu"),
            course(title="北洋园课程", weekday=2, campus_id="beiyangyuan"),
            course(title="未标校区课程", weekday=3, campus_id=None),
        ]),
    ).rows
    resolved = apply_timetable_import_default_campus(mixed, "beiyangyuan")
    assert [row.campus_id for row in resolved] == [
        "weijinlu", "beiyangyuan", "beiyangyuan",
    ]


def test_missing_import_and_settings_campus_still_requires_confirmation():
    rows = run_timetable_image_import(
        "timetable.png", "image/png", b"\x89PNG\r\n\x1a\nno-default",
        settings(), lambda *_: payload([course(campus_id=None)]),
    ).rows
    unresolved = apply_timetable_import_default_campus(rows, None)
    assert unresolved[0].campus_id is None
    assert "缺少明确校区" in unresolved[0].errors
    assert not preview_timetable_import(settings(), unresolved).ready_to_import


def test_per_import_campus_override_changes_only_draft_and_preserves_sports_choice():
    original_settings = PersonalSettings(
        revision=2,
        semester_first_monday="2026-09-07",
        semester_end_date="2026-12-31",
        saved_places=(SavedCampusPlace(
            "beiyangyuan", "study", "北洋园图书馆", "by_lib", "北洋园图书馆",
        ),),
    )
    rows = run_timetable_image_import(
        "timetable.png", "image/png", b"\x89PNG\r\n\x1a\nsports-default",
        original_settings, lambda *_: payload([
            course(title="体育C", teacher="于楠", campus_id=None,
                   choice_group="pe-monday"),
            course(title="体育C", teacher="薛可", campus_id=None,
                   choice_group="pe-monday"),
        ]),
    ).rows
    by_rows = apply_timetable_import_default_campus(rows, "beiyangyuan")
    wj_rows = apply_timetable_import_default_campus(rows, "weijinlu")
    assert all(row.campus_id == "beiyangyuan" for row in by_rows)
    assert all(row.campus_id == "weijinlu" for row in wj_rows)
    assert original_settings.saved_places[0].campus_id == "beiyangyuan"
    assert original_settings.courses == ()
    assert len({row.choice_group for row in wj_rows}) == 1
    assert all(not row.selected for row in wj_rows)
    selected = tuple(
        edit_timetable_import_row(row, selected=(row.teacher == "薛可"))
        for row in wj_rows
    )
    merged = merge_timetable_import(original_settings, selected)
    assert [item.title for item in merged.settings.courses] == ["体育C（薛可）"]
    assert merged.settings.courses[0].campus_id == "weijinlu"
    assert merged.settings.saved_places == original_settings.saved_places

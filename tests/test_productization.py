"""Small product-boundary regressions. All model responses are offline fakes."""
from contextlib import nullcontext
from datetime import date, datetime
import pytest

from scripts.offline_product_model import OfflineProductModel, STORY_TEXT, WHAT_IF_TEXT
from src.local_persistence import LocalProfileStore
from src.personal_settings import load_personal_settings
from src.p2_live_main import (
    LIVE_CAMPUS_SELECT_KEY, LOCAL_PROFILE_STORE_KEY, LOCAL_PROFILE_REVISION_KEY,
    PERSONAL_SETTINGS_PLACE_CAMPUS_KEY, TIMETABLE_IMPORT_CAMPUS_KEY,
    PERSONAL_SETTINGS_DRAFT_VALUES_KEY, _initialize_settings_draft,
    _capture_settings_draft_widgets, _render_timetable_importer, _settings_widget_key,
    _settings_draft_from_widgets, main,
)
from src.p2_session import load_live_final_turn
from src.p2_main import PlanDisplayEntry, _timeline_card_html
from src.p5_what_if import load_what_if_preview
from src.timetable_text_import import TIMETABLE_IMPORT_TEXT_KEY, TIMETABLE_IMPORT_DRAFT_KEY
from tests.test_p2_live_main import _StubSt


def test_default_walk_hint_is_attached_to_only_the_first_walk():
    entries = (
        PlanDisplayEntry("14:00–14:05", "步行去图书馆，约5分钟", True, False, "movement"),
        PlanDisplayEntry("15:00–15:04", "步行去教学楼，约4分钟", True, False, "movement"),
    )
    html = _timeline_card_html(entries, default_walk_hint=True)
    assert html.count("默认按步行计算 · 可在个人设置中修改") == 1
    assert _timeline_card_html(entries, default_walk_hint=False).count("默认按步行计算") == 0


class ImportSurface:
    """Native widget contract only; extraction, merge, persistence stay real."""
    def __init__(self, store):
        self.session_state = store
        self.pressed = None
        self.messages = []
        self.disabled = {}
        self.button_labels = []

    def markdown(self, *args, **kwargs): pass
    def warning(self, message): self.messages.append(message)
    def caption(self, message): self.messages.append(message)
    def expander(self, *args, **kwargs): return nullcontext()
    def columns(self, count): return tuple(nullcontext() for _ in range(count))
    def rerun(self): pass
    def text_area(self, label, key, **kwargs): return self.session_state.get(key, "")
    def text_input(self, label, value="", key=None, **kwargs): return self.session_state.setdefault(key, value)
    def checkbox(self, label, value=False, key=None): return self.session_state.setdefault(key, value)
    def selectbox(self, label, options, index=0, key=None, **kwargs): return self.session_state.setdefault(key, options[index])
    def number_input(self, label, value="min", min_value=0, max_value=60, key=None):
        # Match Streamlit: omitting value uses min_value, while a seeded
        # session value remains the control's actual value.
        if value == "min":
            value = min_value
        assert min_value <= value <= max_value
        return self.session_state.setdefault(key, value)
    def button(self, label, key=None, disabled=False, **kwargs):
        self.button_labels.append(label)
        self.disabled[key] = disabled
        # Deliberately return True even if disabled: backend must also guard.
        return self.pressed == key


class ImageImportSurface(ImportSurface):
    class Uploaded:
        name = "timetable.png"
        type = "image/png"

        @staticmethod
        def getvalue():
            return b"\x89PNG\r\n\x1a\nui-image"

    def file_uploader(self, *args, **kwargs):
        return self.Uploaded()

    def radio(self, label, options, index=0, key=None, **kwargs):
        return self.session_state.setdefault(key, options[index])


def test_timetable_source_has_one_primary_recognition_action_and_natural_class_choice():
    import json

    rows = [
        {"course_name":"体育C","teacher":"于楠","weekday":1,
         "start_time":"13:30","end_time":"15:05","week_start":4,
         "week_end":19,"parity":"all","campus":"beiyangyuan",
         "location":"体育场","candidate_group":"pe-monday"},
        {"course_name":"体育C","teacher":"薛可","weekday":1,
         "start_time":"13:30","end_time":"15:05","week_start":4,
         "week_end":19,"parity":"all","campus":"beiyangyuan",
         "location":"体育场","candidate_group":"pe-monday"},
    ]
    store = {}
    _initialize_settings_draft(store)
    store.update({_settings_widget_key("semester_enabled"):True,
        _settings_widget_key("semester_first"):date(2026,8,31),
        _settings_widget_key("semester_end"):date(2027,1,17)})
    ui = ImageImportSurface(store)
    ui.pressed = "personal_settings_timetable_import_run"
    _render_timetable_importer(
        ui, lambda *_: "", lambda *_: "",
        image_caller=lambda *_: json.dumps({"courses":rows},ensure_ascii=False),
        image_supported=True,
    )
    assert ui.button_labels == ["识别课表"]
    ui.button_labels.clear()
    ui.pressed = None
    _render_timetable_importer(
        ui, lambda *_: "", lambda *_: "",
        image_caller=lambda *_: "unexpected", image_supported=True,
    )
    visible = " ".join(str(item) for item in ui.messages)
    assert "候选教学班：体育C · 于楠 · 第4–19周" in visible
    assert "候选教学班：体育C · 薛可 · 第4–19周" in visible
    assert "识别文字课表" not in ui.button_labels
    assert "识别课表截图" not in ui.button_labels


def test_timetable_preview_reuses_common_place_campus_without_overwriting_explicit_course():
    import json

    rows = [
        {"course_name":"高等数学","weekday":1,"start_time":"08:30",
         "end_time":"10:05","week_start":1,"week_end":16,"parity":"all"},
        {"course_name":"跨校区课程","weekday":2,"start_time":"10:25",
         "end_time":"12:00","week_start":1,"week_end":16,"parity":"all",
         "campus":"weijinlu"},
    ]
    store = {}
    _initialize_settings_draft(store)
    store.update({
        PERSONAL_SETTINGS_PLACE_CAMPUS_KEY: "beiyangyuan",
        _settings_widget_key("semester_enabled"): True,
        _settings_widget_key("semester_first"): date(2026,8,31),
        _settings_widget_key("semester_end"): date(2027,1,17),
    })
    ui = ImageImportSurface(store)
    ui.pressed = "personal_settings_timetable_import_run"
    render = lambda: _render_timetable_importer(
        ui, lambda *_: "", lambda *_: "",
        image_caller=lambda *_: json.dumps({"courses":rows},ensure_ascii=False),
        image_supported=True,
    )
    render()
    ui.pressed = None
    render()
    assert store[TIMETABLE_IMPORT_CAMPUS_KEY] == "beiyangyuan"
    assert store["personal_settings_timetable_import_import_item_001_campus"] == "beiyangyuan"
    assert store["personal_settings_timetable_import_import_item_002_campus"] == "weijinlu"
    assert "缺少明确校区" not in " ".join(str(item) for item in ui.messages)
    assert "有课程缺少实际钟点或必要字段" not in " ".join(str(item) for item in ui.messages)

    # Changing the one-off import default updates only source-missing rows.
    store[TIMETABLE_IMPORT_CAMPUS_KEY] = "weijinlu"
    render()
    assert store["personal_settings_timetable_import_import_item_001_campus"] == "weijinlu"
    assert store[PERSONAL_SETTINGS_PLACE_CAMPUS_KEY] == "beiyangyuan"


def test_import_source_edit_blocks_old_preview_then_real_import_survives_reopen(tmp_path):
    model = OfflineProductModel()
    store = {LOCAL_PROFILE_STORE_KEY: LocalProfileStore(tmp_path / "local.sqlite3"), LOCAL_PROFILE_REVISION_KEY: 0}
    _initialize_settings_draft(store)
    store.update({_settings_widget_key("semester_enabled"): True,
        _settings_widget_key("semester_first"): date(2026, 8, 31),
        _settings_widget_key("semester_end"): date(2027, 1, 17),
        TIMETABLE_IMPORT_TEXT_KEY: "大学英语 周二10:00–11:30 1–16周 卫津路 9教"})
    ui = ImportSurface(store)
    def render():
        _render_timetable_importer(ui, model.agent_caller, model.agent_caller, reference=datetime(2026,9,1,14))
    ui.pressed = "personal_settings_timetable_import_run"
    render()
    original_text = store[TIMETABLE_IMPORT_TEXT_KEY]
    store[TIMETABLE_IMPORT_TEXT_KEY] += "（材料已更改）"
    ui.pressed = "personal_settings_timetable_import_confirm"
    render()
    assert ui.disabled[ui.pressed]
    assert not load_personal_settings(store).courses
    assert model.calls == ["timetable"]
    store[TIMETABLE_IMPORT_TEXT_KEY] = original_text
    render()
    assert len(load_personal_settings(store).courses) == 1
    reopened = LocalProfileStore(tmp_path / "local.sqlite3").load()
    assert reopened.personal_settings.courses == load_personal_settings(store).courses
    assert model.calls == ["timetable"]
    _initialize_settings_draft(store)
    store[_settings_widget_key("course_0_title")] = ""
    with pytest.raises(ValueError, match="缺少名称"):
        _settings_draft_from_widgets(store)
    assert LocalProfileStore(tmp_path / "local.sqlite3").load().personal_settings == reopened.personal_settings


def test_invalid_recognition_fields_are_editable_and_unsaved_import_values_survive_close():
    import json
    model = OfflineProductModel()
    def invalid(system, user):
        payload = json.loads(model.agent_caller(system, user))
        payload["courses"][0].update(weekday=9, campus_id="unknown", week_pattern="unknown", start_week=-5, end_week=80)
        return json.dumps(payload)
    store = {}
    _initialize_settings_draft(store)
    store.update({_settings_widget_key("semester_enabled"):True,
        _settings_widget_key("semester_first"):date(2026,8,31),
        _settings_widget_key("semester_end"):date(2027,1,17),TIMETABLE_IMPORT_TEXT_KEY:"待纠正材料"})
    ui = ImportSurface(store)
    ui.pressed = "personal_settings_timetable_import_run"
    _render_timetable_importer(ui, invalid)
    ui.pressed = None
    _render_timetable_importer(ui, invalid)
    assert ui.disabled["personal_settings_timetable_import_confirm"]
    assert len(store[TIMETABLE_IMPORT_DRAFT_KEY].rows) == 1
    assert not load_personal_settings(store).courses
    _capture_settings_draft_widgets(store)
    values = dict(store[PERSONAL_SETTINGS_DRAFT_VALUES_KEY])
    for key in values:
        store.pop(key, None)  # emulate native widget cleanup after closing
    _initialize_settings_draft(store)
    assert all(store[key] == value for key, value in values.items())


def test_live_initial_preview_adopt_and_failed_update_keep_same_reliable_plan(tmp_path):
    model = OfflineProductModel()
    class Page(_StubSt):
        pressed = None
        def button(self, label, key=None, **kwargs):
            return self.pressed == key or super().button(label, key, **kwargs)
    page = Page()
    page.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    def render():
        main(st=page, adapter_factory=lambda:model,now_provider=lambda:datetime(2026,9,1,14),
            configuration_loader=lambda:(),persistence_factory=lambda:LocalProfileStore(tmp_path / "profile.sqlite3"))
    page.set_inputs(intake=STORY_TEXT,intake_submitted=True)
    render()
    reliable = load_live_final_turn(page.session_state)
    assert reliable is not None and not page.errors
    # A real settings/import operation adds a course earlier on this day.
    # Refresh must consume the new settings, without routing back into the past.
    importer = ImportSurface(page.session_state)
    _initialize_settings_draft(page.session_state)
    page.session_state.update({_settings_widget_key("semester_enabled"): True,
        _settings_widget_key("semester_first"): date(2026,8,31),
        _settings_widget_key("semester_end"): date(2027,1,17),
        TIMETABLE_IMPORT_TEXT_KEY: "大学英语 周二10:00–11:30 1–16周 卫津路 9教"})
    importer.pressed = "personal_settings_timetable_import_run"
    _render_timetable_importer(importer, model.agent_caller)
    importer.pressed = "personal_settings_timetable_import_confirm"
    _render_timetable_importer(importer, model.agent_caller)
    from tests.test_p2_live_main import _refresh_existing_page
    page.set_inputs()
    _refresh_existing_page(page)
    render()
    reliable = load_live_final_turn(page.session_state)
    assert any(c.title == "大学英语" for c in reliable.result.updated_state.commitments)
    page.set_inputs(feedback=WHAT_IF_TEXT,feedback_submitted=True)
    render()
    preview = load_what_if_preview(page.session_state)
    assert preview is not None and preview.can_apply
    assert sum(a.planned_minutes for a in preview.candidate.result.allocation_plan.allocations
               if a.task_ref == "day_task_003") == 40
    assert len(preview.candidate.movement_blocks) == 2
    assert "大学英语" not in preview.important_impact
    assert load_live_final_turn(page.session_state) is reliable
    calls = len(model.calls)
    page.set_inputs()
    page.pressed = "p5_apply_what_if"
    render()
    adopted = load_live_final_turn(page.session_state)
    assert adopted is not reliable
    assert len(model.calls) == calls
    page.pressed = None
    render()
    assert len(model.calls) == calls
    model.failure_mode = "network"
    from src.p2_live_main import LOCAL_PROFILE_STALE_KEY, _LOCAL_BUSINESS_KEYS
    page.session_state[LOCAL_PROFILE_STALE_KEY] = True
    before_failure = {key: page.session_state.get(key) for key in _LOCAL_BUSINESS_KEYS}
    page.set_inputs(reference_hour=16, feedback="演示服务失败",feedback_submitted=True)
    page.markdown_calls.clear()
    render()
    assert load_live_final_turn(page.session_state) is adopted
    assert "cf-plan-hero" in "\n".join(page.markdown_calls)
    assert {key: page.session_state.get(key) for key in _LOCAL_BUSINESS_KEYS} == before_failure

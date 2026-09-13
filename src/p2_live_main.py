"""CampusFlow P2 真实模型页面（TJU Qwen live 接线）（Python 3.8 兼容）。

首次输入自然语言 -> Day Intake Agent -> 程序构造 DayPlanningState
                -> P2c agentic day planning -> P2SessionController
                -> 当前方案（唯一主输出）+ 待确认问题。
后续反馈先由 LLM Router 判定任务/固定安排，再只调用相关 reconciler + replanning。

模型调用策略：
- 普通 Streamlit rerun 不重复调用模型（页面只在用户主动“生成全天计划 / 应用反馈 / 刷新”时触发）；
- Day Intake 最多 1 次格式 repair；
- P2c 每轮最多 10 次调用（既有上限）；
- 只配置 TJU API（TJU_LLM_API_KEY / TJU_LLM_BASE_URL / TJU_LLM_MODEL），不引入第三方地图 API。

导入本模块无网络副作用；测试全部注入 mock adapter / configuration loader，
不真正请求 TJU API。
"""

import sys
from pathlib import Path

# The console-script entry used by hosted Streamlit adds src/, not necessarily
# the repository root. Keep the same imports as the local `python -m` launcher.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import hashlib
import html
import logging
from contextlib import nullcontext
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Optional

from src.p1_tju_llm_adapter import load_project_configuration
from src.runtime_environment import apply_configured_timezone
from src.campusflow_ui import BRAND_HEADER_HTML, CAMPUSFLOW_THEME_CSS, SETTINGS_ACCESSIBILITY_HTML
from src.p2_tju_live_adapter import TJUP2CallAdapter
from src.p2_day_intake import DayIntakeOutcome, run_day_intake
from src.workspace_ui import region, quiet_button, setting_row, render_navigation, toggle_environment, VIEW_KEY, DeferredDrawerRerun
from src.p2_main import render_page_streamlit, render_page_text, sanitize_user_facing_text
from src.p2_session import (
    LAST_CACHE_KEY,
    LAST_TURN_KEY,
    LIVE_FINAL_TURN_KEY,
    LIVE_FINAL_TURN_SEQUENCE_KEY,
    MOVEMENT_DATA_KEY,
    P2SessionController,
    PlanConfig,
    RESULT_KEY,
    STATE_KEY,
    commit_live_final_turn,
    load_live_final_turn,
)
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_campus_session import (
    CAMPUS_BOUND_STATE_KEYS, SELECTED_CAMPUS_ID_KEY, select_campus,
)
from src.p3_campus_scope import require_campus_map
from src.p3_unified_feedback import make_unified_feedback_handler
from src.p4_execution_context import (
    CurrentLocationContext,
    EXECUTION_PLAN_CONTEXT_KEY,
    ExecutionPlanContext,
    load_execution_context,
    save_execution_context,
)
from src.p5_agent_pipeline import (
    AGENT_INTELLIGENCE_KEY,
    AGENT_CALL_TRACE_KEY,
    LATEST_FEEDBACK_TEXT_KEY,
    LATEST_USER_TEXT_KEY,
)
from src.p5_day_preferences import DAY_PREFERENCE_KEY
from src.p5_what_if import (
    WHAT_IF_PREVIEW_KEY,
    WhatIfPreview,
    WhatIfApplyError,
    clear_what_if_preview,
    load_what_if_preview,
)
from src.p4_execution_enrichment import (
    enrich_intake_execution_context,
    reconcile_execution_context,
)
from src.p4_feedback_decision import P4_FEEDBACK_DECISION_KEY
from src.task_estimation import (
    TASK_ESTIMATE_ADDED_DRAFTS_KEY,
    TASK_ESTIMATE_DRAFT_KEY,
    TaskEstimateDraft,
    TaskEstimationError,
    TaskEstimationImageUnsupported,
    TaskEstimationStale,
    confirm_estimated_task,
    load_task_estimate_draft,
    make_material,
    mark_draft_added,
    run_task_estimation,
    save_task_estimate_draft,
)
from src.local_persistence import (
    LocalPersistenceConflict,
    LocalPersistenceError,
    LocalPersistenceIncompatible,
    LocalPersistenceUnreadable,
    LocalProfileStore,
    ProfileDirectory,
    SavedPlanRecord,
)
from src.external_auth import VerifiedExternalIdentity
from src.profile_identity import (
    AUTHENTICATION_CONTEXT_KEY,
    CURRENT_USER_CONTEXT_KEY,
    PROFILE_ENABLED_DRAFT_KEY,
    PROFILE_IMPORT_LEGACY_DRAFT_KEY,
    PROFILE_SWITCH_STATUS_KEY,
    STUDENT_IDENTIFIER_DRAFT_KEY,
    ProfileIdentity,
    activate_authentication_context,
    authenticated_context,
    current_authentication_context,
    current_identity,
    new_anonymous_identity,
)
from src.personal_settings import (
    PERSONAL_SETTINGS_DRAFT_REVISION_KEY,
    PERSONAL_SETTINGS_KEY,
    PERSONAL_SETTINGS_REVISION_KEY,
    PERSONAL_SETTINGS_PANEL_OPEN_KEY,
    PERSONAL_SETTINGS_PLAN_STALE_KEY,
    PERSONAL_OTHER_CAMPUS_COURSES_KEY,
    PERSONAL_TRANSPORT_EXPLICIT_KEY,
    CourseTemplate,
    PersonalSettings,
    SavedCampusPlace,
    apply_personal_defaults_to_context,
    load_personal_settings,
    personal_location_alias,
    resolve_saved_place,
    saved_place_options,
    save_personal_settings,
    with_next_revision,
)
from src.timetable_text_import import (
    TIMETABLE_IMPORT_DIAGNOSTIC_KEY,
    TIMETABLE_IMPORT_DRAFT_KEY,
    TIMETABLE_IMPORT_IMAGE_KEY,
    TIMETABLE_IMPORT_TEXT_KEY,
    TimetableImportDraft,
    TimetableImportError,
    apply_timetable_import_default_campus,
    edit_timetable_import_row,
    merge_timetable_import,
    preview_timetable_import,
    run_timetable_text_import,
    run_timetable_image_import,
    timetable_choice_label,
    timetable_preview_matches,
)

LIVE_SESSION_KEY = "p2_live_session"
LIVE_REFERENCE_KEY = "p2_live_reference"
LIVE_REFERENCE_HOUR_KEY = "p2_live_reference_hour"
LIVE_REFERENCE_MINUTE_KEY = "p2_live_reference_minute"
LIVE_INTAKE_KEY = "p2_live_intake"
LIVE_CURRENT_LOCATION_KEY = "p2_live_current_location"
LIVE_FEEDBACK_KEY = "p2_live_feedback"
LIVE_INTAKE_CACHE_KEY = "p2_live_intake_cache"
LIVE_MAP_KEY = "p2_live_map"
LIVE_CAMPUS_SELECT_KEY = "p2_live_campus_select"
LIVE_CONFIG_PREFIX = "缺少配置："
LIVE_ERROR_KEY = "p2_live_last_error"
LIVE_ADAPTER_KEY = "p2_live_adapter"
LIVE_ESTIMATE_TEXT_KEY = "p2_task_estimate_text"
LIVE_ESTIMATE_IMAGE_KEY = "p2_task_estimate_image"
LIVE_ESTIMATE_SUPPLEMENT_KEY = "p2_task_estimate_supplement"
LIVE_ESTIMATE_DRAFT_SEQUENCE_KEY = "p2_task_estimate_draft_sequence"
LIVE_REFERENCE_SOURCE_KEY = "p2_live_reference_source"
EXTERNAL_AUTH_MODE_KEY = "campusflow_external_auth_mode"
LOCAL_PROFILE_STORE_KEY = "p2_local_profile_store"
LOCAL_PROFILE_REVISION_KEY = "p2_local_profile_revision"
LOCAL_PROFILE_RESTORE_DONE_KEY = "p2_local_profile_restore_done"
LOCAL_PROFILE_STATUS_KEY = "p2_local_profile_status"
LOCAL_PROFILE_STALE_BUNDLE_KEY = "p2_local_profile_stale_bundle"
LOCAL_PROFILE_STALE_KEY = "p2_local_profile_stale"
LOCAL_PROFILE_SAVE_ERROR_KEY = "p2_local_profile_save_error"
PERSONAL_SETTINGS_STATUS_KEY = "personal_settings_status"
PERSONAL_SETTINGS_COURSE_ROWS_KEY = "personal_settings_course_rows"
PERSONAL_SETTINGS_DRAFT_VALUES_KEY = "personal_settings_draft_values"
PERSONAL_SETTINGS_PLACE_CAMPUS_KEY = "personal_settings_draft_place_campus"
TIMETABLE_IMPORT_CAMPUS_KEY = "personal_settings_timetable_import_default_campus"
TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY = "personal_settings_timetable_import_applied_campus"
SAFE_ERROR_TEXT = "这次调整没有成功，原可靠方案仍保留。可以保留输入，稍后重试。"
PAGE_INIT_ERROR_TEXT = "页面暂时未能打开，请刷新后重试；本机保存的记录不会因此清空。"
_LIVE_EXECUTION_CACHE_VERSION = "p4-initial-intake-audit-v1"
_LIVE_EXECUTION_SESSION_VERSION_KEY = "p2_live_execution_session_version"

logger = logging.getLogger("campusflow.live_main")

# 小时/分钟下拉框完整选项：00~23 / 00~59，无步长限制。
# 内部统一使用 int（避免 Streamlit selectbox 状态序列化在 int/str 之间混用），
# 页面显示两位数由 format_func 负责。
_HOUR_OPTIONS = tuple(range(24))
_MINUTE_OPTIONS = tuple(range(60))

_LOCAL_BUSINESS_KEYS = (
    'material_inbox',
    STATE_KEY, RESULT_KEY, LAST_TURN_KEY, LAST_CACHE_KEY, MOVEMENT_DATA_KEY,
    LIVE_FINAL_TURN_KEY, LIVE_FINAL_TURN_SEQUENCE_KEY, EXECUTION_PLAN_CONTEXT_KEY,
    P4_FEEDBACK_DECISION_KEY, DAY_PREFERENCE_KEY, LATEST_USER_TEXT_KEY,
    LATEST_FEEDBACK_TEXT_KEY, TASK_ESTIMATE_DRAFT_KEY,
    TASK_ESTIMATE_ADDED_DRAFTS_KEY, WHAT_IF_PREVIEW_KEY,
    LIVE_INTAKE_CACHE_KEY, LIVE_ESTIMATE_DRAFT_SEQUENCE_KEY,
    PERSONAL_SETTINGS_KEY, PERSONAL_SETTINGS_PLAN_STALE_KEY,
    PERSONAL_SETTINGS_REVISION_KEY, PERSONAL_TRANSPORT_EXPLICIT_KEY,
    PERSONAL_OTHER_CAMPUS_COURSES_KEY,
)

_PROFILE_SESSION_KEYS = tuple(dict.fromkeys(
    _LOCAL_BUSINESS_KEYS + CAMPUS_BOUND_STATE_KEYS + (
        SELECTED_CAMPUS_ID_KEY, LIVE_SESSION_KEY, LIVE_REFERENCE_KEY,
        LIVE_REFERENCE_HOUR_KEY, LIVE_REFERENCE_MINUTE_KEY,
        LIVE_REFERENCE_SOURCE_KEY, LIVE_INTAKE_KEY, LIVE_CURRENT_LOCATION_KEY,
        LIVE_FEEDBACK_KEY, LIVE_MAP_KEY, LIVE_CAMPUS_SELECT_KEY,
        LIVE_ERROR_KEY, LIVE_ADAPTER_KEY, LIVE_ESTIMATE_TEXT_KEY,
        LIVE_ESTIMATE_IMAGE_KEY, LIVE_ESTIMATE_SUPPLEMENT_KEY,
        LOCAL_PROFILE_STORE_KEY, LOCAL_PROFILE_REVISION_KEY,
        LOCAL_PROFILE_RESTORE_DONE_KEY, LOCAL_PROFILE_STATUS_KEY,
        LOCAL_PROFILE_STALE_BUNDLE_KEY, LOCAL_PROFILE_STALE_KEY,
        LOCAL_PROFILE_SAVE_ERROR_KEY, PERSONAL_SETTINGS_STATUS_KEY,
        PERSONAL_SETTINGS_COURSE_ROWS_KEY, PERSONAL_SETTINGS_DRAFT_VALUES_KEY,
        PERSONAL_SETTINGS_DRAFT_REVISION_KEY, PERSONAL_SETTINGS_PANEL_OPEN_KEY,
        TIMETABLE_IMPORT_DRAFT_KEY, TIMETABLE_IMPORT_TEXT_KEY,TIMETABLE_IMPORT_IMAGE_KEY,
        TIMETABLE_IMPORT_DIAGNOSTIC_KEY, TIMETABLE_IMPORT_CAMPUS_KEY,
        TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY,
        AGENT_INTELLIGENCE_KEY, AGENT_CALL_TRACE_KEY,
        _LIVE_EXECUTION_SESSION_VERSION_KEY, AUTHENTICATION_CONTEXT_KEY,
        PROFILE_ENABLED_DRAFT_KEY, STUDENT_IDENTIFIER_DRAFT_KEY,
        "p2_reference_last_auto", "p2_reference_reset_requested",
        "p2_task_estimate_existing_target",
    )
))


_TOP_INPUT_CSS = CAMPUSFLOW_THEME_CSS


def _render_top_input_chrome(st):
    """Presentation-only shared chrome for the brand and the input cards."""
    st.markdown(_TOP_INPUT_CSS, unsafe_allow_html=True)
    render_navigation(st, BRAND_HEADER_HTML, PERSONAL_SETTINGS_PANEL_OPEN_KEY)
    if hasattr(st, "__version__"):
        # Keep its browsing context alive when the drawer closes: an iframe
        # mounted only inside the drawer cannot reliably restore focus after
        # its own removal. This bridge never owns any business/form state.
        import streamlit.components.v1 as components
        components.html(SETTINGS_ACCESSIBILITY_HTML, height=0, scrolling=False)


def _business_snapshot(store):
    """Capture only mutation-owned values so a failed disk save can roll back."""
    missing = object()
    values = {key: store.get(key, missing) for key in _LOCAL_BUSINESS_KEYS}
    # Refresh updates this small reservation envelope in place. Its fact
    # objects are immutable, but the envelope must survive a failed build.
    if isinstance(values.get(MOVEMENT_DATA_KEY), dict):
        values[MOVEMENT_DATA_KEY] = dict(values[MOVEMENT_DATA_KEY])
    return missing, values


def _restore_business_snapshot(store, snapshot):
    missing, values = snapshot
    for key, value in values.items():
        if value is missing:
            store.pop(key, None)
        else:
            store[key] = value


def _clear_profile_session(store):
    """Remove every user-owned fact while retaining only identity controls."""
    for key in _PROFILE_SESSION_KEYS:
        store.pop(key, None)
    for key in tuple(store):
        if isinstance(key, str) and (
            key.startswith("personal_settings_draft_")
            or key.startswith("cf_material_")
            or key.startswith("personal_settings_timetable_import_")
        ):
            store.pop(key, None)
    store.pop(PROFILE_IMPORT_LEGACY_DRAFT_KEY, None)
    store.pop("campusflow_profile_keep_current_session_draft", None)


def _has_unsaved_settings_draft(store):
    """Return true when switching profiles would discard an editing draft."""
    if store.get(TIMETABLE_IMPORT_DRAFT_KEY):
        return True
    try:
        return _settings_draft_from_widgets(
            store, bump_revision=False
        ) != load_personal_settings(store)
    except (ValueError, TypeError):
        return True


def _base_profile_store(persistence_factory):
    if persistence_factory is None:
        return LocalProfileStore()
    value = persistence_factory()
    if not isinstance(value, LocalProfileStore):
        raise LocalPersistenceError("当前存储实现不支持个人档案切换。")
    return value


def _profile_store_for(persistence_factory, identity):
    return _base_profile_store(persistence_factory).for_user(identity)


def _apply_profile_snapshot(store, snapshot, now):
    store[LOCAL_PROFILE_REVISION_KEY] = snapshot.revision
    if getattr(snapshot, 'material_inbox', None) is not None:
        store['material_inbox'] = snapshot.material_inbox
    if snapshot.personal_settings is not None:
        save_personal_settings(store, snapshot.personal_settings)
    if snapshot.estimate_draft is not None:
        store[TASK_ESTIMATE_DRAFT_KEY] = snapshot.estimate_draft
        store.setdefault(LIVE_ESTIMATE_TEXT_KEY, snapshot.estimate_draft.material.text)
        store.setdefault(
            LIVE_ESTIMATE_SUPPLEMENT_KEY,
            snapshot.estimate_draft.supplemental_context,
        )
    if snapshot.added_estimate_drafts:
        store[TASK_ESTIMATE_ADDED_DRAFTS_KEY] = dict(snapshot.added_estimate_drafts)
    record = snapshot.latest_plan
    if record is None:
        if snapshot.estimate_draft is not None:
            store[LOCAL_PROFILE_STATUS_KEY] = "已恢复个人档案中的估时草稿。"
        return
    try:
        registration = DEFAULT_CAMPUS_REGISTRY.get_registration(record.campus_id)
    except Exception:  # corrupted campus id is a recoverable read warning
        store[LOCAL_PROFILE_STATUS_KEY] = "档案中的校区已不可用，原记录仍保留。"
        return
    store[LIVE_CAMPUS_SELECT_KEY] = registration.display_name
    store[SELECTED_CAMPUS_ID_KEY] = record.campus_id
    store[LIVE_REFERENCE_HOUR_KEY] = record.reference_datetime.hour
    store[LIVE_REFERENCE_MINUTE_KEY] = record.reference_datetime.minute
    store[LIVE_REFERENCE_SOURCE_KEY] = (
        "restored_manual" if record.reference_source != "system_now" else "system_now"
    )
    store[_LIVE_EXECUTION_SESSION_VERSION_KEY] = _LIVE_EXECUTION_CACHE_VERSION
    if record.plan_date != now.date().isoformat():
        store[LOCAL_PROFILE_STALE_BUNDLE_KEY] = record.final_turn
        store[LOCAL_PROFILE_STALE_KEY] = True
        store[LOCAL_PROFILE_STATUS_KEY] = (
            "已找到个人档案上次保存的方案（{}），未自动复制为今天的安排。".format(
                record.plan_date
            )
        )
        return
    _restore_bundle_keys(store, record)
    stale = now.replace(second=0, microsecond=0) > record.reference_datetime
    store[LOCAL_PROFILE_STALE_KEY] = stale
    store[LOCAL_PROFILE_STATUS_KEY] = (
        "已恢复个人档案；上次方案待更新，参考时间为 {}。请在时间设置中确认后刷新方案。".format(
            record.reference_datetime.strftime("%H:%M")
        ) if stale else "已恢复个人档案。"
    )


def _reference_source(store, reference, now):
    value = store.get(LIVE_REFERENCE_SOURCE_KEY)
    if value in ("manual", "restored_manual"):
        return value
    delta = abs((reference - now.replace(second=0, microsecond=0)).total_seconds())
    return "system_now" if delta < 60 else "manual"


def _profile_save_payload(store, reference=None, campus_id=None, now=None):
    """Build an explicit profile snapshot without serializing session_state."""
    now = (now or datetime.now()).replace(microsecond=0)
    bundle = load_live_final_turn(store)
    plan_record = None
    if bundle is not None:
        plan_reference = bundle.state.reference_datetime
        plan_campus = campus_id or store.get(SELECTED_CAMPUS_ID_KEY)
        if not isinstance(plan_campus, str) or not plan_campus:
            raise LocalPersistenceError("保存当前方案前需要明确校区。")
        plan_record = SavedPlanRecord(
            plan_date=plan_reference.date().isoformat(),
            campus_id=plan_campus,
            reference_datetime=plan_reference,
            reference_source=_reference_source(
                store, reference or plan_reference, now
            ),
            saved_at=now,
            final_turn=bundle,
            day_preferences=store.get(DAY_PREFERENCE_KEY),
            latest_user_text=store.get(LATEST_USER_TEXT_KEY),
            latest_feedback_text=store.get(LATEST_FEEDBACK_TEXT_KEY),
            transport_was_explicit=bool(store.get(PERSONAL_TRANSPORT_EXPLICIT_KEY)),
        )
    draft = load_task_estimate_draft(store)
    # Uploaded images and their encodings never enter the local profile.
    persisted_draft = (
        draft if isinstance(draft, TaskEstimateDraft) and draft.material.kind == "text" else None
    )
    preserve_estimate_snapshot = bool(
        isinstance(draft, TaskEstimateDraft) and draft.material.kind == "image"
    )
    return {
        "plan_record": plan_record,
        "estimate_draft": persisted_draft,
        "added_estimate_drafts": store.get(TASK_ESTIMATE_ADDED_DRAFTS_KEY, {}),
        "personal_settings": load_personal_settings(store),
        "preserve_estimate_snapshot": preserve_estimate_snapshot,
        "material_inbox": store.get('material_inbox'),
    }


def _persist_local_profile(store, reference=None, campus_id=None, now=None):
    """Persist one coherent formal turn and the latest text-only estimate draft."""
    persistence = store.get(LOCAL_PROFILE_STORE_KEY)
    if persistence is None or not callable(getattr(persistence, "save", None)):
        return None
    payload = _profile_save_payload(
        store, reference=reference, campus_id=campus_id, now=now
    )
    revision = int(store.get(LOCAL_PROFILE_REVISION_KEY, 0) or 0)
    new_revision = persistence.save(revision, **payload)
    store[LOCAL_PROFILE_REVISION_KEY] = new_revision
    store.pop(LOCAL_PROFILE_SAVE_ERROR_KEY, None)
    return new_revision


def _restore_bundle_keys(store, record):
    """Restore one validated bundle into legacy compatibility keys exactly once."""
    bundle = record.final_turn
    store[STATE_KEY] = bundle.state
    store[RESULT_KEY] = bundle.result
    store[LAST_TURN_KEY] = bundle.turn
    store[LAST_CACHE_KEY] = bundle.turn.cache_key
    store[LIVE_FINAL_TURN_KEY] = bundle
    store[LIVE_FINAL_TURN_SEQUENCE_KEY] = bundle.version
    store[EXECUTION_PLAN_CONTEXT_KEY] = bundle.execution_context
    store[MOVEMENT_DATA_KEY] = {
        "active_blocks": bundle.movement_blocks,
        "p4_execution_base_state": bundle.result.original_state,
    }
    if bundle.feedback_decision is not None:
        store[P4_FEEDBACK_DECISION_KEY] = bundle.feedback_decision
    if record.day_preferences is not None:
        store[DAY_PREFERENCE_KEY] = record.day_preferences
    if record.latest_user_text:
        store[LATEST_USER_TEXT_KEY] = record.latest_user_text
    if record.latest_feedback_text:
        store[LATEST_FEEDBACK_TEXT_KEY] = record.latest_feedback_text
    store[PERSONAL_TRANSPORT_EXPLICIT_KEY] = bool(record.transport_was_explicit)


def _restore_local_profile_once(st, now, persistence_factory):
    """Load before any widget/default state; ordinary reruns are memory-only."""
    store = st.session_state
    if store.get(LOCAL_PROFILE_RESTORE_DONE_KEY):
        return
    identity = current_identity(store)
    if not identity.persistent:
        store[LOCAL_PROFILE_RESTORE_DONE_KEY] = True
        store[LOCAL_PROFILE_STATUS_KEY] = "当前为临时使用；开启个性化后可跨会话保存。"
        return
    persistence = _profile_store_for(persistence_factory, identity)
    store[LOCAL_PROFILE_STORE_KEY] = persistence
    store[LOCAL_PROFILE_RESTORE_DONE_KEY] = True
    try:
        snapshot = persistence.load()
    except (LocalPersistenceUnreadable, LocalPersistenceIncompatible) as exc:
        store[LOCAL_PROFILE_STATUS_KEY] = str(exc)
        store[LOCAL_PROFILE_REVISION_KEY] = 0
        return
    _apply_profile_snapshot(store, snapshot, now)


def _profile_directory_for(persistence_factory):
    return ProfileDirectory(_base_profile_store(persistence_factory).path)


def _session_has_personal_content(store):
    return bool(
        load_live_final_turn(store) is not None
        or load_task_estimate_draft(store) is not None
        or load_personal_settings(store) != PersonalSettings()
    )


def _switch_to_student_profile(
    store, persistence_factory, student_identifier, now,
    import_legacy=False, keep_current_session=False,
):
    """Resolve, validate and load a complete target before changing session facts."""
    if import_legacy and keep_current_session:
        raise LocalPersistenceError("旧本机档案和当前临时内容请选择一种迁入来源。")
    current = current_identity(store)
    directory = _profile_directory_for(persistence_factory)
    if current.persistent:
        _persist_local_profile(
            store, reference=now, campus_id=store.get(SELECTED_CAMPUS_ID_KEY), now=now
        )
    target = directory.resolve_student(student_identifier, create=True)
    target_store = directory.store_for(target)
    if import_legacy:
        directory.import_legacy_into(target)
    target_snapshot = target_store.load()
    if keep_current_session:
        if current.persistent:
            raise LocalPersistenceError("已有个人档案不能作为临时内容再次复制。")
        target_is_empty = (
            target_snapshot.revision == 0
            and target_snapshot.latest_plan is None
            and target_snapshot.estimate_draft is None
            and target_snapshot.personal_settings is None
        )
        if not target_is_empty:
            raise LocalPersistenceConflict("目标个人档案已有内容，未覆盖当前临时内容。")
        target_store.save(0, **_profile_save_payload(
            store, reference=now, campus_id=store.get(SELECTED_CAMPUS_ID_KEY), now=now
        ))
        target_snapshot = target_store.load()
    # Everything above may fail without touching the active browser state.
    _clear_profile_session(store)
    store[CURRENT_USER_CONTEXT_KEY] = target
    store[LOCAL_PROFILE_STORE_KEY] = target_store
    store[LOCAL_PROFILE_RESTORE_DONE_KEY] = True
    _apply_profile_snapshot(store, target_snapshot, now)
    store[PROFILE_SWITCH_STATUS_KEY] = "个人档案已启用。学号仅用于区分档案，不代表身份验证。"
    store[PERSONAL_SETTINGS_PANEL_OPEN_KEY] = True
    return target


def _switch_to_anonymous_profile(store, now):
    current = current_identity(store)
    if current.persistent:
        _persist_local_profile(
            store, reference=now, campus_id=store.get(SELECTED_CAMPUS_ID_KEY), now=now
        )
    _clear_profile_session(store)
    store[CURRENT_USER_CONTEXT_KEY] = new_anonymous_identity()
    store[LOCAL_PROFILE_RESTORE_DONE_KEY] = True
    store[LOCAL_PROFILE_STATUS_KEY] = "已切换为临时使用；没有读取任何个人档案。"
    store[PROFILE_SWITCH_STATUS_KEY] = "临时使用不会读取已保存的学生档案。"
    store[PERSONAL_SETTINGS_PANEL_OPEN_KEY] = True


def _switch_to_authenticated_profile(
    store, persistence_factory, assertion, now,
):
    """Resolve a verified subject and atomically publish its stored profile."""
    if not isinstance(assertion, VerifiedExternalIdentity):
        raise TypeError("verified external identity required")
    directory = _profile_directory_for(persistence_factory)
    resolution = directory.resolve_external_identity(assertion, create=True)
    active_auth = current_authentication_context(store)
    active_identity = current_identity(store)
    if (
        active_auth.authenticated
        and active_auth.identity_link_id == resolution.identity_link_id
        and active_identity.user_id == resolution.identity.user_id
    ):
        return active_identity
    target_store = directory.store_for(resolution.identity)
    target_snapshot = target_store.load()
    if active_identity.persistent:
        _persist_local_profile(
            store, reference=now, campus_id=store.get(SELECTED_CAMPUS_ID_KEY), now=now
        )
    # Target resolution, target decoding and source save have all succeeded.
    # No user-owned browser fact changes before this point.
    _clear_profile_session(store)
    activate_authentication_context(
        store,
        authenticated_context(
            resolution.identity.user_id,
            assertion.provider,
            resolution.identity_link_id,
        ),
    )
    store[LOCAL_PROFILE_STORE_KEY] = target_store
    store[LOCAL_PROFILE_RESTORE_DONE_KEY] = True
    _apply_profile_snapshot(store, target_snapshot, now)
    store[PROFILE_SWITCH_STATUS_KEY] = "已恢复认证对应的个人档案。"
    return resolution.identity


def _fail_closed_external_identity(store):
    """Remove prior user facts when a configured auth source becomes invalid."""
    _clear_profile_session(store)
    store[CURRENT_USER_CONTEXT_KEY] = new_anonymous_identity()
    store[LOCAL_PROFILE_RESTORE_DONE_KEY] = True
    store[LOCAL_PROFILE_STATUS_KEY] = "认证状态暂时无法确认，已切换为不读取档案的临时会话。"


def _synchronize_external_identity(
    store, persistence_factory, assertion_loader, now,
):
    """Read only a trusted server-side assertion loader; never URL/form identity."""
    store[EXTERNAL_AUTH_MODE_KEY] = assertion_loader is not None
    if assertion_loader is None:
        return current_identity(store)
    try:
        assertion = assertion_loader()
        if assertion is None:
            if current_identity(store).persistent:
                _switch_to_anonymous_profile(store, now)
            return current_identity(store)
        if not isinstance(assertion, VerifiedExternalIdentity):
            raise TypeError("trusted identity loader returned an invalid assertion")
        return _switch_to_authenticated_profile(
            store, persistence_factory, assertion, now
        )
    except (TypeError, ValueError, LocalPersistenceError, LocalPersistenceConflict):
        if current_identity(store).persistent:
            _fail_closed_external_identity(store)
        raise


def _persist_after_mutation(st, before, reference=None, campus_id=None, now=None):
    persistence = st.session_state.get(LOCAL_PROFILE_STORE_KEY)
    if persistence is None or not callable(getattr(persistence, "save", None)):
        st.session_state[LOCAL_PROFILE_STATUS_KEY] = (
            "本次会话内容已更新；开启个性化后可保存到个人档案。"
        )
        return True
    try:
        _persist_local_profile(
            st.session_state, reference=reference, campus_id=campus_id, now=now
        )
    except (LocalPersistenceError, LocalPersistenceConflict) as exc:
        _restore_business_snapshot(st.session_state, before)
        st.session_state[LOCAL_PROFILE_SAVE_ERROR_KEY] = str(exc)
        st.warning(str(exc))
        return False
    st.session_state[LOCAL_PROFILE_STATUS_KEY] = "已在本机保存。"
    return True


def _prepare_restored_replan(store, reference, config):
    """Rebase a stale recovered day only after the user requests an update."""
    if not store.get(LOCAL_PROFILE_STALE_KEY):
        return
    bundle = load_live_final_turn(store)
    if bundle is None or bundle.state.reference_datetime.date() != reference.date():
        return
    from datetime import timedelta
    from src.p2_window_derivation import derive_day_state

    day_end = bundle.state.day_end
    if day_end <= reference:
        day_end = reference + timedelta(hours=2)
    rebased = derive_day_state(
        reference,
        day_end,
        bundle.state.commitments,
        bundle.state.tasks,
        default_safety_buffer_minutes=config.default_safety_buffer_minutes,
        travel_minutes_by_commitment=config.travel_minutes_by_commitment,
        history=bundle.state.history,
        reference_datetime=reference,
    )
    context = load_execution_context(store).with_current_location(
        CurrentLocationContext()
    )
    store[STATE_KEY] = rebased
    store[EXECUTION_PLAN_CONTEXT_KEY] = context
    store[MOVEMENT_DATA_KEY] = {"p4_execution_base_state": rebased}


def _next_estimate_draft_id(store):
    sequence = int(store.get(LIVE_ESTIMATE_DRAFT_SEQUENCE_KEY, 0) or 0) + 1
    store[LIVE_ESTIMATE_DRAFT_SEQUENCE_KEY] = sequence
    return "task_estimate_{:03d}".format(sequence)


def _estimator_material(text, uploaded, previous_draft=None):
    """Read upload bytes only for an explicit estimate/add action."""
    text = str(text or "").strip()
    if text and uploaded is not None:
        raise TaskEstimationError("文字和图片请选择一种，这一版一次估算一份任务。")
    if text:
        return make_material(text=text)
    if uploaded is not None:
        return make_material(
            image_name=getattr(uploaded, "name", None),
            image_mime=getattr(uploaded, "type", None),
            image_bytes=uploaded.getvalue(),
        )
    if previous_draft is not None and previous_draft.material.kind == "image":
        return previous_draft.material
    return make_material(text="")


def _run_estimator_action(
    store, session, adapter, material, supplement,
    confirmed_scope=None, confirmed_completion=None, previous_result=None,
):
    """The only task-estimation model boundary used by the live page."""
    previous = load_task_estimate_draft(store)
    draft_id = previous.draft_id if previous is not None else _next_estimate_draft_id(store)
    outcome = run_task_estimation(
        draft_id,
        material,
        supplement,
        session.caller,
        repair_caller=session.repair_caller,
        image_caller=adapter.task_estimation_caller,
        image_supported=bool(getattr(adapter, "supports_image_inputs", False)),
        confirmed_scope=confirmed_scope,
        confirmed_completion=confirmed_completion,
        previous_result=previous_result,
        default_user_context=load_personal_settings(store).estimation_context(),
    )
    save_task_estimate_draft(store, outcome.draft)
    return outcome


def _settings_widget_key(name):
    return "personal_settings_draft_" + name


def _initialize_settings_draft(store, reopening=False):
    settings = load_personal_settings(store)
    if store.get(PERSONAL_SETTINGS_DRAFT_REVISION_KEY) == settings.revision:
        for key, value in dict(store.get(PERSONAL_SETTINGS_DRAFT_VALUES_KEY, {})).items():
            if reopening:
                # A removed native date widget can still deserialize to its
                # default through SessionState. setdefault would keep today
                # instead of the user's explicitly retained draft date.
                store[key] = value
            else:
                store.setdefault(key, value)
        if store.get(_settings_widget_key("travel_mode")) not in ("walk", "bike"):
            store[_settings_widget_key("travel_mode")] = settings.travel_mode or "walk"
        if store.get(_settings_widget_key("learning_rhythm")) not in ("fewer_switches", "rest_breaks"):
            store[_settings_widget_key("learning_rhythm")] = settings.learning_rhythm or "rest_breaks"
        store.setdefault(PERSONAL_SETTINGS_PLACE_CAMPUS_KEY, "beiyangyuan")
        return
    values = {
        "travel_mode": settings.travel_mode or "walk",
        "learning_rhythm": settings.learning_rhythm or "rest_breaks",
        "lunch_start": settings.lunch_start or "",
        "lunch_end": settings.lunch_end or "",
        "dinner_start": settings.dinner_start or "",
        "dinner_end": settings.dinner_end or "",
        "default_note": settings.default_note,
        "semester_first": (
            datetime.strptime(settings.semester_first_monday, "%Y-%m-%d").date()
            if settings.semester_first_monday else date.today()
        ),
        "semester_end": (
            datetime.strptime(settings.semester_end_date, "%Y-%m-%d").date()
            if settings.semester_end_date else date.today() + timedelta(days=120)
        ),
        "semester_enabled": settings.semester_first_monday is not None,
    }
    for name, value in values.items():
        store[_settings_widget_key(name)] = value
    for campus_id in ("weijinlu", "beiyangyuan"):
        for role in ("dormitory", "study"):
            place = settings.place_for(campus_id, role)
            store[_settings_widget_key("{}_{}_text".format(campus_id, role))] = (
                place.raw_text if place else ""
            )
            store[_settings_widget_key("{}_{}_anchor".format(campus_id, role))] = (
                place.node_id if place and place.node_id
                else "__unmatched__" if place else ""
            )
    store.setdefault(PERSONAL_SETTINGS_PLACE_CAMPUS_KEY, "beiyangyuan")
    store[PERSONAL_SETTINGS_COURSE_ROWS_KEY] = [
        {
            "template_ref": item.template_ref,
            "title": item.title,
            "weekday": item.weekday,
            "start_time": item.start_time,
            "end_time": item.end_time or "",
            "start_week": item.start_week,
            "end_week": item.end_week,
            "week_pattern": item.week_pattern,
            "campus_id": item.campus_id,
            "location_text": item.location_text or "",
            "location_node_id": item.location_node_id or "",
        }
        for item in settings.courses
    ]
    store[PERSONAL_SETTINGS_DRAFT_REVISION_KEY] = settings.revision
    _capture_settings_draft_widgets(store)


def _capture_settings_draft_widgets(store):
    store[PERSONAL_SETTINGS_DRAFT_VALUES_KEY] = {
        key: value for key, value in store.items()
        if isinstance(key, str) and (key.startswith("personal_settings_draft_")
            or key in (TIMETABLE_IMPORT_TEXT_KEY, TIMETABLE_IMPORT_CAMPUS_KEY,
                       TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY)
            or key.startswith("personal_settings_timetable_import_import_item_")
            or key.startswith("personal_settings_timetable_choice_"))
        and key not in (PERSONAL_SETTINGS_DRAFT_REVISION_KEY, PERSONAL_SETTINGS_DRAFT_VALUES_KEY)
    }


def _settings_draft_from_widgets(store, bump_revision=True):
    current = load_personal_settings(store)
    places = []
    for campus_id in ("weijinlu", "beiyangyuan"):
        map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id)
        for role in ("dormitory", "study"):
            text = str(store.get(_settings_widget_key("{}_{}_text".format(campus_id, role)), "") or "").strip()
            anchor = str(store.get(_settings_widget_key("{}_{}_anchor".format(campus_id, role)), "") or "").strip()
            if anchor == "__unmatched__" and text:
                # Retain a legacy unmatched value exactly as an unresolved
                # preference.  Re-running generic place resolution here could
                # silently bind it to a POI outside the role-filtered choices.
                places.append(SavedCampusPlace(campus_id, role, text))
            elif anchor:
                node = next(
                    (item for item in saved_place_options(map_data, role) if item.id == anchor),
                    None,
                )
                if node is None:
                    raise ValueError("所选常用地点类型或校区不正确。")
                places.append(resolve_saved_place(node.name, campus_id, role, map_data, anchor))
    courses = []
    for index, row in enumerate(store.get(PERSONAL_SETTINGS_COURSE_ROWS_KEY, ())):
        title = str(store.get(_settings_widget_key("course_{}_title".format(index)), row.get("title", "")) or "").strip()
        if not title:
            raise ValueError("第{}门课缺少名称，请补全或删除这一条。".format(index + 1))
        campus_id = str(store.get(_settings_widget_key("course_{}_campus".format(index)), row.get("campus_id", "weijinlu")))
        raw_location = str(store.get(_settings_widget_key("course_{}_location".format(index)), row.get("location_text", "")) or "").strip()
        anchor = str(store.get(_settings_widget_key("course_{}_anchor".format(index)), row.get("location_node_id", "")) or "").strip()
        resolved = None
        if raw_location:
            resolved = resolve_saved_place(
                raw_location, campus_id, "study",
                DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id), anchor or None,
            )
        courses.append(CourseTemplate(
            template_ref=row.get("template_ref") or "course_{:03d}".format(index + 1),
            title=title,
            weekday=int(store.get(_settings_widget_key("course_{}_weekday".format(index)), row.get("weekday", 1))),
            start_time=str(store.get(_settings_widget_key("course_{}_start".format(index)), row.get("start_time", "08:00"))),
            end_time=(str(store.get(_settings_widget_key("course_{}_end".format(index)), row.get("end_time", ""))).strip() or None),
            start_week=int(store.get(_settings_widget_key("course_{}_start_week".format(index)), row.get("start_week", 1))),
            end_week=int(store.get(_settings_widget_key("course_{}_end_week".format(index)), row.get("end_week", 18))),
            week_pattern=str(store.get(_settings_widget_key("course_{}_pattern".format(index)), row.get("week_pattern", "every"))),
            campus_id=campus_id,
            location_text=raw_location or None,
            location_node_id=(resolved.node_id if resolved else None),
            location_display_name=(resolved.display_name if resolved else None),
        ))
    semester_enabled = bool(store.get(_settings_widget_key("semester_enabled"), False))
    first = store.get(_settings_widget_key("semester_first"))
    end = store.get(_settings_widget_key("semester_end"))
    value = PersonalSettings(
        revision=current.revision,
        travel_mode=store.get(_settings_widget_key("travel_mode")) or "walk",
        learning_rhythm=store.get(_settings_widget_key("learning_rhythm")) or "rest_breaks",
        lunch_start=str(store.get(_settings_widget_key("lunch_start"), "") or "").strip() or None,
        lunch_end=str(store.get(_settings_widget_key("lunch_end"), "") or "").strip() or None,
        dinner_start=str(store.get(_settings_widget_key("dinner_start"), "") or "").strip() or None,
        dinner_end=str(store.get(_settings_widget_key("dinner_end"), "") or "").strip() or None,
        default_note=str(store.get(_settings_widget_key("default_note"), "") or ""),
        saved_places=tuple(places),
        semester_first_monday=(first.isoformat() if semester_enabled else None),
        semester_end_date=(end.isoformat() if semester_enabled else None),
        courses=tuple(courses),
        occurrence_overrides=current.occurrence_overrides,
        default_walk_hint_seen=current.default_walk_hint_seen,
    )
    return with_next_revision(value) if bump_revision else value


def _render_place_fields(st, campus_id):
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id)
    names = {item.id: item.name for item in map_data.nodes}
    for role, role_label in (("dormitory", "宿舍"), ("study", "常用学习地点")):
        key = _settings_widget_key("{}_{}_anchor".format(campus_id, role))
        current = str(st.session_state.get(key, "") or "")
        options = [""] + [item.id for item in saved_place_options(map_data, role)]
        if current == "__unmatched__":
            options.insert(1, current)
        elif current and current not in options:
            st.session_state[key] = ""
        with setting_row(st, role_label):
            st.selectbox(
                role_label, options, key=key, label_visibility='collapsed',
                format_func=lambda value, lookup=names, saved_text=str(st.session_state.get(
                    _settings_widget_key("{}_{}_text".format(campus_id, role)), "") or ""
                ): (
                    "不设置" if not value
                    else "尚未匹配：{}".format(saved_text) if value == "__unmatched__"
                    else lookup.get(value, value)
                ),
            )
        saved = load_personal_settings(st.session_state).place_for(campus_id, role)
        if saved is not None and not saved.matched and current == "__unmatched__":
            st.caption("原记录尚未匹配，请选择校园地点后再用于路线。")


def _import_widget_key(item_ref, name):
    return "personal_settings_timetable_import_{}_{}".format(item_ref, name)


def _settings_timetable_campus(store):
    """Reuse the campus selected in Personal Settings > 常用地点."""
    value = store.get(PERSONAL_SETTINGS_PLACE_CAMPUS_KEY)
    return value if value in ("beiyangyuan", "weijinlu") else None


def _render_timetable_import_diagnostic(st, diagnostic):
    """Show only bounded structural facts; never model text, prompt or image data."""
    if diagnostic is None:
        return
    with st.expander("识别情况", expanded=False):
        st.caption("识别到 {} 门课程 · {} 组需要核对".format(
            diagnostic.draft_course_count, diagnostic.ambiguity_count))
        if diagnostic.exception_type:
            st.caption("部分内容未识别，原课表未改变。可以修改材料后重新识别。")


def _settings_semester_context(store):
    current = load_personal_settings(store)
    if not store.get(_settings_widget_key("semester_enabled"), False):
        raise TimetableImportError("请先启用手动课表并填写学期日期。")
    first = store.get(_settings_widget_key("semester_first"))
    end = store.get(_settings_widget_key("semester_end"))
    if not isinstance(first, date) or not isinstance(end, date):
        raise TimetableImportError("请先填写第1教学周周一和学期结束日期。")
    return replace(
        current,
        semester_first_monday=first.isoformat(),
        semester_end_date=end.isoformat(),
    )


def _resolve_imported_course_anchors(settings, template_refs):
    refs = set(template_refs)
    courses = []
    for item in settings.courses:
        if item.template_ref not in refs or not item.location_text:
            courses.append(item)
            continue
        resolved = resolve_saved_place(
            item.location_text, item.campus_id, "study",
            DEFAULT_CAMPUS_REGISTRY.get_campus_map(item.campus_id),
        )
        courses.append(replace(
            item,
            location_node_id=(resolved.node_id if resolved else None),
            location_display_name=(resolved.display_name if resolved else None),
        ))
    return replace(settings, courses=tuple(courses))


def _render_timetable_importer(
    st, caller=None, repair_caller=None, image_caller=None,
    image_supported=False, missing=(), reference=None,
):
    """Native-widget timetable recognition, editable preview and atomic import."""
    store = st.session_state
    st.markdown(
        '<div class="cf-timetable-import"><div class="cf-settings-section">导入课表</div>'
        '<div class="cf-settings-help">粘贴课表文字或上传截图。CampusFlow 会先整理成可编辑预览，确认后才保存。</div></div>',
        unsafe_allow_html=True,
    )
    raw_text = st.text_area(
        "课表文字",
        key=TIMETABLE_IMPORT_TEXT_KEY,
        placeholder="高等数学，周一 10:00–11:30，第1–16周，北洋园校区，46教学楼",
        height=110,
    )
    uploaded = st.file_uploader(
        "上传课表截图",type=("jpg","jpeg","png"),key=TIMETABLE_IMPORT_IMAGE_KEY,
    ) if image_supported and callable(getattr(st,"file_uploader",None)) else None
    if image_supported and callable(getattr(st,"file_uploader",None)):
        st.caption("截图会先进入可编辑预览；复杂课表请逐项确认。")
    use_image = uploaded is not None
    recognize = st.button(
        "识别课表",
        key="personal_settings_timetable_import_run",
        disabled=(not use_image and not str(raw_text or "").strip()),
    )
    if use_image and str(raw_text or "").strip():
        st.caption("当前将识别已上传的课表截图；文字内容不会同时提交。")
    if recognize:
        if missing:
            st.warning("请先连接模型服务后再识别课表；手动录入仍可使用。")
            return
        try:
            with (st.spinner("THINKING.......") if callable(getattr(st, "spinner", None)) else nullcontext()):
                semester = _settings_semester_context(store)
                if use_image:
                    draft = run_timetable_image_import(
                        uploaded.name,uploaded.type,uploaded.getvalue(),semester,
                        image_caller,repair_caller=repair_caller,
                    )
                else:
                    draft = run_timetable_text_import(
                        raw_text, semester, caller, repair_caller=repair_caller,
                    )
        except (TimetableImportError, ValueError, TypeError) as exc:
            diagnostic = getattr(exc, "diagnostic", None)
            if diagnostic is not None:
                store[TIMETABLE_IMPORT_DIAGNOSTIC_KEY] = diagnostic
            st.warning(sanitize_user_facing_text(str(exc)))
            _render_timetable_import_diagnostic(st, diagnostic)
            return
        for key in tuple(store):
            if isinstance(key, str) and key.startswith("personal_settings_timetable_import_import_item_"):
                store.pop(key, None)
        store.pop(TIMETABLE_IMPORT_CAMPUS_KEY, None)
        store.pop(TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY, None)
        store[TIMETABLE_IMPORT_DRAFT_KEY] = draft
        if draft.diagnostic is not None:
            store[TIMETABLE_IMPORT_DIAGNOSTIC_KEY] = draft.diagnostic
        else:
            store.pop(TIMETABLE_IMPORT_DIAGNOSTIC_KEY, None)
        _rerun(st)
        return

    draft = store.get(TIMETABLE_IMPORT_DRAFT_KEY)
    if not isinstance(draft, TimetableImportDraft):
        _render_timetable_import_diagnostic(
            st, store.get(TIMETABLE_IMPORT_DIAGNOSTIC_KEY),
        )
        return
    source_current = timetable_preview_matches(
        draft,raw_text,image_mime=(uploaded.type if uploaded is not None else None),
        image_bytes=(uploaded.getvalue() if uploaded is not None else None),
    )
    if not source_current:
        st.warning("课表材料已变化或原截图已不在当前会话。请重新识别后再导入；原课表未改变。")
    if draft.diagnostic is not None:
        _render_timetable_import_diagnostic(st, draft.diagnostic)
    st.markdown('<div class="cf-import-preview-title">识别预览</div>', unsafe_allow_html=True)
    campus_options = (None, "beiyangyuan", "weijinlu")
    if store.get(TIMETABLE_IMPORT_CAMPUS_KEY) not in campus_options:
        store[TIMETABLE_IMPORT_CAMPUS_KEY] = _settings_timetable_campus(store)
    store.setdefault(TIMETABLE_IMPORT_CAMPUS_KEY, _settings_timetable_campus(store))
    import_campus = st.selectbox(
        "本次课表校区", campus_options,
        format_func=lambda value: (
            "暂不设置" if value is None
            else DEFAULT_CAMPUS_REGISTRY.get_registration(value).display_name
        ),
        key=TIMETABLE_IMPORT_CAMPUS_KEY,
    )
    if import_campus is not None:
        st.caption(
            "本次课表默认按{}处理；截图中明确标出的其他校区仍以截图为准。".format(
                DEFAULT_CAMPUS_REGISTRY.get_registration(import_campus).display_name
            )
        )
    else:
        st.caption("本次课表尚未设置默认校区；未标明校区的课程需要确认。")

    previous_import_campus = store.get(TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY, "__unset__")
    if previous_import_campus != import_campus:
        for source_row in draft.rows:
            if source_row.campus_id is None:
                store[_import_widget_key(source_row.item_ref, "campus")] = import_campus
        store[TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY] = import_campus
    preview_rows = apply_timetable_import_default_campus(draft.rows, import_campus)
    if draft.message and (not preview_rows or any(not row.ready for row in preview_rows)):
        st.warning(sanitize_user_facing_text(draft.message))
    edited = []
    weekdays = (None, 1, 2, 3, 4, 5, 6, 7)
    patterns = (None, "every", "odd", "even")
    campuses = (None, "weijinlu", "beiyangyuan")
    group_choices = {}
    groups = {}
    for row in preview_rows:
        if row.choice_group:
            groups.setdefault(row.choice_group, []).append(row)
    for group, candidates in groups.items():
        options = (None,) + tuple(row.item_ref for row in candidates)
        current = next((row.item_ref for row in candidates if row.selected), None)
        group_choices[group] = st.radio(
            "这里识别到多个可能教学班，请选择你实际上的一个",
            options,
            index=options.index(current),
            format_func=lambda value, rows=tuple(candidates): (
                "暂不选择" if value is None else timetable_choice_label(
                    next(row for row in rows if row.item_ref == value)
                )
            ),
            key="personal_settings_timetable_choice_{}".format(candidates[0].item_ref),
        )
    for row in preview_rows:
        # Seed values once; explicit defaults plus restored session values
        # cause Streamlit duplication warnings and changing widget identity.
        defaults = dict(selected=row.selected, title=row.title, teacher=row.teacher or '',
            weekday=row.weekday if row.weekday in weekdays else None,
            start=row.start_time or '', end=row.end_time or '',
            start_week=int(row.start_week) if row.start_week is not None and 1 <= row.start_week <= 60 else 0,
            end_week=int(row.end_week) if row.end_week is not None and 1 <= row.end_week <= 60 else 0,
            pattern=row.week_pattern if row.week_pattern in patterns else None,
            campus=row.campus_id if row.campus_id in campuses else None, location=row.location_text or '')
        for name, initial in defaults.items():
            store.setdefault(_import_widget_key(row.item_ref, name), initial)
        with st.expander(row.title or "待补课程", expanded=True):
            if row.choice_group:
                selected = group_choices.get(row.choice_group) == row.item_ref
                st.caption("候选教学班：{}".format(timetable_choice_label(row)))
            else:
                selected = st.checkbox(
                    "导入这门课",
                    key=_import_widget_key(row.item_ref, "selected"),
                )
            title = st.text_input(
                "课程名",
                key=_import_widget_key(row.item_ref, "title"),
            )
            teacher = st.text_input(
                "教师（可选）",
                key=_import_widget_key(row.item_ref, "teacher"),
            )
            c1, c2, c3 = st.columns(3)
            with c1:
                weekday = st.selectbox(
                    "星期", weekdays,
                    format_func=lambda value: "待补" if value is None else "星期{}".format("一二三四五六日"[value - 1]),
                    key=_import_widget_key(row.item_ref, "weekday"),
                )
            with c2:
                start = st.text_input(
                    "开始时间", placeholder="HH:MM",
                    key=_import_widget_key(row.item_ref, "start"),
                )
            with c3:
                end = st.text_input(
                    "结束时间", placeholder="HH:MM",
                    key=_import_widget_key(row.item_ref, "end"),
                )
            w1, w2, w3 = st.columns(3)
            with w1:
                start_week = st.number_input(
                    "起始周（0=待补）", min_value=0, max_value=60,
                    key=_import_widget_key(row.item_ref, "start_week"),
                )
            with w2:
                end_week = st.number_input(
                    "结束周（0=待补）", min_value=0, max_value=60,
                    key=_import_widget_key(row.item_ref, "end_week"),
                )
            with w3:
                pattern = st.selectbox(
                    "频率", patterns,
                    format_func=lambda value: {None: "待补", "every": "每周", "odd": "单周", "even": "双周"}[value],
                    key=_import_widget_key(row.item_ref, "pattern"),
                )
            campus = st.selectbox(
                "校区", campuses,
                format_func=lambda value: "待补" if value is None else DEFAULT_CAMPUS_REGISTRY.get_registration(value).display_name,
                key=_import_widget_key(row.item_ref, "campus"),
            )
            location = st.text_input(
                "上课地点",
                key=_import_widget_key(row.item_ref, "location"),
            )
            value = edit_timetable_import_row(
                row, selected=selected, title=title, weekday=weekday,
                start_time=start, end_time=end, start_week=int(start_week),
                end_week=int(end_week), week_pattern=pattern,
                campus_id=campus, location_text=location, teacher=teacher,
            )
            edited.append(value)
            for issue in value.errors:
                st.warning(issue)
            for warning in value.warnings:
                st.caption(warning)
            if value.ready and value.location_text:
                map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(value.campus_id)
                matched = resolve_saved_place(
                    value.location_text, value.campus_id, "study", map_data,
                )
                if matched is not None and not matched.matched:
                    st.caption("地点尚未匹配校园地图；导入后会保留原文，不会猜测锚点。")

    try:
        base = _settings_draft_from_widgets(store, bump_revision=False)
        check = preview_timetable_import(base, edited)
    except (ValueError, TypeError) as exc:
        check = None
        st.warning(sanitize_user_facing_text(str(exc)))
    if check is not None:
        for error in check.errors:
            st.warning(sanitize_user_facing_text(error))
        if check.duplicate_item_refs:
            st.caption("已存在的相同课程会跳过，不会重复创建。")
    confirm = st.button(
        "确认导入并保存设置",
        key="personal_settings_timetable_import_confirm",
        type="primary",
        disabled=(not source_current or check is None or not check.ready_to_import),
    )
    if not confirm or not source_current or check is None or not check.ready_to_import:
        return
    before = _business_snapshot(store)
    try:
        merged = merge_timetable_import(base, edited)
        imported_settings = _resolve_imported_course_anchors(
            merged.settings, merged.added_template_refs
        )
        save_personal_settings(store, imported_settings)
        clear_what_if_preview(store)
        store[PERSONAL_SETTINGS_PLAN_STALE_KEY] = True
        if not _persist_after_mutation(st, before, reference=reference):
            return
    except (TimetableImportError, ValueError, TypeError) as exc:
        st.warning(sanitize_user_facing_text(str(exc)))
        return
    store.pop(TIMETABLE_IMPORT_DRAFT_KEY, None)
    store.pop(TIMETABLE_IMPORT_CAMPUS_KEY, None)
    store.pop(TIMETABLE_IMPORT_APPLIED_CAMPUS_KEY, None)
    store[PERSONAL_SETTINGS_STATUS_KEY] = "已导入{}门课程并保存；当前方案尚未更新。".format(
        len(merged.added_template_refs)
    )
    store.pop(PERSONAL_SETTINGS_DRAFT_REVISION_KEY, None)
    _rerun(st)


def _render_course_editor(
    st, caller=None, repair_caller=None, image_caller=None,
    image_supported=False, missing=(), reference=None,
):
    store = st.session_state
    st.checkbox("启用手动课表", key=_settings_widget_key("semester_enabled"))
    if not store.get(_settings_widget_key("semester_enabled")):
        return
    date_left, date_right = st.columns(2)
    with date_left:
        st.date_input("第1教学周的周一", key=_settings_widget_key("semester_first"))
    with date_right:
        st.date_input("有效结束日期", key=_settings_widget_key("semester_end"))
    _render_timetable_importer(
        st, caller=caller, repair_caller=repair_caller,image_caller=image_caller,
        image_supported=image_supported,
        missing=missing, reference=reference,
    )
    rows = list(store.get(PERSONAL_SETTINGS_COURSE_ROWS_KEY, ()))
    remove_index = None
    for index, row in enumerate(rows):
        summary = "{} · 周{} {}–{}".format(
            row.get("title"), "一二三四五六日"[int(row.get("weekday", 1)) - 1],
            row.get("start_time", "待补"), row.get("end_time") or "待补结束时间",
        ) if row.get("title") else "新课程"
        with st.expander(summary, expanded=not bool(row.get("title"))):
            st.text_input("课程名", value=row.get("title", ""), key=_settings_widget_key("course_{}_title".format(index)))
            c1, c2, c3 = st.columns(3)
            with c1:
                st.selectbox("星期", tuple(range(1, 8)), index=max(0, int(row.get("weekday", 1)) - 1), key=_settings_widget_key("course_{}_weekday".format(index)))
            with c2:
                st.text_input("开始时间", value=row.get("start_time", "08:00"), key=_settings_widget_key("course_{}_start".format(index)))
            with c3:
                st.text_input("结束时间（可留空）", value=row.get("end_time", ""), key=_settings_widget_key("course_{}_end".format(index)))
            w1, w2, w3 = st.columns(3)
            with w1:
                st.number_input("起始周", min_value=1, max_value=60, value=int(row.get("start_week", 1)), key=_settings_widget_key("course_{}_start_week".format(index)))
            with w2:
                st.number_input("结束周", min_value=1, max_value=60, value=int(row.get("end_week", 18)), key=_settings_widget_key("course_{}_end_week".format(index)))
            with w3:
                patterns = ("every", "odd", "even")
                st.selectbox("频率", patterns, index=patterns.index(row.get("week_pattern", "every")), format_func=lambda x: {"every": "每周", "odd": "单周", "even": "双周"}[x], key=_settings_widget_key("course_{}_pattern".format(index)))
            campus_options = ("weijinlu", "beiyangyuan")
            st.selectbox("校区", campus_options, index=campus_options.index(row.get("campus_id", "weijinlu")), format_func=lambda x: DEFAULT_CAMPUS_REGISTRY.get_registration(x).display_name, key=_settings_widget_key("course_{}_campus".format(index)))
            campus_id = store.get(_settings_widget_key("course_{}_campus".format(index)), row.get("campus_id", "weijinlu"))
            map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id)
            names = {item.id: item.name for item in map_data.nodes}
            st.text_input("上课地点", value=row.get("location_text", ""), key=_settings_widget_key("course_{}_location".format(index)))
            anchor_options = [""] + list(names)
            anchor_key = _settings_widget_key("course_{}_anchor".format(index))
            saved_anchor = store.get(anchor_key, row.get("location_node_id", ""))
            if saved_anchor not in anchor_options:
                # Campus changed in this draft: an anchor from the other map
                # cannot survive as if it were still valid.
                store[anchor_key] = ""
                saved_anchor = ""
            st.selectbox(
                "地点锚点（可选）", anchor_options,
                index=anchor_options.index(saved_anchor),
                format_func=lambda value, lookup=names: "不指定" if not value else lookup.get(value, value),
                key=anchor_key,
            )
            if st.button("删除这门课", key="personal_settings_remove_course_{}".format(index)):
                remove_index = index
    if remove_index is not None:
        rows.pop(remove_index)
        for key in tuple(store):
            if isinstance(key, str) and key.startswith("personal_settings_draft_course_"):
                store.pop(key, None)
        store[PERSONAL_SETTINGS_COURSE_ROWS_KEY] = rows
        _capture_settings_draft_widgets(store)
        _rerun(st)
    if st.button("新增课程", key="personal_settings_add_course"):
        existing = {row.get("template_ref") for row in rows}
        sequence = 1
        while "course_{:03d}".format(sequence) in existing:
            sequence += 1
        rows.append({
            "template_ref": "course_{:03d}".format(sequence), "title": "",
            "weekday": 1, "start_time": "08:00", "end_time": "",
            "start_week": 1, "end_week": 18, "week_pattern": "every",
            "campus_id": "weijinlu", "location_text": "", "location_node_id": "",
        })
        store[PERSONAL_SETTINGS_COURSE_ROWS_KEY] = rows
        _rerun(st)


def _render_profile_controls(st, persistence_factory, reference):
    """Compact, optional profile selector kept out of the main settings flow."""
    identity = current_identity(st.session_state)
    authentication = current_authentication_context(st.session_state)
    if st.session_state.get(EXTERNAL_AUTH_MODE_KEY) and not authentication.authenticated:
        st.checkbox("当前为临时使用", value=False, disabled=True, key="campusflow_external_auth_waiting")
        st.caption("需要个人档案时，请使用页面提供的认证入口；学号不能用于登录。")
        return False
    if authentication.authenticated:
        st.checkbox("认证个人档案已启用", value=True, disabled=True,
            key="campusflow_authenticated_profile_enabled")
        st.caption("档案由外部认证身份保护；退出请使用认证服务提供的退出入口。")
        directory = _profile_directory_for(persistence_factory)
        try:
            has_student_identifier = directory.has_student_identifier(identity)
        except LocalPersistenceError as exc:
            st.warning(sanitize_user_facing_text(str(exc)))
            has_student_identifier = True
        if has_student_identifier:
            st.caption("已关联学号资料；学号不是认证凭据，也不会发送给模型。")
            return False
        st.text_input("学号（可选）", key=STUDENT_IDENTIFIER_DRAFT_KEY,
            placeholder="用于关联天津大学个性化资料")
        st.caption("学号只作为可选档案资料，不决定认证结果，也不会发送给模型。")
        if st.button("关联学号资料", key="campusflow_authenticated_student_link"):
            try:
                directory.associate_student_identifier(
                    identity, st.session_state.get(STUDENT_IDENTIFIER_DRAFT_KEY)
                )
            except (ValueError, LocalPersistenceError, LocalPersistenceConflict) as exc:
                st.warning(sanitize_user_facing_text(str(exc)))
                return True
            st.session_state.pop(STUDENT_IDENTIFIER_DRAFT_KEY, None)
            st.session_state[PROFILE_SWITCH_STATUS_KEY] = "学号资料已关联；认证身份没有改变。"
            _rerun(st)
            return True
        return False

    st.checkbox("开启个性化", value=(identity.kind == "student"), key=PROFILE_ENABLED_DRAFT_KEY)
    profile_enabled = bool(st.session_state.get(PROFILE_ENABLED_DRAFT_KEY))
    switch_status = st.session_state.get(PROFILE_SWITCH_STATUS_KEY)
    if switch_status:
        st.caption(sanitize_user_facing_text(switch_status))
    elif identity.kind == "student":
        st.caption("个人档案已启用；完整学号只在切换档案时输入。")
    if profile_enabled:
        st.text_input("学号", key=STUDENT_IDENTIFIER_DRAFT_KEY,
            placeholder="用于区分并保存你的个人档案")
        st.caption("学号具有唯一性，但这里只用于选择档案，不是登录或身份认证。不会发送给模型。")
        legacy_available = False
        try:
            legacy_available = _profile_directory_for(persistence_factory).has_legacy_data()
        except LocalPersistenceError as exc:
            st.warning(sanitize_user_facing_text(str(exc)))
        if legacy_available and identity.kind != "student":
            st.checkbox("将升级前的本机档案复制到这个个人档案", key=PROFILE_IMPORT_LEGACY_DRAFT_KEY)
        keep_current = False
        if identity.kind == "anonymous" and _session_has_personal_content(st.session_state):
            keep_current = st.checkbox("将本次临时计划和设置保存到这个个人档案", value=True,
                key="campusflow_profile_keep_current_session_draft")
        if st.button("打开并使用此档案", key="campusflow_profile_switch"):
            import_legacy = bool(st.session_state.get(PROFILE_IMPORT_LEGACY_DRAFT_KEY))
            if _has_unsaved_settings_draft(st.session_state):
                st.warning("请先保存当前设置，或关闭面板保留草稿，再切换个人档案。")
                return True
            if import_legacy and keep_current:
                st.warning("旧本机档案和当前临时内容请选择一种迁入来源。")
                return True
            try:
                _switch_to_student_profile(
                    st.session_state, persistence_factory,
                    st.session_state.get(STUDENT_IDENTIFIER_DRAFT_KEY),
                    reference or datetime.now(), import_legacy=import_legacy,
                    keep_current_session=keep_current,
                )
            except (ValueError, LocalPersistenceError, LocalPersistenceConflict) as exc:
                st.warning(sanitize_user_facing_text(str(exc)))
                return True
            _rerun(st)
            return True
    elif identity.kind == "student":
        st.caption("关闭后将进入新的临时会话；当前个人档案会先安全保存。")
        if st.button("切换为临时使用", key="campusflow_profile_use_temporary"):
            if _has_unsaved_settings_draft(st.session_state):
                st.warning("请先保存当前设置，或关闭面板保留草稿，再切换为临时使用。")
                return True
            try:
                _switch_to_anonymous_profile(st.session_state, reference or datetime.now())
            except (LocalPersistenceError, LocalPersistenceConflict) as exc:
                st.warning(sanitize_user_facing_text(str(exc)))
                return True
            _rerun(st)
            return True
    else:
        st.caption("临时使用不要求学号，也不会读取其他人的个人档案。")
    return False


def _render_personal_settings_panel(
    st, reference=None, caller=None, repair_caller=None, image_caller=None,
    image_supported=False, missing=(),
    persistence_factory=None,
):
    if not st.session_state.get(PERSONAL_SETTINGS_PANEL_OPEN_KEY):
        st.session_state['cf_settings_was_open'] = False
        return False
    _initialize_settings_draft(st.session_state, reopening=not st.session_state.get('cf_settings_was_open',False))
    st.session_state['cf_settings_was_open'] = True
    st.markdown('<div class="cf-settings-backdrop"></div>', unsafe_allow_html=True)
    with st.container():
        st.markdown('<div class="cf-settings-marker"></div>', unsafe_allow_html=True)
        with st.container():
            actions = st.columns((8, 1))
            with actions[0]:
                st.markdown('<span class="cf-settings-action-marker"></span>', unsafe_allow_html=True)
                st.markdown('<div class="cf-settings-title">个人设置</div>', unsafe_allow_html=True)
            with actions[1]:
                close_clicked = st.button("×", key="personal_settings_close")
        if close_clicked:
            _capture_settings_draft_widgets(st.session_state)
            st.session_state[PERSONAL_SETTINGS_PANEL_OPEN_KEY] = False
            try:
                changed = _settings_draft_from_widgets(st.session_state, bump_revision=False) != load_personal_settings(st.session_state)
            except (ValueError, TypeError):
                changed = True  # An incomplete draft also deserves retention.
            if changed or st.session_state.get(TIMETABLE_IMPORT_DRAFT_KEY):
                st.session_state[PERSONAL_SETTINGS_STATUS_KEY] = "未保存的编辑草稿已保留。"
            _rerun(st)
            return True
        st.markdown('<div class="cf-settings-unsaved">保存后用于后续规划。本次安排中的明确要求优先。</div>', unsafe_allow_html=True)
        settings_notice = st.session_state.get(PERSONAL_SETTINGS_STATUS_KEY)
        if settings_notice:
            st.markdown('<div class="cf-persist-notice">{}</div>'.format(
                html.escape(sanitize_user_facing_text(settings_notice))
            ), unsafe_allow_html=True)
        tabs = st.tabs(("我的偏好", "常用地点", "我的课表"))
        with tabs[0]:
            from src.model_service_ui import render_settings_service
            render_settings_service(st)
            with st.expander("个人档案", expanded=False):
                if _render_profile_controls(st, persistence_factory, reference):
                    return True
            st.markdown('<div class="cf-settings-section">出行与学习</div>', unsafe_allow_html=True)
            with setting_row(st, '常用出行方式'):
                st.selectbox("常用出行方式", ("walk", "bike"), format_func=lambda x: {"walk": "步行", "bike": "骑行"}[x], key=_settings_widget_key("travel_mode"), label_visibility='collapsed')
            with setting_row(st, '学习节奏'):
                st.selectbox("学习节奏", ("rest_breaks", "fewer_switches"), format_func=lambda x: {"fewer_switches": "希望少切换", "rest_breaks": "希望适当穿插休息"}[x], key=_settings_widget_key("learning_rhythm"), label_visibility='collapsed')
            st.markdown('<div class="cf-settings-section">用餐时间 · 可留空</div>', unsafe_allow_html=True)
            l1, l2 = st.columns(2)
            with l1:
                st.text_input("常用午饭开始（可留空）", key=_settings_widget_key("lunch_start"), placeholder="11:30")
            with l2:
                st.text_input("常用午饭结束（可留空）", key=_settings_widget_key("lunch_end"), placeholder="13:00")
            d1, d2 = st.columns(2)
            with d1:
                st.text_input("常用晚饭开始（可留空）", key=_settings_widget_key("dinner_start"), placeholder="17:30")
            with d2:
                st.text_input("常用晚饭结束（可留空）", key=_settings_widget_key("dinner_end"), placeholder="19:00")
            st.markdown('<div class="cf-settings-section">其他偏好</div>', unsafe_allow_html=True)
            st.text_area(
                "还有什么希望 CampusFlow 平时记住？",
                key=_settings_widget_key("default_note"),
                label_visibility='collapsed', height=85,
                placeholder="我做数学比较慢；连续学习久了希望留一点休息",
            )
        with tabs[1]:
            st.markdown('<div class="cf-settings-section">常用地点</div>', unsafe_allow_html=True)
            place_campus = st.selectbox(
                "校区", ("beiyangyuan", "weijinlu"),
                format_func=lambda value: DEFAULT_CAMPUS_REGISTRY.get_registration(value).display_name,
                key=PERSONAL_SETTINGS_PLACE_CAMPUS_KEY,
            )
            _render_place_fields(st, place_campus)
            st.caption("宿舍只是常用目的地，不会被当成你当前所在的位置。")
        with tabs[2]:
            _render_course_editor(
                st, caller=caller, repair_caller=repair_caller,
                image_caller=image_caller,image_supported=image_supported,
                missing=missing, reference=reference,
            )
            st.caption("课表按规划日期和教学周展开；保存本身不会立刻重排当前方案。")
        with st.container():
            st.markdown('<span class="cf-settings-save-marker"></span>', unsafe_allow_html=True)
            save_clicked = st.button("保存设置", key="personal_settings_save", type="primary")
        if save_clicked:
            before = _business_snapshot(st.session_state)
            try:
                settings = _settings_draft_from_widgets(st.session_state)
                save_personal_settings(st.session_state, settings)
                clear_what_if_preview(st.session_state)
                st.session_state[PERSONAL_SETTINGS_PLAN_STALE_KEY] = True
                if not _persist_after_mutation(st, before, reference=reference):
                    return True
            except (ValueError, TypeError) as exc:
                st.warning(sanitize_user_facing_text(str(exc)))
                return True
            st.session_state[PERSONAL_SETTINGS_DRAFT_REVISION_KEY] = settings.revision
            _capture_settings_draft_widgets(st.session_state)
            st.session_state[PERSONAL_SETTINGS_STATUS_KEY] = (
                "设置已保存到个人档案；当前方案尚未更新。"
                if current_identity(st.session_state).persistent
                else "设置已用于本次临时会话；当前方案尚未更新。"
            )
            st.session_state[PERSONAL_SETTINGS_PANEL_OPEN_KEY] = False
            _rerun(st)
            return True
    return True


def _render_task_estimator(st, session, adapter, missing, reference):
    """Compact isolated estimator draft; returns True after an action rerun."""
    if not hasattr(st, "file_uploader") or not hasattr(st, "number_input"):
        # Older presentation test doubles intentionally expose only the core
        # live controls.  The production Streamlit surface has both methods.
        return False
    import html

    draft = load_task_estimate_draft(st.session_state)
    with st.expander(
        "任务时间不确定？帮我估一下",
        expanded=bool(st.session_state.get("p2_task_estimate_open", False)),
    ):
        st.markdown(
            '<div class="cf-estimate-heading">估一份任务需要的专注时间</div>'
            '<div class="cf-estimate-help">写下这一份任务包含什么，估完后仍由你确认采用时间。</div>',
            unsafe_allow_html=True,
        )
        # These draft widgets intentionally stay outside a Streamlit form.
        # Their ordinary reruns perform no model call, while either explicit
        # estimate button always sees the latest material and supplement.
        material_text = st.text_area(
            "任务文字",
            key=LIVE_ESTIMATE_TEXT_KEY,
            placeholder="完成高数作业第三章，并订正错题",
        )
        image_supported = bool(getattr(adapter, "supports_image_inputs", False))
        if image_supported:
            uploaded = st.file_uploader(
                "上传任务图片", type=("jpg", "jpeg", "png"),
                key=LIVE_ESTIMATE_IMAGE_KEY,
            )
            st.caption("支持 JPG、PNG，单张最大 5MB")
        else:
            uploaded = None
        supplement = st.text_area(
            "有什么会影响你完成速度？（可选）",
            key=LIVE_ESTIMATE_SUPPLEMENT_KEY,
            placeholder="这章刚学会，计算比较慢；前两题已经完成",
        )
        estimate_submitted = st.button(
            "重新估算" if draft is not None else "帮我估时",
            key="p2_task_estimate_run",
            type="secondary",
        )
        if estimate_submitted:
            if missing:
                st.error("请先连接模型服务后再估时，当前输入已保留。")
                return False
            before = _business_snapshot(st.session_state)
            try:
                material = _estimator_material(material_text, uploaded, draft)
                _run_estimator_action(
                    st.session_state,
                    session,
                    adapter,
                    material,
                    supplement,
                    previous_result=(draft.result if draft is not None else None),
                )
            except TaskEstimationImageUnsupported as exc:
                st.warning(str(exc))
                return False
            except TaskEstimationError as exc:
                st.warning(str(exc))
                return False
            except Exception as exc:  # noqa: BLE001 - service failure, no raw output in UI
                logger.error("[CampusFlow][task_estimate] %s", type(exc).__name__)
                st.error("估时服务暂时没有完成；材料仍在输入框中，已有估算也会保留。请稍后重试。")
                return False
            if not _persist_after_mutation(st, before, reference=reference):
                return False
            # An initial successful estimate adds a persistence notice above
            # this expander. Native remounting must not hide the new result.
            st.session_state["p2_task_estimate_open"] = True
            _rerun(st)
            return True

        draft = load_task_estimate_draft(st.session_state)
        if draft is None or draft.result is None:
            return False
        # Editing text material or the supplemental facts invalidates the old
        # estimate immediately, without calling the model.  Persisting that
        # stale state prevents a restart from making an old result look fresh.
        if (
            draft.status == "ready"
            and draft.material.kind == "text"
            and (
                str(material_text or "").strip() != draft.material.text
                or str(supplement or "").strip() != draft.supplemental_context
            )
        ):
            before = _business_snapshot(st.session_state)
            stale_draft = replace(
                draft,
                status="stale",
                error_message="任务材料或补充情况已变化，请重新估算。",
            )
            save_task_estimate_draft(st.session_state, stale_draft)
            if not _persist_after_mutation(st, before, reference=reference):
                return False
            draft = stale_draft
        result = draft.result
        if draft.status == "stale":
            st.warning("任务材料或补充情况已变化，请重新估算后再加入。")
        if not result.ready:
            st.warning(sanitize_user_facing_text(result.clarification_question))
            return False
        assumptions = "；".join(result.assumptions) if result.assumptions else "无额外假设"
        adjustment = (
            '<div class="cf-estimate-row"><b>本次调整：</b>{}</div>'.format(
                html.escape(sanitize_user_facing_text(result.adjustment_basis))
            ) if result.adjustment_basis else ""
        )
        requirements = []
        if result.location_text:
            requirements.append("地点：{}".format(result.location_text))
        if result.deadline_time:
            requirements.append("最晚完成：{}".format(result.deadline_time))
        if result.requires_single_session is True:
            requirements.append("一次完成")
        elif result.is_splittable is True:
            requirements.append("可以拆开完成")
        requirement_row = (
            '<div class="cf-estimate-row"><b>明确要求：</b>{}</div>'.format(
                html.escape("；".join(requirements))
            ) if requirements else ""
        )
        st.markdown(
            '<section class="cf-estimate-result">'
            '<div class="cf-estimate-name">{}</div>'
            '<div class="cf-estimate-row"><b>任务范围：</b>{}</div>'
            '<div class="cf-estimate-row"><b>完成标准：</b>{}</div>'
            '<div class="cf-estimate-metrics">'
            '<div class="cf-estimate-metric">粗略专注用时<strong>{}–{} <small>分钟</small></strong></div>'
            '<div class="cf-estimate-metric">建议用于规划<strong>{} <small>分钟</small></strong></div></div>'
            '<div class="cf-estimate-row"><b>估算依据：</b>{}</div>{}{}'
            '<div class="cf-estimate-note">关键假设：{}。这是可调整的粗估，不含通勤、休息和等待。</div>'
            '</section>'.format(
                html.escape(sanitize_user_facing_text(result.task_name)),
                html.escape(sanitize_user_facing_text(result.scope_summary)),
                html.escape(sanitize_user_facing_text(result.completion_criteria)),
                result.min_focus_minutes,
                result.max_focus_minutes,
                result.recommended_minutes,
                html.escape(sanitize_user_facing_text(result.basis)),
                requirement_row,
                adjustment,
                html.escape(sanitize_user_facing_text(assumptions)),
            ),
            unsafe_allow_html=True,
        )
        if draft.status == "added":
            st.markdown('<div class="cf-persist-notice">已更新今日计划，不会重复加入；可以收起这里查看方案。</div>', unsafe_allow_html=True)
            return False
        widget_suffix = "{}_{}".format(draft.draft_id, draft.call_count)
        task_name, scope, completion = result.task_name, result.scope_summary, result.completion_criteria
        if st.checkbox("需要修改任务名称或范围", key="p2_task_estimate_edit_" + widget_suffix):
            task_name = st.text_input(
                "任务名称", value=result.task_name,
                key="p2_task_estimate_name_" + widget_suffix,
            )
            scope = st.text_area(
                "确认任务范围", value=result.scope_summary, height=68,
                key="p2_task_estimate_scope_" + widget_suffix,
            )
            completion = st.text_area(
                "确认完成标准", value=result.completion_criteria, height=68,
                key="p2_task_estimate_completion_" + widget_suffix,
            )
        adopted = st.number_input(
            "采用的分钟数", min_value=1, max_value=7 * 24 * 60,
            value=int(draft.adopted_minutes), step=5,
            key="p2_task_estimate_minutes_" + widget_suffix,
        )
        st.caption("最终采用 {} 分钟 · {}。这不代表已经完成，也不会改变任务范围。".format(
            int(adopted), "用户修改" if int(adopted) != result.recommended_minutes else "沿用估算建议"
        ))
        edited_result = result
        confirmed_fields_changed = (
            str(task_name).strip() != str(result.task_name).strip()
            or str(scope).strip() != str(result.scope_summary).strip()
            or str(completion).strip() != str(result.completion_criteria).strip()
        )
        adopted_changed = int(adopted) != int(draft.adopted_minutes)
        if confirmed_fields_changed or adopted_changed:
            before = _business_snapshot(st.session_state)
            if confirmed_fields_changed:
                edited_result = replace(
                    result,
                    task_name=str(task_name).strip(),
                    scope_summary=str(scope).strip(),
                    completion_criteria=str(completion).strip(),
                )
            draft = replace(
                draft,
                result=edited_result,
                adopted_minutes=int(adopted),
                duration_source=("user_modified" if adopted_changed else draft.duration_source),
                status=("stale" if confirmed_fields_changed else draft.status),
                error_message=(
                    "确认范围已变化，请重新估算。"
                    if confirmed_fields_changed else draft.error_message
                ),
            )
            save_task_estimate_draft(st.session_state, draft)
            if not _persist_after_mutation(st, before, reference=reference):
                return False
            result = edited_result
        reestimate = st.button(
            "按补充情况重新估算", key="p2_task_estimate_reestimate"
        )
        current_bundle = load_live_final_turn(st.session_state)
        unknown_tasks = tuple(
            task for task in current_bundle.state.tasks
            if task.total_minutes is None and task.completed_minutes == 0 and task.state.value == "active"
        ) if current_bundle is not None else ()
        existing_ref = None
        if unknown_tasks:
            options = [None] + [task.task_ref for task in unknown_tasks]
            labels = {task.task_ref: "补充已有任务：" + task.title for task in unknown_tasks}
            if st.session_state.get("p2_task_estimate_existing_target") not in options:
                st.session_state["p2_task_estimate_existing_target"] = None
            existing_ref = st.selectbox(
                "这份估算用于", options,
                format_func=lambda ref: labels.get(ref, "加入一项新任务"),
                key="p2_task_estimate_existing_target",
            )
            st.caption("为已有任务补充用时会保留原任务，不重复创建。")
        add_today = st.button(
            "确认用时并更新计划" if existing_ref else "加入今日计划", key="p2_task_estimate_add", type="primary",
            disabled=draft.status != "ready",
        )
        if draft.status != "ready":
            st.caption("材料或确认范围已变化，先重新估算，再加入今日计划。")
        if reestimate:
            if missing:
                st.error("请先连接模型服务后再估时，当前输入已保留。")
                return False
            before = _business_snapshot(st.session_state)
            try:
                material = _estimator_material(material_text, uploaded, draft)
                _run_estimator_action(
                    st.session_state,
                    session,
                    adapter,
                    material,
                    supplement,
                    confirmed_scope=scope,
                    confirmed_completion=completion,
                    previous_result=result,
                )
            except TaskEstimationError as exc:
                st.warning(str(exc))
                return False
            except Exception as exc:  # noqa: BLE001
                logger.error("[CampusFlow][task_reestimate] %s", type(exc).__name__)
                st.error("估时服务暂时没有完成；材料仍在输入框中，已有估算也会保留。请稍后重试。")
                return False
            if not _persist_after_mutation(st, before, reference=reference):
                return False
            _rerun(st)
            return True
        if add_today:
            before = _business_snapshot(st.session_state)
            try:
                _prepare_restored_replan(
                    st.session_state, reference, session.config
                )
                material = _estimator_material(material_text, uploaded, draft)
                confirmed = confirm_estimated_task(
                    draft,
                    material,
                    supplement,
                    task_name,
                    scope,
                    completion,
                    int(adopted),
                )
                outcome = session.add_confirmed_estimated_task_atomic(
                    confirmed, reference_datetime=reference, **(
                        {"existing_task_ref": existing_ref} if existing_ref else {}
                    )
                )
                save_task_estimate_draft(
                    st.session_state, mark_draft_added(draft, outcome.task_ref)
                )
            except TaskEstimationStale as exc:
                _restore_business_snapshot(st.session_state, before)
                st.warning(str(exc))
                return False
            except TaskEstimationError as exc:
                _restore_business_snapshot(st.session_state, before)
                st.warning(str(exc))
                return False
            except Exception as exc:  # noqa: BLE001
                logger.error("[CampusFlow][task_estimate_add] %s", type(exc).__name__)
                _restore_business_snapshot(st.session_state, before)
                st.error("这份任务暂时没有加入，原方案和估时草稿都已保留。")
                return False
            if not _persist_after_mutation(st, before, reference=reference):
                st.error("这份任务暂时没有加入，原方案和估时草稿都已保留。")
                return False
            st.session_state[LOCAL_PROFILE_STALE_KEY] = False
            st.session_state[PERSONAL_SETTINGS_PLAN_STALE_KEY] = False
            _rerun(st)
            return True
    return False


def _coerce_time_part(value, lo, hi, default):
    """把旧 session_state 的任意遗留值安全转换为合法 int。

    - int 且在 [lo, hi] 内：原样使用；
    - 字符串 "21" / "03"：解析为 21 / 3；
    - bool（int 子类）：视为非法，回退 default；
    - 其余（None / 越界 / 无法解析）：回退 default。
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if lo <= value <= hi else default
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = int(text)
            except (TypeError, ValueError):
                return default
            return parsed if lo <= parsed <= hi else default
    return default


def _sync_reference_clock(store, now):
    """Advance untouched automatic controls before render; retain manual input."""
    current = (store.get(LIVE_REFERENCE_HOUR_KEY), store.get(LIVE_REFERENCE_MINUTE_KEY))
    last_auto = store.get("p2_reference_last_auto")
    reset = store.pop("p2_reference_reset_requested", False)
    if not reset and last_auto is not None and current != last_auto:
        store[LIVE_REFERENCE_SOURCE_KEY] = "manual"
    source = store.get(LIVE_REFERENCE_SOURCE_KEY)
    if reset or source == "system_now" or (source is None and current == (None, None)):
        store[LIVE_REFERENCE_HOUR_KEY] = now.hour
        store[LIVE_REFERENCE_MINUTE_KEY] = now.minute
        store[LIVE_REFERENCE_SOURCE_KEY] = "system_now"
        store["p2_reference_last_auto"] = (now.hour, now.minute)
    elif source is None:
        store[LIVE_REFERENCE_SOURCE_KEY] = "manual"


def _migrate_reference_hour_minute(store, now_provider):
    """selectbox 渲染前统一迁移小时/分钟 session_state 为合法 int。

    历史值可能为 "21" / "03"（str）、datetime.time、int 或非法值；
    迁移后一律写入 int 并返回 (hour, minute)。
    必须发生在 st.selectbox 渲染之前，避免 Streamlit 状态序列化类型不一致。
    """
    now = now_provider()
    default_hour = now.hour
    default_minute = now.minute
    raw_hour = store.get(LIVE_REFERENCE_HOUR_KEY)
    raw_minute = store.get(LIVE_REFERENCE_MINUTE_KEY)
    if isinstance(raw_hour, time) or isinstance(raw_minute, time):
        legacy_time = raw_hour if isinstance(raw_hour, time) else raw_minute
        raw_hour = legacy_time.hour
        raw_minute = legacy_time.minute
    hour = _coerce_time_part(raw_hour, 0, 23, default_hour)
    minute = _coerce_time_part(raw_minute, 0, 59, default_minute)
    store[LIVE_REFERENCE_HOUR_KEY] = hour
    store[LIVE_REFERENCE_MINUTE_KEY] = minute
    return hour, minute


def _resolve_reference_time(value, now: datetime) -> datetime:
    """time 输入与当天日期组合成 reference_datetime（截断到分钟）。

    - None：使用系统当前时间；
    - datetime.time：取当天（now 的日期）该时刻；
    - datetime：截断到分钟。
    """
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    now = now.replace(second=0, microsecond=0)
    if value is None:
        return now
    if isinstance(value, datetime):
        return value.replace(second=0, microsecond=0)
    hour = getattr(value, "hour", None)
    minute = getattr(value, "minute", None)
    if isinstance(hour, int) and isinstance(minute, int):
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    raise TypeError("reference value must be a datetime.time or datetime")


def _resolve_reference_time_select(store):
    """post-widget 纯读取：把小时/分钟 session_state 解析为 datetime.time。

    - 只读取，绝不写 session_state（widget 已实例化后 Streamlit 禁止修改其 key）；
    - 值应在 pre-widget migration 后已是合法 int，此处仅做防御性校验；
    - 非法值只返回人话错误，不写回。
    """
    if not isinstance(store, Mapping):
        raise TypeError("store must be a dict-like session state")
    hour = store.get(LIVE_REFERENCE_HOUR_KEY)
    minute = store.get(LIVE_REFERENCE_MINUTE_KEY)
    if not isinstance(hour, int) or isinstance(hour, bool):
        hour = -1
    if not isinstance(minute, int) or isinstance(minute, bool):
        minute = -1
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute), ""
    return None, "请选择 00~23 小时与 00~59 分钟。"


def make_live_session(
    store,
    adapter,
    config: Optional[PlanConfig] = None,
    map_data=None,
    campus_id=None,
    companion_enabled=False,
    agent_intelligence_enabled=False,
) -> P2SessionController:
    """用 adapter 的 agent_caller 组装 P2 会话控制器（所有 Agent 角色共用同一 caller）。

    map_data 提供时自动接入 P3 统一反馈流程（理解 -> 任务/固定安排/移动分别执行
    -> 真实寻路 + 时间估计 + 统一重规划）；不提供时保持 P2d/P2e 原行为。
    """
    caller = adapter.agent_caller
    if config is None:
        # P3e live：停用旧“固定安排前默认预留10分钟”，只由真实 movement 决定预留。
        config = PlanConfig(default_safety_buffer_minutes=0)
    unified_handler = None
    if map_data is not None:
        if campus_id is not None:
            require_campus_map(campus_id, map_data)
        # P3e：统一反馈理解（task + commitment + movement 一次解析、分别执行）
        unified_handler = make_unified_feedback_handler(
            map_data,
            caller,
            config=config,
            pipeline_kwargs={
                "plan_caller": caller,
                "review_caller": caller,
                "revision_caller": caller,
                "reconciliation_caller": caller,
            },
            location_alias_resolver=lambda text: (
                personal_location_alias(text, campus_id, load_personal_settings(store)).display_name
                if personal_location_alias(text, campus_id, load_personal_settings(store)) is not None
                else text
            ),
        )
    return P2SessionController(
        store,
        caller,
        commitment_caller=caller,
        repair_caller=caller,
        plan_caller=caller,
        review_caller=caller,
        revision_caller=caller,
        reconciliation_caller=caller,
        config=config,
        unified_handler=unified_handler,
        companion_caller=caller,
        companion_enabled=companion_enabled,
        agent_intelligence_enabled=agent_intelligence_enabled,
        map_data=map_data,
        campus_id=campus_id,
    )


def _load_map_data(campus_id):
    """只加载调用方显式选择的本地校区地图，绝不 fallback。"""
    try:
        return DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id)
    except (ValueError, OSError):
        return None


def _intake_cache_key(
    reference: datetime, user_text: str, current_location_text: str = "", campus_id: str = "",
    settings_revision: int = 0,
) -> str:
    """Cache only an intake outcome compatible with the selected map contract."""
    payload = "{}|{}|{}|{}|{}|{}".format(
        _LIVE_EXECUTION_CACHE_VERSION,
        campus_id or "",
        reference.isoformat(),
        user_text,
        current_location_text or "",
        settings_revision,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_live_intake(
    store, reference: datetime, user_text: str, caller, repair_caller=None, map_data=None,
    campus_id=None,
    current_location_text=None,
    default_safety_buffer_minutes: int = 0,
) -> DayIntakeOutcome:
    """Intake 结果缓存：同一参考时间 + 同一文本只调用一次模型；失败不缓存以便重试。

    map_data 提供时，首次输入中的地点信息进入 P3 空间理解（移动进入首版计划）。
    """
    if map_data is not None and campus_id is not None:
        require_campus_map(campus_id, map_data)
    current_location_text = str(current_location_text or "").strip()
    settings = load_personal_settings(store)
    key = _intake_cache_key(
        reference, user_text, current_location_text, campus_id or "", settings.revision
    )
    cached = store.get(LIVE_INTAKE_CACHE_KEY)
    if (
        isinstance(cached, dict)
        and cached.get("key") == key
        and cached.get("outcome") is not None
        and cached["outcome"].applied is not None
    ):
        logger.info("[CampusFlow][intake] cache_hit reference=%s", reference.isoformat())
        return cached["outcome"]
    logger.info("[CampusFlow][intake] cache_miss reference=%s", reference.isoformat())
    from src.low_input_context import intake_background, with_intake_background
    background = intake_background(settings, reference, campus_id)
    intake_caller = with_intake_background(caller, background)
    intake_repair = with_intake_background(repair_caller or caller, background)
    outcome = run_day_intake(
        reference,
        user_text,
        intake_caller,
        intake_repair,
        default_safety_buffer_minutes=default_safety_buffer_minutes,
        map_data=map_data,
        # P4 execution locations need one coherent two-pass sequence.  The
        # legacy P3 spatial path remains available for old proposals below.
        defer_spatial_intake=map_data is not None,
        # Production initial intake always receives the bounded semantic
        # chain: raw events -> semantic links -> bounded audit.  Direct
        # legacy callers may omit these passes explicitly.
        semantic_auditor_caller=intake_caller,
        raw_event_caller=intake_caller,
        semantic_linker_caller=intake_caller,
        campus_id=campus_id,
    )
    if outcome.applied is not None:
        if (
            map_data is not None
            and outcome.proposal is not None
            and not _proposal_has_execution_semantics(outcome.proposal)
        ):
            from src.p3_route_planner import run_spatial_intake
            spatial = run_spatial_intake(
                outcome.applied.state,
                outcome.proposal.current_location,
                outcome.proposal.transport_mode,
                map_data,
                caller,
                repair_caller,
            )
            outcome = replace(
                outcome,
                applied=replace(outcome.applied, state=spatial.state),
                movement_blocks=tuple(spatial.blocks),
                movement_requests=tuple(spatial.requests),
                current_location_text=spatial.current_location_text,
                movement_questions=tuple(spatial.questions),
                movement_warnings=tuple(spatial.warnings),
                call_count=outcome.call_count + spatial.call_count,
            )
        if map_data is not None and outcome.proposal is not None:
            # An explicit new intake starts a new semantic contract.  Reusing
            # an old same-ref authorization/preference would attach stale
            # meaning to newly extracted tasks.
            prior_context = ExecutionPlanContext()
            context = enrich_intake_execution_context(
                outcome.proposal,
                outcome.applied,
                map_data,
                context=prior_context,
                # The page field is authoritative.  Preserve legacy Day
                # Intake wording only when the page field was left empty.
                current_location_text=current_location_text or outcome.proposal.current_location,
                caller=caller,
                repair_caller=repair_caller,
                personal_settings=settings,
            )
            context = apply_personal_defaults_to_context(
                context, settings,
                transport_is_explicit=outcome.proposal.transport_mode is not None,
            )
            store[PERSONAL_TRANSPORT_EXPLICIT_KEY] = outcome.proposal.transport_mode is not None
            save_execution_context(store, context)
        store[LIVE_INTAKE_CACHE_KEY] = {"key": key, "outcome": outcome}
    return outcome


def _proposal_has_execution_semantics(proposal):
    """New structured task facts opt into the P4 two-pass movement path."""
    return any(
        task.activity_kind is not None or task.location_text is not None
        for task in proposal.tasks
    )



def render_live_page_text(turn, intake_questions=(), intake_warnings=()) -> str:
    """P2 live 页文本主体：只保留“当前方案”主区块 + “待确认问题”副区块。

    AI 暂估内联在对应任务行；首次输入待确认并入“待确认问题”；
    普通提示不再单独成区块。
    """
    return render_page_text(turn, extra_questions=intake_questions)


def _handle_intake_submit(
    st, session, missing, reference, user_text, map_data=None, campus_id=None,
    current_location_text=None, rerun_after_commit=True,
) -> bool:
    """“生成全天计划”：Intake -> 程序构造 state -> P3 空间 intake -> P2c start_day。"""
    if missing:
        st.error("请先连接模型服务后再生成计划，当前输入已保留。")
        return False
    if not user_text:
        st.warning("请先输入今天的情况和想完成的任务。")
        return False
    outcome = run_live_intake(
        st.session_state, reference, user_text, session.caller, map_data=map_data,
        campus_id=campus_id,
        current_location_text=current_location_text,
    )
    logger.info(
        "[CampusFlow][intake] stage=run_day_intake applied=%s calls=%s blocks=%s",
        outcome.applied is not None,
        outcome.call_count,
        len(outcome.movement_blocks or ()),
    )
    if outcome.applied is None:
        st.warning("首次全天计划暂未生成：")
        for question in outcome.questions:
            st.write("- " + sanitize_user_facing_text(question))
        for warning in outcome.warnings:
            st.write("- " + sanitize_user_facing_text(warning))
        return False
    # Only a successfully parsed new intake starts a new semantic task
    # contract.  A failed attempt leaves the previous reliable turn and its
    # ref-bound preferences intact.
    st.session_state.pop(P4_FEEDBACK_DECISION_KEY, None)
    for key in (
        LATEST_FEEDBACK_TEXT_KEY, AGENT_INTELLIGENCE_KEY,
        AGENT_CALL_TRACE_KEY, DAY_PREFERENCE_KEY, WHAT_IF_PREVIEW_KEY,
    ):
        st.session_state.pop(key, None)
    st.session_state[LATEST_USER_TEXT_KEY] = user_text.strip()
    # An explicit new intake must never reuse an older turn merely because
    # the reconstructed DayPlanningState happens to be identical.  Its fresh
    # ExecutionPlanContext is the source of truth for final movement/timeline.
    turn = session.start_day(
        outcome.applied.state,
        movement_blocks=outcome.movement_blocks,
        force=True,
    )
    commit_live_final_turn(
        st.session_state,
        turn,
        map_data,
        extra_questions=outcome.questions + tuple(outcome.movement_questions),
    )
    logger.info(
        "[CampusFlow][intake] stage=session.start_day turn_ok=1 movement_blocks=%s",
        len(outcome.movement_blocks or ()),
    )
    if outcome.current_location_text or outcome.movement_blocks:
        movement_data = st.session_state.setdefault(MOVEMENT_DATA_KEY, {})
        if outcome.current_location_text:
            movement_data["current_location_text"] = outcome.current_location_text
        if getattr(outcome, "movement_requests", ()):
            movement_data["last_request"] = outcome.movement_requests[0]
    if rerun_after_commit:
        _rerun(st)
    return True


def main(
    st=None,
    adapter_factory=TJUP2CallAdapter,
    now_provider=None,
    configuration_loader=None,
    persistence_factory=None,
    identity_assertion_loader=None,
    configure_page=True,
):
    injected_streamlit = st is not None
    if st is None:
        import streamlit as st
    now_provider = datetime.now if now_provider is None else now_provider
    configuration_loader = (
        load_project_configuration if configuration_loader is None else configuration_loader
    )
    if persistence_factory is None and not injected_streamlit:
        persistence_factory = LocalProfileStore

    if configure_page:
        st.set_page_config(page_title="CampusFlow · 今日工作台", layout="wide")
    _render_top_input_chrome(st)

    try:
        missing = configuration_loader()
        apply_configured_timezone()
        from src.model_service_ui import render_first_run
        if render_first_run(st, missing):
            return
        if st.session_state.get("cf_model_notice") and not st.session_state.get(PERSONAL_SETTINGS_PANEL_OPEN_KEY):
            st.success(st.session_state.pop("cf_model_notice"))
        drawer = DeferredDrawerRerun(st)
        _render_live_page(
            st, missing, adapter_factory, now_provider,
            persistence_factory=persistence_factory,
            identity_assertion_loader=identity_assertion_loader,
            drawer=drawer,
        )
        if drawer.requested:
            _rerun(st)
    except Exception as exc:  # noqa: BLE001 - 页面初始化/控件渲染安全边界
        # 开发诊断信息只写 session_state + 终端，不暴露页面；用户只看到一句人话。
        logger.error("[CampusFlow][page_init] %s", type(exc).__name__)
        try:
            st.session_state[LIVE_ERROR_KEY] = "page_init: {}".format(type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass
        try:
            st.error(PAGE_INIT_ERROR_TEXT)
        except Exception:  # noqa: BLE001
            pass


def _render_live_page(
    st, missing, adapter_factory, now_provider,
    persistence_factory=None, identity_assertion_loader=None, drawer=None,
):
    """页面主体：会话初始化 + 时间控件 + 规划入口（仅由 main 的安全边界包裹）。"""
    page_now = now_provider().replace(second=0, microsecond=0)
    _synchronize_external_identity(
        st.session_state, persistence_factory, identity_assertion_loader, page_now
    )
    _restore_local_profile_once(st, page_now, persistence_factory)
    # A settings/import action may rerun before the main widgets are rendered.
    # Streamlit otherwise cleans their keys up as "removed widgets", losing
    # campus/input selections. Detach only these known draft widget values
    # from cleanup, before any of those widgets is instantiated this run.
    for key in (
        LIVE_CAMPUS_SELECT_KEY, LIVE_REFERENCE_HOUR_KEY, LIVE_REFERENCE_MINUTE_KEY,
        LIVE_INTAKE_KEY, LIVE_CURRENT_LOCATION_KEY, LIVE_FEEDBACK_KEY,
        LIVE_ESTIMATE_TEXT_KEY, LIVE_ESTIMATE_SUPPLEMENT_KEY,
    ):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    settings_status = st.session_state.get(PERSONAL_SETTINGS_STATUS_KEY)
    if settings_status and not st.session_state.get(PERSONAL_SETTINGS_PANEL_OPEN_KEY):
        st.markdown(
            '<div class="cf-persist-notice">{}</div>'.format(
                html.escape(sanitize_user_facing_text(settings_status))
            ),
            unsafe_allow_html=True,
        )
    settings_adapter = st.session_state.get(LIVE_ADAPTER_KEY)
    if settings_adapter is None and st.session_state.get(PERSONAL_SETTINGS_PANEL_OPEN_KEY):
        # Constructing the adapter is local and side-effect free.  The course
        # importer calls it only after the explicit recognition button.
        settings_adapter = adapter_factory()
        st.session_state[LIVE_ADAPTER_KEY] = settings_adapter
    with drawer if drawer is not None else nullcontext(st) as settings_surface:
        _render_personal_settings_panel(
            settings_surface,
            reference=page_now,
            caller=(settings_adapter.agent_caller if settings_adapter is not None else None),
            repair_caller=(settings_adapter.agent_caller if settings_adapter is not None else None),
            image_caller=(getattr(settings_adapter,"task_estimation_caller",None)
                if settings_adapter is not None else None),
            image_supported=bool(getattr(settings_adapter,"supports_image_inputs",False)),
            missing=missing,
            persistence_factory=persistence_factory,
        )
    profile_status = st.session_state.get(LOCAL_PROFILE_STATUS_KEY)
    temporary_profile_notice = "当前为临时使用；开启个性化后可跨会话保存。"
    if (
        profile_status
        and profile_status != temporary_profile_notice
        and not (settings_status and profile_status == "已在本机保存。")
    ):
        st.markdown(
            '<div class="{}">{}</div>'.format(
                "cf-saved-inline" if profile_status == "已在本机保存。" else "cf-persist-notice",
                html.escape(sanitize_user_facing_text(profile_status))
            ),
            unsafe_allow_html=True,
        )
    save_error = st.session_state.get(LOCAL_PROFILE_SAVE_ERROR_KEY)
    if save_error:
        st.markdown(
            '<div class="cf-persist-notice cf-persist-notice-error">{}</div>'.format(
                html.escape(sanitize_user_facing_text(save_error))
            ),
            unsafe_allow_html=True,
        )
    registrations = DEFAULT_CAMPUS_REGISTRY.list_campuses()
    labels = [item.display_name for item in registrations]
    if LIVE_CAMPUS_SELECT_KEY not in st.session_state:
        st.session_state[LIVE_CAMPUS_SELECT_KEY] = DEFAULT_CAMPUS_REGISTRY.get_registration(
            "beiyangyuan"
        ).display_name
    environment_summary = st.container() if hasattr(st, 'container') else nullcontext()
    with region(st, 'environment', any(st.session_state.get('cf_environment_' + name + '_open', False) for name in ('campus', 'time'))):
        environment_columns = st.columns(2)
        with environment_columns[0], region(st, 'environment-campus', st.session_state.get('cf_environment_campus_open', False)):
            st.markdown('<div class="cf-campus-kicker"><span class="cf-environment-row-marker"></span>当前校区</div>', unsafe_allow_html=True)
            chosen_label = st.selectbox(
                "当前校区", labels, key=LIVE_CAMPUS_SELECT_KEY, label_visibility="collapsed"
            )
        with environment_columns[1], region(st, 'environment-time', st.session_state.get('cf_environment_time_open', False)):
            st.markdown(
                '<div class="cf-campus-kicker">时间设置</div>',
                unsafe_allow_html=True,
            )
            with st.container() if hasattr(st, 'container') else nullcontext():
                st.markdown('<span class="cf-env-clock-marker"></span>', unsafe_allow_html=True)
                _sync_reference_clock(st.session_state, page_now)
                _migrate_reference_hour_minute(st.session_state, now_provider)
                hour_col, minute_col = st.columns(2)
                with hour_col:
                    st.selectbox(
                        "小时", _HOUR_OPTIONS, key=LIVE_REFERENCE_HOUR_KEY,
                        format_func=lambda x: "{:02d}".format(x),
                        label_visibility="collapsed",
                    )
                with minute_col:
                    st.selectbox(
                        "分钟", _MINUTE_OPTIONS, key=LIVE_REFERENCE_MINUTE_KEY,
                        format_func=lambda x: "{:02d}".format(x),
                        label_visibility="collapsed",
                    )
                reference_time, time_error = _resolve_reference_time_select(st.session_state)
                if time_error:
                    st.warning(time_error)
                if hasattr(st, "button") and quiet_button(st, "使用当前时间", key="p2_reference_use_now"):
                    st.session_state["p2_reference_reset_requested"] = True
                    # As with the settings drawer, finish mounting the native
                    # inputs before rerun. An early return cleans them up and
                    # can discard the next form edit or an attached file.
                    if drawer is not None:
                        drawer.requested = True
                    else:
                        _rerun(st)
                        return
    registration = next((item for item in registrations if item.display_name == chosen_label), None)
    if registration is None:
        st.session_state.pop(LIVE_MAP_KEY, None)
        st.warning("请选择一个可用校区后再开始安排。")
        return
    else:
        changed = select_campus(st.session_state, registration.campus_id, DEFAULT_CAMPUS_REGISTRY)
        if changed or LIVE_MAP_KEY not in st.session_state:
            st.session_state[LIVE_MAP_KEY] = _load_map_data(registration.campus_id)
    map_data = st.session_state.get(LIVE_MAP_KEY)
    if map_data is None:
        st.error("当前校区地图无法加载，暂不开始规划。")
        return
    require_campus_map(registration.campus_id, map_data)
    other_courses = tuple(st.session_state.get(PERSONAL_OTHER_CAMPUS_COURSES_KEY, ()) or ())
    if other_courses:
        st.info("今天还有其他校区的课表安排；当前不会自动切换校区或生成跨校区路线。")

    # Streamlit keeps session_state across hot reloads.  A P4 context saved by
    # an older timeline contract must not be rendered beside a newer planner:
    # that is exactly how a stale meal/task result can coexist with newer class
    # prep copy.  Preserve the explicit campus selection, invalidate only the
    # campus-bound planning snapshots once, then stamp the current contract.
    if (
        st.session_state.get(_LIVE_EXECUTION_SESSION_VERSION_KEY)
        != _LIVE_EXECUTION_CACHE_VERSION
        and EXECUTION_PLAN_CONTEXT_KEY in st.session_state
    ):
        for key in CAMPUS_BOUND_STATE_KEYS:
            st.session_state.pop(key, None)
        st.session_state[LIVE_MAP_KEY] = _load_map_data(registration.campus_id)
        map_data = st.session_state[LIVE_MAP_KEY]
        require_campus_map(registration.campus_id, map_data)
    st.session_state[_LIVE_EXECUTION_SESSION_VERSION_KEY] = _LIVE_EXECUTION_CACHE_VERSION

    existing_session = st.session_state.get(LIVE_SESSION_KEY)
    if (
        existing_session is None
        or (
            hasattr(existing_session, "campus_id")
            and getattr(existing_session, "campus_id", None) != registration.campus_id
        )
        or (
            hasattr(existing_session, "map_data")
            and getattr(getattr(existing_session, "map_data", None), "campus_id", None)
            != registration.campus_id
        )
    ):
        adapter = adapter_factory()
        st.session_state[LIVE_ADAPTER_KEY] = adapter
        st.session_state[LIVE_SESSION_KEY] = make_live_session(
            st.session_state, adapter, map_data=map_data,
            campus_id=registration.campus_id, companion_enabled=True,
            agent_intelligence_enabled=True,
        )
    session = st.session_state[LIVE_SESSION_KEY]
    adapter = st.session_state.get(LIVE_ADAPTER_KEY)
    if adapter is None:
        # Hot-reloaded legacy sessions did not retain the lightweight adapter.
        # Constructing it has no network side effect and does not call Qwen.
        adapter = adapter_factory()
        st.session_state[LIVE_ADAPTER_KEY] = adapter

    has_current_plan = load_live_final_turn(st.session_state) is not None or session.last_turn() is not None
    if time_error:
        # 时间异常：阻断本轮任何规划/反馈操作，只显示人话提示，不调用模型。
        bundle = load_live_final_turn(st.session_state)
        turn = bundle.turn if bundle is not None else session.last_turn()
        if turn is not None:
            render_page_streamlit(
                st, turn,
                extra_questions=bundle.extra_questions if bundle is not None else (),
            )
        return
    reference = _resolve_reference_time(reference_time, now_provider())
    with environment_summary:
        environment_info, campus_action, time_action = st.columns(3)
        with environment_info:
            st.markdown('<div class="cf-reference cf-environment-summary">{} · {} {}</div>'.format(
                html.escape(chosen_label), reference.strftime("%m月%d日 %H:%M"),
                '（手动）' if st.session_state.get(LIVE_REFERENCE_SOURCE_KEY) != 'system_now' else '',
            ), unsafe_allow_html=True)
        with campus_action:
            quiet_button(st, '切换校区', key='cf_environment_inline',
                on_click=toggle_environment, args=(st.session_state, 'campus'))
        with time_action:
            quiet_button(st, '时间设置', key='cf_environment_time_inline',
                on_click=toggle_environment, args=(st.session_state, 'time'))
    if missing:
        st.caption("需要先连接模型服务；可以先查看页面和编辑设置。")
        from src.model_service_ui import editor_allowed, open_service_settings
        if editor_allowed(st):
            st.button("连接模型服务", key="cf_model_open", on_click=open_service_settings,
                      args=(st.session_state, PERSONAL_SETTINGS_PANEL_OPEN_KEY))
        else:
            st.caption("请由运行此服务的配置者补全模型配置。")

    view = st.session_state.get(VIEW_KEY, '今天')
    if not has_current_plan and view == '今天':
        st.markdown('<div class="cf-empty-title">接下来有什么安排？</div><div class="cf-empty-intro">填写任务、时间要求和固定安排。</div>', unsafe_allow_html=True)
    elif view == '时间线':
        st.markdown('<div class="cf-page-title">{}</div>'.format(view), unsafe_allow_html=True)
    focus_slot = st.container() if hasattr(st, 'container') else nullcontext()
    intake_slot = st.container() if hasattr(st, 'container') else nullcontext()
    feedback_slot = st.container() if hasattr(st, 'container') else nullcontext()
    timeline_slot = st.container() if hasattr(st, 'container') else nullcontext()
    material_slot = st.container() if hasattr(st, 'container') else nullcontext()

    # Keep existing widgets alive on rerun, while giving the current action
    # priority once a reliable plan exists. Collapsing never submits anything.
    with intake_slot, region(st, 'intake', view == '今天'):
        with (st.expander("重新规划", expanded=False) if has_current_plan else nullcontext()):
            with st.form("p2_live_intake_form"):
                st.markdown('<div class="cf-input-heading">今天还有什么事？</div>', unsafe_allow_html=True)
                first_input = st.text_area(
                    "接下来想做什么？", key=LIVE_INTAKE_KEY,
                    label_visibility="collapsed",
                    placeholder="下午有课，课前想吃饭，还有一份作业要写",
                    height=110,
                )
                # Native disclosure stays mounted inside the same form: no
                # widget identity change or eager submission when opened.
                # Replanning already has a disclosure; Streamlit forbids
                # nesting expanders. Only the empty homepage needs this one.
                with (st.expander("补充当前位置（可选）", expanded=False) if not has_current_plan else nullcontext()):
                    current_location = st.text_input("我现在的位置（可选）", key=LIVE_CURRENT_LOCATION_KEY)
                    st.markdown(
                        '<div class="cf-input-help">只有包含通勤时才需要确认位置。</div>',
                        unsafe_allow_html=True,
                    )
                intake_submitted = st.form_submit_button("帮我安排", type="primary")
    if intake_submitted:
        before = _business_snapshot(st.session_state)
        with (st.spinner("THINKING.......") if callable(getattr(st, "spinner", None)) else nullcontext()):
            completed = _safe_user_action(
                st,
                st.session_state,
                "intake",
                lambda: _handle_intake_submit(
                    st, session, missing, reference,
                    first_input, map_data=map_data,
                    campus_id=registration.campus_id,
                    current_location_text=current_location,
                    rerun_after_commit=False,
                ),
            )
        if completed and _persist_after_mutation(
            st, before, reference, registration.campus_id, page_now
        ):
            st.session_state[LOCAL_PROFILE_STALE_KEY] = False
            st.session_state[PERSONAL_SETTINGS_PLAN_STALE_KEY] = False
            st.session_state.pop(PERSONAL_SETTINGS_STATUS_KEY, None)
            _rerun(st)
        else:
            _restore_business_snapshot(st.session_state, before)
            _render_retained_plan(st)
        return

    with feedback_slot, region(st, 'feedback', has_current_plan and view != '材料估时'):
        with st.expander('更新变化', expanded=False):
            with st.form("p2_live_feedback_form"):
                st.markdown('<div class="cf-feedback-heading">更新进度或变化</div>', unsafe_allow_html=True)
                feedback = st.text_input(
                    "进度、位置或临时变化",
                    key=LIVE_FEEDBACK_KEY,
                    placeholder="作业还剩40分钟，我现在已经到图书馆了",
                )
                feedback_submitted = st.form_submit_button("更新方案", type="primary")
    if feedback_submitted:
        if missing:
            st.error("请先连接模型服务后再更新方案，原方案已保留。")
        elif not str(feedback).strip():
            pass
        elif load_live_final_turn(st.session_state) is None and session.last_turn() is None:
            st.warning("请先生成全天计划。")
        else:
            current_bundle = load_live_final_turn(st.session_state)

            def _apply_feedback_and_publish():
                _prepare_restored_replan(
                    st.session_state, reference, session.config
                )
                if _bundle_uses_p4_execution(current_bundle):
                    return session.rebuild_feedback_atomic(str(feedback).strip())
                legacy_turn = session.apply_feedback(str(feedback).strip())
                questions = current_bundle.extra_questions if current_bundle is not None else ()
                return commit_live_final_turn(
                    st.session_state, legacy_turn, map_data, extra_questions=questions
                )

            before = _business_snapshot(st.session_state)
            with (st.spinner("THINKING.......") if callable(getattr(st, "spinner", None)) else nullcontext()):
                applied = _safe_user_action(
                    st,
                    st.session_state,
                    "apply_feedback",
                    _apply_feedback_and_publish,
                )
            if applied is None:
                _restore_business_snapshot(st.session_state, before)
                with focus_slot:
                    _render_retained_plan(st)
                return
            if not _persist_after_mutation(
                st, before, reference, registration.campus_id, page_now
            ):
                with focus_slot:
                    _render_retained_plan(st)
                return
            st.session_state[LOCAL_PROFILE_STALE_KEY] = False
            st.session_state[PERSONAL_SETTINGS_PLAN_STALE_KEY] = False
            st.session_state.pop(PERSONAL_SETTINGS_STATUS_KEY, None)
            _rerun(st)
            return

    from src.material_ui import render_material_inbox
    with material_slot, region(st, 'material', view == '材料估时'):
        if render_material_inbox(st, session, adapter, missing, reference):
            return
    if not has_current_plan:
        if view == '时间线':
            st.caption('当前没有计划。请在“今天”创建安排。')
        archived = st.session_state.get(LOCAL_PROFILE_STALE_BUNDLE_KEY)
        if archived is not None and view != '材料估时':
            render_page_streamlit(st, archived.turn, extra_questions=archived.extra_questions, saved_snapshot=True)
        return

    preview = load_what_if_preview(st.session_state)
    if preview is not None:
        with region(st, 'whatif', view != '材料估时'):
            _render_what_if_preview(st, preview)
            apply_preview = preview.can_apply and st.button("采用这个调整", key="p5_apply_what_if", type="primary")
            keep_current = quiet_button(st, "保持现在方案", key="p5_keep_current_plan")
        if apply_preview:
            def _apply_preview():
                try:
                    return session.apply_what_if_preview_atomic()
                except WhatIfApplyError as exc:
                    st.warning(str(exc))
                    clear_what_if_preview(st.session_state)
                    return None
            before = _business_snapshot(st.session_state)
            with (st.spinner("THINKING.......") if callable(getattr(st, "spinner", None)) else nullcontext()):
                committed = _safe_user_action(st, st.session_state, "apply_what_if", _apply_preview)
            if committed is not None:
                if not _persist_after_mutation(
                    st, before, reference, registration.campus_id, page_now
                ):
                    with focus_slot:
                        _render_retained_plan(st)
                    return
                st.session_state[LOCAL_PROFILE_STALE_KEY] = False
                st.session_state[PERSONAL_SETTINGS_PLAN_STALE_KEY] = False
                st.session_state.pop(PERSONAL_SETTINGS_STATUS_KEY, None)
                _rerun(st)
                return
            # Keep rendering the reliable current bundle after an expected
            # rejection; do not leave the page at just a failure message.
        if keep_current:
            clear_what_if_preview(st.session_state)
            _rerun(st)
            return

    with region(st, "refresh", view != "材料估时"):
        refresh_clicked = quiet_button(st, "刷新方案", key="p2_live_refresh")
    if refresh_clicked:
        if not missing:
            def _refresh_and_publish():
                _prepare_restored_replan(
                    st.session_state, reference, session.config
                )
                refreshed_turn = session.refresh()
                previous = load_live_final_turn(st.session_state)
                questions = previous.extra_questions if previous is not None else ()
                return commit_live_final_turn(
                    st.session_state, refreshed_turn, map_data, extra_questions=questions
                )

            before = _business_snapshot(st.session_state)
            with (st.spinner("THINKING.......") if callable(getattr(st, "spinner", None)) else nullcontext()):
                refreshed = _safe_user_action(st, st.session_state, "refresh", _refresh_and_publish)
            if refreshed is None:
                _restore_business_snapshot(st.session_state, before)
                with focus_slot:
                    _render_retained_plan(st)
                return
            if not _persist_after_mutation(
                st, before, reference, registration.campus_id, page_now
            ):
                with focus_slot:
                    _render_retained_plan(st)
                return
            st.session_state[LOCAL_PROFILE_STALE_KEY] = False
            st.session_state[PERSONAL_SETTINGS_PLAN_STALE_KEY] = False
            st.session_state.pop(PERSONAL_SETTINGS_STATUS_KEY, None)
            _rerun(st)
            return

    bundle = load_live_final_turn(st.session_state)
    if bundle is None:
        # Legacy P2/P3 sessions without P4 execution semantics remain
        # renderable.  A P4 page never reconstructs a turn from these keys.
        turn = session.last_turn()
    else:
        turn = bundle.turn
    if turn is None:
        archived = st.session_state.get(LOCAL_PROFILE_STALE_BUNDLE_KEY)
        if archived is not None:
            render_page_streamlit(
                st, archived.turn, extra_questions=archived.extra_questions,
                saved_snapshot=True,
            )
            return
        return

    intake_questions = bundle.extra_questions if bundle is not None else ()
    default_walk_hint = _consume_default_walk_hint(
        st.session_state, turn, reference, registration.campus_id, page_now
    )
    with focus_slot, region(st, "focus", view == "今天"):
        _safe_user_action(
            st, st.session_state, "render",
            lambda: render_page_streamlit(
                st, turn, extra_questions=intake_questions,
                saved_snapshot=bool(st.session_state.get(LOCAL_PROFILE_STALE_KEY)),
                default_walk_hint=default_walk_hint, part="focus",
            ),
        )
    with timeline_slot, region(st, "timeline", view != "材料估时"):
        _safe_user_action(
            st, st.session_state, "render_details",
            lambda: render_page_streamlit(
                st, turn, extra_questions=intake_questions,
                saved_snapshot=bool(st.session_state.get(LOCAL_PROFILE_STALE_KEY)),
                default_walk_hint=default_walk_hint, part="timeline" if view == "时间线" else "summary",
            ),
        )


def _render_retained_plan(st):
    """An unsuccessful action must not make the reliable plan disappear."""
    bundle = load_live_final_turn(st.session_state)
    if bundle is not None:
        st.caption("原方案仍保留在下面。输入没有清空，可以修改后再试。")
        _safe_user_action(st, st.session_state, "render", lambda: render_page_streamlit(
            st, bundle.turn, extra_questions=bundle.extra_questions,
            saved_snapshot=bool(st.session_state.get(LOCAL_PROFILE_STALE_KEY)),
        ))


def _consume_default_walk_hint(store, turn, reference, campus_id, page_now):
    """Persist the one-time product hint only after a real default walk exists."""
    settings = load_personal_settings(store)
    if (
        settings.default_walk_hint_seen
        or bool(store.get(PERSONAL_TRANSPORT_EXPLICIT_KEY))
        or (settings.travel_mode or "walk") != "walk"
    ):
        return False
    has_walk = any(
        getattr(getattr(block, "mode", None), "value", getattr(block, "mode", None)) == "walk"
        for block in getattr(turn, "movement_blocks", ())
    )
    if not has_walk:
        return False
    updated = replace(settings, default_walk_hint_seen=True)
    save_personal_settings(store, updated)
    try:
        _persist_local_profile(
            store, reference=reference, campus_id=campus_id, now=page_now
        )
    except (LocalPersistenceError, ValueError, TypeError):
        # The route remains reliable; a failed hint receipt simply means it
        # may be shown once more after reopening.
        save_personal_settings(store, settings)
    return True


def _bundle_uses_p4_execution(bundle):
    return bool(
        bundle is not None
        and (
            any(
                binding.activity_kind in ("generic", "meal")
                for binding in bundle.execution_context.bindings
            )
            or bundle.execution_context.concurrency_authorizations
        )
    )


def _render_what_if_preview(st, preview):
    """Render a hypothetical result without presenting it as the live plan."""
    if not isinstance(preview, WhatIfPreview):
        return
    import html
    rows = "".join(
        '<div class="cf-whatif-diff">{}</div>'.format(
            html.escape(sanitize_user_facing_text(item))
        )
        for item in preview.key_differences[:3]
    )
    warning = (
        '<div class="cf-whatif-diff">{}</div>'.format(
            html.escape(sanitize_user_facing_text(preview.warning))
        )
        if preview.warning and preview.warning != preview.summary else ""
    )
    impact = (
        '<div class="cf-whatif-diff">{}</div>'.format(
            html.escape(sanitize_user_facing_text(preview.important_impact))
        ) if preview.important_impact else ""
    )
    st.markdown(
        '<section class="cf-whatif-card"><div class="cf-whatif-title"><span class="cf-preview-label">预览 · 尚未采用</span>如果这样调整</div>'
        '<div class="cf-whatif-summary">{}</div>{}{}{}</section>'.format(
            html.escape(sanitize_user_facing_text(preview.summary)), rows, impact, warning
        ),
        unsafe_allow_html=True,
    )


def _safe_user_action(st, store, label, fn):
    """live 用户操作安全边界：内部异常不向用户暴露 traceback / 路径 / 内部信息。

    开发调试信息写入 store（session_state）内部保留，不破坏诊断能力；
    用户最多看到一句简短人话。
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - UI 边界，需兜住所有内部异常
        # 开发侧诊断只输出到终端，绝不展示给用户页面。
        logger.error("[CampusFlow][%s] %s", label, type(exc).__name__)
        store[LIVE_ERROR_KEY] = "{}: {}".format(label, type(exc).__name__)
        st.error(SAFE_ERROR_TEXT)
        return None


def _rerun(st):
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


if __name__ == "__main__":
    main()

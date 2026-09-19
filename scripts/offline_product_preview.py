"""Interactive synthetic rehearsal through the real CampusFlow live entry.

No production profile or model configuration is read. See docs/demo_guide.md.
"""
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    from scripts.offline_product_model import OfflineProductModel, STORY_TEXT
    from src.local_persistence import LocalProfileStore
    from src.p2_live_main import main as live_main, LIVE_INTAKE_KEY
    from src.profile_identity import CURRENT_USER_CONTEXT_KEY, ProfileIdentity
    import requests

    st.set_page_config(page_title="CampusFlow · 离线开发演示", layout="wide")

    class PreviewStreamlit:
        def __getattr__(self, name):
            return getattr(st, name)

        def set_page_config(self, **kwargs):
            pass  # Configured above before reading demo query/cache state.

    # Defence in depth: this development process cannot accidentally fall
    # through to requests-based model services, even if an adapter is changed.
    def deny_network(*args, **kwargs):
        raise RuntimeError("Offline preview blocks external model requests")
    requests.sessions.Session.request = deny_network

    @st.cache_resource
    def preview_directory():
        # Never reuse CAMPUSFLOW_DATA_DIR supplied by a user's normal service.
        specified = os.environ.get("CAMPUSFLOW_PREVIEW_DATA_DIR")
        if specified:
            directory = Path(specified).resolve()
            if (directory.parent != Path(tempfile.gettempdir()).resolve()
                    or not directory.name.startswith("campusflow-rehearsal-")):
                raise ValueError("Preview data must be an isolated rehearsal directory")
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        return Path(tempfile.mkdtemp(prefix="campusflow-rehearsal-"))

    profile = st.query_params.get("profile", "demo")
    if profile not in ("demo", "round1", "round2", "visual", "estimate", "short", "final", "profiles", "release"):
        profile = "demo"
    directory = preview_directory() / profile
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["CAMPUSFLOW_DATA_DIR"] = str(directory)
    if st.query_params.get("identity") != "anonymous":
        st.session_state.setdefault(
            CURRENT_USER_CONTEXT_KEY,
            ProfileIdentity("demo-" + profile, True, "student"),
        )
    low_input_story = st.query_params.get("low_input")
    material_story = st.query_params.get("material")
    if st.query_params.get('confirmation') == 'location':
        from scripts.confirmation_preview_model import LocationConfirmationPreviewModel
        model = LocationConfirmationPreviewModel()
    elif st.query_params.get('confirmation') in ('end', 'next'):
        from scripts.confirmation_preview_model import ConfirmationPreviewModel
        model = ConfirmationPreviewModel(st.query_params.get('confirmation') == 'next')
    elif st.query_params.get('documents') == '1':
        from scripts.document_material_preview import DocumentPreviewModel
        rehearsal=st.query_params.get('estimate_rehearsal','')
        model = DocumentPreviewModel(rehearsal if rehearsal in ('fallback','full','needs_input','exam','action','action_broken','quality','intake_chain','workload','workload_partial','workload_empty','workload_model') else '')
    elif profile == 'release':
        from scripts.release_demo_model import ReleaseDemoModel
        model = ReleaseDemoModel()
    elif material_story in ('a','b','c','relative','dirty_a','dirty_b','dirty_c',
                          'dirty_d','dirty_e','dirty_f','dirty_none','existing_old'):
        from scripts.material_demo_model import MaterialDemoModel
        model = MaterialDemoModel(material_story)
    elif low_input_story in ("a", "b", "c", "unknown"):
        from scripts.low_input_model import LowInputModel
        model = LowInputModel("b" if low_input_story == "unknown" else low_input_story,
                              estimate=low_input_story != "unknown")
    else:
        model = OfflineProductModel()
    if st.query_params.get('ui_delay') == '1':
        # Development-only observation window for native THINKING controls.
        # This wraps the fake response seam, never a production API adapter.
        import time
        original_response = model._response
        def delayed_response(system, user):
            if 'timetable' in system or '课表文字识别器' in system:
                time.sleep(1.5)
            if 'Raw Event Extractor' in system:
                time.sleep(8)
            if 'p3.unified-feedback.v1' in system:
                time.sleep(2)
            return original_response(system, user)
        model._response = delayed_response
    adapter = st.session_state.setdefault("offline_product_adapter", model)
    story = st.query_params.get("story", "first")
    if "offline_story_seeded" not in st.session_state:
        st.session_state["offline_story_seeded"] = True
        if story in ("plan", "whatif"):
            st.session_state[LIVE_INTAKE_KEY] = STORY_TEXT
    # A fresh browser session shares this temporary profile, just as reopening
    # the local product does. Query time simulates a stale record, not progress.
    now = datetime(2026, 9, 7 if material_story or profile == 'release' else 1, 16 if story == "restored" else 14, 0)
    presentation = st.query_params.get('presentation') in ('1', 'readme')
    if st.query_params.get('presentation') == '1':
        st.caption('展示样例 · 合成数据')
    elif not presentation:
        st.markdown('<div style="position:fixed;bottom:8px;left:12px;z-index:999;font-size:11px;'
                'color:#715b30;background:#fff8e8;border:1px solid #ebd9af;border-radius:6px;padding:3px 7px">'
                '离线开发演示 · 合成数据 · 无真实模型调用</div>', unsafe_allow_html=True)
    live_main(
        st=PreviewStreamlit(), adapter_factory=lambda: adapter, now_provider=lambda: now,
        configuration_loader=lambda: ("TJU_LLM_API_KEY",) if st.query_params.get("unconfigured") == "1" else (),
        persistence_factory=lambda: LocalProfileStore(directory / "campusflow.sqlite3"),
    )
    if presentation:
        return  # finite development entry only; production has no demo switch
    if st.query_params.get('confirmation') == 'next':
        from scripts.confirmation_preview_model import seed_followup_question
        if seed_followup_question(st.session_state):
            st.rerun()
    with st.expander("开发演示 · synthetic demo · 不连接真实模型", expanded=False):
        st.caption("只支持演示指南中的固定材料。所有结果由 mock 响应进入正式业务流程生成；这不是理解能力验收。")
        st.code(STORY_TEXT, language=None)
        st.caption("本次浏览器会话的 mock 调用：{} 次；普通操作不应增加。临时档案与真实档案隔离。".format(len(adapter.calls)))
        if st.query_params.get('ui_probe') == '1':
            import json
            import hashlib
            from src.p2_session import load_live_final_turn
            bundle = load_live_final_turn(st.session_state)
            uploaded = st.session_state.get('cf_material_image')
            st.code(json.dumps(dict(
                material_attached=callable(getattr(uploaded, 'getvalue', None)),
                material_bytes=len(uploaded.getvalue()) if callable(getattr(uploaded, 'getvalue', None)) else 0,
                supplement=st.session_state.get('cf_material_flow',{}).get('supplement'),
                phase=st.session_state.get('cf_material_flow',{}).get('phase'),
                retained_dates={k:str(v) for k,v in st.session_state.get('personal_settings_draft_values',{}).items() if k.endswith(('semester_first','semester_end'))},
                widget_dates={k:str(v) for k,v in st.session_state.items() if k.endswith(('semester_first','semester_end'))},
                plan=hashlib.sha256(repr(bundle).encode()).hexdigest() if bundle else None,
                calls=len(adapter.calls),
                roles=adapter.calls,
                bindings=getattr(adapter, 'bindings', []),
                commitments=[dict(ref=c.commitment_ref, title=c.title, start=str(c.starts_at), end=str(c.ends_at)) for c in bundle.state.commitments] if bundle else [],
                tasks=repr(bundle.state.tasks) if bundle else None,
            ), ensure_ascii=False), language='json')
        if material_story is None and getattr(adapter,'rehearsal','') in ('intake_chain','workload','workload_partial','workload_empty','workload_model'):
            import json
            st.code(json.dumps(adapter.material_requests,ensure_ascii=False),language='json')
        mode = st.selectbox("演示服务状态", ("正常", "模拟网络失败", "模拟格式错误"))
        adapter.failure_mode = {"正常": None, "模拟网络失败": "network", "模拟格式错误": "format"}[mode]


if __name__ == "__main__":
    main()

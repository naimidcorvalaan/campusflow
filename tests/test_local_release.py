from pathlib import Path
import zipfile
import pytest
from scripts.build_local_release import build, source_files


def test_source_package_excludes_local_data_and_keeps_required_product_files(tmp_path):
    root = tmp_path / 'source'
    for name in ('README.md','DESIGN.md','requirements.txt','启动 CampusFlow.bat',
                 'src/p2_live_main.py','.env.example','.streamlit/config.toml',
                 '.env','.env.dev','.streamlit/secrets.toml','src/__pycache__/a.pyc',
                 'docs/personal.sqlite3','screenshots/demo.png','artifacts/private.png'):
        file = root / name
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text('synthetic',encoding='utf-8')
    output = tmp_path / 'delivery.zip'
    build(root,output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert 'CampusFlow-v1.0/.env.example' in names
        assert 'CampusFlow-v1.0/screenshots/demo.png' in names
        assert not any(x.endswith(('.env','.env.dev','secrets.toml','.sqlite3','.pyc','private.png')) for x in names)
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        build(root,output)
    assert output.read_bytes() == original


def test_release_story_feedback_uses_real_progress_and_preserves_course():
    from datetime import datetime
    from types import SimpleNamespace
    from scripts.release_demo_model import ReleaseDemoModel, FEEDBACK_TEXT
    from scripts.offline_product_model import STORY_TEXT
    from src.p2_live_main import _handle_intake_submit, make_live_session
    from src.p2_session import load_live_final_turn
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY

    state, messages = {}, []
    model = ReleaseDemoModel()
    campus_map = DEFAULT_CAMPUS_REGISTRY.get_campus_map('weijinlu')
    session = make_live_session(state, model, map_data=campus_map, campus_id='weijinlu',
        companion_enabled=True, agent_intelligence_enabled=True)
    st = SimpleNamespace(session_state=state, error=messages.append,
        warning=messages.append, write=messages.append)
    assert _handle_intake_submit(st, session, (), datetime(2026,9,7,14), STORY_TEXT,
        campus_map, 'weijinlu', current_location_text='', rerun_after_commit=False), messages
    before = load_live_final_turn(state)
    session.rebuild_feedback_atomic(FEEDBACK_TEXT)
    after = load_live_final_turn(state)
    old, work = before.state.tasks[0], after.state.tasks[0]
    assert work.task_ref == old.task_ref
    assert old.total_minutes == work.total_minutes == 90
    assert old.completed_minutes == 0 and work.completed_minutes == 50
    assert after.state.commitments == before.state.commitments

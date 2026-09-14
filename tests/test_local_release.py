from pathlib import Path
import zipfile
import pytest
from scripts.build_local_release import (
    build, source_files, inspect_archive, REQUIRED_FILES, PACKAGE, ROOT,
)


def runtime_source(tmp_path):
    root = tmp_path / 'source'
    for name in REQUIRED_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}' if path.suffix == '.json' else '# synthetic fixture\n', encoding='utf-8')
    (root / 'requirements.txt').write_text('pytest==7.4.0\nstreamlit==1.31.1\n', encoding='utf-8')
    return root


def test_runtime_package_excludes_development_user_data_and_preserves_existing_zip(tmp_path):
    root = runtime_source(tmp_path)
    excluded = ('.env', '.env.dev', '.git/config', '.github/workflows/ci.yml',
        '.venv/pyvenv.cfg', '.streamlit/secrets.toml', 'src/__pycache__/a.pyc',
        'src/nested/debug.py', 'src/user.sqlite3', 'src/user.sqlite-wal', 'data/uploads.json',
        'data/model-service.json', 'docs/personal.sqlite3', 'docs/FRONTEND_FREEZE.md',
        'docs/assets/readme/today.png', 'screenshots/demo.png', 'artifacts/private.png',
        'deploy/secrets.toml', 'prototype/app.js', 'tests/test_app.py', 'scripts/spacetime_preview.py')
    for name in excluded:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('excluded private fixture', encoding='utf-8')
    output = tmp_path / 'delivery.zip'
    count = build(root, output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert PACKAGE + '/.env.example' in names
        assert PACKAGE + '/README.md' in names
        assert not any(PACKAGE + '/' + name in names for name in excluded)
        assert archive.read(PACKAGE + '/requirements.txt') == b'streamlit==1.31.1\n'
    assert inspect_archive(output)['files'] == count
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        build(root, output)
    assert output.read_bytes() == original


@pytest.mark.parametrize('missing', sorted(REQUIRED_FILES))
def test_builder_requires_every_essential_runtime_file(tmp_path, missing):
    root = runtime_source(tmp_path)
    (root / missing).unlink()
    with pytest.raises(ValueError, match='缺少运行文件'):
        build(root, tmp_path / 'absent.zip')
    assert not (tmp_path / 'absent.zip').exists()


@pytest.mark.parametrize('kind', ('token', 'private-key', 'machine-path', 'dotenv-value'))
def test_builder_rejects_sensitive_content_before_creating_zip(tmp_path, kind):
    root = runtime_source(tmp_path)
    content = {'token': 'sk-' + 'A' * 32,
        'private-key': '-----BEGIN ' + 'PRIVATE KEY-----',
        'machine-path': 'C:' + chr(92) + 'Users' + chr(92) + 'example-user',
        'dotenv-value': 'TJU_LLM_API_KEY=' + 'A' * 32}[kind]
    target = '.env.example' if kind == 'dotenv-value' else 'src/p2_live_main.py'
    (root / target).write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='发布文件检查未通过') as error:
        build(root, tmp_path / 'absent.zip')
    assert content not in str(error.value)
    assert not (tmp_path / 'absent.zip').exists()


def test_inspector_rejects_an_extra_unallowlisted_archive_entry(tmp_path):
    root = runtime_source(tmp_path)
    output = tmp_path / 'delivery.zip'
    build(root, output)
    with zipfile.ZipFile(output, 'a') as archive:
        archive.writestr(PACKAGE + '/.env', 'private fixture')
    with pytest.raises(ValueError, match='非运行文件'):
        inspect_archive(output)


def test_production_archive_is_reproducible_and_contains_identical_campus_data(tmp_path):
    first, second = tmp_path / 'first.zip', tmp_path / 'second.zip'
    build(ROOT, first)
    build(ROOT, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        for campus in ('beiyangyuan', 'weijinlu'):
            name = 'data/' + campus + '_map.json'
            assert archive.read(PACKAGE + '/' + name) == (ROOT / name).read_bytes()
        assert archive.read(PACKAGE + '/src/workspace.css') == (ROOT / 'src/workspace.css').read_bytes()
        requirements = archive.read(PACKAGE + '/requirements.txt').decode()
        assert 'pytest' not in requirements
        assert 'streamlit==1.31.1' in requirements


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

import subprocess
import os
import shutil
import sys
from pathlib import Path

import pytest

from scripts.launch_campusflow import (
    InstanceLock, LaunchError, check_python, environment_python, prepare_environment,
)


@pytest.mark.parametrize("version", ((3, 7), (3, 13), (2, 7), (3,9,7)))
def test_wrong_python_has_actionable_chinese_error(version):
    with pytest.raises(LaunchError, match="Python 3.12"):
        check_python(version)


def test_existing_environment_does_not_install_on_every_launch(tmp_path, monkeypatch):
    import scripts.launch_campusflow as launcher
    environment = tmp_path / "venv"
    python = environment_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert prepare_environment(environment, tmp_path / "requirements.txt") == python
    assert not any("pip" in c or "venv" in c for c in calls)


def test_initializer_creates_once_and_reports_package_failure_without_details(tmp_path, monkeypatch, capsys):
    import scripts.launch_campusflow as launcher
    calls = []
    options = []
    def run(command, **kwargs):
        calls.append(command)
        options.append(kwargs)
        return subprocess.CompletedProcess(command, 1 if "pip" in command else 0)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    def fail_install(*args):
        calls.append(['pip'])
        raise LaunchError('依赖安装未完成。请检查网络后重新双击。')
    monkeypatch.setattr(launcher, 'install_dependencies', fail_install)
    monkeypatch.setattr(launcher, "dependencies_ready", lambda *a: False)
    with pytest.raises(LaunchError, match="检查网络"):
        prepare_environment(tmp_path / "new", tmp_path / "requirements.txt")
    assert sum("venv" in c for c in calls) == 1
    assert sum("pip" in c for c in calls) == 1
    assert all(c["stderr"] is subprocess.DEVNULL for c in options)


def test_install_progress_never_echoes_sensitive_package_index_output():
    from scripts.launch_campusflow import pip_status
    assert pip_status('Looking in indexes: https://person:private@example.invalid/simple') == (None,None)
    assert pip_status('Downloading https://example.invalid/wheel?token=private') == (None,None)
    status,category = pip_status('WARNING ProxyError https://person:private@example.invalid')
    assert status is None and category == '软件源网络连接失败或超时'
    assert pip_status('Using cached streamlit-1.31.1-py2.py3-none-any.whl')[0].startswith('依赖：使用缓存')


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher close-window lifecycle')
def test_abrupt_launcher_exit_terminates_its_child(tmp_path):
    # Exercise the real OS boundary, not a mocked terminate call. os._exit skips
    # Python finally blocks just as a closed launcher window can do.
    code = (
        'import os,subprocess,sys; from scripts.windows_child_job import ChildJob; '
        'job=ChildJob(); p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(45)"],'
        'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
        'job.attach(p); print(p.pid,flush=True); os._exit(0)'
    )
    result = subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x100000,False,int(result.stdout.strip()))
    if handle:
        try:
            assert api.WaitForSingleObject(handle,5000) == 0
        finally:
            api.CloseHandle(handle)


def test_lock_blocks_a_second_process_and_stale_metadata_does_not_block(tmp_path):
    lock = InstanceLock(tmp_path, tmp_path / "environment")
    assert lock.acquire()
    lock.save({"status": "ready", "port": 8545})
    code = (
        "from pathlib import Path; from scripts.launch_campusflow import InstanceLock; "
        "import sys; p=Path(sys.argv[1]); l=InstanceLock(p,p/'environment'); "
        "print(l.acquire()); l.close()"
    )
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "False"
    lock.close()
    again = InstanceLock(tmp_path, tmp_path / "environment")
    assert again.acquire()
    again.close()


def test_nonempty_broken_environment_is_not_overwritten(tmp_path):
    (tmp_path / "keep.txt").write_text("user work", encoding="utf-8")
    with pytest.raises(LaunchError, match="不会覆盖"):
        prepare_environment(tmp_path, tmp_path / "requirements.txt")
    assert (tmp_path / "keep.txt").read_text() == "user work"


@pytest.mark.skipif(os.name != "nt", reason="Windows double-click entry")
def test_real_batch_without_python_explains_installation(tmp_path):
    # A fresh folder and restricted process-local PATH; no system installation
    # or existing project environment is changed.
    entry = tmp_path / "start_campusflow.bat"
    shutil.copy2(Path(__file__).resolve().parents[1] / entry.name, entry)
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    environment = dict(os.environ, PATH=str(system32))
    result = subprocess.run([str(system32 / "cmd.exe"), "/d", "/c", str(entry), "--no-pause"],
        input="\n", capture_output=True, encoding="utf-8", errors="replace",
        env=environment, timeout=20)
    assert "未找到 Python" in result.stdout
    assert "Add python.exe to PATH" in result.stdout
    assert "Traceback" not in result.stdout

"""Local desktop launcher. Standard library bootstrap; never logs model secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request
import webbrowser
import queue
import re

try:
    from scripts.windows_child_job import ChildJob
except ModuleNotFoundError:  # double-click executes this file by its path
    from windows_child_job import ChildJob


ROOT = Path(__file__).resolve().parents[1]


class LaunchError(Exception):
    pass


def check_python(version=None):
    version = tuple(version or sys.version_info[:3])
    if not (3, 8) <= version[:2] < (3, 13) or version[:3] == (3,9,7):
        raise LaunchError("当前版本需要 Python 3.8–3.12（不含3.9.7）；建议安装 Python 3.12 后重新启动。")


def environment_python(environment):
    return Path(environment) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def dependencies_ready(python, requirements):
    # Compare installed distribution metadata, not a marker that can go stale.
    code = (
        "from importlib.metadata import version; import sys; "
        "lines=[x.strip() for x in open(sys.argv[1],encoding='utf-8')]; "
        "pairs=[x.split('==') for x in lines if x and not x.startswith('#')]; "
        "assert all(version(n)==v for n,v in pairs)"
    )
    try:
        result = subprocess.run([str(python), "-c", code, str(requirements)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pip_status(line):
    """Return only allowlisted stages/names, never raw index URLs or errors."""
    for prefix,label in (("Collecting ","准备"),("Downloading ","下载"),("Using cached ","使用缓存")):
        if line.strip().startswith(prefix):
            name = line.strip()[len(prefix):].split()[0]
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.=+!<>~-]{0,150}',name):
                return '依赖：{} {}'.format(label,name),None
    lowered = line.lower()
    if 'installing collected packages' in lowered:
        return '依赖已下载，正在安装到独立环境。',None
    for tokens,category in (
        (('sslerror','certificate_verify_failed'),'证书校验失败'),
        (('proxyerror','connectionerror','connection broken','timed out','failed to establish','getaddrinfo failed'),'软件源网络连接失败或超时'),
        (('requires a different python','no matching distribution','resolutionimpossible'),'依赖或Python版本不匹配'),
        (('failed building wheel','could not build wheels'),'依赖构建失败'),
        (('permission denied','access is denied','no space left'),'目录权限或可用磁盘空间不足')):
        if any(token in lowered for token in tokens):
            return None,category
    return None,None


def install_dependencies(python, requirements):
    started = time.monotonic()
    messages = queue.Queue()
    job = ChildJob()
    process = None
    try:
        process = subprocess.Popen([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
            '--progress-bar','off','--timeout','20','--retries','1','-r',str(requirements)],
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        job.attach(process)
        def read_output():
            for raw in iter(process.stdout.readline,b''):
                status,category = pip_status(raw.decode('utf-8',errors='replace'))
                if status or category:
                    messages.put((status,category))
        reader = threading.Thread(target=read_output,daemon=True)
        reader.start()
        last_notice = started
        last_status = None
        failure = '请检查网络后重试'
        while process.poll() is None or reader.is_alive() or not messages.empty():
            elapsed = time.monotonic()-started
            if elapsed > 600:
                raise LaunchError('依赖安装超过10分钟，已停止本次安装。重新双击可核对已装依赖并安全重试。')
            try:
                status,category = messages.get(timeout=0.2)
                if category:
                    failure = category
                if status and status != last_status:
                    print(status,flush=True)
                    last_status = status
                    last_notice = time.monotonic()
            except queue.Empty:
                pass
            if time.monotonic()-last_notice >= 30:
                print('依赖仍在准备，已用{}秒；可按Ctrl+C中断，稍后重新双击继续。'.format(int(elapsed)),flush=True)
                last_notice = time.monotonic()
        if process.returncode:
            raise LaunchError('依赖安装未完成：{}（退出码{}）。请检查网络后重新双击；已有环境和档案保留。'.format(failure,process.returncode))
        print('依赖安装完成，用时{}秒。'.format(int(time.monotonic()-started)),flush=True)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        job.close()
        if process is not None and process.stdout:
            process.stdout.close()


def prepare_environment(environment, requirements, allow_install=True):
    python = environment_python(environment)
    if not python.exists():
        if Path(environment).exists() and any(Path(environment).iterdir()):
            raise LaunchError("虚拟环境目录已有文件但无法使用。请保留目录，并查看本地启动说明；不会覆盖它。")
        if not allow_install:
            raise LaunchError("尚未初始化运行环境；首次启动需要安装依赖。")
        print("首次启动：正在创建独立运行环境，请稍候。", flush=True)
        result = subprocess.run([sys.executable, "-m", "venv", str(environment)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise LaunchError("运行环境创建失败。请确认 Python 安装包含 venv，且项目目录可写。")
    result = subprocess.run([str(python), "-c",
        "import sys; sys.exit(0 if (3,8)<=sys.version_info[:2]<(3,13) and sys.version_info[:3]!=(3,9,7) else 1)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise LaunchError("项目环境的 Python 版本不兼容。请保留原环境，按本地启动说明重新准备。")
    if dependencies_ready(python, requirements):
        print("运行环境已就绪，无需重复安装。", flush=True)
        return python
    if not allow_install:
        raise LaunchError("运行依赖尚未就绪，请联网完成首次初始化后再启动。")
    print("首次准备或依赖有变化：正在安装项目依赖。这一步需要访问 Python 软件源。", flush=True)
    install_dependencies(python,requirements)
    if not dependencies_ready(python, requirements):
        raise LaunchError("依赖安装未完成。请检查网络后重新双击，已有档案不会受影响。")
    return python


class InstanceLock:
    """An OS-held lock survives stale files and serializes repeated double-clicks."""
    def __init__(self, root, environment):
        digest = hashlib.sha256((str(root.resolve()) + str(environment.resolve())).encode()).hexdigest()[:20]
        directory = Path(tempfile.gettempdir()) / "campusflow-launcher"
        directory.mkdir(exist_ok=True)
        self.path = directory / (digest + ".lock")
        self.state_path = directory / (digest + ".json")
        self.file = None

    def acquire(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            return False
        return True

    def save(self, state):
        # Launcher runtime metadata only; never stores profiles or model config.
        state = dict(state,launcher_pid=os.getpid())
        temporary = self.state_path.with_suffix(".new")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self.state_path)

    def read(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


def healthy(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:{}/_stcore/health".format(port), timeout=1) as response:
            return response.status == 200 and response.read(100).strip() == b"ok"
    except (OSError, ValueError):
        return False


def launch(args):
    check_python()
    environment = Path(args.venv or ROOT / ".venv").resolve()
    lock = InstanceLock(ROOT, environment)
    if not lock.acquire():
        state = lock.read()
        port = state.get("port")
        if state.get("status") == "ready" and isinstance(port, int) and healthy(port):
            print("CampusFlow 已在运行，正在打开原页面。", flush=True)
            if not args.no_browser:
                webbrowser.open("http://127.0.0.1:{}".format(port))
        else:
            print("CampusFlow 正在启动，请查看已经打开的启动窗口，无需再次双击。", flush=True)
        return 0
    child = None
    job = None
    try:
        lock.save({"status": "preparing", "port": args.port})
        python = prepare_environment(environment, ROOT / "requirements.txt", not args.no_install)
        if args.prepare_only:
            return 0
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", args.port))
        except OSError:
            raise LaunchError("本地端口 {} 已被其他服务使用；请关闭对应服务或查看启动说明选择端口。".format(args.port))
        entry = ROOT / ("scripts/offline_product_preview.py" if args.offline_demo else "src/p2_live_main.py")
        # Model configuration is loaded by the normal page at use time, never
        # passed through command arguments or copied into launcher state.
        print("正在打开 CampusFlow。模型未配置时仍可浏览页面、编辑本地设置。", flush=True)
        command = [str(python), "-m", "streamlit", "run", str(entry),
            "--server.address=127.0.0.1", "--server.port={}".format(args.port),
            "--server.headless=true", "--browser.gatherUsageStats=false"]
        job = ChildJob()
        child = subprocess.Popen(command, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        job.attach(child)
        started = time.monotonic()
        while not healthy(args.port):
            if child.poll() is not None or time.monotonic() - started > 60:
                raise LaunchError("页面服务未能启动。请运行本地启动说明中的诊断，档案不会被清空。")
            time.sleep(0.3)
        lock.save({"status": "ready", "port": args.port})
        url = "http://127.0.0.1:{}".format(args.port)
        print("CampusFlow 已就绪：{}\n使用期间请保留此窗口；按 Ctrl+C 结束服务。".format(url), flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        child.wait()
        return child.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if job is not None:
            job.close()
        lock.save({"status": "stopped", "port": args.port})
        lock.close()
        print("CampusFlow 本次服务已结束。重新双击可再次打开。", flush=True)


def main():
    parser = argparse.ArgumentParser(description="CampusFlow 本地一键启动")
    parser.add_argument("--venv", help="开发验证用独立环境；默认复用项目 .venv")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-install", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--offline-demo", action="store_true")
    parser.add_argument("--no-pause", action="store_true",help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if not 1024 <= args.port <= 65535:
            raise LaunchError("本地端口应在1024到65535之间。")
        return launch(args)
    except (LaunchError, OSError) as exc:
        print(str(exc) if isinstance(exc, LaunchError) else "本地环境无法访问。请检查目录权限和 Python 安装。", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

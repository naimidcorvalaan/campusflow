"""Build the Windows runtime ZIP from an explicit allowlist, never a repo ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'v1.0.0-rc1'
PACKAGE = 'CampusFlow-' + VERSION
ARCHIVE_NAME = PACKAGE + '-windows.zip'
FIXED_FILES = frozenset((
    '启动 CampusFlow.bat', 'start_campusflow.bat', 'requirements.txt', '.env.example',
    '.streamlit/config.toml', 'scripts/launch_campusflow.py', 'scripts/windows_child_job.py',
    'docs/windows_release_readme.md', 'docs/local_quickstart.md', 'src/workspace.css',
    'data/beiyangyuan_locations.json', 'data/beiyangyuan_map.json', 'data/weijinlu_map.json',
))
REQUIRED_FILES = FIXED_FILES | {'src/p2_live_main.py', 'src/spacetime_ui.py',
    'src/model_service_config.py', 'src/model_service_ui.py', 'src/p3_campus_registry.py'}
RENAME = {'docs/windows_release_readme.md': 'README.md'}
# All production modules are currently flat. Nested developer/output folders
# are deliberately not accepted through a broad recursive suffix rule.
def deliverable(relative):
    return relative.as_posix() in FIXED_FILES or (
        len(relative.parts) == 2 and relative.parts[0] == 'src' and relative.suffix == '.py')


def source_files(root):
    root = Path(root).resolve()
    candidates = [root / name for name in FIXED_FILES]
    candidates.extend((root / 'src').glob('*.py'))
    result = []
    for path in candidates:
        relative = path.relative_to(root)
        if not deliverable(relative) or not path.is_file():
            continue
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('发布文件不能来自链接或仓库外部：' + relative.as_posix())
        result.append(path)
    present = {p.relative_to(root).as_posix() for p in result}
    missing = REQUIRED_FILES - present
    if missing:
        raise ValueError('缺少运行文件：' + ', '.join(sorted(missing)))
    return sorted(set(result))


SECRET_RULES = {
    'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'provider-token': r'\bsk-[A-Za-z0-9_-]{24,}',
    'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})',
    'aws-key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'slack-token': r'\bxox[baprs]-[A-Za-z0-9-]{20,}',
    'machine-path': r'(?i)[a-z]:[\\/](?:Users|Documents and Settings)[\\/]',
    'credential-value': r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_+/=-]{20,})[\"']",
}


def scan_text(name, data):
    text = data.decode('utf-8-sig')
    if Path(name).name == '.env.example':
        for line in text.splitlines():
            assignment = re.match(
                r'(?i)^\s*(?:export\s+)?[a-z0-9_]*(?:api_key|token|password|secret)\s*=\s*(.*?)\s*$', line)
            if assignment:
                value = assignment.group(1).strip("\"'")
                if value and not re.match(r'(?i)^(replace[-_]|your[-_])', value):
                    raise ValueError('发布文件检查未通过：{} (env-template-value)'.format(name))
    for rule, pattern in SECRET_RULES.items():
        for match in re.finditer(pattern, text):
            # The developer .env template may contain explicit replacement text.
            if rule == 'credential-value' and re.match(
                    r'(?i)^(replace[-_]|your[-_])', match.group(1)):
                continue
            raise ValueError('发布文件检查未通过：{} ({})'.format(name, rule))


def package_payload(root):
    root = Path(root).resolve()
    payload = {}
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        name = RENAME.get(relative, relative)
        data = path.read_bytes()
        if name == 'requirements.txt':
            # Keep the exact runtime pins; pytest is only needed by source tests.
            lines = data.decode('utf-8-sig').splitlines()
            data = ('\n'.join(line for line in lines if not re.match(
                r'(?i)^\s*pytest\s*==', line)) + '\n').encode('utf-8')
        scan_text(name, data)
        if name.endswith('.json'):
            json.loads(data.decode('utf-8'))
        payload[PACKAGE + '/' + name] = data
    return payload


def inspect_archive(output):
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('发布包包含重复文件')
        for item in archive.infolist():
            prefix = PACKAGE + '/'
            if not item.filename.startswith(prefix):
                raise ValueError('发布包顶层目录不匹配')
            relative = Path(item.filename[len(prefix):])
            source = 'docs/windows_release_readme.md' if relative.as_posix() == 'README.md' else relative.as_posix()
            if not deliverable(Path(source)):
                raise ValueError('发布包包含非运行文件：' + relative.as_posix())
            scan_text(item.filename, archive.read(item))
        expected = {PACKAGE + '/' + RENAME.get(name, name) for name in REQUIRED_FILES}
        if not expected <= set(names) or archive.testzip() is not None:
            raise ValueError('发布包缺少必要文件或校验失败')
        return dict(filename=Path(output).name, version=VERSION, top_level=PACKAGE + '/',
            files=len(names), compressed_bytes=Path(output).stat().st_size,
            extracted_bytes=sum(item.file_size for item in archive.infolist()),
            sha256=hashlib.sha256(Path(output).read_bytes()).hexdigest())


def build(root, output):
    payload = package_payload(root)  # Check all inputs before creating an output.
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    inspect_archive(output)
    return len(payload)


def main():
    parser = argparse.ArgumentParser(description='构建干净的 Windows 运行包，不发布或上传')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ARCHIVE_NAME)
    args = parser.parse_args()
    try:
        build(ROOT, args.output)
        print(json.dumps(inspect_archive(args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc) if isinstance(exc, ValueError) else '未能创建发布包；检查目录权限或目标文件是否已存在。不会覆盖已有包。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

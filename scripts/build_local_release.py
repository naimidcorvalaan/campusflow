"""Create a local source delivery zip. No Git writes, tags, upload or secrets."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = frozenset(('README.md','DESIGN.md','CHANGELOG.md','requirements.txt',
    '.env.example','.gitignore','.gitattributes','启动 CampusFlow.bat','start_campusflow.bat',
    '版权合规承诺书.docx'))
DIRECTORIES = frozenset(('src','data','scripts','tests','docs','screenshots','deploy'))
SUFFIXES = frozenset(('.py','.md','.json','.svg','.png','.jpg','.jpeg','.cjs','.js',
    '.bat','.ps1','.sh','.toml','.example','.txt','.yaml','.yml'))
BLOCKED_PARTS = frozenset(('.git','.venv','__pycache__','.pytest_cache','node_modules',
    'logs','artifacts','.campusflow','.preview-data'))


def deliverable(relative):
    if any(part in BLOCKED_PARTS for part in relative.parts):
        return False
    name = relative.name.lower()
    if name.startswith('.env') and relative.as_posix() != '.env.example':
        return False
    if name in ('secrets.toml', 'model-service.json') or name.startswith('.model-service-') or any(mark in name for mark in ('.sqlite','.db','.log')):
        return False
    if relative.as_posix() == '.streamlit/config.toml':
        return True
    if len(relative.parts) == 1:
        return relative.name in ROOT_FILES
    return relative.parts[0] in DIRECTORIES and relative.suffix.lower() in SUFFIXES


def source_files(root):
    # Traverse only the explicitly selected delivery roots. Never open a real
    # .env, profile, runtime output or arbitrary file beside this repository.
    candidates = [root / name for name in ROOT_FILES] + [root / '.streamlit/config.toml']
    for directory in sorted(DIRECTORIES):
        path = root / directory
        if path.exists():
            candidates.extend(path.rglob('*'))
    result = []
    for path in candidates:
        relative = path.relative_to(root)
        if not deliverable(relative) or not path.is_file():
            continue
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError('交付目录包含链接或外部文件，请先人工核对。')
        result.append(path)
    for required in ('README.md','DESIGN.md','requirements.txt','启动 CampusFlow.bat','src/p2_live_main.py'):
        if root / required not in result:
            raise ValueError('缺少交付文件：'+required)
    return sorted(result)


def build(root, output):
    files = source_files(root)
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(str(output),'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(str(path),'CampusFlow-v1.0/'+path.relative_to(root).as_posix())
    return len(files)


def main():
    parser = argparse.ArgumentParser(description='准备本地源码包，不发布或上传')
    parser.add_argument('--output',type=Path,required=True,help='尚不存在的本地zip路径')
    args = parser.parse_args()
    try:
        count = build(ROOT,args.output.resolve())
    except (OSError,ValueError,zipfile.BadZipFile):
        print('未能完成本地包：请检查目标是否已存在、目录权限及交付文件；不会覆盖已有包。')
        return 1
    print('本地源码包已准备：{}个文件。未打tag、未上传；发布前请核对比赛清单。'.format(count))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Safe, offline startup diagnostics for local or small-server trials."""

import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def main():
    from src.local_persistence import default_profile_path

    required = ("TJU_LLM_API_KEY", "TJU_LLM_BASE_URL", "TJU_LLM_MODEL")
    missing = tuple(name for name in required if not str(os.environ.get(name, "")).strip())
    path = default_profile_path()
    path_in_repo = _inside(path, REPOSITORY_ROOT)
    print("CampusFlow deployment check")
    print("python={}.{}.{}".format(*sys.version_info[:3]))
    print("data_path={}".format(path))
    print("data_path_in_repo={}".format(str(path_in_repo).lower()))
    print("model_configuration={}".format("incomplete" if missing else "present"))
    if missing:
        print("missing_variables={}".format(",".join(missing)))
    if path_in_repo:
        print("data_path_error=configure CAMPUSFLOW_DATA_DIR outside the source repository")
    print("identity_mode=profile selector only; external authentication is required before public access")
    return 2 if missing or path_in_repo else 0


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())

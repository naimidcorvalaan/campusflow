"""Local TJU credentials, separate from profile data and never cached in env."""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from src.local_persistence import default_data_directory

FIELDS = ("TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL")
LABELS = dict(zip(FIELDS, ("API 地址", "API Key", "模型名称")))


class ModelConfigError(ValueError):
    """Only fixed, user-safe messages; never include input or file paths."""


def config_path():
    return default_data_directory() / "model-service.json"


def local_config_exists():
    try:
        return config_path().exists()
    except OSError:
        return False


def read_local_config():
    try:
        path = config_path()
        if not path.exists():
            return {}
        if path.stat().st_size > 16384:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError()
        return {name: data[name] for name in FIELDS
                if isinstance(data.get(name), str) and data[name].strip()}
    except (OSError, ValueError, TypeError):
        raise ModelConfigError("本机模型配置无法读取；请重新保存配置。") from None


def effective_configuration(environ=None):
    values = dict(os.environ if environ is None else environ)
    if str(values.get("CAMPUSFLOW_LLM_PROVIDER", "tju")).strip().lower() not in ("", "tju"):
        return values
    # Complete environment/.env never depends on an unreadable local file.
    if all(str(values.get(name, "")).strip() for name in FIELDS):
        return values
    try:
        saved = read_local_config()
    except ModelConfigError:
        saved = {}
    for name in FIELDS:
        if not str(values.get(name, "")).strip():
            values[name] = saved.get(name, "")
    return values


def validate_configuration(values):
    result = {name: str(values.get(name, "")).strip() for name in FIELDS}
    missing = [LABELS[name] for name in FIELDS if not result[name]]
    if missing:
        raise ModelConfigError("请填写：" + "、".join(missing) + "。")
    try:
        url = urlsplit(result[FIELDS[0]])
        valid = (url.scheme == "https" or
                 (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")))
        if (not valid or not url.hostname or url.username or url.password or url.query
                or url.fragment or any(c.isspace() for c in result[FIELDS[0]])):
            raise ValueError()
        _ = url.port
    except ValueError:
        raise ModelConfigError("请填写 HTTPS 服务地址，不要在地址中附带密钥或登录信息。") from None
    if any(len(value) > 4096 or any(ord(c) < 32 for c in value) for value in result.values()):
        raise ModelConfigError("配置格式不正确，请检查三个字段。")
    # Never allow a pasted key to become a visible model label or request URL.
    if result[FIELDS[1]] in result[FIELDS[0]] or result[FIELDS[1]] in result[FIELDS[2]]:
        raise ModelConfigError("请将 API Key 仅填写在密钥栏，不要放入地址或模型名称。")
    try:
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 16384:
            raise ValueError()
    except (UnicodeError, ValueError):
        raise ModelConfigError("配置格式不正确，请检查三个字段。") from None
    if result[FIELDS[1]] in ("your_api_key_here", "<your-api-key>"):
        raise ModelConfigError("请填写自己的 API Key，不要使用示例占位内容。")
    return result


def save_local_config(values):
    # External fields are never copied to disk or overwritten by this editor.
    external = {name: os.environ.get(name, "").strip() for name in FIELDS}
    combined = dict(values)
    combined.update({name: value for name, value in external.items() if value})
    valid = validate_configuration(combined)
    saved = {name: value for name, value in valid.items() if not external[name]}
    path = config_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".model-service-", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(saved, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
        temporary = None
    except OSError:
        raise ModelConfigError("配置未保存，请检查本机用户目录是否可写；原配置未改变。") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def clear_local_config():
    try:
        path = config_path()
        if path.exists():
            path.unlink()
    except OSError:
        raise ModelConfigError("配置未清除，请检查本机用户目录权限。") from None


def test_saved_connection(caller=None):
    """Explicit, one short request using saved config, never a save prerequisite."""
    from src.llm_provider import provider_name
    if provider_name() != "tju":
        return False, "当前服务由高级配置管理。"
    values = effective_configuration()
    if any(not values.get(name) for name in FIELDS):
        return False, "需要先保存完整的模型配置。"
    if caller is None:
        from src.tju_llm_client import call_tju_llm
        caller = call_tju_llm
    try:
        answer = caller("只回复：连接成功", timeout=10, max_tokens=16, temperature=0)
        if not isinstance(answer, str) or not answer.strip():
            return False, "服务未返回有效内容；已保存的配置未改变。"
    except Exception:
        return False, "当前无法连接模型服务，请检查地址、密钥及校园网或 VPN；已保存的配置未改变。"
    return True, "连接测试通过。"

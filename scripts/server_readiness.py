"""Small, non-authenticated health probe for a CampusFlow Streamlit server."""

import argparse
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen


class ReadinessError(RuntimeError):
    pass


def health_url(base_url):
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ReadinessError("服务地址必须是 http 或 https URL。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ReadinessError("服务地址不能包含凭据、查询参数或片段。")
    return "{}://{}{}/_stcore/health".format(
        parsed.scheme,
        parsed.netloc,
        parsed.path.rstrip("/"),
    )


def check_server(base_url, opener=urlopen, timeout=5.0):
    target = health_url(base_url)
    try:
        with opener(target, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read(64).decode("utf-8", errors="replace").strip().lower()
    except (HTTPError, URLError, OSError) as exc:
        raise ReadinessError("Streamlit 健康检查无法连接。") from exc
    if status != 200 or body != "ok":
        raise ReadinessError("Streamlit 健康检查未返回 ok。")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="CampusFlow 服务就绪检查")
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    args = parser.parse_args(argv)
    try:
        target = check_server(args.url)
    except ReadinessError as exc:
        print("ready=false")
        print("reason={}".format(exc), file=sys.stderr)
        return 2
    print("ready=true")
    print("health_url={}".format(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

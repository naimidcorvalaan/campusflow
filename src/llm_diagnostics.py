"""Shared allowlisted diagnostics; never serialize payloads or exceptions."""
import ipaddress
import re
import socket
import logging
from urllib.parse import urlsplit
import requests


def _exception_chain(exc):
    """Inspect exception objects only, never their messages or request data."""
    found, pending, seen = [], [exc], set()
    while pending and len(found) < 12:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        pending.extend((current.__cause__, current.__context__,
                        getattr(current, "reason", None)))
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))
    return found


def _failure_kind(exc):
    chain = _exception_chain(exc)
    names = {type(item).__name__ for item in chain}
    if any(isinstance(item, socket.gaierror) for item in chain) or "NameResolutionError" in names:
        return "dns_error", False
    if names & {"SSLError", "SSLCertVerificationError", "CertificateError"}:
        return "tls_error", False
    if names & {"ConnectTimeout", "ConnectTimeoutError"}:
        return "connect_timeout", True
    if names & {"ReadTimeout", "ReadTimeoutError"}:
        return "read_timeout", True
    if any(isinstance(item, requests.exceptions.Timeout) for item in chain) or names & {
        "TimeoutException", "TimeoutError", "PoolTimeout", "WriteTimeout"
    }:
        return "timeout", True
    if any(isinstance(item, requests.exceptions.ConnectionError) for item in chain):
        return "connection_error", False
    if any(type(item).__module__.split(".")[0] == "httpx" for item in chain):
        return "httpx_exception", False
    if isinstance(exc, requests.exceptions.RequestException):
        return "requests_exception", False
    return "unexpected_exception", False


def _safe_endpoint(url, api_key):
    """Only known public hosts/IPs and generic API path segments may be logged.

    Unknown service subdomains and tenant paths can identify a user. Omit them,
    as well as userinfo, port, query and fragment. Never log the original URL.
    """
    host, path = "redacted", "redacted"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        try:
            ipaddress.ip_address(hostname)
            host = hostname
        except ValueError:
            if hostname in {"ai.tju.edu.cn", "api.tju.edu.cn", "llm.tju.edu.cn",
                            "model.invalid", "api.deepseek.com"}:
                host = hostname
        segments = parsed.path.split("/")
        if all(part in {"", "api", "openai", "compatible-mode", "chat", "completions"}
               or re.fullmatch(r"v[0-9]{1,2}", part) for part in segments):
            path = parsed.path
        else:
            path = "/redacted/chat/completions"
    except (ValueError, TypeError):
        pass
    return tuple(_safe_diagnostic_token(value, api_key) for value in (host, path))


def _safe_diagnostic_token(value, api_key):
    if api_key and api_key.casefold() in value.casefold():
        return "redacted"
    return value if re.fullmatch(r"[A-Za-z0-9_./:\-]{1,180}", value) else "redacted"


def _log_failure(url, api_key, *, stage, category, exc=None, response=None,
                 timeout=False, provider="tju"):
    """Fixed metadata only: no str(exc), traceback, headers, body or payload."""
    if response is None and exc is not None:
        response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    status = status if type(status) is int and 100 <= status <= 599 else None
    chain = _exception_chain(exc)
    exception_type = _safe_diagnostic_token(type(exc).__name__, api_key) if exc else "none"
    cause_type = _safe_diagnostic_token(type(chain[-1]).__name__, api_key) if chain else "none"
    host, path = _safe_endpoint(url, api_key)
    logger = logging.getLogger("campusflow." + provider + "_client")
    logger.error(
        "[CampusFlow][%s] stage=%s category=%s exception_type=%s cause_type=%s "
        "status=%s timeout=%s response_received=%s endpoint_host=%s endpoint_path=%s provider=%s",
        provider.upper(), stage, category, exception_type, cause_type, status, timeout,
        response is not None, host, path, provider,
    )


def _log_transport_failure(url, api_key, exc, provider="tju"):
    category, timeout = _failure_kind(exc)
    _log_failure(url, api_key, stage="request", category=category, exc=exc,
                 timeout=timeout, provider=provider)

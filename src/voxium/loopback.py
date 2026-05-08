"""Loopback URL / host validation and URL helpers (pure, testable)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def normalize_loopback_host(host: str) -> str:
    return "127.0.0.1" if host.lower() == "localhost" else host


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: object) -> bool:
    try:
        if not isinstance(url, str):
            return False
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme != "http" or not host:
            return False
        return is_loopback_host(host)
    except Exception:
        return False


def normalize_loopback_url(url: str | None) -> str:
    """
    Rewrite ``http://localhost…`` to ``http://127.0.0.1…`` so client connections
    skip the IPv6→IPv4 fallback stall on dual-stack hosts (Windows + WSL2 in
    particular). Non-loopback or already-IPv4 URLs are returned unchanged.

    Voxium is IPv4-loopback by design — every server in the stack binds to
    ``127.0.0.1`` via :func:`get_server_listen_args`, so a hostname URL adds a
    ~1-2s ``getaddrinfo`` stall per request without buying anything.
    """
    if not isinstance(url, str) or not url:
        return url or ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    host = parsed.hostname
    if not host or host.lower() != "localhost":
        return url
    port = f":{parsed.port}" if parsed.port else ""
    rebuilt_netloc = f"127.0.0.1{port}"
    # `parsed._replace` keeps path/query/fragment intact.
    return parsed._replace(netloc=rebuilt_netloc).geturl()


def get_server_endpoint_url(server_url: str, endpoint: str) -> str:
    parsed = urlparse(server_url)
    return f"{parsed.scheme}://{parsed.netloc}/{endpoint.lstrip('/')}"


def get_health_url(server_url: str) -> str:
    return get_server_endpoint_url(server_url, "health")


def get_gpu_url(server_url: str) -> str:
    return get_server_endpoint_url(server_url, "gpu")


def get_server_listen_args(server_url: str) -> tuple[str, str]:
    parsed = urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    return normalize_loopback_host(host), str(parsed.port or 8002)

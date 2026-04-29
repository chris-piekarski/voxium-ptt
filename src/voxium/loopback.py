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

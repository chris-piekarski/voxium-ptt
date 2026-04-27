"""Tests for voxium.loopback (pure URL / host helpers)."""

from voxium.loopback import (
    get_health_url,
    get_server_endpoint_url,
    get_server_listen_args,
    is_loopback_host,
    is_loopback_url,
    normalize_loopback_host,
)


def test_normalize_loopback_host_localhost():
    assert normalize_loopback_host("LocalHost") == "127.0.0.1"
    assert normalize_loopback_host("10.0.0.1") == "10.0.0.1"


def test_is_loopback_host():
    assert is_loopback_host(None) is False
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("1.1.1.1") is False
    assert is_loopback_host("not-an-ip") is False


def test_is_loopback_url():
    assert is_loopback_url("http://127.0.0.1:8002/") is True
    assert is_loopback_url("http://localhost/health") is True
    assert is_loopback_url("https://127.0.0.1/") is False
    assert is_loopback_url("not a url") is False
    # urlparse can raise; outer except returns False
    assert is_loopback_url(1) is False  # type: ignore[arg-type]


def test_get_server_endpoint_url():
    assert get_server_endpoint_url("http://h:1/", "x") == "http://h:1/x"
    assert get_server_endpoint_url("http://h:1/", "/y/z") == "http://h:1/y/z"


def test_get_health_url():
    assert get_health_url("http://a:3/base") == "http://a:3/health"


def test_get_server_listen_args():
    assert get_server_listen_args("http://LocalHost:9000/") == ("127.0.0.1", "9000")
    assert get_server_listen_args("http://:45") == ("127.0.0.1", "45")


def test_get_server_listen_args_default_port():
    h, p = get_server_listen_args("http://127.0.0.1")
    assert h == "127.0.0.1"
    assert p == "8002"

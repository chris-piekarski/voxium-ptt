"""Tests for voxium.metrics_text formatting helpers."""

from voxium.metrics_text import (
    describe_server,
    format_bytes,
    format_number,
    format_number_plain,
    format_optional_seconds,
    format_seconds,
)


def test_format_seconds_none():
    s = format_seconds(None)
    assert "n/a" in s


def test_format_seconds_invalid_uses_dim():
    assert "n/a" in format_seconds("x")


def test_format_seconds_ms_and_s():
    assert "ms" in format_seconds(0.5)
    s1 = format_seconds(1.0)
    assert "1.00" in s1
    assert "s" in s1


def test_format_number_none_and_int():
    assert "n/a" in format_number(None)
    assert format_number(3.0) == "3"
    assert "weird" in format_number("weird")
    assert "1.1" in format_number(1.1)


def test_format_number_plain_non_numeric():
    assert format_number_plain(None) == "n/a"
    assert "weird" in format_number_plain("weird")
    assert "3" in format_number_plain(3.0)
    assert "1.20" in format_number_plain(1.2) or "1.2" in format_number_plain(1.2)


def test_format_bytes_scales():
    b = format_bytes(1024.0 * 2)
    assert "KB" in b
    g = format_bytes(1024.0**4)
    assert "GB" in g
    assert "n/a" in format_bytes(None)
    assert format_bytes("nope") == "nope"


def test_format_optional_seconds_scalar():
    out = format_optional_seconds(0.1)
    assert "ms" in out


def test_format_optional_seconds_list():
    t = format_optional_seconds([0.5, 1.0])
    assert "ms" in t and "/" in t


def test_describe_server():
    assert "model=foo" in describe_server(
        {"model": "foo", "device": "cuda", "compute": "f16"}
    )


def test_describe_server_minimal():
    assert describe_server({}) == "model=unknown"

"""Tests for voxium.http_detail."""

from types import SimpleNamespace

from voxium.http_detail import http_error_detail_text


class _Resp:
    def __init__(self, data=None, text: str = "", fail_json: bool = False):
        self._data = data
        self._fail_json = fail_json
        self.text = text

    def json(self):
        if self._fail_json:
            raise ValueError("bad")
        return self._data or {}


def test_prefers_json_detail_string():
    e = SimpleNamespace(
        response=_Resp(data={"detail": "  oops  "}, text="other"),
    )
    assert http_error_detail_text(e) == "oops"


def test_empty_json_detail_uses_text_or_truncated_body():
    e = SimpleNamespace(response=_Resp(data={"detail": "  "}, text="  plain  "))
    assert http_error_detail_text(e) == "plain"


def test_falls_back_to_text_when_no_detail():
    e = SimpleNamespace(response=_Resp(data={}, text="  body  "))
    assert http_error_detail_text(e) == "body"


def test_json_error_falls_back_to_text():
    e = SimpleNamespace(
        response=_Resp(data=None, text="x" * 600, fail_json=True),
    )
    out = http_error_detail_text(e)
    assert out.startswith("x")
    assert len(out) == 500


def test_no_response_uses_str_exc():
    e = ValueError("plain")
    assert http_error_detail_text(e) == "plain"

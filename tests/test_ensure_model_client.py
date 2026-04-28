"""Client preflight to /ensure-model (mocked HTTP)."""

from __future__ import annotations

from rich.console import Console

import voxium.ensure_model_client as emc


def test_ensure_model_http_200_ready(monkeypatch) -> None:
    class Resp:
        status_code = 200

        def json(self) -> dict:
            return {"status": "ready", "message": "Model is on the stack, copy."}

    def fake_post(*_a, **_k):
        return Resp()

    monkeypatch.setattr(emc.requests, "post", fake_post)
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert (
        emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "base") is True
    )


def test_ensure_model_http_202_then_ready(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"status": "pending", "job_id": "abc123", "model": "tiny"}

    class GetResp:
        status_code = 200

        def json(self) -> dict:
            return {
                "status": "ready",
                "model": "tiny",
                "lines": [],
                "progress_line": "Model on disk and loaded — ready for PTT, copy.",
                "error": None,
                "done": True,
            }

    class _DummyLive:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def update(self, *_a, **_k) -> None:
            return None

    monkeypatch.setattr(emc, "Live", lambda *a, **k: _DummyLive())
    monkeypatch.setattr(emc, "_ENSURE_POLL_INTERVAL", 0.01)
    calls: list[str] = []

    def fake_post(*_a, **_k) -> PostResp:
        return PostResp()

    def fake_get(url: str, *_a, **_k) -> GetResp:
        calls.append(url)
        return GetResp()

    monkeypatch.setattr(emc.requests, "post", fake_post)
    monkeypatch.setattr(emc.requests, "get", fake_get)
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert (
        emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "tiny") is True
    )
    assert calls and "abc123" in calls[0]


def test_ensure_model_skips_non_loopback(monkeypatch) -> None:
    called: list[str] = []

    def nope(*_a, **_k):
        called.append("nope")
        raise AssertionError("should not call")

    monkeypatch.setattr(emc.requests, "post", nope)
    c = Console(width=100)
    assert emc.ensure_model_on_loopback_server("http://example.com/", c, "base") is True
    assert not called


def test_ensure_model_post_oserror(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("no route")

    monkeypatch.setattr(emc.requests, "post", boom)
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert (
        emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "base") is False
    )


def test_ensure_model_http_200_default_message(monkeypatch) -> None:
    class Resp:
        status_code = 200

        def json(self) -> dict:
            return {}

    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: Resp())
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "z") is True


def test_ensure_model_non_202_error_json_detail(monkeypatch) -> None:
    class Resp:
        status_code = 400
        text = "bad"
        _j = {"detail": "nope detail"}

        def json(self) -> dict:
            return self._j

    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: Resp())
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert (
        emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "base") is False
    )


def test_ensure_model_202_missing_job_id(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"status": "pending"}

    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: PostResp())
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "x") is False


def test_ensure_model_model_fetch_panel_truncates_long_line() -> None:
    c = Console(width=100, record=True, force_terminal=True, color_system="truecolor")
    pl = "x" * 600
    p = emc._model_fetch_panel(c, "m", "j1", pl)
    assert p is not None


def test_freeze_callback_swallowed_on_error(monkeypatch) -> None:
    def bad_freeze() -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(emc, "print_agent_telemetry_panel", lambda *a, **k: None)

    def post_down(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(emc.requests, "post", post_down)
    c = Console(width=100)
    emc._freeze_ptt(bad_freeze)  # should not raise
    emc.ensure_model_on_loopback_server(
        "http://127.0.0.1:8002",
        c,
        "m",
        freeze_for_external_output=bad_freeze,
    )


def test_ensure_model_poll_get_oserror(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"job_id": "j1", "status": "pending"}

    class _DummyLive:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def update(self, *_a, **_k) -> None:
            return None

    monkeypatch.setattr(emc, "Live", lambda *a, **k: _DummyLive())
    monkeypatch.setattr(emc, "_ENSURE_POLL_INTERVAL", 0.0)
    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: PostResp())
    monkeypatch.setattr(
        emc.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")),
    )
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "m") is False


def test_ensure_model_poll_non_200(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"job_id": "j1"}

    class GetResp:
        status_code = 500

    monkeypatch.setattr(
        emc,
        "Live",
        lambda *a, **k: type(
            "L",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda *a: None,
                "update": lambda *a, **k: None,
            },
        )(),
    )
    monkeypatch.setattr(emc, "_ENSURE_POLL_INTERVAL", 0.0)
    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: PostResp())
    monkeypatch.setattr(emc.requests, "get", lambda *a, **k: GetResp())
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "m") is False


def test_ensure_model_poll_error_state(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"job_id": "j1"}

    class GetResp:
        status_code = 200

        def json(self) -> dict:
            return {"status": "error", "error": "load failed", "progress_line": "p"}

    monkeypatch.setattr(
        emc,
        "Live",
        lambda *a, **k: type(
            "L",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda *a: None,
                "update": lambda *a, **k: None,
            },
        )(),
    )
    monkeypatch.setattr(emc, "_ENSURE_POLL_INTERVAL", 0.0)
    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: PostResp())
    monkeypatch.setattr(emc.requests, "get", lambda *a, **k: GetResp())
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "m") is False


def test_ensure_model_ready_with_pline_updates_live(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"job_id": "j1"}

    class GetOnce:
        status_code = 200

        def json(self) -> dict:
            return {
                "status": "ready",
                "done": True,
                "error": None,
                "progress_line": "almost",
            }

    updates: list[object] = []

    class _L:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def update(self, p):
            updates.append(p)

    monkeypatch.setattr(emc, "Live", lambda *a, **k: _L())
    monkeypatch.setattr(emc, "_ENSURE_POLL_INTERVAL", 0.0)
    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: PostResp())
    monkeypatch.setattr(emc.requests, "get", lambda *a, **k: GetOnce())
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert (
        emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "tiny") is True
    )
    assert len(updates) >= 1


def test_ensure_model_poll_timeout(monkeypatch) -> None:
    class PostResp:
        status_code = 202

        def json(self) -> dict:
            return {"job_id": "j1"}

    class _DummyLive:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def update(self, *_a, **_k) -> None:
            return None

    monkeypatch.setattr(emc, "Live", lambda *a, **k: _DummyLive())
    monkeypatch.setattr(emc, "_ENSURE_POLL_TIMEOUT", 0.5)
    monkeypatch.setattr(emc, "_ENSURE_POLL_INTERVAL", 0.0)
    n_calls = [0]

    def fake_mono() -> float:
        n_calls[0] += 1
        if n_calls[0] == 1:
            return 0.0  # t0
        return 100.0  # first while check: 100 - 0 >= 0.5 → no poll

    monkeypatch.setattr(emc.time, "monotonic", fake_mono)
    monkeypatch.setattr(emc.requests, "post", lambda *a, **k: PostResp())
    got: list[str] = []

    def no_get(*_a, **_k):
        got.append("get")
        raise AssertionError("should not poll when window already expired")

    monkeypatch.setattr(emc.requests, "get", no_get)
    c = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    assert emc.ensure_model_on_loopback_server("http://127.0.0.1:8002", c, "m") is False
    assert not got

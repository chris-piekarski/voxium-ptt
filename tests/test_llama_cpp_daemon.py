"""Tests for managed llama.cpp startup used by the polish path."""

from __future__ import annotations

import types
from pathlib import Path
from unittest import mock

from voxium import llama_cpp_daemon as lcd


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.args = None
        self.kwargs = None

    def poll(self):
        return self.returncode


def test_ensure_llama_cpp_daemon_reuses_existing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: (True, None))
    monkeypatch.setattr(lcd, "llama_cpp_loaded_model", lambda *a, **k: "plain.gguf")

    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=tmp_path / "plain.gguf",
        model_alias="plain.gguf",
        log_path=tmp_path / "llama_cpp.log",
    )

    assert managed is None
    assert any("already on station" in msg for msg, _level in entries)


def test_ensure_llama_cpp_daemon_starts_configured_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    calls = iter([(False, "offline"), (True, None)])
    fake_proc = _FakeProcess()
    seen: dict[str, object] = {}
    model_path = tmp_path / "models" / "plain.gguf"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"gguf")

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        fake_proc.args = args
        fake_proc.kwargs = kwargs
        return fake_proc

    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: next(calls))
    monkeypatch.setattr(lcd, "llama_cpp_loaded_model", lambda *a, **k: "plain.gguf")
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/usr/bin/llama-server"
    )

    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=model_path,
        model_alias="plain.gguf",
        log_path=tmp_path / "logs" / "llama_cpp.log",
        parallel=3,
        ctx_size=4096,
        gpu_layers=999,
        sleep_idle_seconds=600,
        popen=fake_popen,
        sleep=lambda _seconds: None,
    )

    assert managed is not None
    assert managed.started_by_voxium is True
    assert seen["args"] == [
        "/usr/bin/llama-server",
        "-m",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        "11435",
        "--alias",
        "plain.gguf",
        "--parallel",
        "3",
        "--jinja",
        "--warmup",
        "--ctx-size",
        "4096",
        "--n-gpu-layers",
        "999",
        "--sleep-idle-seconds",
        "600",
    ]
    assert any("ready for local polish" in msg for msg, _level in entries)


def test_ensure_llama_cpp_daemon_warns_when_cli_missing(
    monkeypatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "plain.gguf"
    model_path.write_bytes(b"gguf")
    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: (False, "offline"))
    monkeypatch.setattr(lcd, "llama_server_cli_path", lambda *_a, **_k: None)

    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=model_path,
        model_alias="plain.gguf",
        log_path=tmp_path / "llama_cpp.log",
    )

    assert managed is None
    assert any(
        "could not find `llama-server`" in msg.lower() for msg, _level in entries
    )


def test_default_llama_server_path_uses_os_name() -> None:
    p = lcd.default_llama_server_path()
    assert p.name in ("llama-server", "llama-server.exe")
    assert "llama.cpp" in p.parts or "voxium" in p.parts[0:3]


def test_ensure_warns_on_model_missing(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "nope.gguf"
    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: (False, "x"))
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/bin/llama-server"
    )
    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=missing,
        model_alias="m",
        log_path=tmp_path / "l.log",
    )
    assert managed is None
    assert any("missing" in msg.lower() for msg, _l in entries)


def test_ensure_reuses_with_model_mismatch_warning(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.gguf"
    p.write_bytes(b"x")
    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: (True, None))
    monkeypatch.setattr(lcd, "llama_cpp_loaded_model", lambda *a, **k: "other-model")
    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=p,
        model_alias="a.gguf",
        log_path=tmp_path / "l.log",
    )
    assert managed is None
    assert any("other-model" in msg and "a.gguf" in msg for msg, _ in entries)


def test_ensure_popen_oserror_closes_log(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.gguf"
    p.write_bytes(b"x")
    log_path = tmp_path / "logs" / "ll.log"
    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: (False, "x"))
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/bin/llama-server"
    )

    def bad_popen(*a, **k):
        raise OSError("no")

    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=p,
        model_alias="a.gguf",
        log_path=log_path,
        popen=bad_popen,
    )
    assert managed is None
    assert any("Could not start" in msg for msg, _ in entries)


def test_ensure_exits_during_startup(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.gguf"
    p.write_bytes(b"x")
    model_path = p
    log_path = tmp_path / "ll.log"
    proc = _FakeProcess()
    proc.returncode = 1

    def fake_popen(*a, **k):
        return proc

    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: (False, "off"))
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/bin/llama-server"
    )

    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=model_path,
        model_alias="a.gguf",
        log_path=log_path,
        popen=fake_popen,
        sleep=lambda _s: None,
    )
    assert managed is not None
    assert any("exited during startup" in msg for msg, _ in entries)


def test_ensure_timeout_keeps_process(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.gguf"
    p.write_bytes(b"x")
    log_path = tmp_path / "ll2.log"

    def never_ok(*a, **k):
        return (False, "loading")

    proc = _FakeProcess()

    def fake_popen(*a, **k):
        return proc

    t_iter = iter([0.0, 0.0, 1.0])  # deadline; first while; exit while

    def fake_time() -> float:
        return next(t_iter, 2.0)

    fake_mod = types.SimpleNamespace(time=fake_time, sleep=lambda s: None)
    monkeypatch.setattr(lcd, "time", fake_mod)

    monkeypatch.setattr(lcd, "llama_cpp_reachable", never_ok)
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/bin/llama-server"
    )

    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=p,
        model_alias="a.gguf",
        log_path=log_path,
        popen=fake_popen,
        sleep=lambda _s: None,
        startup_timeout=1.0,
    )
    assert managed is not None
    assert any("did not answer" in msg for msg, _ in entries)


def test_ensure_came_up_wrong_model(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.gguf"
    p.write_bytes(b"x")
    log_path = tmp_path / "ll3.log"
    it = iter([(False, "x"), (True, None)])

    def reach(*a, **k):
        return next(it)

    def loaded(*a, **k):
        return "wrong"

    def fake_popen(*a, **k):
        return _FakeProcess()

    monkeypatch.setattr(lcd, "llama_cpp_reachable", reach)
    monkeypatch.setattr(lcd, "llama_cpp_loaded_model", loaded)
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/bin/llama-server"
    )
    managed, entries = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=p,
        model_alias="a.gguf",
        log_path=log_path,
        popen=fake_popen,
        sleep=lambda _s: None,
    )
    assert managed is not None
    assert any("came up serving model" in msg and "a.gguf" in msg for msg, _ in entries)


def test_shlex_join_fallback(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "a.gguf"
    p.write_bytes(b"x")
    log_path = tmp_path / "ll4.log"
    it = iter([(False, "x"), (True, None)])

    monkeypatch.setattr(lcd, "llama_cpp_reachable", lambda *a, **k: next(it))
    monkeypatch.setattr(lcd, "llama_cpp_loaded_model", lambda *a, **k: "a.gguf")
    monkeypatch.setattr(
        lcd, "llama_server_cli_path", lambda *_a, **_k: "/bin/llama-server"
    )

    def join_raises(seq):
        raise TypeError("bad join")

    monkeypatch.setattr(lcd.shlex, "join", join_raises)

    proc = _FakeProcess()

    def fake_popen(*a, **k):
        return proc

    managed, _ = lcd.ensure_llama_cpp_daemon(
        base_url="http://127.0.0.1:11435",
        cmd_path=None,
        model_path=p,
        model_alias="a.gguf",
        log_path=log_path,
        popen=fake_popen,
        sleep=lambda _s: None,
    )
    assert managed is not None
    data = log_path.read_text(encoding="utf-8")
    assert "argv" in data


def test_stop_managed_closes_log_process_already_exited(tmp_path: Path) -> None:
    proc = mock.Mock()
    proc.poll.return_value = 0
    with open(tmp_path / "l2.log", "a", encoding="utf-8") as h:
        m = lcd.ManagedLlamaCpp(proc, h, started_by_voxium=True)
        lcd.stop_managed_llama_cpp(m, timeout=0.1)
    assert h.closed


def test_stop_managed_terminates_running_process(tmp_path: Path, monkeypatch) -> None:
    """Cover SIGTERM / wait / close log when ``poll()`` is still ``None`` (Linux / non-Windows)."""
    if lcd.IS_WINDOWS:
        return
    proc = mock.Mock()
    proc.poll.return_value = None
    proc.wait = mock.Mock()
    proc.pid = 42
    monkeypatch.setattr(lcd.os, "killpg", lambda *_a, **_k: None)
    with open(tmp_path / "l3.log", "a", encoding="utf-8") as h:
        m = lcd.ManagedLlamaCpp(proc, h, started_by_voxium=True)
        lcd.stop_managed_llama_cpp(m, timeout=0.1)
    assert h.closed
    proc.wait.assert_called_once()


def test_stop_managed_timeout_expired_triggers_kill_non_windows(
    tmp_path: Path, monkeypatch
) -> None:
    if lcd.IS_WINDOWS:
        return
    import subprocess as sp

    proc = mock.Mock()
    proc.poll.return_value = None
    proc.wait = mock.Mock(side_effect=sp.TimeoutExpired("cmd", 0.01))
    proc.kill = mock.Mock()
    proc.pid = 999
    monkeypatch.setattr(lcd.os, "killpg", mock.Mock(side_effect=OSError("x")))
    with open(tmp_path / "k.log", "a", encoding="utf-8") as h:
        m = lcd.ManagedLlamaCpp(proc, h, started_by_voxium=True)
        lcd.stop_managed_llama_cpp(m, timeout=0.01)
    assert h.closed
    proc.kill.assert_called_once()


def test_llama_server_cli_path_prefers_existing_file(tmp_path: Path) -> None:
    exe = tmp_path / "llama-server"
    exe.write_text("x", encoding="utf-8")
    out = lcd.llama_server_cli_path(str(exe))
    assert out == str(exe.resolve())

"""Tests for voxium.local_server_cmd."""

from voxium.local_server_cmd import LocalServerLaunchConfig, argv_after_interpreter


def test_argv_includes_flags_and_extras():
    cfg = LocalServerLaunchConfig(
        server_url="http://LocalHost:9999/",
        server_timeout=5,
        metrics_sample_interval=1.0,
        model="tiny",
        server_device=None,
        server_compute=None,
        server_vad=False,
        server_gpu_metrics=False,
    )
    out = argv_after_interpreter(
        cfg,
        log_level="INFO",
        default_device="auto",
        default_compute="f16",
    )
    assert out[:4] == ["-m", "voxium", "server", "--host"]
    assert "127.0.0.1" in out
    assert "9999" in out
    assert "--no-vad" in out
    assert "--no-gpu-metrics" in out
    assert "--model" in out
    assert "tiny" in out
    assert "--llama-cpp-url" in out
    assert "http://127.0.0.1:11435" in out
    assert "--polish-timeout" in out
    assert "--polish-max-concurrent" in out


def test_argv_omits_flags_when_vad_and_gpu_on():
    cfg = LocalServerLaunchConfig(
        server_url="http://127.0.0.1:1/",
        server_timeout=1,
        metrics_sample_interval=0.5,
        model=None,
        server_device="cuda",
        server_compute="int8",
        server_vad=True,
        server_gpu_metrics=True,
    )
    out = argv_after_interpreter(
        cfg, log_level="ERROR", default_device="cpu", default_compute="f16"
    )
    assert "--no-vad" not in out
    assert "--no-gpu-metrics" not in out
    assert "cuda" in out
    assert "int8" in out


def test_argv_polish_disable_and_warmup_and_default_model() -> None:
    cfg = LocalServerLaunchConfig(
        server_url="http://127.0.0.1:1/",
        server_timeout=1,
        metrics_sample_interval=0.5,
        model=None,
        server_device="cuda",
        server_compute="int8",
        server_vad=True,
        server_gpu_metrics=True,
        polish_default_model="q4.gguf",
        polish_enabled_by_default=False,
        polish_warmup_on_start=True,
    )
    out = argv_after_interpreter(
        cfg, log_level="ERROR", default_device="cpu", default_compute="f16"
    )
    assert "--polish-default-model" in out
    assert "q4.gguf" in out
    assert "--no-polish-enabled-by-default" in out
    assert "--polish-warmup-on-start" in out
    assert "--no-polish-warmup-on-start" not in out

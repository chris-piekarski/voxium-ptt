"""Build the argv fragment for `python -m voxium server ...` (pure, testable)."""

from __future__ import annotations

from dataclasses import dataclass

from voxium.loopback import get_server_listen_args


@dataclass
class LocalServerLaunchConfig:
    server_url: str
    server_timeout: int
    metrics_sample_interval: float
    model: str | None
    server_device: str | None
    server_compute: str | None
    server_vad: bool
    server_gpu_metrics: bool
    llama_cpp_url: str = "http://127.0.0.1:11435"
    polish_default_model: str | None = None
    polish_timeout: float = 25.0
    polish_enabled_by_default: bool = True
    polish_keep_alive: str = "10m"
    polish_warmup_on_start: bool = False
    polish_max_concurrent: int = 2


def argv_after_interpreter(
    cfg: LocalServerLaunchConfig,
    *,
    log_level: str,
    default_device: str,
    default_compute: str,
) -> list[str]:
    """Return argv tokens that follow ``sys.executable`` (i.e. ``-m voxium server`` …)."""
    host, port = get_server_listen_args(cfg.server_url)
    cmd: list[str] = [
        "-m",
        "voxium",
        "server",
        "--host",
        host,
        "--port",
        port,
        "--timeout",
        str(cfg.server_timeout),
        "--metrics-sample-interval",
        str(cfg.metrics_sample_interval),
        "--log-level",
        log_level,
    ]
    if cfg.model:
        cmd.extend(["--model", cfg.model])
    dev = cfg.server_device or default_device
    cmd.extend(["--device", dev])
    comp = cfg.server_compute or default_compute
    cmd.extend(["--compute", comp])
    if not cfg.server_vad:
        cmd.append("--no-vad")
    if not cfg.server_gpu_metrics:
        cmd.append("--no-gpu-metrics")
    cmd.extend(["--llama-cpp-url", cfg.llama_cpp_url])
    if cfg.polish_default_model:
        cmd.extend(["--polish-default-model", cfg.polish_default_model])
    cmd.extend(["--polish-timeout", str(cfg.polish_timeout)])
    if cfg.polish_enabled_by_default:
        cmd.append("--polish-enabled-by-default")
    else:
        cmd.append("--no-polish-enabled-by-default")
    cmd.extend(["--polish-keep-alive", cfg.polish_keep_alive])
    if cfg.polish_warmup_on_start:
        cmd.append("--polish-warmup-on-start")
    else:
        cmd.append("--no-polish-warmup-on-start")
    cmd.extend(["--polish-max-concurrent", str(cfg.polish_max_concurrent)])
    return cmd

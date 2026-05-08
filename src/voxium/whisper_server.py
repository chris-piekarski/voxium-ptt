#!/usr/bin/env python3

import argparse
import json
import logging
import os
import signal
import shutil
import sys
import io
import tempfile
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Dict, Tuple
import uuid

if __name__ == "__main__":
    print(
        "This module is internal — use: voxium server --help (PTT path is voxium run).",
        file=sys.stderr,
    )
    sys.exit(2)


_DLL_DIR_HANDLES = []


def _prepend_env_paths(var_name: str, new_paths: list[str]) -> None:
    existing = [p for p in os.environ.get(var_name, "").split(os.pathsep) if p]
    merged: list[str] = []
    seen: set[str] = set()
    for path in new_paths + existing:
        if not path:
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        merged.append(path)
    if merged:
        os.environ[var_name] = os.pathsep.join(merged)


def _setup_cuda_paths():
    try:
        import nvidia
    except ImportError:
        return

    candidates: list[str] = []
    for base_str in getattr(nvidia, "__path__", []):
        base = Path(base_str)
        for package_name in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            for subdir in ("bin", "lib"):
                candidate = base / package_name / subdir
                if candidate.is_dir():
                    candidates.append(str(candidate))

    if not candidates:
        return

    if sys.platform == "win32":
        _prepend_env_paths("PATH", candidates)
        if hasattr(os, "add_dll_directory"):
            for path in candidates:
                try:
                    _DLL_DIR_HANDLES.append(os.add_dll_directory(path))
                except OSError:
                    pass
    else:
        _prepend_env_paths("LD_LIBRARY_PATH", candidates)


_setup_cuda_paths()

import ctranslate2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse
import uvicorn
from huggingface_hub import snapshot_download
from pydantic import BaseModel
from tqdm import tqdm

from voxium.constants import env_polish_enabled_default
from voxium.model_registry import (
    DEFAULT_MODEL_NAME,
    TRUSTED_MODEL_HELP,
    resolve_model_repo,
    validate_model_name,
)
from voxium.llama_cpp_client import (
    llama_cpp_chat,
    llama_cpp_loaded_model,
    llama_cpp_reachable,
)
from voxium.loopback import is_loopback_host, is_loopback_url, normalize_loopback_host
from voxium import polish_profile
from voxium.polish_models import DEFAULT_POLISH_MODEL, validate_polish_model_tag
from voxium.polish_provision import ensure_polish_model_downloaded
from voxium.model_arg import trusted_model_arg
from voxium.paths import ensure_runtime_dirs, models_dir

DEFAULT_MODEL = os.getenv("WHISPER_MODEL", DEFAULT_MODEL_NAME)
DEFAULT_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
DEFAULT_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")
EXPECTED_FASTER_WHISPER_HOME = "https://github.com/SYSTRAN/faster-whisper"


def setup_logging(level: str = "INFO") -> logging.Logger:

    logger = logging.getLogger("voxium_whisper_server")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    logger.handlers.clear()

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


@dataclass
class ServerStats:

    request_count: int = 0
    total_transcription_time: float = 0.0
    total_request_time: float = 0.0
    error_count: int = 0
    audio_seconds_processed: float = 0.0
    input_bytes_processed: int = 0
    output_chars_generated: int = 0
    segments_generated: int = 0
    model_sampled_request_count: int = 0
    decoder_tokens_generated: int = 0
    output_words_generated: int = 0
    input_audio_frames_estimate: int = 0
    total_duration_after_vad_seconds: float = 0.0
    total_vad_removed_seconds: float = 0.0
    quality_sample_count: int = 0
    logprob_sample_count: int = 0
    total_avg_logprob: float = 0.0
    min_avg_logprob: float | None = None
    no_speech_sample_count: int = 0
    total_avg_no_speech_prob: float = 0.0
    peak_no_speech_prob: float | None = None
    compression_sample_count: int = 0
    total_avg_compression_ratio: float = 0.0
    peak_compression_ratio: float | None = None
    gpu_sampled_request_count: int = 0
    gpu_energy_sampled_request_count: int = 0
    total_gpu_energy_wh: float = 0.0
    peak_vram_used_mb: float | None = None
    peak_gpu_utilization_percent: float | None = None
    peak_power_watts: float | None = None
    peak_temperature_c: float | None = None
    capture_sampled_request_count: int = 0
    total_capture_seconds: float = 0.0
    total_captured_frames: int = 0
    capture_devices: Dict[str, int] = field(default_factory=dict)
    capture_host_apis: Dict[str, int] = field(default_factory=dict)
    last_audio_capture: dict | None = None
    startup_time: float = field(default_factory=time.time)

    def record_request(self, metrics: dict) -> None:

        self.request_count += 1
        self.total_transcription_time += metrics.get("transcription_seconds") or 0.0
        self.total_request_time += metrics.get("total_request_seconds") or 0.0
        self.audio_seconds_processed += metrics.get("audio_seconds") or 0.0
        self.input_bytes_processed += metrics.get("input_bytes") or 0
        self.output_chars_generated += metrics.get("output_chars") or 0
        self.segments_generated += metrics.get("segments") or 0

        model = metrics.get("model")
        if model:
            self.model_sampled_request_count += 1
            self.decoder_tokens_generated += model.get("decoder_tokens") or 0
            self.output_words_generated += model.get("output_words") or 0
            self.input_audio_frames_estimate += (
                model.get("input_audio_frames_estimate") or 0
            )
            self.total_duration_after_vad_seconds += (
                model.get("duration_after_vad_seconds") or 0.0
            )
            self.total_vad_removed_seconds += model.get("vad_removed_seconds") or 0.0

            avg_logprob = model.get("avg_logprob")
            avg_no_speech = model.get("avg_no_speech_prob")
            avg_compression = model.get("avg_compression_ratio")
            if (
                avg_logprob is not None
                or avg_no_speech is not None
                or avg_compression is not None
            ):
                self.quality_sample_count += 1
            if avg_logprob is not None:
                self.logprob_sample_count += 1
                self.total_avg_logprob += avg_logprob
                min_logprob = model.get("min_avg_logprob")
                if min_logprob is not None:
                    if (
                        self.min_avg_logprob is None
                        or min_logprob < self.min_avg_logprob
                    ):
                        self.min_avg_logprob = min_logprob
            if avg_no_speech is not None:
                self.no_speech_sample_count += 1
                self.total_avg_no_speech_prob += avg_no_speech
                self._update_peak(
                    "peak_no_speech_prob", model.get("max_no_speech_prob")
                )
            if avg_compression is not None:
                self.compression_sample_count += 1
                self.total_avg_compression_ratio += avg_compression
                self._update_peak(
                    "peak_compression_ratio", model.get("max_compression_ratio")
                )

        gpu = metrics.get("gpu")
        if gpu:
            self.gpu_sampled_request_count += 1
            energy_wh = gpu.get("energy_wh_estimate")
            if energy_wh is not None:
                self.gpu_energy_sampled_request_count += 1
                self.total_gpu_energy_wh += energy_wh
            self._update_peak("peak_vram_used_mb", gpu.get("vram_used_peak_mb"))
            self._update_peak(
                "peak_gpu_utilization_percent", gpu.get("utilization_peak_percent")
            )
            self._update_peak("peak_power_watts", gpu.get("power_peak_watts"))
            self._update_peak("peak_temperature_c", gpu.get("temperature_peak_c"))

        capture = metrics.get("capture")
        if capture:
            self.capture_sampled_request_count += 1
            self.last_audio_capture = capture

            device = capture.get("device") or {}
            host_api = capture.get("host_api") or {}
            recording = capture.get("recording") or {}
            self._increment_count(self.capture_devices, device.get("name"))
            self._increment_count(self.capture_host_apis, host_api.get("name"))

            capture_seconds = _as_optional_float(recording.get("capture_seconds"))
            captured_frames = recording.get("captured_frames")
            if capture_seconds is not None:
                self.total_capture_seconds += capture_seconds
            if captured_frames is not None:
                try:
                    self.total_captured_frames += int(captured_frames)
                except (TypeError, ValueError):
                    pass

    def _update_peak(self, attr: str, value: float | None) -> None:

        if value is None:
            return
        current = getattr(self, attr)
        if current is None or value > current:
            setattr(self, attr, value)

    def _increment_count(self, bucket: Dict[str, int], key: str | None) -> None:

        if not key:
            return
        bucket[key] = bucket.get(key, 0) + 1

    def record_error(self) -> None:

        self.error_count += 1

    @property
    def avg_transcription_time(self) -> float:

        if self.request_count == 0:
            return 0.0
        return self.total_transcription_time / self.request_count

    @property
    def avg_request_time(self) -> float:

        if self.request_count == 0:
            return 0.0
        return self.total_request_time / self.request_count

    @property
    def avg_realtime_factor(self) -> float:

        if self.audio_seconds_processed == 0:
            return 0.0
        return self.total_transcription_time / self.audio_seconds_processed

    @property
    def avg_tokens_per_audio_second(self) -> float:

        if self.audio_seconds_processed == 0:
            return 0.0
        return self.decoder_tokens_generated / self.audio_seconds_processed

    @property
    def avg_tokens_per_inference_second(self) -> float:

        if self.total_transcription_time == 0:
            return 0.0
        return self.decoder_tokens_generated / self.total_transcription_time

    def _avg_quality(self, total: float, count: int) -> float | None:

        if count == 0:
            return None
        return total / count

    @property
    def uptime_seconds(self) -> float:

        return time.time() - self.startup_time

    def to_dict(self) -> dict:

        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "avg_transcription_time_ms": round(self.avg_transcription_time * 1000, 1),
            "avg_request_time_ms": round(self.avg_request_time * 1000, 1),
            "avg_realtime_factor": round(self.avg_realtime_factor, 4),
            "total_audio_processed_seconds": round(self.audio_seconds_processed, 1),
            "total_transcription_seconds": round(self.total_transcription_time, 3),
            "total_request_seconds": round(self.total_request_time, 3),
            "input_bytes_processed": self.input_bytes_processed,
            "output_chars_generated": self.output_chars_generated,
            "segments_generated": self.segments_generated,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "model_metrics": {
                "sampled_request_count": self.model_sampled_request_count,
                "decoder_tokens_generated": self.decoder_tokens_generated,
                "output_words_generated": self.output_words_generated,
                "input_audio_frames_estimate": self.input_audio_frames_estimate,
                "total_duration_after_vad_seconds": round(
                    self.total_duration_after_vad_seconds, 1
                ),
                "total_vad_removed_seconds": round(self.total_vad_removed_seconds, 1),
                "avg_tokens_per_audio_second": round(
                    self.avg_tokens_per_audio_second, 3
                ),
                "avg_tokens_per_inference_second": round(
                    self.avg_tokens_per_inference_second, 3
                ),
                "quality_sample_count": self.quality_sample_count,
                "avg_logprob": _round_optional(
                    self._avg_quality(
                        self.total_avg_logprob, self.logprob_sample_count
                    ),
                    4,
                ),
                "min_avg_logprob": self.min_avg_logprob,
                "avg_no_speech_prob": _round_optional(
                    self._avg_quality(
                        self.total_avg_no_speech_prob, self.no_speech_sample_count
                    ),
                    4,
                ),
                "peak_no_speech_prob": self.peak_no_speech_prob,
                "avg_compression_ratio": _round_optional(
                    self._avg_quality(
                        self.total_avg_compression_ratio, self.compression_sample_count
                    ),
                    4,
                ),
                "peak_compression_ratio": self.peak_compression_ratio,
            },
            "gpu": {
                "sampled_request_count": self.gpu_sampled_request_count,
                "energy_sampled_request_count": self.gpu_energy_sampled_request_count,
                "energy_wh_estimate_total": (
                    round(self.total_gpu_energy_wh, 6)
                    if self.gpu_energy_sampled_request_count
                    else None
                ),
                "peak_vram_used_mb": self.peak_vram_used_mb,
                "peak_gpu_utilization_percent": self.peak_gpu_utilization_percent,
                "peak_power_watts": self.peak_power_watts,
                "peak_temperature_c": self.peak_temperature_c,
            },
            "capture": {
                "sampled_request_count": self.capture_sampled_request_count,
                "total_capture_seconds": round(self.total_capture_seconds, 3),
                "total_captured_frames": self.total_captured_frames,
                "devices": self.capture_devices,
                "host_apis": self.capture_host_apis,
                "last": self.last_audio_capture,
            },
        }


@dataclass
class ServerConfig:

    model: str
    device: str
    compute: str
    timeout: int
    vad_enabled: bool
    host: str
    port: int
    gpu_metrics_enabled: bool
    metrics_sample_interval: float
    llama_cpp_url: str = "http://127.0.0.1:11435"
    polish_default_model: str = DEFAULT_POLISH_MODEL
    polish_timeout_seconds: float = 25.0
    polish_backend_default: str = "llama.cpp"
    polish_enabled_default: bool = True
    polish_keep_alive_default: str = "-1"
    polish_warmup_on_start: bool = True
    polish_max_concurrent: int = 2


def _as_float(value) -> float | None:

    if value is None:
        return None
    try:
        text = str(value).strip()
        if text.lower() in {"", "n/a", "[not supported]", "not supported"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _round_optional(value: float | None, digits: int = 3) -> float | None:

    if value is None:
        return None
    return round(value, digits)


def _avg(values: list[float | None]) -> float | None:

    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _weighted_avg(values: list[float | None], weights: list[int]) -> float | None:

    total = 0.0
    weight_total = 0
    for value, weight in zip(values, weights):
        if value is None:
            continue
        actual_weight = max(weight, 1)
        total += value * actual_weight
        weight_total += actual_weight
    if weight_total == 0:
        return None
    return total / weight_total


def _as_optional_float(value) -> float | None:

    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_model_metrics(
    model_name: str,
    info,
    segments: list,
    text: str,
    transcription_seconds: float,
) -> dict:

    audio_duration = _as_optional_float(getattr(info, "duration", None))
    duration_after_vad = _as_optional_float(getattr(info, "duration_after_vad", None))
    vad_removed = None
    vad_removed_percent = None
    if audio_duration is not None and duration_after_vad is not None:
        vad_removed = max(audio_duration - duration_after_vad, 0.0)
        if audio_duration > 0:
            vad_removed_percent = vad_removed / audio_duration * 100

    token_counts = [len(getattr(segment, "tokens", None) or []) for segment in segments]
    decoder_tokens = sum(token_counts)
    output_words = 0
    for segment in segments:
        words = getattr(segment, "words", None)
        if words:
            output_words += len(words)
        else:
            output_words += len((getattr(segment, "text", "") or "").split())

    avg_logprobs = [
        _as_optional_float(getattr(segment, "avg_logprob", None))
        for segment in segments
    ]
    no_speech_probs = [
        _as_optional_float(getattr(segment, "no_speech_prob", None))
        for segment in segments
    ]
    compression_ratios = [
        _as_optional_float(getattr(segment, "compression_ratio", None))
        for segment in segments
    ]
    temperatures = sorted(
        {
            round(value, 3)
            for value in (
                _as_optional_float(getattr(segment, "temperature", None))
                for segment in segments
            )
            if value is not None
        }
    )

    top_languages = []
    all_language_probs = getattr(info, "all_language_probs", None) or []
    for language, probability in list(all_language_probs)[:5]:
        top_languages.append(
            {
                "language": language,
                "probability": _round_optional(_as_optional_float(probability), 4),
            }
        )

    tokens_per_audio_second = None
    if audio_duration and audio_duration > 0:
        tokens_per_audio_second = decoder_tokens / audio_duration
    tokens_per_inference_second = None
    if transcription_seconds > 0:
        tokens_per_inference_second = decoder_tokens / transcription_seconds

    chars_per_token = None
    if decoder_tokens > 0:
        chars_per_token = len(text) / decoder_tokens

    return {
        "name": model_name,
        "repo": resolve_model_repo(model_name),
        "device": get_actual_device()["device"],
        "configured_device": config.device,
        "compute": config.compute,
        "language": getattr(info, "language", None),
        "language_probability": _round_optional(
            _as_optional_float(getattr(info, "language_probability", None)),
            4,
        ),
        "top_language_probs": top_languages,
        "duration_after_vad_seconds": _round_optional(duration_after_vad, 3),
        "vad_removed_seconds": _round_optional(vad_removed, 3),
        "vad_removed_percent": _round_optional(vad_removed_percent, 2),
        "input_audio_frames_estimate": (
            int(round(audio_duration * 100)) if audio_duration else None
        ),
        "decoder_tokens": decoder_tokens,
        "output_words": output_words,
        "tokens_per_audio_second": _round_optional(tokens_per_audio_second, 3),
        "tokens_per_inference_second": _round_optional(tokens_per_inference_second, 3),
        "chars_per_token": _round_optional(chars_per_token, 3),
        "avg_logprob": _round_optional(_weighted_avg(avg_logprobs, token_counts), 4),
        "min_avg_logprob": _round_optional(
            min((v for v in avg_logprobs if v is not None), default=None), 4
        ),
        "avg_no_speech_prob": _round_optional(_avg(no_speech_probs), 4),
        "max_no_speech_prob": _round_optional(
            max((v for v in no_speech_probs if v is not None), default=None), 4
        ),
        "avg_compression_ratio": _round_optional(_avg(compression_ratios), 4),
        "max_compression_ratio": _round_optional(
            max((v for v in compression_ratios if v is not None), default=None),
            4,
        ),
        "temperature_values": temperatures,
    }


class GpuProbe:

    def __init__(self, enabled: bool):
        self.provider: str | None = None
        self.unavailable_reason: str | None = None
        self._pynvml = None
        self._handle = None

        if not enabled:
            self.unavailable_reason = "disabled"
            return

        try:
            import pynvml

            pynvml.nvmlInit()
            self._pynvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.provider = "pynvml"
            return
        except Exception as exc:
            self.unavailable_reason = f"pynvml unavailable: {type(exc).__name__}"

        if shutil.which("nvidia-smi"):
            self.provider = "nvidia-smi"
            self.unavailable_reason = None
        else:
            self.unavailable_reason = "nvidia-smi not found"

    @property
    def available(self) -> bool:

        return self.provider is not None

    def snapshot(self) -> dict | None:

        if self.provider == "pynvml":
            return self._snapshot_pynvml()
        if self.provider == "nvidia-smi":
            return self._snapshot_nvidia_smi()
        return None

    def _snapshot_pynvml(self) -> dict | None:

        try:
            nvml = self._pynvml
            handle = self._handle
            name = nvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")

            memory = nvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = nvml.nvmlDeviceGetUtilizationRates(handle)

            power_watts = None
            power_limit_watts = None
            temperature_c = None
            try:
                power_watts = nvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
            except Exception:
                pass
            try:
                power_limit_watts = (
                    nvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0
                )
            except Exception:
                pass
            try:
                temperature_c = float(
                    nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
                )
            except Exception:
                pass

            return {
                "timestamp": time.time(),
                "name": name,
                "vram_used_mb": memory.used / (1024 * 1024),
                "vram_total_mb": memory.total / (1024 * 1024),
                "utilization_percent": float(utilization.gpu),
                "power_watts": power_watts,
                "power_limit_watts": power_limit_watts,
                "temperature_c": temperature_c,
            }
        except Exception:
            return None

    def _snapshot_nvidia_smi(self) -> dict | None:

        query = "name,memory.used,memory.total,utilization.gpu,power.draw,power.limit,temperature.gpu"
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    f"--query-gpu={query}",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                check=True,
                text=True,
                timeout=1.0,
            )
            line = result.stdout.strip().splitlines()[0]
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 7:
                return None
            return {
                "timestamp": time.time(),
                "name": parts[0],
                "vram_used_mb": _as_float(parts[1]),
                "vram_total_mb": _as_float(parts[2]),
                "utilization_percent": _as_float(parts[3]),
                "power_watts": _as_float(parts[4]),
                "power_limit_watts": _as_float(parts[5]),
                "temperature_c": _as_float(parts[6]),
            }
        except Exception:
            return None


class GpuMetricsSampler:

    def __init__(self, probe: GpuProbe | None, interval: float):
        self.probe = probe
        self.interval = max(0.05, interval)
        self.samples: list[dict] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:

        if not self.probe or not self.probe.available:
            return

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self, inference_seconds: float) -> dict | None:

        if not self._thread:
            return None
        self._stop_event.set()
        self._thread.join(timeout=min(1.0, self.interval + 0.5))
        return self._summarize(inference_seconds)

    def _sample_loop(self) -> None:

        while not self._stop_event.is_set():
            sample = self.probe.snapshot() if self.probe else None
            if sample:
                self.samples.append(sample)
            if self._stop_event.wait(self.interval):
                break

    def _summarize(self, inference_seconds: float) -> dict | None:

        if not self.samples or not self.probe:
            return None

        vram_used = [s.get("vram_used_mb") for s in self.samples]
        vram_total = [s.get("vram_total_mb") for s in self.samples]
        utilization = [s.get("utilization_percent") for s in self.samples]
        power = [s.get("power_watts") for s in self.samples]
        power_limit = [s.get("power_limit_watts") for s in self.samples]
        temperature = [s.get("temperature_c") for s in self.samples]

        power_avg = _avg(power)
        energy_wh = None
        if power_avg is not None:
            energy_wh = power_avg * max(inference_seconds, 0.0) / 3600

        return {
            "provider": self.probe.provider,
            "name": next((s.get("name") for s in self.samples if s.get("name")), None),
            "sample_count": len(self.samples),
            "vram_used_start_mb": _round_optional(vram_used[0], 1),
            "vram_used_peak_mb": _round_optional(
                max((v for v in vram_used if v is not None), default=None), 1
            ),
            "vram_used_end_mb": _round_optional(vram_used[-1], 1),
            "vram_total_mb": _round_optional(
                max((v for v in vram_total if v is not None), default=None), 1
            ),
            "utilization_avg_percent": _round_optional(_avg(utilization), 1),
            "utilization_peak_percent": _round_optional(
                max((v for v in utilization if v is not None), default=None), 1
            ),
            "power_avg_watts": _round_optional(power_avg, 2),
            "power_peak_watts": _round_optional(
                max((v for v in power if v is not None), default=None), 2
            ),
            "power_limit_watts": _round_optional(
                max((v for v in power_limit if v is not None), default=None), 2
            ),
            "temperature_peak_c": _round_optional(
                max((v for v in temperature if v is not None), default=None), 1
            ),
            "energy_wh_estimate": _round_optional(energy_wh, 6),
        }


def gpu_metrics_dict_from_probe_snapshot(snap: dict, provider: str) -> dict:
    """One probe snapshot in the same shape as transcribe response ``metrics[\"gpu\"]``."""
    u = snap.get("utilization_percent")
    v_used = snap.get("vram_used_mb")
    pw = snap.get("power_watts")
    return {
        "provider": provider,
        "name": snap.get("name"),
        "sample_count": 1,
        "vram_used_peak_mb": _round_optional(v_used, 1),
        "vram_total_mb": _round_optional(snap.get("vram_total_mb"), 1),
        "utilization_avg_percent": _round_optional(u, 1),
        "utilization_peak_percent": _round_optional(u, 1),
        "power_avg_watts": _round_optional(pw, 2),
        "power_peak_watts": _round_optional(pw, 2),
        "power_limit_watts": _round_optional(snap.get("power_limit_watts"), 2),
        "temperature_peak_c": _round_optional(snap.get("temperature_c"), 1),
        "energy_wh_estimate": None,
    }


def sanitize_metadata_value(value, depth: int = 0):

    if depth > 5:
        return str(value)[:200]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [sanitize_metadata_value(item, depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        clean = {}
        for key, item in list(value.items())[:50]:
            clean[str(key)[:80]] = sanitize_metadata_value(item, depth + 1)
        return clean
    return str(value)[:200]


def parse_capture_metadata(raw: str | None) -> dict | None:

    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"error": "invalid_capture_metadata"}
    clean = sanitize_metadata_value(parsed)
    return clean if isinstance(clean, dict) else None


_models: Dict[Tuple[str, str, str], Any] = {}
_warmed_model_keys: set[tuple[str, str, str]] = set()
_warmed_model_keys_lock = threading.Lock()
_whisper_model_class = None
faster_whisper_distribution_info: dict | None = None


def verify_faster_whisper_distribution() -> dict:

    try:
        dist = importlib_metadata.distribution("faster-whisper")
    except importlib_metadata.PackageNotFoundError as exc:
        raise RuntimeError("faster-whisper is not installed") from exc

    package_name = (dist.metadata.get("Name") or "").strip().lower()
    home_page = (dist.metadata.get("Home-page") or "").strip()
    project_urls = dist.metadata.get_all("Project-URL") or []
    source_text = "\n".join([home_page, *project_urls]).lower()

    if package_name != "faster-whisper":
        raise RuntimeError(
            f"Unexpected faster-whisper package name: {package_name or 'unknown'}"
        )
    if EXPECTED_FASTER_WHISPER_HOME.lower() not in source_text:
        raise RuntimeError(
            "Installed faster-whisper package metadata does not point to "
            f"{EXPECTED_FASTER_WHISPER_HOME}"
        )

    return {
        "name": dist.metadata.get("Name"),
        "version": dist.version,
        "home_page": home_page,
        "location": str(dist.locate_file("")),
    }


# Same as faster_whisper.utils.download_model — keep in sync with faster-whisper.
_HF_MODEL_ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

_vox_hf_progress_sink: Callable[[str], None] | None = None


def _set_hf_progress_sink(sink: Callable[[str], None] | None) -> None:
    global _vox_hf_progress_sink
    _vox_hf_progress_sink = sink


class VoxiumHubTqdm(tqdm):
    """tqdm for huggingface_hub: mirror progress to operator sink or stderr."""

    def __init__(self, *args, **kwargs):
        if _vox_hf_progress_sink is not None:
            kwargs = dict(kwargs)
            kwargs.setdefault("file", io.StringIO())
        super().__init__(*args, **kwargs)

    def update(self, n=1):  # type: ignore[override]
        r = super().update(n)
        sink = _vox_hf_progress_sink
        if sink is not None and getattr(self, "total", None):
            try:
                t = float(self.total)
                if t > 0:
                    n_done = float(self.n)
                    pct = 100.0 * n_done / t
                    desc = (self.desc or "file").strip()
                    sink(f"{desc}  {pct:.0f}%")
            except Exception:
                pass
        return r


_model_key_locks: dict[tuple[str, str, str], threading.Lock] = {}
_model_key_locks_main = threading.Lock()

_ensure_jobs: dict[str, dict[str, Any]] = {}
_ensure_jobs_lock = threading.Lock()
_active_ensure_by_key: dict[tuple[str, str, str], str] = {}


def _lock_for_model_key(key: tuple[str, str, str]) -> threading.Lock:
    with _model_key_locks_main:
        if key not in _model_key_locks:
            _model_key_locks[key] = threading.Lock()
        return _model_key_locks[key]


def _download_hf_snapshot_to_models_dir(
    repo_id: str,
    model_label: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> str:
    """
    Run Hugging Face snapshot download with Voxium logging. Progress uses the
    default HF tqdm (stderr; merged into the server log when stderr is combined
    with stdout in the parent process) unless ``progress`` is set — then tqdm
    also feeds the Downlink / ensure-model job, copy.
    """
    root = models_dir()
    root_abs = str(root.resolve())
    logger.info(
        "Voxium: starting Hugging Face model download: model_id=%r repo_id=%r",
        model_label,
        repo_id,
    )
    logger.info(
        "Voxium: local model / Hub cache root (see hub layout under this path): %s",
        root_abs,
    )
    tqdm_class = VoxiumHubTqdm if progress is not None else tqdm
    try:
        if progress is not None:
            _set_hf_progress_sink(progress)
        snapshot_path = snapshot_download(
            repo_id,
            local_files_only=False,
            allow_patterns=_HF_MODEL_ALLOW_PATTERNS,
            cache_dir=root_abs,
            tqdm_class=tqdm_class,
        )
    finally:
        if progress is not None:
            _set_hf_progress_sink(None)
    snap_abs = str(Path(snapshot_path).resolve())
    logger.info("Voxium: Hugging Face snapshot available on disk: %s", snap_abs)
    return snapshot_path


def get_whisper_model_class():

    global _whisper_model_class, faster_whisper_distribution_info
    if _whisper_model_class is None:
        faster_whisper_distribution_info = verify_faster_whisper_distribution()
        from faster_whisper import WhisperModel

        _whisper_model_class = WhisperModel
    return _whisper_model_class


def get_model(
    name: str,
    device: str,
    compute: str,
    *,
    progress: Callable[[str], None] | None = None,
):

    model_name = validate_model_name(name)
    repo_id = resolve_model_repo(model_name)
    key = (model_name, device, compute)
    lock = _lock_for_model_key(key)
    with lock:
        if key in _models:
            return _models[key]
        whisper_model = get_whisper_model_class()
        logger.info(
            "Voxium: loading model %r repo_id=%r (device=%s, compute=%s); "
            "any download is logged with progress below (HF tqdm on stderr).",
            model_name,
            repo_id,
            device,
            compute,
        )
        model_path = _download_hf_snapshot_to_models_dir(
            repo_id, model_name, progress=progress
        )
        if progress:
            progress("Loading model into memory (CTranslate2), copy.")
        _models[key] = whisper_model(
            model_path,
            device=device,
            compute_type=compute,
        )
        logger.info("Voxium: CTranslate2 model loaded from disk successfully")
    return _models[key]


def _write_stt_warmup_wav(path: str) -> None:
    """Write a tiny 16 kHz mono WAV so faster-whisper performs its first inference."""
    sample_count = 16000 // 4
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * sample_count)


def ensure_stt_model_ready(
    name: str,
    device: str,
    compute: str,
    *,
    progress: Callable[[str], None] | None = None,
):
    """
    Load the selected STT model and force one small inference so the first real PTT
    does not pay lazy decoder / native runtime setup costs.
    """
    model_name = validate_model_name(name)
    key = (model_name, device, compute)
    whisper = get_model(model_name, device, compute, progress=progress)
    with _warmed_model_keys_lock:
        if key in _warmed_model_keys:
            return whisper
    if progress:
        progress(
            "Running first-inference STT warmup — keeping this model ready for PTT, copy."
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = tmp.name
    tmp.close()
    try:
        _write_stt_warmup_wav(tmp_path)
        segments, _info = whisper.transcribe(
            tmp_path,
            language="en",
            vad_filter=False,
        )
        list(segments)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    with _warmed_model_keys_lock:
        _warmed_model_keys.add(key)
    logger.info(
        "Voxium: STT first-inference warmup complete for model=%r device=%s compute=%s",
        model_name,
        device,
        compute,
    )
    return whisper


def get_actual_device() -> dict:

    cuda_count = ctranslate2.get_cuda_device_count()
    cuda_available = cuda_count > 0

    device_info = {
        "device": "cuda" if cuda_available and config.device != "cpu" else "cpu",
        "cuda_available": cuda_available,
        "cuda_device_count": cuda_count,
    }

    try:
        import torch

        if torch.cuda.is_available():
            device_info["cuda_device_name"] = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    return device_info


app = FastAPI(
    title="Voxium",
    description="Voxium local transcription HTTP API (faster-whisper). Not for direct use — prefer the voxium CLI.",
    version="0.0.1",
)


stats: ServerStats = None
config: ServerConfig = None
logger: logging.Logger = None
gpu_probe: GpuProbe = None
_polish_semaphore: threading.BoundedSemaphore | None = None


class PolishRequestBody(BaseModel):
    text: str
    model: str | None = None
    backend: str | None = "llama.cpp"
    keep_alive: str | int | None = None


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):

    logger.error(f"Voxium: unhandled exception: {exc}", exc_info=True)
    if stats:
        stats.record_error()
    return JSONResponse(
        status_code=500,
        content={"detail": f"Voxium server error: {type(exc).__name__}"},
    )


class EnsureModelBody(BaseModel):
    model: str


def _run_ensure_model_job(
    job_id: str, model_name: str, key: tuple[str, str, str]
) -> None:
    def progress(msg: str) -> None:
        one = (msg or "").strip().replace("\n", " ")
        if len(one) > 240:
            one = one[:237] + "…"
        with _ensure_jobs_lock:
            if job_id in _ensure_jobs:
                _ensure_jobs[job_id]["progress_line"] = one

    try:
        with _ensure_jobs_lock:
            if job_id in _ensure_jobs:
                _ensure_jobs[job_id]["status"] = "running"
                _ensure_jobs[job_id][
                    "progress_line"
                ] = "Starting Hugging Face fetch for this model, copy."
        ensure_stt_model_ready(
            model_name,
            config.device,
            config.compute,
            progress=progress,
        )
    except Exception as e:
        logger.error("Voxium: ensure-model job failed: %s", e, exc_info=True)
        with _ensure_jobs_lock:
            if job_id in _ensure_jobs:
                _ensure_jobs[job_id]["status"] = "error"
                _ensure_jobs[job_id]["error"] = str(e)
                _ensure_jobs[job_id]["done"] = True
    else:
        with _ensure_jobs_lock:
            if job_id in _ensure_jobs:
                _ensure_jobs[job_id]["status"] = "ready"
                _ensure_jobs[job_id]["done"] = True
                _ensure_jobs[job_id][
                    "progress_line"
                ] = "Model on disk and loaded — ready for PTT, copy."
    finally:
        with _ensure_jobs_lock:
            if _active_ensure_by_key.get(key) == job_id:
                del _active_ensure_by_key[key]


@app.get("/health")
def health():

    device_info = get_actual_device()
    loaded_transcribe_models = sorted({name for (name, _dev, _comp) in _models.keys()})
    with _warmed_model_keys_lock:
        warmed_transcribe_models = sorted(
            {name for (name, _dev, _comp) in _warmed_model_keys}
        )
    body: dict[str, Any] = {
        "status": "ok",
        "model": config.model,
        "startup_model": config.model,
        "model_repo": resolve_model_repo(config.model),
        "startup_model_repo": resolve_model_repo(config.model),
        "loaded_transcribe_models": loaded_transcribe_models,
        "warmed_transcribe_models": warmed_transcribe_models,
        "device": device_info["device"],
        "compute": config.compute,
        "cuda_available": device_info["cuda_available"],
        "cuda_device_count": device_info.get("cuda_device_count", 0),
        "cuda_device_name": device_info.get("cuda_device_name"),
        "vad_enabled": config.vad_enabled,
        "timeout_seconds": config.timeout,
        "gpu_metrics_enabled": bool(gpu_probe and gpu_probe.available),
        "gpu_metrics_provider": gpu_probe.provider if gpu_probe else None,
        "gpu_metrics_unavailable_reason": (
            gpu_probe.unavailable_reason if gpu_probe else None
        ),
        "metrics_sample_interval_seconds": config.metrics_sample_interval,
        "faster_whisper": faster_whisper_distribution_info,
    }
    ok_llama, reason_llama = llama_cpp_reachable(
        config.llama_cpp_url,
        timeout=min(1.0, max(0.1, config.polish_timeout_seconds / 5)),
    )
    loaded_model: str | None = None
    if ok_llama:
        loaded_model = llama_cpp_loaded_model(config.llama_cpp_url, timeout=1.0)
    body.update(
        {
            "polish_backend_default": config.polish_backend_default,
            "polish_enabled_default": config.polish_enabled_default,
            "polish_default_model": config.polish_default_model,
            "polish_timeout_seconds": config.polish_timeout_seconds,
            "polish_keep_alive_default": config.polish_keep_alive_default,
            "polish_llama_cpp_reachable": ok_llama,
            "polish_llama_cpp_reachable_reason": reason_llama,
            "polish_loaded_model": loaded_model,
            "polish_model_loaded": loaded_model is not None,
        }
    )
    return body


@app.post("/ensure-model")
def ensure_model_start(body: EnsureModelBody):
    if config is None:
        raise HTTPException(503, "Voxium: server not ready")
    try:
        m = validate_model_name(body.model)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    key: tuple[str, str, str] = (m, config.device, config.compute)
    with _warmed_model_keys_lock:
        ready = key in _models and key in _warmed_model_keys
    if ready:
        return {
            "status": "ready",
            "model": m,
            "message": f"Model {m!r} is already loaded and warmed on this /transcribe server, copy.",
        }
    with _ensure_jobs_lock:
        if key in _active_ensure_by_key:
            jid = _active_ensure_by_key[key]
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending",
                    "job_id": jid,
                    "model": m,
                    "reused": True,
                },
            )
        job_id = uuid.uuid4().hex[:12]
        _ensure_jobs[job_id] = {
            "model": m,
            "status": "pending",
            "progress_line": "Queued — local server will pull from Hugging Face, copy.",
            "error": None,
            "done": False,
        }
        _active_ensure_by_key[key] = job_id
    t = threading.Thread(
        target=_run_ensure_model_job,
        args=(job_id, m, key),
        daemon=True,
        name=f"voxium-ensure-model-{job_id}",
    )
    t.start()
    return JSONResponse(
        status_code=202,
        content={"status": "pending", "job_id": job_id, "model": m},
    )


@app.get("/ensure-model/jobs/{job_id}")
def ensure_model_job_status(job_id: str):
    with _ensure_jobs_lock:
        j = _ensure_jobs.get(job_id)
    if not j:
        raise HTTPException(404, f"Unknown job_id {job_id!r}, copy.")
    return {
        "status": j.get("status"),
        "model": j.get("model"),
        "lines": j.get("lines") or [],
        "progress_line": j.get("progress_line"),
        "error": j.get("error"),
        "done": bool(j.get("done")),
    }


@app.get("/stats")
def get_stats():

    return {
        **stats.to_dict(),
        "model": config.model,
        "model_repo": resolve_model_repo(config.model),
        "device": get_actual_device()["device"],
        "gpu_metrics_provider": gpu_probe.provider if gpu_probe else None,
        "faster_whisper": faster_whisper_distribution_info,
    }


@app.get("/gpu")
def get_gpu_snapshot():

    if not gpu_probe or not gpu_probe.available:
        return JSONResponse(
            status_code=503,
            content={
                "error": "gpu_metrics_unavailable",
                "reason": gpu_probe.unavailable_reason if gpu_probe else "no probe",
            },
        )
    snap = gpu_probe.snapshot()
    if not snap:
        return JSONResponse(
            status_code=503,
            content={
                "error": "gpu_snapshot_failed",
                "reason": "probe returned no data",
            },
        )
    return {"gpu": gpu_metrics_dict_from_probe_snapshot(snap, gpu_probe.provider)}


@app.post("/polish")
def polish_endpoint(body: PolishRequestBody):

    t_handler0 = time.perf_counter()
    if config is None:
        raise HTTPException(503, "Voxium: server not ready")
    raw = (body.text or "").strip()
    if not raw:
        raise HTTPException(400, "Voxium: polish text is empty, copy.")
    backend = (body.backend or "llama.cpp").strip().lower()
    if backend != "llama.cpp":
        raise HTTPException(
            400, f"Voxium: unknown polish backend {body.backend!r}, copy."
        )
    if not is_loopback_url(config.llama_cpp_url):
        raise HTTPException(500, "Voxium: llama.cpp URL must be loopback http, copy.")
    m = (body.model or config.polish_default_model or "").strip()
    if not m:
        raise HTTPException(400, "Voxium: polish model is required, copy.")
    try:
        requested_model = validate_polish_model_tag(m)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    t_ensure0 = time.perf_counter()
    try:
        resolved_model = ensure_polish_model_downloaded(model_name=requested_model)
    except RuntimeError as e:
        err = str(e)
        prep = round(time.perf_counter() - t_ensure0, 4)
        return {
            "text": raw,
            "text_raw": raw,
            "polish": {
                "enabled": True,
                "attempted": True,
                "applied": False,
                "model": requested_model,
                "backend": backend,
                "seconds": 0.0,
                "prepare_seconds": prep,
                "handler_seconds": round(time.perf_counter() - t_handler0, 4),
                "tokens_in": None,
                "tokens_out": None,
                "error": err,
            },
            "metrics": {
                "polish": {
                    "model": requested_model,
                    "backend": backend,
                    "seconds": 0.0,
                    "prepare_seconds": prep,
                    "handler_seconds": round(time.perf_counter() - t_handler0, 4),
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "error": err,
                }
            },
        }

    prepare_seconds = round(time.perf_counter() - t_ensure0, 4)
    sem = _polish_semaphore
    if sem is None:
        raise HTTPException(503, "Voxium: polish capacity not initialized, copy.")
    try:
        acquired = sem.acquire(blocking=False)
    except Exception:
        acquired = False
    if not acquired:
        return JSONResponse(
            status_code=503,
            content={
                "error": "polish_saturated",
                "detail": "Too many concurrent /polish requests — try again shortly, copy.",
            },
        )
    try:
        res = llama_cpp_chat(
            config.llama_cpp_url,
            resolved_model.name,
            raw,
            timeout=float(config.polish_timeout_seconds),
        )
    finally:
        try:
            sem.release()
        except ValueError:
            pass
    polish_profile.record("polish", model=resolved_model.name, result=res)

    handler_seconds = round(time.perf_counter() - t_handler0, 4)
    if res.ok and (res.text or "").strip():
        out_text = res.text.strip()
        pol = {
            "enabled": True,
            "attempted": True,
            "applied": True,
            "model": resolved_model.name,
            "backend": backend,
            "seconds": round(res.seconds, 4),
            "prepare_seconds": prepare_seconds,
            "handler_seconds": handler_seconds,
            "tokens_in": res.prompt_tokens,
            "tokens_out": res.completion_tokens,
            "total_tokens": res.total_tokens,
            "error": None,
        }
        metrics_pol = {
            "model": resolved_model.name,
            "backend": backend,
            "seconds": pol["seconds"],
            "prepare_seconds": prepare_seconds,
            "handler_seconds": handler_seconds,
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "total_tokens": res.total_tokens,
        }
        return {
            "text": out_text,
            "text_raw": raw,
            "polish": pol,
            "metrics": {"polish": metrics_pol},
        }

    err = (res.error or "polish failed") if not res.ok else "empty model output"
    pol = {
        "enabled": True,
        "attempted": True,
        "applied": False,
        "model": resolved_model.name,
        "backend": backend,
        "seconds": round(res.seconds, 4),
        "prepare_seconds": prepare_seconds,
        "handler_seconds": handler_seconds,
        "tokens_in": res.prompt_tokens,
        "tokens_out": res.completion_tokens,
        "total_tokens": res.total_tokens,
        "error": err,
    }
    return {
        "text": raw,
        "text_raw": raw,
        "polish": pol,
        "metrics": {
            "polish": {
                "model": resolved_model.name,
                "backend": backend,
                "seconds": pol["seconds"],
                "prepare_seconds": prepare_seconds,
                "handler_seconds": handler_seconds,
                "prompt_tokens": res.prompt_tokens,
                "completion_tokens": res.completion_tokens,
                "total_tokens": res.total_tokens,
                "error": err,
            }
        },
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form(None),
    model: str = Form(None),
    capture_metadata: str = Form(None),
):

    request_start = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]
    try:
        m = validate_model_name(model or config.model)
    except ValueError as exc:
        stats.record_error()
        raise HTTPException(400, str(exc)) from exc
    capture_metrics = parse_capture_metadata(capture_metadata)

    logger.info(
        f"Voxium: transcription request | id={request_id} model={m} language={language or 'auto'}"
    )

    try:
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(400, "Voxium: empty audio file")
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(400, "Voxium: audio file too large (max 50MB)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voxium: failed to read audio file: {e}")
        stats.record_error()
        raise HTTPException(400, f"Voxium: invalid audio file: {e}")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    try:
        tmp.write(content)
        tmp.close()

        try:
            whisper = get_model(m, config.device, config.compute)
        except Exception as e:
            logger.error(f"Voxium: model load failed: {e}")
            stats.record_error()
            raise HTTPException(503, "Voxium: model not available — try again later")

        gpu_sampler = GpuMetricsSampler(gpu_probe, config.metrics_sample_interval)
        gpu_sampler.start()
        transcribe_start = time.perf_counter()
        gpu_metrics = None
        try:
            if config.vad_enabled:
                segments, info = whisper.transcribe(
                    tmp.name,
                    language=language,
                    vad_filter=True,
                    vad_parameters={
                        "min_silence_duration_ms": 500,
                        "speech_pad_ms": 200,
                    },
                )
            else:
                segments, info = whisper.transcribe(tmp.name, language=language)

            segment_objs = list(segments)
            segments_list = [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "decoder_tokens": len(getattr(s, "tokens", None) or []),
                    "avg_logprob": _round_optional(
                        _as_optional_float(getattr(s, "avg_logprob", None)), 4
                    ),
                    "compression_ratio": _round_optional(
                        _as_optional_float(getattr(s, "compression_ratio", None)),
                        4,
                    ),
                    "no_speech_prob": _round_optional(
                        _as_optional_float(getattr(s, "no_speech_prob", None)), 4
                    ),
                    "temperature": _round_optional(
                        _as_optional_float(getattr(s, "temperature", None)), 3
                    ),
                }
                for s in segment_objs
            ]
            text = "".join(s["text"] for s in segments_list)

        except Exception as e:
            transcribe_duration = time.perf_counter() - transcribe_start
            gpu_metrics = gpu_sampler.stop(transcribe_duration)
            logger.error(f"Voxium: transcription failed: {e}")
            stats.record_error()
            raise HTTPException(
                500, f"Voxium: transcription failed: {type(e).__name__}: {e}"
            )
        else:
            transcribe_duration = time.perf_counter() - transcribe_start
            gpu_metrics = gpu_sampler.stop(transcribe_duration)

        total_duration = time.perf_counter() - request_start
        audio_duration = getattr(info, "duration", 0) or 0
        realtime_factor = (
            transcribe_duration / audio_duration if audio_duration else None
        )
        model_metrics = build_model_metrics(
            m, info, segment_objs, text, transcribe_duration
        )

        request_metrics = {
            "request_id": request_id,
            "input_bytes": len(content),
            "audio_seconds": round(audio_duration, 3),
            "transcription_seconds": round(transcribe_duration, 4),
            "total_request_seconds": round(total_duration, 4),
            "realtime_factor": _round_optional(realtime_factor, 4),
            "output_chars": len(text),
            "segments": len(segments_list),
            "capture": capture_metrics,
            "model": model_metrics,
            "gpu": gpu_metrics,
        }

        stats.record_request(request_metrics)

        gpu_log = ""
        if gpu_metrics:
            gpu_log = (
                f" gpu_provider={gpu_metrics.get('provider')}"
                f" gpu_util_avg={gpu_metrics.get('utilization_avg_percent')}"
                f" gpu_util_peak={gpu_metrics.get('utilization_peak_percent')}"
                f" vram_peak_mb={gpu_metrics.get('vram_used_peak_mb')}"
                f" power_avg_w={gpu_metrics.get('power_avg_watts')}"
                f" energy_wh={gpu_metrics.get('energy_wh_estimate')}"
            )

        capture_log = ""
        if capture_metrics:
            device = capture_metrics.get("device") or {}
            host_api = capture_metrics.get("host_api") or {}
            recording = capture_metrics.get("recording") or {}
            capture_log = (
                f" capture_device={device.get('name')}"
                f" capture_api={host_api.get('name')}"
                f" capture_seconds={recording.get('capture_seconds')}"
                f" capture_frames={recording.get('captured_frames')}"
            )

        logger.info(
            f"Voxium: transcription complete | "
            f"id={request_id} time={transcribe_duration:.2f}s audio={audio_duration:.1f}s "
            f"rtf={request_metrics['realtime_factor']} chars={len(text)} "
            f"tokens={model_metrics['decoder_tokens']} lang={info.language} "
            f"avg_logprob={model_metrics['avg_logprob']}"
            f"{gpu_log}"
            f"{capture_log}"
        )

        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "model": m,
            "model_repo": resolve_model_repo(m),
            "segments": segments_list,
            "duration": audio_duration,
            "metrics": request_metrics,
        }

    finally:

        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def handle_shutdown(_signum, _frame):

    logger.info("Voxium: shutdown — safing the stack, cleaning up...")
    sys.exit(0)


def main(argv: list[str] | None = None):
    global config, logger, stats, gpu_probe, _polish_semaphore

    parser = argparse.ArgumentParser(
        description="Voxium transcription server (faster-whisper) — internal / diagnostics use; normally started by voxium run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  voxium server                    # Default: base model, CUDA
  voxium server --model medium     # Better accuracy
  voxium server --device cpu       # CPU only
  voxium server --no-vad           # Disable VAD
        """,
    )
    parser.add_argument(
        "--model",
        "-m",
        type=trusted_model_arg,
        default=DEFAULT_MODEL,
        help=f"Voxium model (Systran faster-whisper): {TRUSTED_MODEL_HELP} (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--device",
        "-d",
        default=DEFAULT_DEVICE,
        help=f"Device: auto, cuda, cpu (default: {DEFAULT_DEVICE})",
    )
    parser.add_argument(
        "--compute",
        "-c",
        default=DEFAULT_COMPUTE,
        help=f"Compute type: auto, float16, int8 (default: {DEFAULT_COMPUTE})",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=8002, help="Port to run on (default: 8002)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=120,
        help="Transcription timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="Disable VAD (voice activity detection) filtering",
    )
    parser.add_argument(
        "--no-gpu-metrics",
        action="store_true",
        help="Disable per-request GPU metrics sampling",
    )
    parser.add_argument(
        "--metrics-sample-interval",
        type=float,
        default=0.25,
        help="GPU metrics sampling interval in seconds (default: 0.25)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)",
    )
    parser.add_argument(
        "--llama-cpp-url",
        default=(os.getenv("VOXIUM_LLAMA_CPP_URL") or "http://127.0.0.1:11435"),
        help="Loopback URL for llama.cpp (default: http://127.0.0.1:11435)",
    )
    parser.add_argument(
        "--polish-default-model",
        default=os.getenv("VOXIUM_POLISH_MODEL", DEFAULT_POLISH_MODEL),
        metavar="MODEL",
        help=f"Default GGUF model selector for /polish (default: {DEFAULT_POLISH_MODEL})",
    )
    parser.add_argument(
        "--polish-timeout",
        type=float,
        default=float(os.getenv("VOXIUM_POLISH_TIMEOUT", "25")),
        help="Per-/polish timeout in seconds (default: 25)",
    )
    parser.add_argument(
        "--polish-enabled-by-default",
        action=argparse.BooleanOptionalAction,
        default=env_polish_enabled_default(),
        help="Health hint: default on for client re-encode (default: on; VOXIUM_POLISH_ENABLED=0 to opt out)",
    )
    parser.add_argument(
        "--polish-keep-alive",
        default=os.environ.get("VOXIUM_POLISH_KEEP_ALIVE", "-1"),
        help="Default llama.cpp idle unload window for /polish telemetry (default: -1, keep loaded)",
    )
    parser.add_argument(
        "--polish-warmup-on-start",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("VOXIUM_POLISH_WARMUP", "1").lower()
        not in ("0", "false", "no", "off"),
        help="Minimal llama.cpp chat at startup to warm the polish model (default: on)",
    )
    parser.add_argument(
        "--polish-max-concurrent",
        type=int,
        default=int(os.environ.get("VOXIUM_POLISH_MAX_CONCURRENT", "2")),
        help="Max concurrent /polish requests (default: 2)",
    )
    args = parser.parse_args(argv)
    try:
        args.model = validate_model_name(args.model)
    except ValueError as exc:
        parser.error(str(exc))
    if not is_loopback_url(args.llama_cpp_url):
        parser.error(
            "llama-cpp-url must be http loopback (127.0.0.1, localhost, or ::1)"
        )
    try:
        args.polish_default_model = validate_polish_model_tag(args.polish_default_model)
    except ValueError as exc:
        parser.error(str(exc))
    if not is_loopback_host(args.host):
        parser.error("host must be loopback-only: localhost, 127.0.0.1, or ::1")
    args.host = normalize_loopback_host(args.host)

    ensure_runtime_dirs()

    logger = setup_logging(args.log_level)
    stats = ServerStats()
    config = ServerConfig(
        model=args.model,
        device=args.device,
        compute=args.compute,
        timeout=args.timeout,
        vad_enabled=not args.no_vad,
        host=args.host,
        port=args.port,
        gpu_metrics_enabled=not args.no_gpu_metrics,
        metrics_sample_interval=max(0.05, args.metrics_sample_interval),
        llama_cpp_url=args.llama_cpp_url,
        polish_default_model=args.polish_default_model,
        polish_timeout_seconds=max(1.0, float(args.polish_timeout)),
        polish_enabled_default=bool(args.polish_enabled_by_default),
        polish_keep_alive_default=str(args.polish_keep_alive or "-1"),
        polish_warmup_on_start=bool(args.polish_warmup_on_start),
        polish_max_concurrent=max(1, int(args.polish_max_concurrent)),
    )
    _polish_semaphore = threading.BoundedSemaphore(config.polish_max_concurrent)
    gpu_probe = GpuProbe(config.gpu_metrics_enabled)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info(
        f"Voxium: launch — /transcribe at http://{args.host}:{args.port} "
        f"(PTT on client; VOX over loopback here)"
    )
    logger.info(
        f"Voxium: config model={args.model} device={args.device} vad={config.vad_enabled} timeout={args.timeout}s"
    )
    if gpu_probe.available:
        logger.info(
            f"Voxium: GPU metrics enabled via {gpu_probe.provider} (interval={config.metrics_sample_interval}s)"
        )
    else:
        logger.info(f"Voxium: GPU metrics unavailable: {gpu_probe.unavailable_reason}")

    try:
        ensure_stt_model_ready(config.model, config.device, config.compute)
        if faster_whisper_distribution_info:
            logger.info(
                "Voxium: faster-whisper package verified: "
                f"version={faster_whisper_distribution_info.get('version')} "
                f"home={faster_whisper_distribution_info.get('home_page')}"
            )
    except Exception as e:
        logger.error(f"Voxium: failed to load model: {e}")
        sys.exit(1)

    if config.polish_warmup_on_start:
        ok_l, lmsg = llama_cpp_reachable(
            config.llama_cpp_url,
            timeout=min(3.0, max(0.5, config.polish_timeout_seconds)),
        )
        if ok_l:
            try:
                warm_model = ensure_polish_model_downloaded(
                    model_name=config.polish_default_model
                ).name
            except (ValueError, RuntimeError) as exc:
                logger.warning("Voxium: polish warmup skipped: %s", exc)
            else:
                w = llama_cpp_chat(
                    config.llama_cpp_url,
                    warm_model,
                    "warmup",
                    timeout=min(20.0, max(2.0, config.polish_timeout_seconds)),
                    temperature=0.0,
                    max_tokens=1,
                )
                polish_profile.record("polish", model=warm_model, result=w)
                if w.ok:
                    logger.info(
                        "Voxium: polish llama.cpp warmup copy — model primed, copy."
                    )
                else:
                    logger.warning("Voxium: polish warmup skipped: %s", w.error)
        else:
            logger.warning(
                "Voxium: llama.cpp not reachable for polish warmup: %s", lmsg
            )

    logger.info(
        "Voxium: server on station — /transcribe and /polish open for traffic (roger, copy)"
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

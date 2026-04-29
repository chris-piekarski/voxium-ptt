"""Trusted polish model registry and repo-local GGUF inventory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from voxium.paths import polish_models_dir

POLISH_DEFAULT_MODEL = "auto"
DEFAULT_TRUSTED_POLISH_MODEL_ID = "qwen2.5-coder-3b-q5km"
LOCAL_POLISH_PREFIX = "local:"


@dataclass(frozen=True)
class TrustedPolishModel:
    model_id: str
    repo_id: str
    filename: str
    description: str
    size_text: str
    backend: str = "llama.cpp"

    def local_path(self, root: Path | None = None) -> Path:
        base = Path(root) if root is not None else polish_models_dir()
        return (base / self.filename).resolve()


TRUSTED_POLISH_MODELS: dict[str, TrustedPolishModel] = {
    "qwen2.5-coder-3b-q4km": TrustedPolishModel(
        model_id="qwen2.5-coder-3b-q4km",
        repo_id="Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        filename="qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        description="Fast code-aware polish for shell, logs, and agent copy",
        size_text="2.1 GB",
    ),
    "qwen2.5-coder-3b-q5km": TrustedPolishModel(
        model_id="qwen2.5-coder-3b-q5km",
        repo_id="Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        filename="qwen2.5-coder-3b-instruct-q5_k_m.gguf",
        description="Default code-aware polish with stronger fidelity",
        size_text="2.44 GB",
    ),
    "qwen2.5-3b-q4km": TrustedPolishModel(
        model_id="qwen2.5-3b-q4km",
        repo_id="Qwen/Qwen2.5-3B-Instruct-GGUF",
        filename="qwen2.5-3b-instruct-q4_k_m.gguf",
        description="General-purpose rewrite model with lower latency",
        size_text="2.1 GB",
    ),
    "qwen2.5-3b-q5km": TrustedPolishModel(
        model_id="qwen2.5-3b-q5km",
        repo_id="Qwen/Qwen2.5-3B-Instruct-GGUF",
        filename="qwen2.5-3b-instruct-q5_k_m.gguf",
        description="General-purpose rewrite model with stronger quality",
        size_text="2.44 GB",
    ),
}

TRUSTED_POLISH_MODEL_NAMES = tuple(TRUSTED_POLISH_MODELS)
TRUSTED_POLISH_MODEL_HELP = ", ".join(TRUSTED_POLISH_MODEL_NAMES)


@dataclass(frozen=True)
class LocalPolishModel:
    name: str
    path: Path
    size_bytes: int
    trusted_id: str | None = None
    repo_id: str | None = None
    filename: str | None = None
    description: str | None = None

    @property
    def size_gib_text(self) -> str:
        gib = self.size_bytes / (1024**3)
        if gib >= 10:
            return f"{gib:.0f} GiB"
        if gib >= 1:
            return f"{gib:.1f} GiB"
        mib = self.size_bytes / (1024**2)
        return f"{mib:.0f} MiB"

    @property
    def is_trusted(self) -> bool:
        return self.trusted_id is not None

    @property
    def local_selector(self) -> str | None:
        if self.is_trusted or self.filename is None:
            return None
        return f"{LOCAL_POLISH_PREFIX}{self.filename}"


def list_available_polish_models() -> list[TrustedPolishModel]:
    return [TRUSTED_POLISH_MODELS[name] for name in TRUSTED_POLISH_MODEL_NAMES]


def trusted_polish_model(model_name: str | None) -> TrustedPolishModel:
    requested = (model_name or DEFAULT_TRUSTED_POLISH_MODEL_ID).strip()
    if requested not in TRUSTED_POLISH_MODELS:
        raise ValueError(
            f"Unknown polish model {requested!r}. "
            f"Allowed ids: {TRUSTED_POLISH_MODEL_HELP}, copy."
        )
    return TRUSTED_POLISH_MODELS[requested]


def trusted_polish_model_path(model_name: str | None, root: Path | None = None) -> Path:
    return trusted_polish_model(model_name).local_path(root)


def is_trusted_polish_model_on_disk(
    model_name: str | None, root: Path | None = None
) -> bool:
    return trusted_polish_model_path(model_name, root).is_file()


def _local_selector_for_path(path: Path, base: Path) -> str:
    return f"{LOCAL_POLISH_PREFIX}{path.relative_to(base).as_posix()}"


def _trusted_local_entry(
    model: TrustedPolishModel, base: Path
) -> LocalPolishModel | None:
    path = model.local_path(base)
    if not path.is_file():
        return None
    return LocalPolishModel(
        name=model.model_id,
        path=path,
        size_bytes=path.stat().st_size,
        trusted_id=model.model_id,
        repo_id=model.repo_id,
        filename=model.filename,
        description=model.description,
    )


def list_local_polish_models(root: Path | None = None) -> list[LocalPolishModel]:
    base = Path(root) if root is not None else polish_models_dir()
    if not base.is_dir():
        return []

    out: list[LocalPolishModel] = []
    trusted_paths: set[Path] = set()
    for model in list_available_polish_models():
        entry = _trusted_local_entry(model, base)
        if entry is not None:
            out.append(entry)
            trusted_paths.add(entry.path.resolve())

    for path in sorted(base.rglob("*.gguf")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in trusted_paths:
            continue
        rel = path.relative_to(base).as_posix()
        out.append(
            LocalPolishModel(
                name=_local_selector_for_path(path, base),
                path=resolved,
                size_bytes=path.stat().st_size,
                filename=rel,
                description="Local custom GGUF",
            )
        )
    return out


def list_installed_trusted_polish_models(
    root: Path | None = None,
) -> list[LocalPolishModel]:
    return [model for model in list_local_polish_models(root) if model.is_trusted]


def list_installed_custom_polish_models(
    root: Path | None = None,
) -> list[LocalPolishModel]:
    return [model for model in list_local_polish_models(root) if not model.is_trusted]


def _installed_by_selector(root: Path | None = None) -> dict[str, LocalPolishModel]:
    return {m.name: m for m in list_local_polish_models(root)}


def _canonical_local_selector(requested: str, root: Path | None = None) -> str | None:
    models = list_local_polish_models(root)
    by_name = {m.name: m for m in models}
    if requested in by_name:
        return requested

    stripped = requested
    if stripped.startswith(LOCAL_POLISH_PREFIX):
        stripped = stripped[len(LOCAL_POLISH_PREFIX) :]
    matches: list[LocalPolishModel] = []
    for model in models:
        if stripped in (model.filename, Path(model.path).name):
            matches.append(model)
    if len(matches) == 1:
        return matches[0].name
    if len(matches) > 1:
        opts = ", ".join(model.name for model in matches)
        raise ValueError(
            f"Polish model {requested!r} is ambiguous. Use one of: {opts}, copy."
        )
    return None


def validate_polish_model_name(model_name: str | None, root: Path | None = None) -> str:
    requested = (model_name or POLISH_DEFAULT_MODEL).strip()
    if requested in {"", POLISH_DEFAULT_MODEL}:
        return POLISH_DEFAULT_MODEL
    if requested in TRUSTED_POLISH_MODELS:
        return requested

    for model in list_available_polish_models():
        if requested == model.filename:
            return model.model_id

    selector = _canonical_local_selector(requested, root)
    if selector is not None:
        return selector

    raise ValueError(
        f"Unknown polish model {requested!r}. "
        f"Use one of: {TRUSTED_POLISH_MODEL_HELP}, or `local:<relative.gguf>` for a custom installed GGUF, copy."
    )


def resolve_polish_model(
    model_name: str | None, root: Path | None = None
) -> LocalPolishModel:
    requested = validate_polish_model_name(model_name, root)
    if requested == POLISH_DEFAULT_MODEL:
        default_entry = _trusted_local_entry(
            trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID),
            Path(root) if root is not None else polish_models_dir(),
        )
        if default_entry is not None:
            return default_entry
        raise ValueError(
            f"Registry default polish model {DEFAULT_TRUSTED_POLISH_MODEL_ID!r} is not installed under models/polish. "
            f"Trusted ids: {TRUSTED_POLISH_MODEL_HELP}. "
            "Use `/models polish use <id>` or `voxium models polish pull <id>`, copy."
        )

    if requested in TRUSTED_POLISH_MODELS:
        entry = _trusted_local_entry(
            trusted_polish_model(requested),
            Path(root) if root is not None else polish_models_dir(),
        )
        if entry is not None:
            return entry
        raise ValueError(
            f"Polish model {requested!r} is not installed under models/polish. "
            f"Use `/models polish use {requested}` or `voxium models polish pull {requested}`, copy."
        )

    by_name = _installed_by_selector(root)
    if requested in by_name:
        return by_name[requested]

    raise ValueError(
        f"Polish model {requested!r} is not available under models/polish. "
        "Use `/models polish list` to inspect trusted and local options, copy."
    )


POLISH_MODEL_NAMES = TRUSTED_POLISH_MODEL_NAMES
POLISH_MODEL_HELP = TRUSTED_POLISH_MODEL_HELP

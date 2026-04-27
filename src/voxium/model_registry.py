

from __future__ import annotations

TRUSTED_MODEL_NAMESPACE = "Systran"
DEFAULT_MODEL_NAME = "base"

TRUSTED_MODELS: dict[str, dict[str, str]] = {
    "tiny.en": {
        "repo": "Systran/faster-whisper-tiny.en",
        "description": "Fastest English-only model",
        "vram": "~1GB",
    },
    "tiny": {
        "repo": "Systran/faster-whisper-tiny",
        "description": "Fastest multilingual model",
        "vram": "~1GB",
    },
    "base.en": {
        "repo": "Systran/faster-whisper-base.en",
        "description": "Small English-only model",
        "vram": "~1GB",
    },
    "base": {
        "repo": "Systran/faster-whisper-base",
        "description": "Recommended multilingual default",
        "vram": "~1GB",
    },
    "small.en": {
        "repo": "Systran/faster-whisper-small.en",
        "description": "Better English-only accuracy",
        "vram": "~2GB",
    },
    "small": {
        "repo": "Systran/faster-whisper-small",
        "description": "Better multilingual accuracy",
        "vram": "~2GB",
    },
    "medium.en": {
        "repo": "Systran/faster-whisper-medium.en",
        "description": "High English-only accuracy",
        "vram": "~5GB",
    },
    "medium": {
        "repo": "Systran/faster-whisper-medium",
        "description": "High multilingual accuracy",
        "vram": "~5GB",
    },
    "large-v1": {
        "repo": "Systran/faster-whisper-large-v1",
        "description": "Legacy large multilingual model",
        "vram": "~10GB",
    },
    "large-v2": {
        "repo": "Systran/faster-whisper-large-v2",
        "description": "Large multilingual v2 model",
        "vram": "~10GB",
    },
    "large-v3": {
        "repo": "Systran/faster-whisper-large-v3",
        "description": "Latest large multilingual model",
        "vram": "~10GB",
    },
    "large": {
        "repo": "Systran/faster-whisper-large-v3",
        "description": "Alias for large-v3",
        "vram": "~10GB",
    },
    "distil-small.en": {
        "repo": "Systran/faster-distil-whisper-small.en",
        "description": "Distilled English-only small model",
        "vram": "~2GB",
    },
    "distil-medium.en": {
        "repo": "Systran/faster-distil-whisper-medium.en",
        "description": "Distilled English-only medium model",
        "vram": "~5GB",
    },
    "distil-large-v2": {
        "repo": "Systran/faster-distil-whisper-large-v2",
        "description": "Distilled large v2 model",
        "vram": "~6GB",
    },
    "distil-large-v3": {
        "repo": "Systran/faster-distil-whisper-large-v3",
        "description": "Distilled large v3 model",
        "vram": "~6GB",
    },
}

TRUSTED_MODEL_NAMES = tuple(TRUSTED_MODELS)
TRUSTED_MODEL_HELP = ", ".join(TRUSTED_MODEL_NAMES)

def validate_model_name(model_name: str | None) -> str:

    model = (model_name or DEFAULT_MODEL_NAME).strip()
    if model not in TRUSTED_MODELS:
        raise ValueError(
            f"Voxium: unsupported model '{model}'. "
            f"Allowed ids: {TRUSTED_MODEL_HELP}. "
            f"Only {TRUSTED_MODEL_NAMESPACE} faster-whisper models are permitted."
        )
    return model

def resolve_model_repo(model_name: str | None) -> str:

    return TRUSTED_MODELS[validate_model_name(model_name)]["repo"]

"""
Central config: all env-based settings in one place, no hardcoding in logic.
"""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_list(name: str, default: list[str], sep: str = ",") -> list[str]:
    v = os.environ.get(name)
    if not v:
        return default
    return [s.strip() for s in v.split(sep) if s.strip()]


# --- API / Provider ---
def openai_base_url() -> str:
    return _env("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")


def openai_api_key() -> str:
    return _env("OPENAI_API_KEY", "ollama")


# --- Model ---
def shellgeist_model() -> str:
    """Preferred model; use a 7B variant for speed when only small models are available."""
    return _env("SHELLGEIST_MODEL", "qwen2.5-coder:7b")


def shellgeist_model_fallback_keywords() -> list[str]:
    return _env_list("SHELLGEIST_MODEL_FALLBACK_KEYWORDS", ["coder", "qwen", "llama", "mistral"])


# --- Timeouts ---
def http_timeout() -> int:
    return _env_int("SHELLGEIST_HTTP_TIMEOUT", 300)


def models_list_timeout() -> int:
    return _env_int("SHELLGEIST_MODELS_LIST_TIMEOUT", 5)


def models_probe_timeout() -> int:
    return _env_int("SHELLGEIST_MODELS_PROBE_TIMEOUT", 1)

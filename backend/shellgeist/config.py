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


def stream_idle_timeout() -> int:
    """Max seconds to wait for the next LLM stream chunk before aborting (avoids hanging 'thinking')."""
    return _env_int("SHELLGEIST_STREAM_IDLE_TIMEOUT", 90)


def models_list_timeout() -> int:
    return _env_int("SHELLGEIST_MODELS_LIST_TIMEOUT", 5)


def models_probe_timeout() -> int:
    return _env_int("SHELLGEIST_MODELS_PROBE_TIMEOUT", 1)


# --- Debug ---
def debug_enabled() -> bool:
    """True when ``SHELLGEIST_DEBUG`` is set to a truthy value."""
    v = str(_env("SHELLGEIST_DEBUG", "")).strip().lower()
    return v in {"1", "true", "yes", "on", "debug"}


# --- Paths (cache, socket, DB) ---
def cache_dir() -> str:
    """Directory for ShellGeist cache (e.g. history DB). Override with SHELLGEIST_CACHE_DIR."""
    return os.path.expanduser(_env("SHELLGEIST_CACHE_DIR", "~/.cache/shellgeist"))


def socket_path() -> str:
    """Unix socket path for the daemon. Override with SHELLGEIST_SOCKET."""
    return os.path.expanduser(_env("SHELLGEIST_SOCKET", "~/.cache/shellgeist.sock"))


def history_db_path() -> str:
    """SQLite path for session history."""
    return os.path.join(cache_dir(), "history.db")


# Re-export for external consumers that need env_int
env_int = _env_int

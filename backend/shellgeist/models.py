from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class _Msg:
    content: str


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Resp:
    choices: list[_Choice]


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


class _ChatCompletions:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def create(self, *, model: str, messages: list[dict[str, str]]) -> _Resp:
        url = f"{self.base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "stream": False}

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        timeout_s = _env_int("SHELLGEIST_HTTP_TIMEOUT", 120)

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                raw = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            # IMPORTANT: capture response body for debugging (ollama gives details)
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise RuntimeError(f"http_error: status={e.code} reason={e.reason} body={body[:2000]}")
        except Exception as e:
            raise RuntimeError(f"http_error: {e}")

        try:
            data = json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"bad_json_response: {e} raw={raw[:2000]}")

        try:
            content = (data["choices"][0]["message"]["content"] or "")
        except Exception:
            raise RuntimeError(f"bad_openai_schema raw={raw[:2000]}")

        return _Resp(choices=[_Choice(message=_Msg(content=content))])


class _Chat:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.completions = _ChatCompletions(base_url, api_key)


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.chat = _Chat(base_url, api_key)


def get_client(kind: str) -> tuple[OpenAICompatClient, str]:
    """
    kind: "fast" | "smart" (extensible)
    Uses OPENAI_BASE_URL + OPENAI_API_KEY.
    Works with Ollama's OpenAI-compatible server.
    """
    base_url = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    api_key = os.environ.get("OPENAI_API_KEY", "ollama")

    # Defaults aligned with your installed models (ollama list)
    default_fast = "deepseek-coder:6.7b"
    default_smart = "deepseek-coder-v2:16b-lite-instruct-q4_K_M"

    if kind == "fast":
        model = os.environ.get("SHELLGEIST_MODEL_FAST", default_fast)
    else:
        model = os.environ.get("SHELLGEIST_MODEL_SMART", default_smart)

    return OpenAICompatClient(base_url=base_url, api_key=api_key), model

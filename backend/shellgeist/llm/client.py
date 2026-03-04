"""LLM client discovery: auto-detect Ollama, OpenAI-compatible, and cloud providers."""
from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from shellgeist.config import (
    debug_enabled as _debug_enabled,
)
from shellgeist.config import (
    http_timeout,
    models_list_timeout,
    models_probe_timeout,
    openai_api_key,
    openai_base_url,
    shellgeist_model,
    shellgeist_model_fallback_keywords,
)


@dataclass
class _ToolCall:
    id: str
    type: str
    function: dict[str, Any]


@dataclass
class _Msg:
    content: str | None
    role: str = "assistant"
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Resp:
    choices: list[_Choice]


class _ChatCompletions:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def create(self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, tool_choice: str | None = None) -> _Resp:
        url = f"{self.base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        timeout_s = http_timeout()

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                raw = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise RuntimeError(f"Ollama/OpenAI Error (HTTP {e.code}): {e.reason}\nDetails: {body[:2000]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection Failed to AI Provider ({self.base_url}). Is your model server (Ollama) running?\nError: {e.reason}")
        except Exception as e:
            err_msg = str(e)
            if "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
                raise RuntimeError(
                    f"AI Provider request timed out (limit: {timeout_s}s). "
                    f"For slow/large models set SHELLGEIST_HTTP_TIMEOUT to a higher value (e.g. 600). Original: {e}"
                )
            raise RuntimeError(f"Unexpected Error calling AI Provider: {e}")

        try:
            data = json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"bad_json_response: {e} raw={raw[:2000]}")

        try:
            msg_data = data["choices"][0]["message"]
            tool_calls = None
            if "tool_calls" in msg_data and msg_data["tool_calls"]:
                tool_calls = [
                    _ToolCall(id=tc["id"], type=tc["type"], function=tc["function"])
                    for tc in msg_data["tool_calls"]
                ]

            msg = _Msg(
                content=msg_data.get("content"),
                role=msg_data.get("role", "assistant"),
                tool_calls=tool_calls
            )
            return _Resp(choices=[_Choice(message=msg)])
        except Exception as e:
            raise RuntimeError(f"bad_openai_schema: {e} raw={raw[:2000]}")

    async def stream(self, *, model: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Stream completion chunks. Yields content deltas."""
        timeout_s = http_timeout()
        async for chunk in create_stream(
            model=model,
            messages=messages,
            timeout_s=timeout_s,
            base_url=self.base_url,
            api_key=self.api_key,
        ):
            yield chunk


def _stream_reader(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: int,
    out_queue: queue.Queue[Any],
) -> None:
    """Runs in a thread: reads NDJSON stream and puts (chunk_str, done) into out_queue. Puts None when done."""
    import sys
    dbg = _debug_enabled()
    def _log(m: str) -> None:
        if not dbg:
            return
        sys.stderr.write(f"DEBUG [stream_thread]: {m}\n")
        sys.stderr.flush()

    _log(f"Starting request to {url}")
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            _log("Response received, reading lines...")
            for line in r:
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                # Handle SSE (Server-Sent Events) for /v1 compat
                if line_str.startswith("data: "):
                    line_str = line_str[6:].strip()

                if line_str == "[DONE]":
                    break

                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    continue
                # OpenAI compat: choices[0].delta.content
                delta = data.get("choices", [{}])[0].get("delta", {}) if data.get("choices") else {}
                content = delta.get("content") or ""
                # Ollama native /api/chat: message.content
                if not content and "message" in data:
                    msg = data.get("message") or {}
                    content = msg.get("content") or ""
                # Ollama native generate: "response" field
                if not content and "response" in data:
                    content = data["response"] or ""
                if content:
                    out_queue.put(content)
                done = data.get("done") is True or delta.get("finish_reason") or (data.get("choices", [{}])[0].get("finish_reason"))
                if done:
                    break
        out_queue.put(None)
    except Exception as e:
        out_queue.put(e)


async def create_stream(
    *,
    model: str,
    messages: list[dict[str, Any]],
    timeout_s: int,
    base_url: str,
    api_key: str,
) -> AsyncIterator[str]:
    """Stream completion chunks. Yields content deltas; caller accumulates for full message."""
    import asyncio
    import sys
    dbg = _debug_enabled()

    def _log(m: str) -> None:
        if not dbg:
            return
        sys.stderr.write(f"DEBUG [create_stream]: {m}\n")
        sys.stderr.flush()

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    out_queue: queue.Queue[Any] = queue.Queue()
    loop = asyncio.get_event_loop()
    thread = threading.Thread(
        target=_stream_reader,
        args=(url, payload, headers, timeout_s, out_queue),
        daemon=True,
    )
    thread.start()

    _log("Stream thread started, waiting for first chunk...")
    idle_timeout_s = max(30, timeout_s)
    while True:
        try:
            item = await loop.run_in_executor(None, lambda: out_queue.get(timeout=idle_timeout_s))
        except queue.Empty:
            _log(f"Timeout: No chunks from Ollama for {idle_timeout_s}s. Aborting.")
            yield (
                f"ERROR: AI Provider (Ollama) timed out after {idle_timeout_s}s. "
                "The model might be too large for your RAM or loading slowly."
            )
            break

        if item is None:
            _log("Stream finished normally")
            break
        if isinstance(item, Exception):
            _log(f"Stream thread error: {item}")
            yield f"ERROR: Stream failed: {item}"
            break
        yield item


class _Chat:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.completions = _ChatCompletions(base_url, api_key)


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.chat = _Chat(base_url, api_key)


def list_local_models(base_url: str) -> list[str]:
    """List available models from the provider."""
    url = f"{base_url.rstrip('/')}/models"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=models_list_timeout()) as r:
            data = json.loads(r.read().decode("utf-8"))
            return [m["id"] for m in data.get("data", [])]
    except Exception:
        return []


def get_client(kind: str = "smart") -> tuple[OpenAICompatClient, str]:
    """
    Returns the configured AI client and model.
    Implements discovery and fallbacks to avoid crashes when the preferred model is missing.
    """
    base_url = openai_base_url()
    api_key = openai_api_key()
    preferred = shellgeist_model()
    fallback_kw = shellgeist_model_fallback_keywords()

    available = list_local_models(base_url)

    if not available:
        try:
            req = urllib.request.Request(f"{base_url.rstrip('/')}/models")
            with urllib.request.urlopen(req, timeout=models_probe_timeout()) as _:
                raise RuntimeError(
                    f"Ollama server is running but NO models are downloaded.\n"
                    f"Please run 'ollama pull {preferred}' or another model first."
                )
        except urllib.error.URLError:
            # Server is down, let the normal error handler handle it later
            # or return preferred and it will fail with "Connection Refused"
            return OpenAICompatClient(base_url=base_url, api_key=api_key), preferred
        except RuntimeError as e:
            raise e
        except Exception:
            return OpenAICompatClient(base_url=base_url, api_key=api_key), preferred

    # 1. Exact match
    if preferred in available:
        return OpenAICompatClient(base_url=base_url, api_key=api_key), preferred

    # 2. Tag match (e.g. user has qwen2.5-coder but env asks for qwen2.5-coder:32b)
    base_pref = preferred.split(":")[0]
    for m in available:
        if m.startswith(base_pref) or base_pref in m:
            return OpenAICompatClient(base_url=base_url, api_key=api_key), m

    # 3. Fallback: first model matching any configured keyword
    for kw in fallback_kw:
        for m in available:
            if kw.lower() in m.lower():
                return OpenAICompatClient(base_url=base_url, api_key=api_key), m

    # 4. Last resort: first available
    return OpenAICompatClient(base_url=base_url, api_key=api_key), available[0]

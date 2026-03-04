"""LLM streaming with automatic retry on transient failures."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from shellgeist.safety.retry import classify_result_payload


async def run_llm_stream_with_retry(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    retry_engine: Any,
    telemetry: Any,
    log_retry: Callable[[str], Awaitable[None]],
    debug_log: Callable[[str], None] | None = None,
) -> tuple[str | None, Any]:
    def _dbg(message: str) -> None:
        if debug_log is not None:
            debug_log(message)

    async def _collect_stream_once(_attempt: int) -> str:
        content_parts: list[str] = []
        _dbg("Requesting stream...")
        async for chunk in client.chat.completions.stream(
            model=model,
            messages=messages,
        ):
            delta = str(chunk)
            if delta.startswith("ERROR:"):
                raise RuntimeError(delta)
            content_parts.append(delta)
            _dbg(f"Chunk: {len(delta)} chars")
        _dbg("Stream finished")
        return "".join(content_parts)

    async def _on_llm_retry(attempt: int, error_class: str, reason: str, delay_ms: int, _last: Any | None) -> None:
        await telemetry.emit_retry_status(
            "llm",
            attempt=attempt,
            error_class=error_class,
            reason=reason,
            delay_ms=delay_ms,
        )
        await log_retry(
            f"Retry LLM stream (attempt {attempt + 1}) in {delay_ms}ms [{error_class}] {reason}"
        )

    result: tuple[str | None, Any] = await retry_engine.run_async(
        key="llm_stream",
        operation=_collect_stream_once,
        classify_result=lambda result: classify_result_payload(result),
        on_retry=_on_llm_retry,
    )
    return result

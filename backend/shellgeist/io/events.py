"""UI event emitter: streams v5 execution events to connected clients.

Only v5 ``execution_event`` frames are sent.  Legacy ``log`` / ``status``
frames were previously duplicated alongside the v5 events, causing the
frontend to render every message twice when both handlers fired.
"""
from __future__ import annotations

from typing import Any

from shellgeist.io.transport import safe_drain, send_json

_LOG_TYPE_TO_CHANNEL: dict[str, str] = {
    "thought": "reasoning",
    "assistant_chunk": "response",
    "assistant": "response",
    "action": "tool_call",
    "observation": "tool_result",
    "error": "error",
    "info": "status",
}


def channel_from_log_type(log_type: str) -> str:
    return _LOG_TYPE_TO_CHANNEL.get(log_type, "status")


class UIEventEmitter:
    def __init__(self, writer: Any | None) -> None:
        self.writer = writer

    async def emit_execution_event(
        self,
        channel: str,
        content: str = "",
        *,
        phase: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if not self.writer:
            return
        payload: dict[str, Any] = {
            "type": "execution_event",
            "event": {
                "version": "v1",
                "channel": channel,
                "content": content,
            },
        }
        if phase:
            payload["event"]["phase"] = phase
        if meta:
            payload["event"]["meta"] = meta
        send_json(self.writer, payload)
        await safe_drain(self.writer)

    async def log(self, text: str, type: str = "info", meta: dict[str, Any] | None = None) -> None:
        """Send a single v5 execution_event (no legacy ``log`` frame)."""
        await self.emit_execution_event(channel_from_log_type(type), text, meta=meta)

    async def status(self, thinking: bool) -> None:
        """Send a single v5 execution_event (no legacy ``status`` frame)."""
        await self.emit_execution_event(
            "status",
            "",
            phase="thinking" if thinking else "idle",
            meta={"thinking": thinking},
        )

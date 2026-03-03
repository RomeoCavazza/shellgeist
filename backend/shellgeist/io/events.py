"""UI event emitter: streams execution events to connected clients."""
from __future__ import annotations

from typing import Any

from shellgeist.io.transport import safe_drain, send_json


def channel_from_log_type(log_type: str) -> str:
    mapping = {
        "thought": "reasoning",
        "assistant_chunk": "response",
        "assistant": "response",
        "action": "tool_call",
        "observation": "tool_result",
        "error": "error",
        "info": "status",
    }
    return mapping.get(log_type, "status")


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
        await self.emit_execution_event(channel_from_log_type(type), text, meta=meta)
        if self.writer:
            send_json(self.writer, {"type": "log", "log_type": type, "content": text})
            await safe_drain(self.writer)

    async def status(self, thinking: bool) -> None:
        await self.emit_execution_event(
            "status",
            "",
            phase="thinking" if thinking else "idle",
            meta={"thinking": thinking},
        )
        if self.writer:
            send_json(self.writer, {"type": "status", "thinking": thinking})
            await safe_drain(self.writer)

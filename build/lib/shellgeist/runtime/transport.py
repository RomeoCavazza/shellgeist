"""Low-level transport and UI event emission."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from shellgeist.agent.signals import UIEvent, UIEventFrame


def send_json(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    """Write a JSON-newline frame to *writer* (sync, no drain)."""
    writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


async def safe_drain(writer: asyncio.StreamWriter) -> bool:
    """Drain the writer buffer; return *False* on client disconnect."""
    try:
        await writer.drain()
        return True
    except (ConnectionResetError, BrokenPipeError):
        return False


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
    def __init__(self, writer: asyncio.StreamWriter | None, reader: asyncio.StreamReader | None = None) -> None:
        self.writer = writer
        self.reader = reader

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
        
        event_frame = UIEventFrame(
            channel=channel,
            content=content,
            phase=phase,
            meta=meta or {}
        )
        payload = UIEvent(event=event_frame)
        
        send_json(self.writer, payload.model_dump())
        await safe_drain(self.writer)

    async def log(self, text: str, type: str = "info", meta: dict[str, Any] | None = None) -> None:
        """Send a single v5 execution_event."""
        await self.emit_execution_event(channel_from_log_type(type), text, meta=meta)

    async def status(self, thinking: bool) -> None:
        """Send a single v5 execution_event status."""
        await self.emit_execution_event(
            "status",
            "",
            phase="thinking" if thinking else "idle",
            meta={"thinking": thinking},
        )

    async def request_approval(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Send an approval_request event and wait for response."""
        if not self.writer or not self.reader:
            return True

        await self.emit_execution_event(
            "approval_request",
            f"{tool_name}",
            phase="tool_use",
            meta={"tool": tool_name, "args": args},
        )

        try:
            line = await self.reader.readline()
            if not line:
                return True
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("cmd") == "approval_response":
                return bool(obj.get("approved", False))
        except Exception:
            pass
        return True

    async def request_review(
        self,
        file: str,
        old_content: str,
        new_content: str,
    ) -> str | None:
        """Send a review_pending event and wait for decision."""
        if not self.writer or not self.reader:
            return None

        await self.emit_execution_event(
            "review_pending",
            file,
            phase="review",
            meta={
                "file": file,
                "old_content": old_content,
                "new_content": new_content,
            },
        )

        try:
            line = await self.reader.readline()
            if not line:
                return None
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("cmd") == "review_decision":
                if obj.get("approved"):
                    content = obj.get("content")
                    return content if isinstance(content, str) else None
        except Exception:
            pass
        return None

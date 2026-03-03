"""Low-level async stream transport: JSON-line writing and safe draining."""
from __future__ import annotations

import asyncio
import json
from typing import Any


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

"""LLM & internal message models."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: dict[str, Any]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # For role="tool"

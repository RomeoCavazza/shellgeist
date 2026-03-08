"""UI event models: streaming feedback to the client."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class UIEventFrame(BaseModel):
    version: str = "v1"
    channel: str
    content: str = ""
    phase: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class UIEvent(BaseModel):
    type: Literal["execution_event"] = "execution_event"
    event: UIEventFrame

"""Centralized data models for ShellGeist (RPC, UI, LLM, Tools)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# RPC Request Models (formerly in protocol/models.py)
# ---------------------------------------------------------------------------

class SGBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str | None = None


class PingRequest(SGBaseRequest):
    cmd: Literal["ping"]


class GitStatusRequest(SGBaseRequest):
    cmd: Literal["git_status"]
    root: str | None = None


class GitAddRequest(SGBaseRequest):
    cmd: Literal["git_add"]
    root: str | None = None
    file: str


class GitRestoreRequest(SGBaseRequest):
    cmd: Literal["git_restore"]
    root: str | None = None
    file: str


class PlanRequest(SGBaseRequest):
    cmd: Literal["plan"]
    root: str | None = None
    goal: str


class EditRequest(SGBaseRequest):
    cmd: Literal["edit"]
    root: str | None = None
    file: str
    instruction: str


class EditApplyRequest(SGBaseRequest):
    cmd: Literal["edit_apply"]
    root: str | None = None
    file: str
    patch: str
    instruction: str | None = None
    stage: bool = False
    backup: bool = True


class EditApplyFullRequest(SGBaseRequest):
    cmd: Literal["edit_apply_full"]
    root: str | None = None
    file: str
    text: str
    instruction: str | None = None
    stage: bool = False
    backup: bool = True


class AgentTaskRequest(SGBaseRequest):
    cmd: Literal["agent_task"]
    goal: str
    root: str | None = None
    session_id: str = "default"
    mode: Literal["auto", "review"] = "auto"


class ShellRequest(SGBaseRequest):
    cmd: Literal["shell"]
    root: str | None = None
    task: str


class ChatRequest(SGBaseRequest):
    cmd: Literal["chat"]
    text: str


class HistoryRequest(SGBaseRequest):
    cmd: Literal["get_history"]
    session_id: str = "default"


SGRequest = (
    PingRequest
    | GitStatusRequest
    | GitAddRequest
    | GitRestoreRequest
    | PlanRequest
    | EditRequest
    | EditApplyRequest
    | EditApplyFullRequest
    | AgentTaskRequest
    | ShellRequest
    | ChatRequest
    | HistoryRequest
)


# ---------------------------------------------------------------------------
# UI Event Models (formerly implicit in io/events.py)
# ---------------------------------------------------------------------------

class UIEventFrame(BaseModel):
    version: str = "v1"
    channel: str
    content: str = ""
    phase: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class UIEvent(BaseModel):
    type: Literal["execution_event"] = "execution_event"
    event: UIEventFrame


# ---------------------------------------------------------------------------
# Agent Result Models (merging protocol/models.py and io/results.py)
# ---------------------------------------------------------------------------

class SGResult(BaseModel):
    ok: bool
    status: Literal["completed", "failed", "stopped", "running"] = "completed"
    type: str = "result"
    error: str | None = None
    detail: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    response: str | None = None
    retry: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# LLM & Internal Message Models
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: dict[str, Any]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # For role="tool"

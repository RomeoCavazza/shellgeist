"""Pydantic models for RPC request/response validation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SGBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str | None = None


class PingRequest(SGBaseRequest):
    cmd: Literal["ping"]


class GitStatusRequest(SGBaseRequest):
    cmd: Literal["git_status"]
    root: str


class GitAddRequest(SGBaseRequest):
    cmd: Literal["git_add"]
    root: str
    file: str


class GitRestoreRequest(SGBaseRequest):
    cmd: Literal["git_restore"]
    root: str
    file: str


class PlanRequest(SGBaseRequest):
    cmd: Literal["plan"]
    root: str
    goal: str


class EditRequest(SGBaseRequest):
    cmd: Literal["edit"]
    root: str
    file: str
    instruction: str


class EditApplyRequest(SGBaseRequest):
    cmd: Literal["edit_apply"]
    root: str
    file: str
    patch: str
    instruction: str | None = None
    stage: bool = False
    backup: bool = True


class EditApplyFullRequest(SGBaseRequest):
    cmd: Literal["edit_apply_full"]
    root: str
    file: str
    text: str
    instruction: str | None = None
    stage: bool = False
    backup: bool = True


class AgentTaskRequest(SGBaseRequest):
    cmd: Literal["agent_task"]
    goal: str
    root: str
    session_id: str = "default"
    mode: Literal["auto", "review"] = "auto"


class ShellRequest(SGBaseRequest):
    cmd: Literal["shell"]
    root: str
    task: str


class ChatRequest(SGBaseRequest):
    cmd: Literal["chat"]
    text: str


class HistoryRequest(SGBaseRequest):
    cmd: Literal["get_history"]
    session_id: str = "default"

# Union for all supported requests
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


class SGResult(BaseModel):
    ok: bool
    type: str = "result"
    error: str | None = None
    detail: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

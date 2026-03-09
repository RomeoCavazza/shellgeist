"""RPC protocol models: requests and results."""
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
    root: str | None = None


class GitAddRequest(SGBaseRequest):
    cmd: Literal["git_add"]
    root: str | None = None
    file: str


class GitRestoreRequest(SGBaseRequest):
    cmd: Literal["git_restore"]
    root: str | None = None
    file: str


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
    fresh_conversation: bool = False


class ResetSessionRequest(SGBaseRequest):
    cmd: Literal["reset_session"]
    session_id: str = "default"


class HistoryRequest(SGBaseRequest):
    cmd: Literal["get_history"]
    session_id: str = "default"


SGRequest = (
    PingRequest
    | GitStatusRequest
    | GitAddRequest
    | GitRestoreRequest
    | EditRequest
    | EditApplyRequest
    | EditApplyFullRequest
    | AgentTaskRequest
    | ResetSessionRequest
    | HistoryRequest
)


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

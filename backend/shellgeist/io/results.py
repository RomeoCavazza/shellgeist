"""Result builders for completed, failed, and stopped agent runs."""
from __future__ import annotations

from typing import Any


def completed_result(*, logs: list[str], response: str | None = None, retry: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "status": "completed",
        "logs": logs,
    }
    if response is not None:
        payload["response"] = response
    if retry is not None:
        payload["retry"] = retry
    return payload


def failed_result(
    *,
    error: str,
    status: str = "failed",
    detail: str | None = None,
    logs: list[str] | None = None,
    response: str | None = None,
    retry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": error,
    }
    if status:
        payload["status"] = status
    if detail is not None:
        payload["detail"] = detail
    if logs is not None:
        payload["logs"] = logs
    if response is not None:
        payload["response"] = response
    if retry is not None:
        payload["retry"] = retry
    return payload


def stopped_result(*, logs: list[str], retry: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "status": "stopped",
        "logs": logs,
    }
    if retry is not None:
        payload["retry"] = retry
    return payload

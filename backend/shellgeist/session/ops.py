"""Session operations: load/save history, goal injection, context appending."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class HistoryStore:
    init_db: Callable[[], None]
    save_message: Callable[..., None]
    get_session_history: Callable[..., list[dict[str, Any]]]


def _default_store() -> HistoryStore:
    from shellgeist.session.store import get_session_history, init_db, save_message

    return HistoryStore(
        init_db=init_db,
        save_message=save_message,
        get_session_history=get_session_history,
    )


def initialize_history_db(*, store: HistoryStore | None = None) -> None:
    (store or _default_store()).init_db()


def load_recent_history(
    history: list[dict[str, Any]],
    *,
    session_id: str,
    max_recent: int = 40,
    store: HistoryStore | None = None,
) -> list[dict[str, Any]]:
    if len(history) > 1:
        return history

    backend = store or _default_store()
    past = backend.get_session_history(session_id)
    if not past:
        return history

    recent = [m for m in past if m.get("role") != "system"][-max_recent:]
    return history + recent


def append_user_goal_once(
    history: list[dict[str, Any]],
    *,
    session_id: str,
    goal: str,
    store: HistoryStore | None = None,
) -> bool:
    if history:
        last_msg = history[-1]
        if last_msg.get("role") == "user" and last_msg.get("content") == goal:
            return False

    history.append({"role": "user", "content": goal})
    (store or _default_store()).save_message(session_id, "user", goal)
    return True


def save_assistant_message(
    *,
    session_id: str,
    content: str,
    store: HistoryStore | None = None,
) -> None:
    (store or _default_store()).save_message(session_id, "assistant", content)


def append_context_observation(
    history: list[dict[str, Any]],
    *,
    session_id: str,
    content: str,
    store: HistoryStore | None = None,
) -> None:
    history.append({"role": "user", "content": content})
    (store or _default_store()).save_message(session_id, "user", content, log_type="context")

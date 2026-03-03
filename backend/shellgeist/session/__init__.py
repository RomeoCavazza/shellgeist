"""Session persistence: SQLite store, history operations, repair."""

from shellgeist.session.ops import (
    append_context_observation,
    append_user_goal_once,
    initialize_history_db,
    load_recent_history,
    save_assistant_message,
)
from shellgeist.session.repair import repair_conversation_history
from shellgeist.session.store import get_session_history, init_db, save_message

__all__ = [
    "init_db",
    "save_message",
    "get_session_history",
    "initialize_history_db",
    "load_recent_history",
    "append_user_goal_once",
    "save_assistant_message",
    "append_context_observation",
    "repair_conversation_history",
]

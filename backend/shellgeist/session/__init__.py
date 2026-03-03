"""Session persistence: SQLite store, history operations, repair."""

from shellgeist.session.store import init_db, save_message, get_session_history
from shellgeist.session.ops import (
    initialize_history_db,
    load_recent_history,
    append_user_goal_once,
    save_assistant_message,
    append_context_observation,
)
from shellgeist.session.repair import repair_conversation_history

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

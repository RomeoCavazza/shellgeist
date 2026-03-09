"""Session management: persistence, history loading, and repair."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from shellgeist.config import history_db_path

DB_PATH = history_db_path()

_DB_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# Database Persistence
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Initialize the SQLite history database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=_DB_TIMEOUT) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                log_type TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)


def save_message(session_id: str, role: str, content: str, log_type: str | None = None) -> None:
    """Persist a message to the database."""
    with sqlite3.connect(DB_PATH, timeout=_DB_TIMEOUT) as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, log_type) VALUES (?, ?, ?, ?)",
            (session_id, role, content, log_type)
        )


_MAX_HISTORY_CONTENT_CHARS = 3200  # truncate when loading from DB to avoid context overflow and "continuation" bloat


def _truncate_history_content(content: str, max_chars: int = _MAX_HISTORY_CONTENT_CHARS) -> str:
    if not content or len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + f"\n\n... [truncated, {len(content)} chars total]"


def get_session_history(session_id: str, for_ui: bool = False) -> list[dict[str, str]]:
    """Retrieve and format session history."""
    with sqlite3.connect(DB_PATH, timeout=_DB_TIMEOUT) as conn:
        cur = conn.cursor()
        if for_ui:
            cur.execute(
                "SELECT role, content, log_type FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
                (session_id,)
            )
        else:
            cur.execute(
                "SELECT role, content FROM messages WHERE session_id = ? AND role IN ('user', 'assistant', 'tool') AND (log_type IS NULL OR log_type != 'context') ORDER BY timestamp ASC",
                (session_id,)
            )
        rows = cur.fetchall()

    history = []
    for row in rows:
        role = row[0]
        content = row[1]
        log_type = row[2] if len(row) > 2 else None

        if role == "log" and for_ui:
            if log_type in ("thought", "action", "observation", "info", "context"):
                continue
            history.append({"role": log_type or "thought", "content": content})
            continue

        if role == "user" and for_ui and (log_type == "context" or content.strip().startswith("<tool_observation")):
            continue

        if role in ("assistant", "tool"):
            if for_ui:
                try:
                    json.loads(content)
                    continue
                except Exception:
                    history.append({"role": "assistant", "content": content})
                continue

            try:
                history.append(json.loads(content))
            except Exception:
                history.append({"role": role, "content": content})
        else:
            history.append({"role": role, "content": content})
    # When loading for agent (not for_ui), truncate very long messages so the model doesn't "continue" them
    if not for_ui:
        for m in history:
            c = m.get("content", "")
            if len(c) > _MAX_HISTORY_CONTENT_CHARS:
                m["content"] = _truncate_history_content(c)
    return history


# ---------------------------------------------------------------------------
# Session Operations
# ---------------------------------------------------------------------------

def load_recent_history(
    history: list[dict[str, Any]],
    *,
    session_id: str,
    max_recent: int = 18,
) -> list[dict[str, Any]]:
    """Inflate agent history from database. Keeps only last max_recent messages so the model
    responds to the current request instead of continuing an old conversation."""
    if len(history) > 1:
        return history

    past = get_session_history(session_id)
    if not past:
        return history

    recent = [m for m in past if m.get("role") != "system"][-max_recent:]
    return history + recent


def append_user_goal_once(
    history: list[dict[str, Any]],
    *,
    session_id: str,
    goal: str,
) -> bool:
    """Append the user's initial goal to history and save it once."""
    if history:
        last_msg = history[-1]
        if last_msg.get("role") == "user" and last_msg.get("content") == goal:
            return False

    history.append({"role": "user", "content": goal})
    save_message(session_id, "user", goal)
    return True


# ---------------------------------------------------------------------------
# Session Repair
# ---------------------------------------------------------------------------

@dataclass
class SessionRepairReport:
    input_count: int
    output_count: int
    dropped_count: int
    deduped_count: int
    normalized_count: int
    merged_count: int = 0

    def changed(self) -> bool:
        return (
            self.input_count != self.output_count
            or self.dropped_count > 0
            or self.deduped_count > 0
            or self.normalized_count > 0
            or self.merged_count > 0
        )


def repair_conversation_history(
    messages: list[dict[str, Any]],
    *,
    max_non_system: int = 80,
) -> tuple[list[dict[str, str]], SessionRepairReport]:
    """Sanitize, deduplicate, merge consecutive same-role, and prune chat history."""
    allowed_roles = {"system", "user", "assistant", "tool"}
    dropped_count = 0
    deduped_count = 0
    normalized_count = 0
    merged_count = 0
    repaired: list[dict[str, str]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            dropped_count += 1
            continue

        raw_role = str(msg.get("role") or "").strip().lower()
        if raw_role not in allowed_roles:
            dropped_count += 1
            continue

        content = msg.get("content")
        if not isinstance(content, str):
            normalized_count += 1
            content = json.dumps(content, ensure_ascii=False) if content else ""

        if raw_role != "system":
            content = content.strip()
            if not content:
                dropped_count += 1
                continue

        entry = {"role": raw_role, "content": content}

        # Dedup exact duplicates
        if repaired and repaired[-1] == entry:
            deduped_count += 1
            continue

        # Merge consecutive same-role messages (especially user-user)
        if repaired and repaired[-1]["role"] == raw_role and raw_role != "system":
            repaired[-1]["content"] += "\n\n" + content
            merged_count += 1
            continue

        repaired.append(entry)

    system_msgs = [m for m in repaired if m["role"] == "system"]
    non_system_msgs = [m for m in repaired if m["role"] != "system"]

    if len(non_system_msgs) > max_non_system:
        dropped_count += len(non_system_msgs) - max_non_system
        non_system_msgs = non_system_msgs[-max_non_system:]

    final_history = []
    if system_msgs:
        final_history.append(system_msgs[0])
    final_history.extend(non_system_msgs)

    report = SessionRepairReport(
        input_count=len(messages),
        output_count=len(final_history),
        dropped_count=dropped_count,
        deduped_count=deduped_count,
        normalized_count=normalized_count,
        merged_count=merged_count,
    )
    return final_history, report

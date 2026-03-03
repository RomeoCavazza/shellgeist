"""SQLite-backed session store: message persistence and retrieval."""
from __future__ import annotations

import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.expanduser("~/.cache/shellgeist/history.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            log_type TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_message(session_id, role, content, log_type=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (session_id, role, content, log_type) VALUES (?, ?, ?, ?)",
        (session_id, role, content, log_type)
    )
    conn.commit()
    conn.close()

def get_session_history(session_id, for_ui=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if for_ui:
        # For the UI, we want ALL messages, including logs and observations
        cur.execute(
            "SELECT role, content, log_type FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        )
    else:
        # For the LLM, we only want the conversation flow
        cur.execute(
            "SELECT role, content FROM messages WHERE session_id = ? AND role IN ('user', 'assistant', 'tool') ORDER BY timestamp ASC",
            (session_id,)
        )
    rows = cur.fetchall()
    conn.close()
    
    history = []
    for row in rows:
        role = row[0]
        content = row[1]
        log_type = row[2] if len(row) > 2 else None

        if role == "log" and for_ui:
            # Quietly skip internal logs for a clean UI
            if log_type in ("thought", "action", "observation", "info", "context"):
                continue
            history.append({"role": log_type or "thought", "content": content})
            continue
            
        if role == "user" and for_ui and (log_type == "context" or content.strip().startswith("<tool_observation")):
            continue

        if role in ("assistant", "tool"):
            if for_ui:
                # For UI, the final response is already captured by logs or processed separately
                # Let's see if we should show assistant messages.
                # If they were saved as logs (thought/action), we already handled them.
                # If it's a final response, it might not be a log.
                # Let's allow them if they are not JSON (i.e. simple text responses).
                try:
                    json.loads(content)
                    continue # Hide structured tool call messages from UI
                except Exception:
                    history.append({"role": "assistant", "content": content})
                continue 
            
            try:
                history.append(json.loads(content))
            except Exception:
                history.append({"role": role, "content": content})
        else:
            history.append({"role": role, "content": content})
    return history

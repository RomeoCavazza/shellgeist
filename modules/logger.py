# modules/logger.py
import json
import time
import os
from pathlib import Path


LOG_FILE_ENV = "AI_LAB_LOG"      # tu peux override le chemin via cette variable
DEFAULT_LOG = "ai_lab_events.jsonl"


def _get_log_path() -> Path:
    """
    Retourne le chemin du fichier de logs (par défaut ./ai_lab_events.jsonl).
    """
    root = Path(".").resolve()
    name = os.getenv(LOG_FILE_ENV, DEFAULT_LOG)
    return root / name


def log_event(event_type: str, **data) -> None:
    """
    Ajoute une ligne JSON dans le fichier de logs.

    event_type: "session_start", "chat", "shell_exec", "edit_proposal", "edit_apply",
                "shell_plan", "shell_choice", etc.
    data: champs additionnels (cmd, file, diff, etc.)
    """
    event = {
        "ts": time.time(),
        "type": event_type,
        **data,
    }

    try:
        path = _get_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # On ne casse jamais l'agent pour un problème de log.
        pass

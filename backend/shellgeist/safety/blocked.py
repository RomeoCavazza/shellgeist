"""Command blocklist: prevents dangerous shell commands from executing."""
from __future__ import annotations

import re

BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bpasswd\b",
    r"\bvisudo\b",
    r">>?/etc/sudoers",
    r"\buseradd\b",
    r"\buserdel\b",
    r":\(\)\{:\|:&\};:",  # fork bomb
    r"\bchown\s+-r\s+/\b",
    r"\bchmod\s+-r\s+7\d{2}\b",
    r"\bsudo\b",
    r"\bapt(-get)?\b",
    r"\byum\b",
    r"\bdnf\b",
    r"\bpacman\b",
    r"\bzypper\b",
    r"\bapk\s+add\b",
    r"\bnix-env\s+-i\b",
    r"\bnix\s+profile\s+install\b",
]


def _normalized_shell(cmd: str) -> str:
    return re.sub(r"\s+", " ", (cmd or "").strip().lower())


def is_blocked(cmd: str) -> bool:
    """
    Renvoie True si la commande contient un pattern jugé trop dangereux.
    """
    lower = _normalized_shell(cmd)
    if not lower:
        return False

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, lower):
            return True

    return False

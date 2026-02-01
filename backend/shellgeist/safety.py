# modules/safety.py

BLOCKED_PATTERNS = [
    "rm -rf",
    "mkfs",
    "dd if=",
    "passwd",
    "visudo",
    ">/etc/sudoers",
    ">>/etc/sudoers",
    "useradd ",
    "userdel ",
    ":(){:|:&};:",  # fork bomb
    "chown -R /",
    "chmod -R 7",
]


def is_blocked(cmd: str) -> bool:
    """
    Renvoie True si la commande contient un pattern jugé trop dangereux.
    """
    lower = cmd.lower()
    return any(p in lower for p in BLOCKED_PATTERNS)

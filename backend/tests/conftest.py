"""Shared test fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so ``import shellgeist`` works even
# when pytest is invoked from the repo root without ``pip install -e .``.
_BACKEND = str(Path(__file__).resolve().parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

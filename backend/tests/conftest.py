"""Shared fixtures for ShellGeist tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path so `import shellgeist` works.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    """Return a temporary project root directory."""
    (tmp_path / "README.md").write_text("# test project\n")
    return tmp_path

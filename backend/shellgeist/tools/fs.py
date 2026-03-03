"""Filesystem tools: read_file, list_directory, find_files."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from shellgeist.tools.base import registry


def _resolve_repo_path(root: str, rel: str) -> Path:
    root_path = Path(root).resolve()
    if not rel:
        raise ValueError("invalid_path")
    p = (root_path / rel).resolve()
    try:
        p.relative_to(root_path)
    except ValueError:
        raise ValueError("path_escape")
    return p


class ReadFileInput(BaseModel):
    path: str


class ListFilesInput(BaseModel):
    directory: str = "."


@registry.register(
    description="Read the contents of a file.",
    input_model=ReadFileInput
)
def read_file(path: str | None = None, root: str = "", file_path: str | None = None) -> str:
    target = (path or file_path or "").strip()
    p = _resolve_repo_path(root, target)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {target}")
    return p.read_text(encoding="utf-8", errors="replace")


class RepoMapInput(BaseModel):
    pass


@registry.register(
    description="Get a tree-like map of the entire repository.",
    input_model=RepoMapInput
)
def get_repo_map(root: str) -> str:
    """
    Returns a string representation of the file tree.
    """
    out = []
    p_root = Path(root)
    for p in sorted(p_root.rglob("*")):
        if any(part.startswith(".") for part in p.parts):
            continue
        rel = p.relative_to(p_root)
        depth = len(rel.parts) - 1
        indent = "  " * depth
        if p.is_dir():
            out.append(f"{indent}{rel.name}/")
        else:
            out.append(f"{indent}{rel.name}")
    return "\n".join(out)


class WriteFileInput(BaseModel):
    path: str
    content: str


@registry.register(
    description="Write content to a file. Overwrites if exists.",
    input_model=WriteFileInput
)
def write_file(path: str | None = None, content: str = "", root: str = "", file_path: str | None = None) -> str:
    target = (path or file_path or "").strip()
    p = _resolve_repo_path(root, target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Successfully wrote to {target}"


@registry.register(
    description="List files in a directory.",
    input_model=ListFilesInput
)
def list_files(directory: str, root: str) -> list[str]:
    p = _resolve_repo_path(root, directory)
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    items = []
    for entry in os.scandir(p):
        if entry.name.startswith("."):
            continue
        rel = os.path.relpath(entry.path, root)
        items.append(rel)
    return sorted(items)

"""Filesystem tools: read_file, write_file, list_files, find_files, get_repo_map."""
from typing import Any

import difflib
import fnmatch
import os
from pathlib import Path

from pydantic import BaseModel

from shellgeist.tools.base import registry
from shellgeist.runtime.paths import (
    DEFAULT_IGNORED_DIRS,
    read_repo_file,
    resolve_existing_repo_file,
    resolve_repo_path,
    workspace_relative_path,
)


from pydantic import BaseModel, ConfigDict

class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class ListFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory: str = "."
    recursive: bool = False
    depth: int = 3


@registry.register(
    description="Read the contents of a file.",
    input_model=ReadFileInput
)
def read_file(path: str | None = None, root: str = "", file_path: str | None = None, file: str | None = None, **kwargs: Any) -> str:
    target = (path or file or file_path or "").strip()
    try:
        resolution = resolve_existing_repo_file(Path(root), target, ignored_dirs=DEFAULT_IGNORED_DIRS)
        p = resolution.path
    except FileNotFoundError as exc:
        if target.startswith("/"):
            detail = f"{exc} Use a path relative to project root, e.g. README.md or backend/main.py."
            raise FileNotFoundError(detail) from None
        raise
    return read_repo_file(p)


class RepoMapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


@registry.register(
    description="Get a tree-like map of the entire repository.",
    input_model=RepoMapInput
)
def get_repo_map(root: str, **kwargs: Any) -> str:
    """
    Returns a string representation of the file tree.
    """
    p_root = Path(root).resolve()
    out = []
    for dirpath, dirnames, filenames in os.walk(p_root):
        # Prune hidden and ignored dirs in-place
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in DEFAULT_IGNORED_DIRS
        )
        rel_dir = os.path.relpath(dirpath, p_root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        indent = "  " * depth
        if rel_dir != ".":
            out.append(f"{indent}{os.path.basename(dirpath)}/")
        for f in sorted(filenames):
            if f.startswith("."):
                continue
            out.append(f"{indent}  {f}")
    return "\n".join(out)


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str


@registry.register(
    description="Write content to a file. Overwrites if exists. Content must be valid, runnable code (e.g. print('hello'), not just text). Returns a unified diff when modifying an existing file.",
    input_model=WriteFileInput
)
def write_file(path: str | None = None, content: str = "", root: str = "", file_path: str | None = None, file: str | None = None, **kwargs: Any) -> str:
    target = (path or file_path or file or "").strip()
    # Some models send absolute paths with double slash (e.g. "//home/..."); normalize so resolve_repo_path works as expected
    while target.startswith("//"):
        target = target[1:]
    p = resolve_repo_path(Path(root), target)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Normalize line endings so LLM \r\n or stray \r don't cause SyntaxError (e.g. "import time\r")
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    # Fix double-escaped newlines from LLMs (literal \n instead of real newlines)
    if "\n" not in content and "\\n" in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "")

    # Capture old content for diff
    old_text = ""
    existed = p.exists()
    if existed:
        try:
            old_text = read_repo_file(p)
        except Exception:
            pass

    # Skip write if file already has the exact same content
    if existed and old_text == content:
        return (
            f"NO_CHANGE: {target} already contains this exact content. "
            "Do NOT call write_file again with the same content. Proceed to the next step or say Status: DONE."
        )

    p.write_text(content, encoding="utf-8")

    msg = f"Successfully wrote to {target}"
    if existed and old_text != content:
        diff_lines = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
        ))
        if diff_lines:
            diff_str = "".join(diff_lines)[:2000]
            msg += f"\n\nDiff:\n{diff_str}"
    elif not existed:
        # New file: show a unified diff (all lines as +) so the sidebar can display a "Diff" card
        diff_lines = list(difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
        ))
        if diff_lines:
            diff_str = "".join(diff_lines)[:2000]
            msg += f" (new file)\n\nDiff:\n{diff_str}"
        else:
            msg += " (new file)"
    return msg


@registry.register(
    description="List files and directories. Set recursive=true to show the full tree. Set depth to limit recursion (default 3).",
    input_model=ListFilesInput
)
def list_files(directory: str = ".", root: str = "", recursive: bool = False, depth: int = 3, max_results: int = 100, **kwargs: Any) -> list[str]:
    root_path = Path(root).resolve()
    p = resolve_repo_path(root_path, directory)

    if not p.exists() or not p.is_dir():
        hint = f" Hint: try a relative path like '{Path(directory).name}' instead." if directory.startswith("/") else ""
        raise FileNotFoundError(f"Directory not found: {directory}.{hint}")

    items: list[str] = []

    if recursive:
        def _walk(dir_path: Path, current_depth: int) -> None:
            if current_depth > depth or len(items) >= max_results:
                return
            try:
                entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
            except PermissionError:
                return
            for entry in entries:
                if len(items) >= max_results:
                    break
                if entry.name.startswith(".") or entry.name in DEFAULT_IGNORED_DIRS:
                    continue
                rel = workspace_relative_path(root_path, Path(entry.path))
                if entry.is_dir():
                    items.append(rel + "/")
                    _walk(Path(entry.path), current_depth + 1)
                else:
                    items.append(rel)
        _walk(p, 1)
    else:
        for entry in os.scandir(p):
            if entry.name.startswith("."):
                continue
            rel = workspace_relative_path(root_path, Path(entry.path))
            items.append(rel + "/" if entry.is_dir() else rel)

    return sorted(items)


class FindFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str
    directory: str = "."


@registry.register(
    description=(
        "Search for files matching a glob pattern (e.g. '*.lua', 'dashboard*', '**/*.py') "
        "recursively from the given directory. Returns matching relative paths. "
        "Use this when you need to locate a file by name."
    ),
    input_model=FindFilesInput
)
def find_files(pattern: str, directory: str = ".", root: str = "", max_results: int = 50, **kwargs: Any) -> str | list[str]:
    root_path = Path(root).resolve()
    p = resolve_repo_path(root_path, directory)
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(p):
        # Prune ignored directories in-place
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in DEFAULT_IGNORED_DIRS
        ]
        for filename in filenames:
            if filename.startswith("."):
                continue
            rel = workspace_relative_path(root_path, Path(dirpath) / filename)
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(rel, pattern):
                results.append(rel)
                if len(results) >= max_results:
                    return sorted(results)

    if not results:
        # Extract just the filename from complex patterns to suggest a broader search
        base_name = pattern.rsplit("/", 1)[-1] if "/" in pattern else pattern
        hint = ""
        if base_name != pattern:
            hint = f" Try a simpler pattern like '{base_name}' to search more broadly."
        return f"No files matching '{pattern}' in {directory}.{hint}"

    return sorted(results)

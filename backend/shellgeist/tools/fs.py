"""Filesystem tools: read_file, write_file, list_files, find_files, get_repo_map."""
from typing import Any

import difflib
import fnmatch
import os
from pathlib import Path

from pydantic import BaseModel

from shellgeist.tools.base import registry
from shellgeist.runtime.paths import resolve_repo_path

# Directories always excluded from recursive listings / searches
_IGNORED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", ".eggs", "target",
})


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
    p = resolve_repo_path(Path(root), target)
    if not p.exists():
        hint = f" Hint: try a relative path like '{Path(target).name}' instead." if target.startswith("/") else ""
        raise FileNotFoundError(f"File not found: {target}.{hint}")
    return p.read_text(encoding="utf-8", errors="replace")


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
    out = []
    p_root = Path(root)
    for dirpath, dirnames, filenames in os.walk(p_root):
        # Prune hidden and ignored dirs in-place
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in _IGNORED_DIRS
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
    description="Write content to a file. Overwrites if exists. Returns a unified diff when modifying an existing file.",
    input_model=WriteFileInput
)
def write_file(path: str | None = None, content: str = "", root: str = "", file_path: str | None = None, file: str | None = None, **kwargs: Any) -> str:
    target = (path or file_path or file or "").strip()
    p = resolve_repo_path(Path(root), target)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Fix double-escaped newlines from LLMs (literal \n instead of real newlines)
    if "\n" not in content and "\\n" in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "")

    # Capture old content for diff
    old_text = ""
    existed = p.exists()
    if existed:
        try:
            old_text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Skip write if file already has the exact same content
    if existed and old_text == content:
        return (
            f"NO_CHANGE: {target} already contains this exact content. "
            "Do NOT call write_file again. Proceed to the next step or say Status: DONE."
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
        msg += " (new file)"
    return msg


@registry.register(
    description="List files and directories. Set recursive=true to show the full tree. Set depth to limit recursion (default 3).",
    input_model=ListFilesInput
)
def list_files(directory: str = ".", root: str = "", recursive: bool = False, depth: int = 3, max_results: int = 100, **kwargs: Any) -> list[str]:
    p = resolve_repo_path(Path(root), directory)
    if not p.exists() or not p.is_dir():
        hint = f" Hint: try a relative path like '{Path(directory).name}' instead." if directory.startswith("/") else ""
        raise FileNotFoundError(f"Directory not found: {directory}.{hint}")

    # Use the resolved directory as base for relative paths
    base = str(p)
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
                if entry.name.startswith(".") or entry.name in _IGNORED_DIRS:
                    continue
                rel = os.path.relpath(entry.path, base)
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
            rel = os.path.relpath(entry.path, base)
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
    p = resolve_repo_path(Path(root), directory)
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    results: list[str] = []
    base = str(p)

    for dirpath, dirnames, filenames in os.walk(p):
        # Prune ignored directories in-place
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _IGNORED_DIRS
        ]
        for filename in filenames:
            if filename.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(dirpath, filename), base)
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

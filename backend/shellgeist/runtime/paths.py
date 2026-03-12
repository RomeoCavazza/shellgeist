"""Unified repo-path resolution for all tools."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IGNORED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", ".eggs", "target",
})

_TOO_BROAD_ROOTS = frozenset({
    "/", "/nix", "/usr", "/var", "/tmp", "/opt", "/run", "/srv", "/mnt",
    "/proc", "/sys", "/dev", "/boot", "/lib", "/lib64", "/bin", "/sbin", "/snap", "/home",
})


@dataclass(frozen=True)
class FileResolution:
    status: str  # exact | resolved
    path: Path
    requested: str
    candidates: tuple[str, ...] = ()


def read_repo_file(path: Path) -> str:
    """Read file content as UTF-8, replacing invalid bytes.

    Use this for any file under the repo so behavior is consistent across tools.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def workspace_relative_path(root: Path | str, path: Path | str) -> str:
    base = Path(root).resolve() if isinstance(root, str) else root.resolve()
    target = Path(path).resolve() if isinstance(path, str) else path.resolve()
    return os.path.relpath(target, base)


def is_root_too_broad(root: Path | str) -> bool:
    resolved = Path(root).resolve() if isinstance(root, str) else root.resolve()
    return str(resolved) in _TOO_BROAD_ROOTS


def resolve_repo_path(root: Path | str, rel: str) -> Path:
    """Resolve *rel* inside *root*, ensuring it stays within the workspace.

    *root* may be a Path or a path string.
    If *rel* is a path suffix of the absolute root (e.g. root is
    /home/user/Bureau/projets/shellgeist and rel is "Bureau/projets/shellgeist"),
    it is normalized to "." so that "describe Bureau/projets/shellgeist" works
    when the workspace is that directory.
    Rules:
    - Home-relative paths (~/) are expanded.
    - Relative paths are resolved against *root*.
    - Absolute paths must already live under *root*; anything outside is rejected
      with a clear error encouraging RELATIVE paths instead.
    """
    if not rel:
        raise ValueError("invalid_path: empty path")

    root = Path(root).resolve() if isinstance(root, str) else root.resolve()
    rel_stripped = rel.strip().rstrip("/")
    if rel_stripped:
        root_parts = root.parts
        rel_parts = Path(rel_stripped).expanduser().parts
        if rel_parts and len(rel_parts) <= len(root_parts):
            if root_parts[-len(rel_parts) :] == rel_parts:
                rel = "."
    p = Path(rel).expanduser()

    # Absolute path: only allowed if it already lives under the workspace root.
    if p.is_absolute():
        res = p.resolve()
        if not res.is_relative_to(root):
            raise PermissionError(
                f"Access denied: absolute path '{p}' is outside project root {root}. "
                f"Use a relative path from the workspace root instead, for example "
                f"'{p.name}' or '{str(p).lstrip('/')}'."
            )
        return res

    # Relative path: resolve against the workspace root and ensure it stays inside.
    res = (root / p).resolve()
    if not res.is_relative_to(root):
        raise PermissionError(
            f"Access denied: '{rel}' resolves outside project root {root}. "
            "Always use paths that stay within this workspace."
        )
    return res


def _iter_workspace_files(root: Path, ignored_dirs: set[str] | frozenset[str] = DEFAULT_IGNORED_DIRS) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in ignored_dirs
        ]
        for filename in filenames:
            if filename.startswith("."):
                continue
            files.append(Path(dirpath) / filename)
    return files


def resolve_existing_repo_file(
    root: Path | str,
    rel: str,
    *,
    ignored_dirs: set[str] | frozenset[str] = DEFAULT_IGNORED_DIRS,
) -> FileResolution:
    requested = (rel or "").strip()
    exact = resolve_repo_path(root, requested)
    if exact.exists() and exact.is_file():
        return FileResolution(status="exact", path=exact, requested=requested)

    root_path = Path(root).resolve() if isinstance(root, str) else root.resolve()
    if is_root_too_broad(root_path):
        raise FileNotFoundError(
            f"Invalid workspace root for file fallback: {root_path}. "
            "Refusing to scan a global/system root. Refresh project context or restart the daemon."
        )
    requested_posix = requested.replace("\\", "/").strip("/")
    requested_name = Path(requested).name
    requested_name_lower = requested_name.lower()
    requested_suffix_lower = requested_posix.lower()

    files = _iter_workspace_files(root_path, ignored_dirs=ignored_dirs)
    exact_rel_matches: list[Path] = []
    basename_matches: list[Path] = []
    basename_ci_matches: list[Path] = []
    suffix_matches: list[Path] = []

    for file_path in files:
        rel_path = workspace_relative_path(root_path, file_path).replace("\\", "/")
        rel_lower = rel_path.lower()
        name = file_path.name
        name_lower = name.lower()

        if requested_posix and rel_path == requested_posix:
            exact_rel_matches.append(file_path)
        if requested_name and name == requested_name:
            basename_matches.append(file_path)
        if requested_name_lower and name_lower == requested_name_lower:
            basename_ci_matches.append(file_path)
        if requested_suffix_lower and rel_lower.endswith(requested_suffix_lower):
            suffix_matches.append(file_path)

    for matches, status in (
        (exact_rel_matches, "resolved"),
        (basename_matches, "resolved"),
        (basename_ci_matches, "resolved"),
        (suffix_matches, "resolved"),
    ):
        unique = []
        seen: set[str] = set()
        for path_obj in matches:
            rel_norm = workspace_relative_path(root_path, path_obj).replace("\\", "/")
            if rel_norm not in seen:
                unique.append(path_obj)
                seen.add(rel_norm)
        if len(unique) == 1:
            return FileResolution(status=status, path=unique[0], requested=requested)
        if len(unique) > 1:
            candidates = tuple(workspace_relative_path(root_path, p).replace("\\", "/") for p in unique[:5])
            raise FileNotFoundError(
                f"Ambiguous file path: {requested}. Possible matches: {', '.join(candidates)}."
            )

    suggestions: list[str] = []
    seen_suggestions: set[str] = set()
    for path_obj in files:
        rel_norm = workspace_relative_path(root_path, path_obj).replace("\\", "/")
        name_lower = path_obj.name.lower()
        rel_lower = rel_norm.lower()
        if requested_name_lower and (name_lower.startswith(requested_name_lower) or requested_name_lower in name_lower):
            if rel_norm not in seen_suggestions:
                suggestions.append(rel_norm)
                seen_suggestions.add(rel_norm)
        elif requested_suffix_lower and requested_suffix_lower in rel_lower:
            if rel_norm not in seen_suggestions:
                suggestions.append(rel_norm)
                seen_suggestions.add(rel_norm)
        if len(suggestions) >= 5:
            break

    hint = ""
    if suggestions:
        hint = f" Possible matches: {', '.join(suggestions)}."
    elif requested_name and ("/" in requested or requested.strip().startswith("~")):
        hint = f" Use a relative path from workspace root, e.g. '{requested_name}'."
    elif requested_name:
        hint = " No file with that name in the workspace."
    raise FileNotFoundError(f"File not found: {requested}.{hint}")

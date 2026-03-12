"""Core agent loop: LLM interaction, tool dispatch, and result handling."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from shellgeist.agent.orchestrator import (
    classify_model_turn,
    decide_no_tool_action,
    is_small_talk,
    normalize_final_response,
    salvage_slope_to_tool_calls,
)
from shellgeist.agent.parsing.normalize import extract_trailing_after_last_fence
from shellgeist.config import debug_enabled as _debug_enabled
from shellgeist.llm import build_system_prompt, get_client, run_llm_stream_with_retry
from shellgeist.runtime.session import (
    append_user_goal_once,
    init_db,
    load_recent_history,
    TurnState,
    save_message as save_db_message,
    repair_conversation_history,
)
from shellgeist.runtime.telemetry import TelemetryEmitter
from shellgeist.runtime.transport import UIEventEmitter
from shellgeist.runtime.paths import resolve_repo_path, read_repo_file
from shellgeist.tools import load_tools, registry
from shellgeist.tools.executor import execute_tool_call
from shellgeist.runtime.policy import LoopGuard, RetryEngine, RetryConfig, is_failed_result


def _debug_log(msg: str) -> None:
    if not _debug_enabled():
        return
    sys.stderr.write(f"DEBUG: {msg}\n")
    sys.stderr.flush()


# Max chars for tool observations and assistant replies kept in history (avoids model "continuing" huge past content)
_MAX_HISTORY_OBS_CHARS = 2800
_MAX_HISTORY_ASSISTANT_CHARS = 4000
_FILE_REF_RE = re.compile(
    r"(?<![\w/.-])(?:/|\.?/)?(?:[A-Za-z0-9_.-]*[A-Za-z_][A-Za-z0-9_.-]*)(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z][A-Za-z0-9_]*"
)
_COMMON_FILE_EXTENSIONS = {
    "bash", "c", "cc", "conf", "cpp", "css", "csv", "env", "go", "h", "hh",
    "hpp", "htm", "html", "ini", "ipynb", "java", "js", "json", "lock", "lua",
    "md", "nix", "php", "py", "rb", "rs", "scss", "sh", "sql", "svg", "toml",
    "ts", "tsx", "txt", "xml", "yaml", "yml", "zsh",
}


def _is_plausible_file_reference(ref: str) -> bool:
    raw = (ref or "").strip().strip("`'\",.;:()[]{}")
    if not raw or raw in {".", ".."}:
        return False

    # Paths with directory separators are usually explicit enough to trust.
    if raw.startswith(("/", "./", "../")) or "/" in raw:
        return True

    suffix = Path(raw).suffix.lower().lstrip(".")
    if not suffix:
        return False
    return suffix in _COMMON_FILE_EXTENSIONS


def _extract_file_references(text: str) -> list[str]:
    s = text or ""
    refs: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"`([^`\n]+\.[A-Za-z0-9_]+)`", s):
        ref = match.group(1).strip()
        if ref and _is_plausible_file_reference(ref) and ref not in seen:
            refs.append(ref)
            seen.add(ref)
    for match in _FILE_REF_RE.finditer(s):
        ref = match.group(0).strip("`'\",.;:()[]{}")
        if ref and _is_plausible_file_reference(ref) and ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs


def _has_file_reference(text: str) -> bool:
    return bool(_extract_file_references(text))


def _looks_like_list_only_request(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low or len(low) >= 200:
        return False
    if _has_file_reference(low):
        return False

    non_list_intents = (
        " and ", " then ", " puis ", "show ", "read ", "cat ", "affiche ", "lis ",
        "résume ", "resume ", "summary", "explique ", "explain ",
        "create", "crée", "write", "run ", "exécute",
    )
    if any(token in low for token in non_list_intents):
        return False

    list_markers = (
        "list files", "liste le contenu", "liste les fichiers",
        "contenu du répertoire", "contenu du dossier", "répertoire courant",
    )
    has_list_marker = any(token in low for token in list_markers) or bool(re.match(r"^\s*(ls|liste|list)\b", low))
    has_directory_scope = any(token in low for token in ("répertoire", "dossier", "directory", "folder"))
    return has_list_marker and has_directory_scope


def _looks_like_run_it_goal(text: str) -> bool:
    """True if the user is asking to run/execute a script (e.g. 'exécute le', 'run it') without naming a file."""
    low = (text or "").strip().lower().rstrip("?.!")
    if not low or len(low) > 150:
        return False
    run_markers = (
        "exécute le", "exécute-le", "execute le", "execute-le",
        "run it", "run the script", "lance le", "lance-le",
        "exécute le pour voir", "run it to see", "execute it",
    )
    if any(m in low for m in run_markers):
        return True
    if re.match(r"^\s*(exécute|execute|run)\s*(le|it)?\s*(pour voir|to see)?\s*$", low):
        return True
    return False


# Regex to find last write_file path to a .py in assistant content
_LAST_WRITE_FILE_PY_RE = re.compile(
    r'"path"\s*:\s*"([^"]+\.py)"',
    re.IGNORECASE,
)
# Regex to find .py filename in user message (e.g. error paste "File \".../script.py\", line 84")
_LAST_PY_IN_USER_RE = re.compile(
    r"[\"']([A-Za-z0-9_]+\.py)[\"']",
)


def _last_py_file_from_history(history: list[dict[str, Any]]) -> str | None:
    """Return the most recent .py file path from conversation: last write_file path in assistant messages, or last .py mentioned in user message."""
    if not history:
        return None
    for msg in reversed(history):
        role = (msg.get("role") or "").strip().lower()
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        if role == "assistant":
            matches = list(_LAST_WRITE_FILE_PY_RE.finditer(content))
            if matches:
                return matches[-1].group(1).strip()
        if role == "user":
            for m in _LAST_PY_IN_USER_RE.finditer(content):
                return m.group(1).strip()
    return None


def _goal_family(goal: str) -> str:
    low = (goal or "").strip().lower()
    if not low:
        return "general"
    if _looks_like_list_only_request(low):
        return "list"
    if _strict_single_target_path(goal):
        return "single_file"
    write_markers = (
        "write ", "create ", "crée", "réécris", "rewrite", "edit ", "modifie",
        "replace ", "overwrite", "run ", "exécute", "python3 ", "py_compile", "timeout ",
    )
    if any(token in low for token in write_markers):
        return "write"
    read_markers = (
        "read ", "lis ", "affiche ", "show ", "cat ", "résume ", "resume ",
        "summary", "explique ", "explain ",
    )
    if any(token in low for token in read_markers):
        return "read"
    if _has_file_reference(low):
        return "read"
    return "general"


def _should_drop_loaded_history(goal: str, history: list[dict[str, Any]]) -> bool:
    if len(history) <= 1:
        return False

    family = _goal_family(goal)
    recent = [m for m in history[1:] if isinstance(m, dict)][-12:]
    recent_text = "\n".join(str(m.get("content") or "") for m in recent).lower()
    if not recent_text.strip():
        return False

    recent_file_names = {Path(ref).name.lower() for ref in _extract_file_references(recent_text)}
    goal_file_names = {Path(ref).name.lower() for ref in _extract_file_references(goal)}
    last_explicit_file_names: set[str] = set()
    for m in reversed(history[1:]):
        if not isinstance(m, dict):
            continue
        refs = _extract_file_references(str(m.get("content") or ""))
        if refs:
            last_explicit_file_names = {Path(ref).name.lower() for ref in refs}
            break

    write_like_history = any(
        token in recent_text
        for token in (
            "write_file", "edit_file", "run_shell", "exec_shell_session", "start_shell_session",
            "py_compile", "timeout ", "ne modifie aucun autre fichier", "un seul fichier",
            "single file", "strict single-file", "strict single file",
        )
    )

    if family in ("read", "list") and write_like_history:
        return True

    if family == "single_file" and write_like_history:
        return True

    if family == "write" and goal_file_names and recent_file_names and goal_file_names.isdisjoint(recent_file_names):
        return True

    if family in ("write", "single_file", "read") and goal_file_names and last_explicit_file_names and goal_file_names.isdisjoint(last_explicit_file_names):
        return True

    if family == "read" and goal_file_names and recent_file_names and goal_file_names.isdisjoint(recent_file_names):
        return True

    if family == "single_file" and goal_file_names and recent_file_names and goal_file_names.isdisjoint(recent_file_names):
        return True

    return False


def _strict_single_target_path(goal: str) -> str | None:
    g = goal or ""
    low = g.lower()
    markers = (
        "un seul fichier", "single file", "only one file", "un unique fichier",
        "ne modifie aucun autre fichier", "do not modify any other file",
        "réécris uniquement", "rewrite only", "crée ou réécris uniquement",
        "only modify this file",
    )
    refs = _extract_file_references(g)
    if not refs:
        return None

    if any(m in low for m in markers):
        return refs[0]

    write_markers = (
        "create ", "crée", "write ", "rewrite", "réécris", "overwrite",
        "modifie", "edit ", "replace ", "execute ", "exécute", "run ",
        "python3 ", "py_compile", "timeout ",
    )
    if len(refs) == 1 and any(m in low for m in write_markers):
        return refs[0]
    return None


def _single_py_file_ref_from_goal(goal: str) -> str | None:
    """Return the single .py file path mentioned in the goal, if exactly one. Generic: any project, any language.
    Used to accept ```code``` slope when the model replies with a code block and the goal mentions one .py file."""
    refs = _extract_file_references(goal or "")
    py_refs = [r for r in refs if (r or "").lower().endswith(".py")]
    return py_refs[0] if len(py_refs) == 1 else None


def _infer_py_path_from_goal(goal: str) -> str | None:
    """When the goal does not mention a .py file but we could infer one from context.
    Kept for future generic heuristics. No project-specific inference (e.g. no cube.py)
    so the tool stays agnostic for Java, Go, etc."""
    return None


def _summarize_list_observation(observation: str, directory: str) -> str:
    raw = (observation or "").strip()
    label = "le répertoire courant" if directory in ("", ".") else f"`{directory}`"
    try:
        items = ast.literal_eval(raw)
    except Exception:
        items = None

    if not isinstance(items, list):
        return f"Voici le contenu de {label}, affiché ci-dessus."

    entries = [str(item) for item in items]
    count = len(entries)
    if count == 0:
        return f"{label} est vide."

    dirs = [entry for entry in entries if entry.endswith("/")]
    files = [entry for entry in entries if not entry.endswith("/")]
    preview = ", ".join(entries[:8])
    if count > 8:
        preview += ", ..."

    noun = "entrée" if count == 1 else "entrées"
    if dirs and files:
        return (
            f"Dans {label}, j’ai trouvé {count} {noun} : "
            f"{len(dirs)} dossier{'s' if len(dirs) > 1 else ''} et {len(files)} fichier{'s' if len(files) > 1 else ''}. "
            f"Principaux éléments : {preview}."
        )
    if dirs:
        return f"Dans {label}, j’ai trouvé {count} dossier{'s' if count > 1 else ''} : {preview}."
    return f"Dans {label}, j’ai trouvé {count} fichier{'s' if count > 1 else ''} : {preview}."


def _looks_like_read_only_goal(goal: str) -> bool:
    return _goal_family(goal) == "read"


# Basenames that must not be overwritten unless the user explicitly asked to create/modify them
_PROTECTED_DOC_BASENAMES = frozenset({"readme.md", "license", "contributing.md", "license.md", "contributing"})

# Phrases that indicate the model sent a placeholder instead of real content for write_file
_WRITE_FILE_PLACEHOLDER_PHRASES = (
    "update with code above",
    "see above",
    "code above",
    "content above",
    "same as above",
    "as above",
    "insert code above",
    "use the code above",
    "the code above",
    "see code above",
    "use code above",
    "replace with code above",
    "paste the code above",
)


def _is_write_file_placeholder(content: Any, path: str) -> bool:
    """True if content looks like a placeholder instead of actual file body."""
    if not isinstance(content, str):
        return True
    s = content.strip().lower()
    # CRITICAL: Prevent accidental truncation/wipe. Empty content is rejected as placeholder
    # unless it's a known empty file intent (rare for 7B models).
    if not s:
        return True
    if any(phrase in s for phrase in _WRITE_FILE_PLACEHOLDER_PHRASES):
        return True
    # Very short content for a .py file with no newline is suspicious
    if path.lower().endswith(".py") and len(s) < 40 and "\n" not in content:
        return True
    return False


def _py_compile_check(file_path: Path, root: str) -> tuple[bool, str]:
    """Run py_compile on a .py file. Returns (success, stderr_or_ok_message)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(file_path)],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=10,
        )
        if proc.returncode == 0:
            return True, "Syntax check: OK"
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or "py_compile failed"
    except subprocess.TimeoutExpired:
        return False, "py_compile timeout"
    except Exception as e:
        return False, str(e)


_WRITE_LIKE_KEYWORDS = (
    "crée", "créer", "réécris", "réécrire", "modifie", "modifier", "write", "create",
    "update", "remplace", "replace", "édite", "edit", "overwrite", "écris", "écrire",
)


def _goal_requests_write_to_path(goal: str, path: str) -> bool:
    """True if the goal explicitly asks to create or modify this file (by name)."""
    if not goal or not path:
        return False
    low = goal.strip().lower()
    base = Path(path).name.lower()
    if base not in low and base.replace(".md", "") not in low:
        return False
    return any(kw in low for kw in _WRITE_LIKE_KEYWORDS)


def _primary_goal_file_reference(goal: str) -> str | None:
    refs = _extract_file_references(goal)
    return refs[0] if refs else None


def _canonical_tool_history_content(tool_calls: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for tc in tool_calls:
        payload = {
            "name": tc.get("name"),
            "arguments": tc.get("arguments", {}) or {},
        }
        parts.append(f"<tool_use>{json.dumps(payload, ensure_ascii=False)}</tool_use>")
    return "\n".join(parts)


def _normalize_workspace_path(path: str, root: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    root_path = Path(root).resolve()
    p = Path(raw).expanduser()
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(root_path)).replace("\\", "/")
        except Exception:
            return str(p.resolve()).replace("\\", "/")
    return str(Path(raw)).replace("\\", "/").lstrip("./")


def _filter_single_target_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    strict_target: str,
    root: str,
) -> tuple[list[dict[str, Any]], str | None]:
    target_rel = _normalize_workspace_path(strict_target, root)
    target_name = Path(target_rel).name
    allowed_dirs = {".", ""}
    parent = str(Path(target_rel).parent).replace("\\", "/")
    if parent and parent != ".":
        allowed_dirs.add(parent)
        allowed_dirs.add(parent + "/")

    allowed: list[dict[str, Any]] = []
    blocked: list[str] = []

    for tc in tool_calls:
        name = tc.get("name") or ""
        args = tc.get("arguments", {}) or {}
        if name == "edit_file":
            path_val = _normalize_workspace_path(
                str(args.get("path") or args.get("file") or ""),
                root,
            )
            if path_val == target_rel:
                allowed.append(tc)
            else:
                blocked.append(f"{name} -> {path_val or '(missing path)'}")
        elif name in ("write_file", "read_file"):
            path_val = _normalize_workspace_path(
                str(args.get("path") or args.get("file") or args.get("file_path") or ""),
                root,
            )
            if path_val == target_rel:
                allowed.append(tc)
            else:
                blocked.append(f"{name} -> {path_val or '(missing path)'}")
        elif name == "list_files":
            directory = _normalize_workspace_path(str(args.get("directory", ".")), root)
            blocked.append(f"{name} -> {directory or '.'}")
        elif name in ("run_shell", "exec_shell_session"):
            command = str(args.get("command") or "").strip()
            if target_name and (target_name in command or target_rel in command or strict_target in command):
                allowed.append(tc)
            else:
                blocked.append(f"{name} -> {command[:60] or '(missing command)'}")
        else:
            blocked.append(name or "unknown_tool")

    if blocked and not allowed:
        feedback = (
            "POLICY_DENY: Strict single-file task. "
            f"Allowed target only: {target_rel}. "
            "Use write_file, edit_file, or read_file on that file, "
            "and run_shell only for commands that execute or validate that target. "
            f"Blocked calls: {', '.join(blocked[:4])}."
        )
        return [], feedback

    return allowed, None


def _extract_simple_python_write_call(goal: str, strict_target: str) -> dict[str, Any] | None:
    if not strict_target or not strict_target.lower().endswith(".py"):
        return None

    low = (goal or "").lower()
    if not any(
        token in low
        for token in (
            "script python minimal",
            "script doit afficher",
            "doit afficher",
            "affiche ",
            "afficher ",
            "prints ",
            "print ",
        )
    ):
        return None

    literal = None
    patterns = (
        r"(?:doit afficher|affiche|afficher|prints?|display)\s+(?:exactement\s+)?`([^`\n]+)`",
        r'(?:doit afficher|affiche|afficher|prints?|display)\s+(?:exactement\s+)?"([^"\n]+)"',
        r"(?:doit afficher|affiche|afficher|prints?|display)\s+(?:exactement\s+)?'([^'\n]+)'",
        r"(?:doit afficher|affiche|afficher|prints?|display)\s+(?:exactement\s+)?([^,\n.]+?)(?:(?:\s*,\s*|\s+)(?:puis\s+(?:quitter|exécute[rz]?|execute|run)\b)|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE)
        if match:
            literal = match.group(1).strip()
            break

    if not literal or any(ch in literal for ch in "\r\n"):
        return None

    content = "print(" + repr(literal) + ")\n"
    return {
        "name": "write_file",
        "arguments": {
            "path": strict_target,
            "content": content,
        },
    }


def _extract_requested_shell_commands(goal: str, strict_target: str) -> list[str]:
    if not goal or not strict_target:
        return []

    target_variants = []
    for variant in dict.fromkeys([strict_target, _normalize_workspace_path(strict_target, "."), Path(strict_target).name]):
        if variant:
            target_variants.append(re.escape(variant))
    if not target_variants:
        return []

    target_group = "(?:" + "|".join(target_variants) + ")"
    patterns = (
        rf"python3\s+-m\s+py_compile\s+{target_group}",
        rf"timeout\s+\S+\s+python3\s+{target_group}",
        rf"python3\s+{target_group}",
    )
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, goal):
            cmd = match.group(0).strip()
            if cmd not in seen:
                found.append((match.start(), cmd))
                seen.add(cmd)
    found.sort(key=lambda item: item[0])
    return [cmd for _, cmd in found]


def _last_tool_observation(history: list[dict[str, Any]]) -> tuple[str | None, str]:
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        match = re.search(r'<tool_observation name="([^"]+)">\n?(.*)\n?</tool_observation>', content, flags=re.DOTALL)
        if match:
            return match.group(1), match.group(2).strip()
    return None, ""


def _next_pending_shell_command(history: list[dict[str, Any]], goal: str, strict_target: str) -> str | None:
    requested = _extract_requested_shell_commands(goal, strict_target)
    if not requested:
        return None

    assistant_contents = "\n".join(
        str(message.get("content") or "")
        for message in history
        if message.get("role") == "assistant"
    )
    for cmd in requested:
        needle = f'"command": {json.dumps(cmd, ensure_ascii=False)}'
        if needle not in assistant_contents:
            return cmd
    return None


def _command_targets_strict_file(command: str, strict_target: str, root: str) -> bool:
    cmd = (command or "").strip()
    if not cmd or not strict_target:
        return False
    target_rel = _normalize_workspace_path(strict_target, root)
    target_name = Path(target_rel).name
    return any(part and part in cmd for part in (strict_target, target_rel, target_name))


def _strict_command_kind(command: str) -> str | None:
    cmd = (command or "").strip()
    if not cmd:
        return None
    if re.search(r"\bpython3\s+-m\s+py_compile\b", cmd):
        return "py_compile"
    if re.search(r"\btimeout\s+\S+\s+python3\b", cmd):
        return "timeout_python"
    if re.search(r"\bpython3\b", cmd):
        return "python"
    return None


def _matching_requested_command(command: str, requested_commands: list[str], strict_target: str, root: str) -> str | None:
    kind = _strict_command_kind(command)
    if not kind or not _command_targets_strict_file(command, strict_target, root):
        return None
    for requested in requested_commands:
        if _strict_command_kind(requested) == kind and _command_targets_strict_file(requested, strict_target, root):
            return requested
    return None


def _is_compile_command(command: str, strict_target: str, root: str) -> bool:
    cmd = (command or "").strip()
    return "py_compile" in cmd and _command_targets_strict_file(cmd, strict_target, root)


def _is_repairable_requested_command(command: str, strict_target: str, root: str) -> bool:
    kind = _strict_command_kind(command)
    if not kind:
        return False
    return kind in {"py_compile", "python", "timeout_python"} and _command_targets_strict_file(command, strict_target, root)


def _is_noninteractive_python_failure(command: str, observation: str) -> bool:
    kind = _strict_command_kind(command)
    low = (observation or "").lower()
    if kind not in {"python", "timeout_python"}:
        return False
    return "eoferror" in low or "when reading a line" in low


def _repair_guidance_for_failure(command: str, observation: str) -> str:
    if _is_noninteractive_python_failure(command, observation):
        return (
            " La commande demandée s’exécute sans stdin interactif. "
            "Réécris le script pour qu’il fonctionne sans `input()` bloquant et sans argument obligatoire absent, "
            "ou qu’il ait un comportement par défaut quand aucun input utilisateur n’est fourni."
        )
    obs_lower = (observation or "").lower()
    if "syntaxerror" in obs_lower:
        hints = []
        # When error points to a specific line, prefer edit_file and only that zone
        if re.search(r"\bline\s+\d+", obs_lower) or re.search(r'file\s+["\'][^"\']+["\']\s*,\s*line', obs_lower):
            hints.append("Si l'erreur pointe une ligne précise (ex. line 82), modifie uniquement ce bloc avec **edit_file** (diff minimal), pas write_file en entier.")
        if "parenthesized" in obs_lower or "cannot be parenthesized" in obs_lower:
            hints.append("Python 3 only: tuple unpacking in function parameters is invalid (e.g. def f((x,y))). Use def f(x, y) or unpack inside the function body.")
        if "f-string" in obs_lower or "single '}'" in obs_lower:
            hints.append("En f-string, pour afficher une variable utilise {e} (un seul }); pour une accolade littérale utilise }}.")
        if "unmatched ')'" in obs_lower or "unmatched '}'" in obs_lower:
            hints.append("Vérifie que chaque ( a une ) correspondante et qu'il n'y a pas d'accolade ou parenthèse en trop.")
        if "line continuation" in obs_lower or ("033" in (observation or "") and "syntaxerror" in obs_lower):
            hints.append("Les séquences d'échappement (ex. \\033[H) doivent être dans une chaîne : print('\\033[H', end='') ou sys.stdout.write(chr(27)+'[H'), pas comme ligne seule.")
        if "unterminated" in obs_lower or "line continuation" in obs_lower or "033" in (observation or ""):
            hints.append("En repair : réécris le fichier **en entier** avec write_file(path, contenu_complet). Ne modifie pas les guillemets ni les séquences \\033 ou \\x1b dans les chaînes (pas de \\\" devant les quotes).")
        if hints:
            return " " + " ".join(hints)
    if "no such file or directory" in obs_lower or "errno 2" in obs_lower:
        return " Le fichier cible n'existe pas encore. Crée-le avec write_file puis relance la validation (py_compile / python3)."
    if "nameerror" in obs_lower and ("not defined" in obs_lower or "forget to import" in obs_lower):
        return " Pour un import manquant : utilise edit_file avec un diff qui ajoute la ligne d'import (ex. import shutil) au bon endroit."
    if "usage:" in obs_lower and "[exit_code=1]" in (observation or ""):
        return " La commande demandée s'exécute sans argument. Le script ne doit pas exiger d'argument (ex. pas de sys.argv obligatoire) ; il doit fonctionner avec un simple « python3 fichier.py »."
    if "[errno " in obs_lower or "no address associated" in obs_lower or "name or service not known" in obs_lower:
        return " Ne copie jamais le message d'erreur littéralement dans le code (ex. comme hostname ou chaîne). Corrige la logique du script : utilise un hostname valide, ou gère l'exception et affiche un message clair, ou exécute sans argument avec un comportement par défaut (ex. print('pong'))."
    return ""


def _is_simple_fix_failure(observation: str) -> bool:
    """True when the failure is typically fixed by a one-line or minimal change (e.g. add missing import, fix one line)."""
    low = (observation or "").lower()
    if "nameerror" in low or "importerror" in low or "modulenotfounderror" in low:
        if "not defined" in low or "forget to import" in low or "cannot import" in low:
            return True
    # SyntaxError / IndentationError with a specific line (File "...", line N) → localized fix with edit_file
    if "syntaxerror" in low or "indentationerror" in low:
        if re.search(r"\bline\s+\d+", low) or re.search(r'file\s+["\'][^"\']+["\']\s*,\s*line', low):
            return True
    return False


def _is_validation_failure_observation(observation: str) -> bool:
    content = (observation or "").strip()
    if not content:
        return False
    low = content.lower()
    if "session_not_found" in low or "policy_deny" in low or "blocked_repeat" in low:
        return False
    if "[exit_code=" in content:
        return True
    return any(
        token in low
        for token in (
            "syntaxerror", "traceback", "nameerror", "typeerror", "valueerror",
            "indentationerror", "unterminated string literal", "validation failed",
            "[errno ", "no address associated with hostname", "name or service not known",
            "syntaxwarning:", "attributeerror:",
        )
    )


def _strict_success_response(strict_target: str, requested_commands: list[str]) -> str:
    label = f"`{Path(strict_target).name}`"
    if requested_commands:
        return f"{label} a été écrit puis validé/exécuté avec succès.\n\nStatus: DONE"
    return f"{label} a été écrit avec succès.\n\nStatus: DONE"


def _fix_final_response_py_path(content: str, strict_target: str | None) -> str:
    """Replace wrong .py filename in final response with actual strict_target (e.g. wrong name from comment → correct path)."""
    if not content or not strict_target:
        return content
    correct_name = Path(strict_target).name
    if not correct_name.endswith(".py"):
        return content
    # Replace any other .py filename mentioned in the response with the actual target name
    for m in re.finditer(r"\b([a-zA-Z0-9_\-]+\.py)\b", content):
        wrong = m.group(1)
        if wrong != correct_name:
            content = content.replace(wrong, correct_name)
    return content


def _normalize_exact_content(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _extract_exact_file_content(goal: str, strict_target: str) -> str | None:
    if not goal or not strict_target:
        return None

    patterns = (
        r"(?:doit contenir exactement|contient exactement|must contain exactly)\s*:\s*`([^`\n]*)`",
        r'(?:doit contenir exactement|contient exactement|must contain exactly)\s*:\s*"([^"\n]*)"',
        r"(?:doit contenir exactement|contient exactement|must contain exactly)\s*:\s*'([^'\n]*)'",
        r"(?:doit contenir exactement|contient exactement|must contain exactly)\s*:\s*(.*?)(?=\s+(?:ensuite|then|ne modifie|do not modify)\b|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE | re.DOTALL)
        if match:
            raw = match.group(1)
            value = _normalize_exact_content(raw.strip())
            if value:
                return value
    return None


def _stdlib_only_requested(goal: str) -> bool:
    low = (goal or "").lower()
    markers = (
        "sans dépendance externe",
        "sans dependance externe",
        "stdlib seulement",
        "standard library seulement",
        "standard library only",
        "no external dependencies",
        "without external dependencies",
    )
    return any(marker in low for marker in markers)


def _detect_external_python_imports(content: str) -> list[str]:
    try:
        tree = ast.parse(content or "")
    except Exception:
        return []
    stdlib = getattr(sys, "stdlib_module_names", set())
    blocked: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            names = [node.module.split(".")[0]]
        else:
            continue
        for name in names:
            if name and stdlib and name not in stdlib:
                blocked.add(name)
    return sorted(blocked)


def _strict_completion_blocker(
    *,
    strict_target: str | None,
    strict_target_written: bool,
    strict_requested_commands: list[str],
    strict_completed_commands: set[str],
    exact_content_expected: str | None,
    exact_content_satisfied: bool,
) -> str | None:
    if not strict_target:
        return None
    if exact_content_expected is not None and not exact_content_satisfied:
        return (
            f"EXACT_CONTENT_REQUIRED: `{Path(strict_target).name}` must be written with the exact content requested "
            "before you can finish."
        )
    if not strict_target_written:
        return f"TARGET_NOT_WRITTEN: You must write `{Path(strict_target).name}` before finishing."
    missing = [cmd for cmd in strict_requested_commands if cmd not in strict_completed_commands]
    if missing:
        return (
            "REQUIRED_COMMANDS_PENDING: You must still execute these commands successfully before finishing: "
            + "; ".join(missing)
        )
    return None


def _strict_tool_only_feedback(content: str, strict_target: str) -> str | None:
    raw = (content or "").strip()
    if not raw:
        return None
    if "Status:" in raw:
        return (
            "PROTOCOL_VIOLATION: Strict single-file task detected. "
            "Do not include Status: DONE/FAILED in the same message as tool calls. "
            "Reply with ONLY one or more <tool_use>...</tool_use> blocks targeting "
            f"`{strict_target}`."
        )
    without_tools = re.sub(r"<tool_use>\s*\{.*?\}\s*</tool_use>", "", raw, flags=re.DOTALL).strip()
    if without_tools:
        return (
            "PROTOCOL_VIOLATION: Strict single-file task detected. "
            "Reply with ONLY one or more <tool_use>...</tool_use> blocks. "
            "Do not include explanations, markdown fences, code samples, or any other prose before or after them. "
            f"Target file: `{strict_target}`."
        )
    return None


def _summarize_read_observation(goal: str, path: str, observation: str) -> str:
    goal_low = (goal or "").lower()
    content = (observation or "").strip()
    display_only = any(token in goal_low for token in ("affiche ", "show ", "cat ", "display "))
    wants_summary = any(token in goal_low for token in ("résume", "resume", "summary", "explique", "explain", "rôle", "role"))
    label = f"`{path}`" if path else "ce fichier"

    if display_only and not wants_summary:
        return f"Voici le contenu de {label}, affiché ci-dessus."

    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return f"J’ai lu {label}, mais le fichier est vide."

    if path.lower().endswith(".py"):
        doc = None
        module_doc = None
        module_doc_match = re.search(r'^\s*"""([^"\n]+)', content)
        if module_doc_match:
            module_doc = module_doc_match.group(1).strip().rstrip(".")
        m = re.search(r'^\s*"""([^"\n]+)', content, re.MULTILINE)
        if m:
            doc = m.group(1).strip().rstrip(".")
        classes = list(dict.fromkeys(re.findall(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", content, flags=re.MULTILINE)))[:4]
        funcs = list(dict.fromkeys(re.findall(r"^(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)", content, flags=re.MULTILINE)))[:6]
        primary_class = classes[0] if classes else None
        primary_funcs = funcs[:3]
        description = (module_doc or doc or "").strip()
        if description and primary_class and primary_funcs:
            return f"{label} {description.lower()}. Le module est centré sur `{primary_class}` et notamment : {', '.join(primary_funcs)}."
        if description and primary_class:
            return f"{label} {description.lower()}. Le module est surtout structuré autour de `{primary_class}`."
        if description and primary_funcs:
            return f"{label} {description.lower()}. Les points d’entrée principaux semblent être : {', '.join(primary_funcs)}."
        if primary_class and primary_funcs:
            return f"{label} est un module Python centré sur `{primary_class}` avec notamment : {', '.join(primary_funcs)}."
        if classes or funcs:
            symbols = (classes + funcs)[:5]
            return f"{label} est un module Python qui définit notamment : {', '.join(symbols)}."
        return f"J’ai lu {label}. C’est un fichier Python, affiché ci-dessus."

    cleaned = []
    for ln in lines:
        if ln.startswith("<") and ln.endswith(">"):
            continue
        text = re.sub(r"^[-*#>\d.\s]+", "", ln).strip()
        if text:
            cleaned.append(text)
    cleaned = cleaned[:4]
    if cleaned:
        summary = " ".join(cleaned)
        if len(summary) > 220:
            summary = summary[:220].rstrip() + "..."
        return f"Résumé de {label} : {summary}"
    return f"Voici le contenu de {label}, affiché ci-dessus."


def _summarize_failure_for_user(observation: str, turn: TurnState | None = None) -> str:
    raw = (observation or "").strip()
    label = f"`{Path(turn.strict_target).name}`" if turn and turn.strict_target else "la tâche"
    bilan = ""
    if turn and getattr(turn, "repair_attempts", 0) >= 1:
        bilan = f"Tentatives de correction : {turn.repair_attempts}. "
    lower = raw.lower()
    if "blocked_repeat" in lower:
        return (
            f"{bilan}{label} n’a pas pu être terminé parce que le runtime a bloqué des appels répétés avant la fin. "
            "Le modèle est probablement resté bloqué dans une boucle de vérification ou de relance. "
            "Je te conseille de relancer avec une consigne plus directive, ou de laisser la boucle de réparation réécrire puis exécuter sans multiplier les mêmes lectures.\n\n"
            f"Détail technique : {raw}"
        )
    if "eoferror" in lower or "when reading a line" in lower:
        return (
            f"{bilan}{label} a bien été généré, mais il attend encore une entrée interactive alors que la commande demandée s’exécute sans stdin. "
            "La correction attendue est de rendre le script autonome sans `input()` bloquant, ou de prévoir une valeur/stratégie par défaut.\n\n"
            f"Détail technique : {raw}"
        )
    if "syntaxerror" in lower or "nameerror" in lower or "typeerror" in lower or "traceback" in lower:
        return (
            f"{bilan}{label} a bien été généré, mais il échoue encore à l’exécution ou à la validation Python. "
            "Le modèle n’a pas terminé correctement la correction du fichier. "
            "Vous pouvez corriger à la main ou relancer la tâche.\n\n"
            f"Détail technique : {raw}"
        )
    return (
        f"{bilan}{label} n’a pas pu être terminé proprement. "
        "Le runtime a interrompu la tâche après un échec répété ou un état non récupérable. "
        "Une nouvelle tentative plus guidée ou une correction ciblée est probablement nécessaire.\n\n"
        f"Détail technique : {raw}"
    )


def _is_failed_read_observation(observation: str) -> bool:
    content = (observation or "").strip()
    if not content:
        return True
    if content.startswith(("Error:", "Blocked:", "BLOCKED_", "POLICY_DENY", "CIRCUIT_BREAKER")):
        return True
    low = content.lower()
    return any(
        low.startswith(token)
        for token in (
            "error:",
            "blocked:",
            "blocked_",
            "policy_deny",
            "circuit_breaker",
        )
    )


def _build_turn_state(goal: str, session_id: str, root: str) -> TurnState:
    strict_target = _strict_single_target_path(goal)
    strict_requested_commands = _extract_requested_shell_commands(goal, strict_target or "")
    strict_exact_content = _extract_exact_file_content(goal, strict_target or "")
    return TurnState(
        goal=goal,
        session_id=session_id,
        intent_family=_goal_family(goal),
        strict_target=strict_target,
        strict_target_rel=_normalize_workspace_path(strict_target, root) if strict_target else "",
        stdlib_only_required=_stdlib_only_requested(goal),
        requested_commands=strict_requested_commands,
        repair_budget=3,
        exact_content_expected=strict_exact_content,
        exact_content_satisfied=strict_exact_content is None,
    )


def _classify_turn(turn: TurnState, history: list[dict[str, Any]], goal: str, iteration: int) -> dict[str, Any]:
    last_user_content = ""
    for message in reversed(history):
        if message.get("role") == "user":
            raw = message.get("content")
            last_user_content = (raw if isinstance(raw, str) else (str(raw) if raw else "")).strip()
            break
    respond_to_raw = (goal or "").strip() if (iteration == 0 and goal) else last_user_content
    respond_to = respond_to_raw.lower()
    read_target = _primary_goal_file_reference(respond_to_raw)
    is_run_it = _looks_like_run_it_goal(respond_to_raw)
    return {
        "respond_to_raw": respond_to_raw,
        "respond_to": respond_to,
        "is_list_only_request": _looks_like_list_only_request(respond_to),
        "read_target": read_target,
        "next_requested_command": turn.next_requested_command(),
        "is_run_it_request": is_run_it,
        "run_it_script": _last_py_file_from_history(history) if is_run_it else None,
    }


def _build_deterministic_batch_if_possible(
    turn: TurnState,
    history: list[dict[str, Any]],
    *,
    respond_to: str,
    read_target: str | None,
    is_list_only_request: bool,
    next_requested_command: str | None,
    run_it_script: str | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, bool]]:
    last_is_user = bool(history and history[-1].get("role") == "user")
    flags = {
        "inject_list": False,
        "inject_read": False,
        "inject_simple_write": False,
        "inject_required_shell": False,
        "inject_repair_rerun": False,
        "inject_run_it": False,
    }
    if not last_is_user:
        return None, flags

    if is_list_only_request:
        list_dir = "."
        for subdir in ("backend", "src", "docs", "nvim"):
            if subdir in respond_to:
                list_dir = subdir
                break
        flags["inject_list"] = True
        calls = [{"name": "list_files", "arguments": {"directory": list_dir}}]
        turn.deterministic_tool_calls = calls
        return calls, flags

    if _looks_like_read_only_goal(respond_to) and read_target:
        flags["inject_read"] = True
        calls = [{"name": "read_file", "arguments": {"path": read_target}}]
        turn.deterministic_tool_calls = calls
        return calls, flags

    if run_it_script and _looks_like_run_it_goal(respond_to):
        flags["inject_run_it"] = True
        cmd = f"python3 {run_it_script}"
        if "timeout" in respond_to or "6s" in respond_to or "animation" in respond_to:
            cmd = f"timeout 6s {cmd}"
        calls = [{"name": "run_shell", "arguments": {"command": cmd}}]
        turn.deterministic_tool_calls = calls
        return calls, flags

    strict_simple_write_call = _extract_simple_python_write_call(turn.goal, turn.strict_target or "")
    if (
        turn.strict_target
        and strict_simple_write_call
        and not turn.target_written
        and not turn.failed_validation_command
    ):
        flags["inject_simple_write"] = True
        calls = [strict_simple_write_call]
        turn.deterministic_tool_calls = calls
        return calls, flags

    # Autonomy: after writing a .py, run it even if user didn't ask to "execute" (default: python3 <file>)
    default_py_run = None
    if turn.strict_target and str(turn.strict_target).lower().endswith(".py"):
        default_py_run = f"python3 {Path(turn.strict_target).name}"
    effective_next_command = next_requested_command or default_py_run

    if (
        turn.strict_target
        and turn.target_written
        and effective_next_command
        and not turn.failed_validation_command
    ):
        flags["inject_required_shell"] = True
        calls = [{"name": "run_shell", "arguments": {"command": effective_next_command}}]
        turn.deterministic_tool_calls = calls
        return calls, flags

    if (
        turn.strict_target
        and turn.failed_validation_command
        and turn.repair_rewritten
    ):
        flags["inject_repair_rerun"] = True
        calls = [{"name": "run_shell", "arguments": {"command": turn.failed_validation_command}}]
        turn.deterministic_tool_calls = calls
        return calls, flags

    turn.deterministic_tool_calls = None
    return None, flags


def _append_assistant_history(history: list[dict[str, Any]], content_str: str) -> None:
    if not content_str:
        return
    to_append = content_str
    if len(to_append) > _MAX_HISTORY_ASSISTANT_CHARS:
        to_append = (
            to_append[:_MAX_HISTORY_ASSISTANT_CHARS].rstrip()
            + f"\n\n... [truncated, {len(content_str)} chars]"
        )
    history.append({"role": "assistant", "content": to_append})


async def _emit_response_draft(ui: UIEventEmitter, turn: TurnState, delta: str) -> None:
    if not delta:
        return
    turn.draft_response_visible = True
    await ui.emit_execution_event(
        "response_draft",
        delta,
        phase="streaming",
        meta={"chunk": True, "draft_id": turn.draft_response_id},
    )


async def _discard_response_draft(ui: UIEventEmitter, turn: TurnState) -> None:
    if not turn.draft_response_visible:
        return
    await ui.emit_execution_event(
        "response_discard",
        "",
        phase="streaming",
        meta={"draft_id": turn.draft_response_id},
    )
    turn.draft_response_visible = False


class Agent:
    def __init__(self, root: str) -> None:
        load_tools()
        self.root = root
        self.client, self.model = get_client()
        self.history: list[dict[str, Any]] = []
        self._setup_system_prompt()

    def _setup_system_prompt(self) -> None:
        # Rules are included via get_project_context (single source)
        sys_prompt = build_system_prompt(
            self.root,
            debug_log=_debug_log,
            tool_schemas_provider=registry.get_tool_schemas,
            local_rules=None,
        )
        self.history = [{"role": "system", "content": sys_prompt}]

    async def run_task(self, goal: str, writer: Any | None = None, session_id: str = "default", mode: str = "auto", reader: Any | None = None, fresh_conversation: bool = False) -> dict[str, Any]:
        greeting = is_small_talk(goal)
        if greeting:
             ui = UIEventEmitter(writer, reader=reader)
             await ui.emit_execution_event("response", greeting, phase="done", meta={"final": True})
             return {"ok": True, "status": "completed", "logs": [greeting], "response": greeting}

        init_db()
        ui = UIEventEmitter(writer, reader=reader)
        review_mode = (mode == "review")

        # Strip internal PROTOCOL_VIOLATION feedbacks from history before the new task
        # so failed attempts from the previous turn don't poison the LLM context.
        self.history = [
            m for m in self.history
            if not (m.get("role") == "user" and m.get("content", "").startswith("PROTOCOL_VIOLATION:"))
        ]

        if not fresh_conversation:
            self.history = load_recent_history(self.history, session_id=session_id)
            if _should_drop_loaded_history(goal, self.history):
                _debug_log("Dropping loaded history due to incompatible task family / file targets.")
                self.history = [self.history[0]] if self.history and self.history[0].get("role") == "system" else []
        append_user_goal_once(self.history, session_id=session_id, goal=goal)

        max_steps = 12
        logs: list[str] = []
        any_tool_succeeded = False
        last_shell_session_id = None
        loop_guard = LoopGuard()
        retry_engine = RetryEngine(RetryConfig.from_env())
        turn = _build_turn_state(goal, session_id, self.root)

        # When the task targets an existing file to complete/modify, remind to read first and inject content (explore-then-edit)
        if turn.strict_target and turn.strict_target_rel:
            try:
                target_path = (Path(self.root) / turn.strict_target_rel).resolve()
                if target_path.is_file():
                    low = (goal or "").lower()
                    if any(
                        kw in low
                        for kw in (
                            "complète", "complete", "modif", "implement", "remplis", "remplir",
                            "to do", "# to do", "pseudo-code", "pseudocode", "squelette", "skeleton",
                        )
                    ):
                        read_first_hint = (
                            f"Rappel : le fichier `{turn.strict_target}` existe déjà. "
                            "Appelle read_file sur ce fichier en premier pour voir son contenu (ex. les # TO DO ou le squelette), puis utilise edit_file ou write_file pour le compléter."
                        )
                        self.history.append({"role": "user", "content": read_first_hint})
                        save_db_message(session_id, "user", read_first_hint, log_type="context")
                        # Pre-inject file content so the model has it in context without calling read_file (explore-first)
                        try:
                            content = read_repo_file(target_path)
                            max_inject = 12_000
                            if len(content) > max_inject:
                                content = content[:max_inject].rstrip() + f"\n\n… (tronqué, {len(content)} chars au total)"
                            inject_msg = f"Contenu actuel de `{turn.strict_target}` (à utiliser pour compléter/modifier) :\n\n```\n{content}\n```"
                            self.history.append({"role": "user", "content": inject_msg})
                            save_db_message(session_id, "user", inject_msg, log_type="context")
                        except (OSError, ValueError):
                            pass
            except (OSError, ValueError):
                pass

        telemetry = TelemetryEmitter(
            emit_execution_event=ui.emit_execution_event,
            total_retries_provider=lambda: retry_engine.total_retries_used
        )

        async def _log_retry(msg: str) -> None:
            await ui.emit_execution_event("status", msg, phase="streaming")

        for i in range(max_steps):
            repaired, report = repair_conversation_history(self.history)
            self.history = repaired

            turn_ctx = _classify_turn(turn, self.history, goal, i)
            deterministic_calls, deterministic_flags = _build_deterministic_batch_if_possible(
                turn,
                self.history,
                respond_to=turn_ctx["respond_to"],
                read_target=turn_ctx["read_target"],
                is_list_only_request=turn_ctx["is_list_only_request"],
                next_requested_command=turn_ctx["next_requested_command"],
                run_it_script=turn_ctx.get("run_it_script"),
            )

            if deterministic_calls:
                tool_calls = deterministic_calls
                content_str = _canonical_tool_history_content(tool_calls)
                _append_assistant_history(self.history, content_str)
                logs.append(content_str)
                await ui.status(False)
            else:
                # Only show "thinking" when we're about to call the LLM (not for deterministic list/read)
                await ui.status(True)
                # Phase 1 refactor: do not stream draft to UI (suppress bloat); user sees only status then tool_call/tool_result or final response
                async def _on_response_chunk(delta: str) -> None:
                    await _emit_response_draft(ui, turn, delta)

                content, stream_report = await run_llm_stream_with_retry(
                    client=self.client,
                    model=self.model,
                    messages=self.history,
                    retry_engine=retry_engine,
                    telemetry=telemetry,
                    log_retry=_log_retry,
                    on_chunk=_on_response_chunk,
                )
                await ui.status(False)

                if stream_report.outcome != "success":
                    await _discard_response_draft(ui, turn)
                    return {"ok": False, "error": "provider_error", "logs": logs}

                content_str = str(content or "")
                if content_str:
                    logs.append(content_str)

            strict_block_feedback = None
            strict_protocol_feedback = None

            if not deterministic_calls:
                model_turn = classify_model_turn(content_str)
                tool_calls = model_turn.tool_calls
                # Accept slope: code blocks / command lines → convert to write_file / run_shell instead of rejecting
                if not tool_calls and turn.strict_target:
                    slope_calls = salvage_slope_to_tool_calls(
                        content_str, turn.strict_target, self.root
                    )
                    if slope_calls:
                        tool_calls = slope_calls
                        trailing = extract_trailing_after_last_fence(content_str)
                        turn.slope_trailing_response = trailing.strip() or None
                # When no strict_target but model replied with ```code``` and goal mentions exactly one .py file, accept slope (generic: any project)
                if not tool_calls and re.search(r"```(?:python)?\s*[\s\S]*?(?:def |import |class |if __name__)", content_str):
                    slope_target = _single_py_file_ref_from_goal(goal)
                    if not slope_target:
                        slope_target = _infer_py_path_from_goal(goal)
                    if slope_target:
                        slope_calls = salvage_slope_to_tool_calls(
                            content_str, slope_target, self.root
                        )
                        if slope_calls:
                            tool_calls = slope_calls
                            trailing = extract_trailing_after_last_fence(content_str)
                            turn.slope_trailing_response = trailing.strip() or None

            if turn.strict_target and not deterministic_calls and not tool_calls:
                strict_protocol_feedback = _strict_tool_only_feedback(content_str, turn.strict_target)
                if strict_protocol_feedback:
                    await _discard_response_draft(ui, turn)
                    self.history.append({"role": "user", "content": strict_protocol_feedback})
                    save_db_message(session_id, "user", strict_protocol_feedback, log_type="context")
                    _debug_log(f"Strict tool-only rejection. Feedback: {strict_protocol_feedback}")
                    continue

            if tool_calls and turn.strict_target and not deterministic_calls:
                tool_calls, strict_block_feedback = _filter_single_target_tool_calls(
                    tool_calls,
                    strict_target=turn.strict_target,
                    root=self.root,
                )

            if tool_calls and not deterministic_calls:
                # Keep the streamed draft visible so the user sees the model generating (e.g. edit_file payload)
                # instead of hiding it and only showing the tool card.
                _append_assistant_history(self.history, _canonical_tool_history_content(tool_calls))

            # LIST FILES: if user just asked to list (previous message) and model did anything else, force list_files
            if tool_calls and len(self.history) >= 2 and self.history[-2].get("role") == "user":
                prev = (self.history[-2].get("content") or "").strip().lower()
                _prev_list = _looks_like_list_only_request(prev)
                _list_dir_prev = "."
                for subdir in ("backend", "src", "docs", "nvim"):
                    if subdir in prev:
                        _list_dir_prev = subdir
                        break
                if _prev_list:
                    tool_calls = [{"name": "list_files", "arguments": {"directory": _list_dir_prev}}]
                elif tool_calls and len(tool_calls) == 1 and tool_calls[0].get("name") == "find_files" and any(x in prev for x in ("list", "liste", "ls", "répertoire", "dossier", "contenu")):
                    tool_calls = [{"name": "list_files", "arguments": {"directory": _list_dir_prev}}]
            elif tool_calls:
                last_user = ""
                for i in range(len(self.history) - 1, -1, -1):
                    m = self.history[i]
                    if m and m.get("role") == "user":
                        c = (m.get("content") or "").strip()
                        if c and "PROTOCOL_VIOLATION" not in c and "Only the first" not in c and "Output must be" not in c and "PARSE_ERROR" not in c:
                            last_user = c
                            break
                if not last_user and len(self.history) >= 2 and self.history[-2].get("role") == "user":
                    last_user = (self.history[-2].get("content") or "").strip()
                last_lower = (last_user or "").lower().strip()
                list_only = _looks_like_list_only_request(last_lower)
                _list_dir_fallback = "."
                for subdir in ("backend", "src", "docs", "nvim"):
                    if subdir in last_lower:
                        _list_dir_fallback = subdir
                        break
                if list_only:
                    tool_calls = [{"name": "list_files", "arguments": {"directory": _list_dir_fallback}}]

            # Enforce max 3 tool calls per turn to avoid LLM dumping 10+ calls and repeated failures
            max_tools_per_turn = 3
            num_calls = len(tool_calls)
            excess = max(0, num_calls - max_tools_per_turn)
            if excess > 0:
                tool_calls = tool_calls[:max_tools_per_turn]

            if not tool_calls:
                # User asked "exécute le" but model replied with code block instead of run_shell → reject and ask for tool call
                run_it_script = turn_ctx.get("run_it_script")
                if (
                    turn_ctx.get("is_run_it_request")
                    and run_it_script
                    and re.search(r"```", content_str or "")
                ):
                    await _discard_response_draft(ui, turn)
                    feedback = (
                        f"PROTOCOL_VIOLATION: The user asked to run the script. "
                        f"Call run_shell with: python3 {run_it_script}. "
                        "Do not output code in a markdown block or mix explanation with code; output only a single <tool_use> with run_shell."
                    )
                    self.history.append({"role": "user", "content": feedback})
                    save_db_message(session_id, "user", feedback, log_type="context")
                    _debug_log(f"Run-it rejection (code block instead of run_shell). Feedback: {feedback}")
                    continue

                decision = decide_no_tool_action(
                    content_str,
                    completion_blocker=_strict_completion_blocker(
                        strict_target=turn.strict_target,
                        strict_target_written=turn.target_written,
                        strict_requested_commands=turn.requested_commands,
                        strict_completed_commands=turn.completed_commands,
                        exact_content_expected=turn.exact_content_expected,
                        exact_content_satisfied=turn.exact_content_satisfied,
                    ),
                    extract_final_response=normalize_final_response,
                    any_tool_succeeded=any_tool_succeeded,
                )
                if decision.action == "complete":
                    await _discard_response_draft(ui, turn)
                    final_response = decision.final_response or ""
                    if final_response:
                        self.history.append({"role": "assistant", "content": final_response})
                    await ui.emit_execution_event("response", final_response, phase="done", meta={"final": True})
                    return {"ok": True, "status": "completed", "logs": logs, "response": final_response}

                await _discard_response_draft(ui, turn)
                if turn.strict_target:
                    feedback = strict_block_feedback or (
                        "PROTOCOL_VIOLATION: Strict single-file task detected. "
                        "Do not inspect unrelated files or directories. Call a tool that directly advances the target file only. "
                        f"Preferred next call: <tool_use>{{\"name\": \"write_file\", "
                        f"\"arguments\": {{\"path\": \"{turn.strict_target}\", \"content\": \"...\"}}}}</tool_use>"
                    )
                else:
                    feedback = f"PROTOCOL_VIOLATION: {decision.feedback}"
                self.history.append({"role": "user", "content": feedback})
                _debug_log(f"No tool call detected. Pruned blather. Feedback: {feedback}")
                continue

            # Phase 1 refactor: discard draft so user sees only tool timeline (no raw stream wall)
            if tool_calls:
                await _discard_response_draft(ui, turn)

            abort_strict_turn = False
            for tc in tool_calls:
                func_name = tc.get("name")
                args = tc.get("arguments", {})
                validation_just_passed = False

                # Emit rich status for each tool call (text labels, no emoji)
                _STATUS_ICONS = {
                    "read_file": "[read]", "list_files": "[list]", "find_files": "[find]",
                    "write_file": "[write]", "edit_file": "[edit]",
                    "run_shell": "[shell]", "start_shell_session": "[shell]",
                    "exec_shell_session": "[shell]", "get_repo_map": "[map]",
                }
                icon = _STATUS_ICONS.get(func_name, "[tool]")
                status_label = f"{icon} {func_name}"
                if func_name in ("read_file", "write_file", "edit_file") and args.get("path"):
                    status_label += f" {args['path']}"
                elif func_name == "run_shell" and args.get("command"):
                    cmd_short = args["command"][:40]
                    status_label += f" {cmd_short}"
                elif func_name == "list_files" and args.get("directory"):
                    status_label += f" {args['directory']}"
                await ui.emit_execution_event("status", status_label, phase="tool_use", meta={"thinking": True})

                # Emit the tool call to the sidebar so the user can see what's happening
                await ui.emit_execution_event("tool_call", func_name or "", phase="tool_use", meta={"tool": func_name, "args": args})

                # Manual approval logic (or for write_file in review mode: diff review below)
                if review_mode and func_name != "write_file":
                    approved = await ui.request_approval(func_name, args)
                    if not approved:
                        continue

                # Block read_file without path: inject clear observation and skip backend call
                if func_name == "read_file":
                    path_val = (args.get("path") or args.get("file") or args.get("file_path") or "").strip()
                    if not path_val:
                        obs = 'Error: read_file requires argument "path". Example: {"path": "README.md"}.'
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        if _looks_like_read_only_goal(goal):
                            await ui.emit_execution_event("error", obs, phase="done", meta={"final": True})
                            return {"ok": False, "status": "failed", "logs": logs, "error": obs}
                        continue

                # Block write_file without path or content: avoid Pydantic validation error, give clear hint
                if func_name == "write_file":
                    path_val = (args.get("path") or args.get("file") or args.get("file_path") or "").strip()
                    content_val = args.get("content")
                    if not path_val or content_val is None:
                        obs = (
                            'Error: write_file requires both "path" and "content". '
                            'Example: {"path": "script.py", "content": "# your code here"}'
                        )
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        continue
                    content_str = (content_val if isinstance(content_val, str) else str(content_val)).strip()
                    if _is_write_file_placeholder(content_str, path_val):
                        obs = (
                            'Error: write_file "content" must be the COMPLETE file content, not a placeholder or empty string. '
                            'The content you provided looks like a placeholder or was truncated (empty). '
                            'Do NOT call read_file again — you already have the file content from your previous read. '
                            'Your next call must be write_file(path, content) with the FULL script in the "content" argument.'
                        )
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        # Steer model to write_file instead of looping on read_file (self-correction)
                        placeholder_reminder = (
                            "Do not call read_file again. Your next action must be a single write_file with the complete file content (the full code)."
                        )
                        self.history.append({"role": "user", "content": placeholder_reminder})
                        save_db_message(session_id, "user", placeholder_reminder, log_type="context")
                        continue

                command_arg = str(args.get("command") or "").strip() if func_name in ("run_shell", "exec_shell_session") else ""
                path_arg = str(args.get("path") or args.get("file") or "").strip() if func_name in ("write_file", "edit_file", "read_file") else ""
                normalized_path_arg = _normalize_workspace_path(path_arg, self.root) if path_arg else ""

                # Strict single-file: if user requested "timeout Ns python3 target.py" but agent sent bare "python3 target.py", rewrite and run (no deny loop)
                if (
                    func_name in ("run_shell", "exec_shell_session")
                    and command_arg
                    and turn.strict_target
                    and turn.requested_commands
                ):
                    requested_with_timeout = [
                        c for c in turn.requested_commands
                        if re.search(r"\btimeout\s+\d+[smh]?\s+python3\b", c)
                        and _command_targets_strict_file(c, turn.strict_target, self.root)
                    ]
                    if requested_with_timeout and _command_targets_strict_file(command_arg, turn.strict_target, self.root):
                        if not re.search(r"\btimeout\s+\d+[smh]?\s+", command_arg):
                            exact_cmd = requested_with_timeout[0]
                            args["command"] = exact_cmd
                            command_arg = exact_cmd

                # Block write_file to README/LICENSE/CONTRIBUTING unless the user explicitly asked to create/modify that file
                if func_name == "write_file" and path_arg:
                    path_basename_lower = Path(path_arg).name.lower()
                    if path_basename_lower in _PROTECTED_DOC_BASENAMES and not _goal_requests_write_to_path(goal, path_arg):
                        obs = (
                            f"POLICY_DENY: You must NOT overwrite `{Path(path_arg).name}` unless the user explicitly asked to create or modify that file. "
                            "The user did not ask to change this file. Use run_shell or read_file only as requested."
                        )
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        continue

                # In review mode, write_file goes through diff review
                if review_mode and func_name == "write_file":
                    path = (args.get("path") or args.get("file") or "").strip()
                    new_content = args.get("content", "")
                    old_content = ""
                    if path:
                        try:
                            p = resolve_repo_path(Path(self.root), path)
                            if p.exists():
                                old_content = read_repo_file(p)
                        except Exception:
                            pass
                    decision = await ui.request_review(path or "?", old_content, new_content, root=self.root)
                    if decision is None:
                        obs = f"User rejected the write to {path}."
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        continue
                    args = {**args, "content": decision}

                if (
                    turn.strict_target
                    and turn.stdlib_only_required
                    and func_name == "write_file"
                    and normalized_path_arg == turn.strict_target_rel
                    and str(turn.strict_target).lower().endswith(".py")
                ):
                    external_imports = _detect_external_python_imports(str(args.get("content", "")))
                    if external_imports:
                        obs = (
                            f"POLICY_DENY: `{Path(turn.strict_target).name}` must use Python standard library only. "
                            f"External imports detected: {', '.join(external_imports)}."
                        )
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        abort_strict_turn = True
                        break

                if (
                    turn.strict_target
                    and turn.failed_validation_command
                    and func_name == "write_file"
                    and normalized_path_arg == turn.strict_target_rel
                    and _is_noninteractive_python_failure(turn.failed_validation_command, turn.last_valid_observation)
                    and "input(" in str(args.get("content", ""))
                ):
                    obs = (
                        f"POLICY_DENY: `{Path(turn.strict_target).name}` must succeed with the requested non-interactive command. "
                        "This repair still introduces `input()`, which would block again without stdin."
                    )
                    await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                    self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                    save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                    abort_strict_turn = True
                    break

                if (
                    turn.strict_target
                    and func_name == "write_file"
                    and normalized_path_arg == turn.strict_target_rel
                    and turn.exact_content_expected is not None
                ):
                    candidate_content = _normalize_exact_content(str(args.get("content", "")))
                    if candidate_content != turn.exact_content_expected:
                        turn.exact_content_denials += 1
                        obs = (
                            f"POLICY_DENY: `{Path(turn.strict_target).name}` must be written with the exact requested content. "
                            "Do not paraphrase, expand, or replace it with another implementation."
                        )
                        await ui.emit_execution_event("tool_result", obs, phase="tool_use", meta={"tool": func_name, "success": False})
                        self.history.append({"role": "user", "content": f'<tool_observation name="{func_name}">\n{obs}\n</tool_observation>'})
                        save_db_message(session_id, "user", self.history[-1]["content"], log_type="context")
                        if turn.exact_content_denials >= turn.repair_budget:
                            user_error = _summarize_failure_for_user(obs, turn)
                            final_error = (
                                f"Le contenu exact demandé pour `{Path(turn.strict_target).name}` a été refusé {turn.exact_content_denials} fois. "
                                "Arrêt pour éviter une boucle de réécriture invalide.\n\nStatus: FAILED"
                            )
                            self.history.append({"role": "assistant", "content": final_error})
                            await ui.emit_execution_event("error", user_error, phase="done", meta={"final": True})
                            return {"ok": False, "status": "failed", "logs": logs, "error": user_error}
                        abort_strict_turn = True
                        break

                outcome = await execute_tool_call(
                    func_name=func_name,
                    args=args,
                    root=self.root,
                    policy=None,
                    loop_guard=loop_guard,
                    retry_engine=retry_engine,
                )

                # Patch 1: after write_file to .py, run py_compile; if it fails, treat as failure so we don't set target_written
                if (
                    func_name == "write_file"
                    and outcome.success
                    and path_arg
                    and str(path_arg).lower().endswith(".py")
                ):
                    full_path = (Path(self.root) / normalized_path_arg).resolve() if normalized_path_arg else (Path(self.root) / path_arg).resolve()
                    if full_path.is_file():
                        py_ok, py_err = _py_compile_check(full_path, self.root)
                        if not py_ok:
                            outcome.observation = (
                                "Error: Syntax check (py_compile) failed:\n"
                                + (py_err or "unknown")
                                + "\n\n(File was written but has syntax errors. Fix them then call run_shell to validate.)\n\n---\n\n"
                                + (outcome.observation or "")
                            )

                if outcome.success:
                    any_tool_succeeded = True
                turn.mark_tool_result(func_name or "", outcome.observation, outcome.success)

                if turn.strict_target and func_name == "read_file" and outcome.success and normalized_path_arg == turn.strict_target_rel and turn.failed_validation_command:
                    turn.repair_reads += 1
                if turn.strict_target and func_name == "write_file" and outcome.success and normalized_path_arg == turn.strict_target_rel:
                    turn.target_written = True
                    turn.validated_after_last_write = False  # Next run_shell on target will set it True if it succeeds
                    turn.exact_content_denials = 0
                    if turn.exact_content_expected is not None:
                        turn.exact_content_satisfied = (
                            _normalize_exact_content(str(args.get("content", ""))) == turn.exact_content_expected
                        )
                    if turn.failed_validation_command:
                        turn.repair_rewritten = True
                if turn.strict_target and func_name in ("run_shell", "exec_shell_session") and outcome.success:
                    if _command_targets_strict_file(command_arg, turn.strict_target, self.root):
                        turn.validated_after_last_write = True  # This run targeted the file and succeeded (after the last write)
                    matched_requested = _matching_requested_command(command_arg, turn.requested_commands, turn.strict_target, self.root)
                    if matched_requested:
                        turn.mark_requested_command_completed(matched_requested)
                    validation_just_passed = (
                        turn.failed_validation_command is not None
                        and command_arg == turn.failed_validation_command
                        and re.search(r"\btimeout\s+\d+[smh]?\s+python3\b", command_arg)
                    )
                    if not validation_just_passed and "[preview_timeout_reached]" in (outcome.observation or ""):
                        if _command_targets_strict_file(command_arg, turn.strict_target, self.root):
                            validation_just_passed = True
                    if turn.failed_validation_command and command_arg == turn.failed_validation_command:
                        turn.failed_validation_command = None
                        turn.repair_attempts = 0
                        turn.repair_reads = 0
                        turn.repair_rewritten = False

                # Emit the tool result to the sidebar immediately — don't wait for LLM to re-present it
                await ui.emit_execution_event(
                    "tool_result", outcome.observation, phase="tool_use",
                    meta={"tool": func_name, "success": outcome.success},
                )

                obs = outcome.observation
                if "BLOCKED_REPEAT" in obs:
                    if func_name == "read_file":
                        obs = obs.rstrip() + "\nYour next call must be write_file(path, content) with the full file content. Do not call read_file again."
                    else:
                        obs = obs.rstrip() + "\nDo not call any tool again. Reply with Status: FAILED and a one-sentence explanation for the user."
                # When in strict-target turn, read_file failed with "file not found" and model used wrong path (e.g. name from a comment): remind target path
                if (
                    func_name == "read_file"
                    and not outcome.success
                    and turn.strict_target
                    and "file not found" in (outcome.observation or "").lower()
                    and normalized_path_arg
                    and normalized_path_arg != turn.strict_target_rel
                ):
                    obs = (
                        obs.rstrip()
                        + f"\n\nRappel: le fichier cible de cette tâche est `{turn.strict_target_rel}`. "
                        "Utilise ce chemin pour read_file et run_shell (pas un autre nom, ex. pas le nom dans un commentaire du fichier)."
                    )
                # Truncate huge observations in history so the model doesn't "continue" them next turn
                if len(obs) > _MAX_HISTORY_OBS_CHARS:
                    obs = (
                        obs[:_MAX_HISTORY_OBS_CHARS].rstrip()
                        + f"\n\n... [truncated, {len(outcome.observation)} chars total]."
                    )
                obs = f"<tool_observation name=\"{func_name}\">\n{obs}\n</tool_observation>"
                self.history.append({"role": "user", "content": obs})
                save_db_message(session_id, "user", obs, log_type="context")

                # Patch 2: after BLOCKED_REPEAT on read_file, inject explicit user message so next action is write_file not another read
                if (
                    func_name == "read_file"
                    and "BLOCKED_REPEAT" in (outcome.observation or "")
                ):
                    blocked_read_hint = (
                        "Tu as déjà lu ce fichier. Ta **seule** prochaine action pour avancer est : **write_file**(path, content) avec le contenu **complet** du fichier. "
                        "Ne rappelle pas read_file. Si tu n'as plus le contenu en contexte, envoie write_file avec tout le code du fichier (celui que tu as lu ou que tu dois produire)."
                    )
                    self.history.append({"role": "user", "content": blocked_read_hint})
                    save_db_message(session_id, "user", blocked_read_hint, log_type="context")

                # When edit_file is rejected due to syntax, steer model toward write_file to avoid retry loop → BLOCKED_REPEAT
                if (
                    func_name == "edit_file"
                    and not outcome.success
                    and "syntax_error_after_edit" in (outcome.observation or "")
                ):
                    edit_feedback = (
                        "L'édition a été rejetée car le fichier aurait une erreur de syntaxe. "
                        "Pour des changements sur tout le fichier (ex. remplacer plusieurs TODO), utilise **write_file** avec le contenu complet — c'est plus fiable qu'edit_file avec une seule instruction. "
                        "Sinon utilise write_file pour remplacer tout le fichier avec un contenu corrigé, au lieu de réessayer edit_file."
                    )
                    self.history.append({"role": "user", "content": edit_feedback})
                    save_db_message(session_id, "user", edit_feedback, log_type="context")

                # Validation just passed (timeout Ns python3): complete immediately to avoid model re-running the same command in a loop
                if validation_just_passed:
                    final_response = (
                        normalize_final_response(turn.slope_trailing_response)
                        if turn.slope_trailing_response
                        else _strict_success_response(turn.strict_target, turn.requested_commands)
                    )
                    final_response = _fix_final_response_py_path(final_response, turn.strict_target)
                    self.history.append({"role": "assistant", "content": final_response})
                    await ui.emit_execution_event(
                        "response", final_response, phase="done",
                        meta={"final": True, "slope_trailing": bool(turn.slope_trailing_response)},
                    )
                    return {"ok": True, "status": "completed", "logs": logs, "response": final_response}

                if turn.strict_target and not outcome.success:
                    abort_strict_turn = True

                if (
                    turn.strict_target
                    and func_name in ("run_shell", "exec_shell_session")
                    and not outcome.success
                    and _is_validation_failure_observation(outcome.observation)
                    and _is_repairable_requested_command(command_arg, turn.strict_target, self.root)
                ):
                    # Autonomy: repair when running the target .py failed, even if user didn't ask to "run" (e.g. "complete cube.py" only)
                    matched_requested = _matching_requested_command(command_arg, turn.requested_commands, turn.strict_target, self.root)
                    command_to_rerun = matched_requested or command_arg
                    if command_to_rerun:
                        if turn.failed_validation_command == command_to_rerun:
                            turn.repair_attempts += 1
                        else:
                            turn.failed_validation_command = command_to_rerun
                            turn.repair_attempts = 1
                        turn.repair_reads = 0
                        turn.repair_rewritten = False
                        turn.exact_content_denials = 0
                        turn.last_valid_observation = outcome.observation
                        turn.last_tool_success = False
                        turn.last_tool_name = func_name

                        if turn.repair_attempts >= turn.repair_budget:
                            user_error = _summarize_failure_for_user(outcome.observation, turn)
                            final_error = (
                                f"Validation a échoué après {turn.repair_attempts} tentative(s) pour `{Path(turn.strict_target).name}`. "
                                "Le script a été généré mais des erreurs restent. Vous pouvez corriger à la main ou relancer.\n\nStatus: FAILED"
                            )
                            self.history.append({"role": "assistant", "content": final_error})
                            await ui.emit_execution_event("error", user_error, phase="done", meta={"final": True})
                            return {"ok": False, "status": "failed", "logs": logs, "error": user_error}

                        repair_feedback = (
                            f"REPAIR_REQUIRED: Validation failed for `{Path(turn.strict_target).name}`. "
                            + (
                                "Prochaine action : pour une correction minime (ex. import manquant), privilégie **edit_file** avec un diff minimal (une ou quelques lignes). Sinon utilise **write_file**. Puis relance la même validation. "
                                if _is_simple_fix_failure(outcome.observation)
                                else "Prochaine action : corrige l'erreur avec un seul **write_file** sur ce fichier, puis relance la même validation. "
                                "Réécris le fichier **en entier** (pas de modification partielle) ; ne modifie pas les séquences d'échappement (\\033, \\x1b) dans les chaînes. "
                            )
                            + " Si l'erreur pointe une ligne ou un bloc précis (ex. File \"...\", line 82), modifie uniquement cette zone avec **edit_file** (diff minimal). "
                            + " N'appelle pas run_shell tant que le fichier n'est pas corrigé. Évite read_file en boucle ou d'autres outils inutiles. "
                            + " Tu peux lire la cible une fois si nécessaire, puis la réécrire avec write_file, puis relancer exactement la même validation."
                            + _repair_guidance_for_failure(command_arg, outcome.observation)
                        )
                        self.history.append({"role": "user", "content": repair_feedback})
                        save_db_message(session_id, "user", repair_feedback, log_type="context")

                if turn.can_finalize_strict():
                    final_response = (
                        normalize_final_response(turn.slope_trailing_response)
                        if turn.slope_trailing_response
                        else _strict_success_response(turn.strict_target, turn.requested_commands)
                    )
                    final_response = _fix_final_response_py_path(final_response, turn.strict_target)
                    self.history.append({"role": "assistant", "content": final_response})
                    await ui.emit_execution_event(
                        "response", final_response, phase="done",
                        meta={"final": True, "slope_trailing": bool(turn.slope_trailing_response)},
                    )
                    return {"ok": True, "status": "completed", "logs": logs, "response": final_response}

                if _looks_like_read_only_goal(goal) and func_name == "read_file":
                    raw_obs = outcome.observation.strip()
                    if _is_failed_read_observation(raw_obs):
                        await ui.emit_execution_event("error", raw_obs, phase="done", meta={"final": True})
                        return {"ok": False, "status": "failed", "logs": logs, "error": raw_obs}
                    final_read = (
                        (normalize_final_response(turn.slope_trailing_response) + "\n\nStatus: DONE")
                        if turn.slope_trailing_response
                        else (_summarize_read_observation(goal, path_val, raw_obs) + "\n\nStatus: DONE")
                    )
                    self.history.append({"role": "assistant", "content": final_read})
                    await ui.emit_execution_event(
                        "response", final_read, phase="done",
                        meta={"final": True, "slope_trailing": bool(turn.slope_trailing_response)},
                    )
                    return {"ok": True, "status": "completed", "logs": logs, "response": final_read}

                if abort_strict_turn:
                    break

            # If we had more than max_tools_per_turn, tell the model only first 3 were run
            if excess > 0:
                feedback = f"Only the first {max_tools_per_turn} tool calls were executed. You sent {excess + max_tools_per_turn}. Next message: use at most {max_tools_per_turn} <tool_use> tags."
                self.history.append({"role": "user", "content": feedback})
                save_db_message(session_id, "user", feedback, log_type="context")

            # If the model sent Status: DONE/FAILED in the same message as <tool_use>, remind it not to
            if re.search(r"Status:\s*(?:DONE|FAILED)", content_str, re.IGNORECASE):
                reminder = (
                    "PROTOCOL_VIOLATION: You must NOT write Status: DONE or Status: FAILED in the same message as <tool_use>. "
                    "Send ONLY <tool_use> tag(s) in one message; in the NEXT message, after you see tool results, reply with your answer and exactly one status line."
                )
                self.history.append({"role": "user", "content": reminder})
                save_db_message(session_id, "user", reminder, log_type="context")

            # List-only request: we ran list_files only; complete the turn without another LLM call to avoid model adding unrelated actions
            if len(tool_calls) == 1 and tool_calls[0].get("name") == "list_files":
                _goal_lower = (goal or "").strip().lower()
                _list_goal = _looks_like_list_only_request(_goal_lower)
                if _list_goal and any_tool_succeeded:
                    _directory = str(tool_calls[0].get("arguments", {}).get("directory", "."))
                    _last_list_obs = ""
                    for m in reversed(self.history):
                        content = m.get("content", "")
                        if m.get("role") == "user" and '<tool_observation name="list_files">' in content:
                            _last_list_obs = re.sub(r"</?tool_observation[^>]*>", "", content).strip()
                            break
                    _final = f"{_summarize_list_observation(_last_list_obs, _directory)}\n\nStatus: DONE"
                    await ui.emit_execution_event("response", _final, phase="done", meta={"final": True})
                    return {"ok": True, "status": "completed", "logs": logs, "response": _final}

            # If every tool call this step was blocked (BLOCKED_REPEAT), stop the loop to avoid infinite retries
            step_observations = [
                m.get("content", "") for m in self.history
                if m.get("role") == "user" and "<tool_observation" in m.get("content", "")
            ]
            recent_obs = step_observations[-len(tool_calls):] if len(tool_calls) else []
            all_blocked = (
                len(recent_obs) >= 1
                and all("BLOCKED_REPEAT" in o for o in recent_obs)
            )
            if all_blocked:
                summary = _summarize_failure_for_user(recent_obs[-1] if recent_obs else "BLOCKED_REPEAT_TOOL", turn)
                await ui.emit_execution_event(
                    "error",
                    summary,
                    phase="done",
                    meta={"final": True},
                )
                return {"ok": False, "status": "failed", "logs": logs, "error": summary}

        # Loop exhausted without completion — prefer the last tool observation over the
        # last LLM output (which is likely a <tool_use> tag, not useful to the user).
        last_obs = None
        for m in reversed(self.history):
            content = m.get("content", "")
            if m.get("role") == "user" and "<tool_observation" in content:
                last_obs = re.sub(r"</?tool_observation[^>]*>", "", content).strip()
                break
        last_response = last_obs or logs[-1] if (last_obs or logs) else "ShellGeist: max steps reached without completing the task."

        # Strict tasks should surface the last failure as a final error, even if
        # earlier steps (like write_file or py_compile) succeeded.
        if last_obs and turn.strict_target and is_failed_result(last_obs):
            summary = _summarize_failure_for_user(last_response, turn)
            await _discard_response_draft(ui, turn)
            await ui.emit_execution_event("error", summary, phase="done", meta={"final": True})
            return {"ok": False, "status": "failed", "logs": logs, "error": summary}

        # If no tool ever succeeded and the last observation looks like a failure,
        # surface this as an explicit error instead of a generic 'stopped' status.
        if last_obs and not any_tool_succeeded and is_failed_result(last_obs):
            summary = _summarize_failure_for_user(last_response, turn)
            await _discard_response_draft(ui, turn)
            await ui.emit_execution_event("error", summary, phase="done", meta={"final": True})
            return {"ok": False, "status": "failed", "logs": logs, "error": summary}

        await _discard_response_draft(ui, turn)
        # Prefer slope trailing prose when loop exhausted (e.g. slope wrote file but we didn't hit can_finalize_strict)
        final_response = (
            normalize_final_response(turn.slope_trailing_response)
            if turn.slope_trailing_response
            else last_response
        )
        final_response = _fix_final_response_py_path(final_response, turn.strict_target if turn else None)
        await ui.emit_execution_event(
            "response", final_response, phase="done",
            meta={"final": True, "slope_trailing": bool(turn.slope_trailing_response)},
        )
        return {"ok": True, "status": "stopped", "logs": logs}

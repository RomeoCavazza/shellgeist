"""Tool runtime helpers: argument normalization, validation, session healing."""
from __future__ import annotations

import json
import re
from typing import Any

SHELL_SESSION_TOOLS = {
    "exec_shell_session",
    "read_shell_session",
    "write_shell_session",
    "close_shell_session",
}

EDIT_TOOLS = {"write_file", "edit_apply", "edit_apply_full"}
EXECUTION_TOOLS = {"run_shell", "run_nix_python", "exec_shell_session"}


def normalize_tool_args(
    func_name: str,
    tc: dict[str, Any],
    current_args: Any,
    *,
    last_shell_session_id: str | None = None,
) -> dict[str, Any]:
    args: dict[str, Any]
    if isinstance(current_args, dict):
        args = dict(current_args)
    elif isinstance(tc.get("input"), dict):
        args = dict(tc.get("input") or {})
    else:
        args = {}

    if not args and isinstance(tc, dict):
        for key in (
            "command",
            "file",
            "path",
            "content",
            "text",
            "instruction",
            "session_id",
            "wait_ms",
            "max_bytes",
            "python_packages",
        ):
            if key in tc:
                args[key] = tc.get(key)

    if "command" not in args:
        for alias in ("cmd", "shell_command", "script"):
            value = args.get(alias)
            if isinstance(value, str) and value.strip():
                args["command"] = value
                break

    if "file" not in args and isinstance(args.get("path"), str):
        args["file"] = args.get("path")

    if func_name in SHELL_SESSION_TOOLS and last_shell_session_id:
        sid = str(args.get("session_id") or "").strip()
        if not sid or "{" in sid or "}" in sid:
            args["session_id"] = last_shell_session_id

    return args


def missing_required_args(func_name: str, args: dict[str, Any]) -> list[str]:
    required_by_tool: dict[str, list[str]] = {
        "run_shell": ["command"],
        "run_nix_python": ["command"],
        "write_file": ["file", "content"],
        "edit_file": ["file", "instruction"],
        "exec_shell_session": ["session_id", "command"],
        "read_shell_session": ["session_id"],
        "write_shell_session": ["session_id", "input"],
        "close_shell_session": ["session_id"],
    }
    required = required_by_tool.get(func_name, [])
    missing: list[str] = []
    for key in required:
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
    return missing


def build_tool_meta(func_name: str, args: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {"tool": func_name}
    target_file = args.get("file") or args.get("path")
    if isinstance(target_file, str) and target_file.strip():
        meta["file"] = target_file.strip()
    return meta


def build_policy_args(args: dict[str, Any], *, task_terminal_only: bool) -> dict[str, Any]:
    policy_args = dict(args)
    if task_terminal_only:
        policy_args["__terminal_only"] = True
    return policy_args


def detect_execution_evidence(func_name: str, res_str: str) -> tuple[bool, bool]:
    touched_code = func_name in EDIT_TOOLS
    verified_execution = False

    if func_name in EXECUTION_TOOLS:
        low = str(res_str or "").lower()
        if "error:" not in low and "[exit_code=" not in low:
            try:
                shell_obj = json.loads(res_str)
                if isinstance(shell_obj, dict):
                    cmd_rc = shell_obj.get("command_exit_code")
                    cmd_ok = False
                    if cmd_rc is None:
                        cmd_ok = True
                    elif isinstance(cmd_rc, int):
                        cmd_ok = cmd_rc == 0
                    elif isinstance(cmd_rc, str) and cmd_rc.strip().isdigit():
                        cmd_ok = int(cmd_rc.strip()) == 0
                    if shell_obj.get("ok") is True and cmd_ok:
                        verified_execution = True
                else:
                    verified_execution = True
            except Exception:
                verified_execution = False

    return touched_code, verified_execution


def auto_heal_session_id(call_args: dict[str, Any], last_shell_session_id: str | None) -> tuple[dict[str, Any], bool]:
    if not last_shell_session_id:
        return call_args, False
    sid = str(call_args.get("session_id") or "").strip()
    if (
        not sid
        or sid == "{session_id}"
        or "{" in sid
        or "}" in sid
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", sid)
    ):
        healed = dict(call_args)
        healed["session_id"] = last_shell_session_id
        return healed, True
    return call_args, False

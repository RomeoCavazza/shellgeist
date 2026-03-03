"""Tool policy: approval rules, auto-approve patterns, confirmation guards."""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False


class ToolPolicy:
    def __init__(self, root: str, session_id: str) -> None:
        self.root = str(Path(root).resolve())
        self.project = Path(self.root).name
        self.session_id = str(session_id or "default")
        self._raw_policy = self._load_policy()

    def _load_policy(self) -> dict[str, Any]:
        raw = os.environ.get("SHELLGEIST_TOOL_POLICY_JSON", "").strip()
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _to_patterns(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            out: list[str] = []
            for item in value:
                s = str(item or "").strip()
                if s:
                    out.append(s)
            return out
        return []

    @staticmethod
    def _matches_any(tool_name: str, patterns: list[str]) -> bool:
        if not patterns:
            return False
        return any(fnmatch.fnmatch(tool_name, p) for p in patterns)

    def _collect_scoped_rules(self, tool_name: str) -> list[tuple[str, dict[str, Any]]]:
        rules: list[tuple[str, dict[str, Any]]] = []
        raw = self._raw_policy

        global_rules = raw.get("global")
        if isinstance(global_rules, dict):
            rules.append(("global", global_rules))

        project_rules = raw.get("project")
        if isinstance(project_rules, dict):
            by_name = project_rules.get(self.project)
            if isinstance(by_name, dict):
                rules.append((f"project:{self.project}", by_name))
            by_root = project_rules.get(self.root)
            if isinstance(by_root, dict):
                rules.append((f"project:{self.root}", by_root))

        session_rules = raw.get("session")
        if isinstance(session_rules, dict):
            by_session = session_rules.get(self.session_id)
            if isinstance(by_session, dict):
                rules.append((f"session:{self.session_id}", by_session))

        tool_rules = raw.get("tool")
        if isinstance(tool_rules, dict):
            direct = tool_rules.get(tool_name)
            if isinstance(direct, dict):
                rules.append((f"tool:{tool_name}", direct))

            for key, rule in tool_rules.items():
                if key == tool_name:
                    continue
                if not isinstance(rule, dict):
                    continue
                if fnmatch.fnmatch(tool_name, str(key)):
                    rules.append((f"tool:{key}", rule))

        return rules

    def evaluate(self, tool_name: str, args: dict[str, Any] | None = None) -> ToolPolicyDecision:
        args = args or {}

        if bool(args.get("__terminal_only")):
            command = str(args.get("command") or args.get("shell") or "").lower()
            content = str(args.get("content") or args.get("text") or "").lower()
            gui_markers = (
                "pygame",
                "pyglet",
                "tkinter",
                "glfw",
                "opengl",
                "x11",
                "sdl",
            )
            target = f"{command}\n{content}"
            if any(marker in target for marker in gui_markers):
                return ToolPolicyDecision(
                    allowed=False,
                    reason=(
                        "POLICY_DENY: terminal-only task forbids GUI/OpenGL stack. "
                        "Use pure terminal output (ASCII/stdout) instead."
                    ),
                )

        rules = self._collect_scoped_rules(tool_name)

        allow_patterns: list[str] = []
        for _, rule in rules:
            allow_patterns.extend(self._to_patterns(rule.get("allow")))
        if allow_patterns and not self._matches_any(tool_name, allow_patterns):
            return ToolPolicyDecision(
                allowed=False,
                reason="POLICY_DENY: tool not in allowlist for active scope.",
            )

        for scope, rule in rules:
            deny_patterns = self._to_patterns(rule.get("deny"))
            if self._matches_any(tool_name, deny_patterns):
                return ToolPolicyDecision(
                    allowed=False,
                    reason=f"POLICY_DENY: tool '{tool_name}' blocked by {scope} policy.",
                )

        for scope, rule in rules:
            if bool(rule.get("approval_required")):
                approved = bool(args.get("__policy_approved"))
                if not approved:
                    return ToolPolicyDecision(
                        allowed=False,
                        reason=(
                            f"POLICY_APPROVAL_REQUIRED: tool '{tool_name}' requires approval in {scope}. "
                            "Retry with __policy_approved=true when explicitly validated."
                        ),
                        requires_approval=True,
                    )

        return ToolPolicyDecision(allowed=True)

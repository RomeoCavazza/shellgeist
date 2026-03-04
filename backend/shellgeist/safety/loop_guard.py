"""Loop guard: detects and halts repetitive tool call patterns."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


@dataclass
class LoopGuardConfig:
    warn_threshold: int = 2
    block_threshold: int = 2
    outcome_block_threshold: int = 2
    success_repeat_threshold: int = 2
    global_call_limit: int = 40
    history_size: int = 16


class LoopGuardVerdict:
    ALLOW = "allow"
    BLOCK = "block"
    CIRCUIT = "circuit"


def is_failed_result(res_str: str) -> bool:
    s = (res_str or "").strip()
    if not s:
        return False
    if s.startswith("Error") or s.startswith("Blocked:") or s.startswith("BLOCKED_REPEAT"):
        return True
    if "[exit_code=" in s:
        return True
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and obj.get("ok") is False:
            return True
    except Exception:
        pass
    return False


class LoopGuard:
    def __init__(self, config: LoopGuardConfig | None = None) -> None:
        self.config = config or LoopGuardConfig()
        self.total_calls = 0
        self.call_counts: dict[str, int] = {}
        self.outcome_counts: dict[str, int] = {}
        self.success_counts: dict[str, int] = {}
        self.blocked_call_hashes: set[str] = set()
        self.recent_calls: deque[str] = deque(maxlen=self.config.history_size)

    def _hash_call(self, tool_name: str, args: dict[str, Any]) -> str:
        try:
            payload = f"{tool_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        except Exception:
            payload = f"{tool_name}:{args}"
        return sha256(payload.encode("utf-8", errors="replace")).hexdigest()

    def _hash_outcome(self, call_hash: str, result: str) -> str:
        key = f"{call_hash}:{(result or '')[:400]}"
        return sha256(key.encode("utf-8", errors="replace")).hexdigest()

    def _detect_ping_pong(self) -> bool:
        if len(self.recent_calls) < 4:
            return False
        a, b, c, d = list(self.recent_calls)[-4:]
        return a == c and b == d and a != b

    def check_call(self, tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
        self.total_calls += 1
        if self.total_calls > self.config.global_call_limit:
            return (
                LoopGuardVerdict.CIRCUIT,
                f"CIRCUIT_BREAKER: exceeded {self.config.global_call_limit} tool calls in one run.",
            )

        call_hash = self._hash_call(tool_name, args)
        self.recent_calls.append(call_hash)

        if call_hash in self.blocked_call_hashes:
            return (
                LoopGuardVerdict.BLOCK,
                "BLOCKED_REPEAT_TOOL: This exact tool call failed repeatedly. Do not retry it; choose another approach.",
            )

        count = self.call_counts.get(call_hash, 0) + 1
        self.call_counts[call_hash] = count
        if count >= self.config.block_threshold:
            return (
                LoopGuardVerdict.BLOCK,
                f"BLOCKED_REPEAT_TOOL: {tool_name} called {count} times with identical parameters. "
                "NO result was returned. Do NOT pretend this succeeded. Try a different approach.",
            )

        if self._detect_ping_pong():
            return (
                LoopGuardVerdict.BLOCK,
                "BLOCKED_PING_PONG: alternating repeated tool pattern detected (A-B-A-B).",
            )

        return (LoopGuardVerdict.ALLOW, "")

    def record_outcome(self, tool_name: str, args: dict[str, Any], result: str) -> tuple[bool, str]:
        call_hash = self._hash_call(tool_name, args)

        # If the call failed, reset call count so a retry is allowed.
        # The outcome_block_threshold below still catches repeated identical failures.
        if is_failed_result(result):
            self.call_counts[call_hash] = max(0, self.call_counts.get(call_hash, 1) - 1)

        # Track successful identical calls — block after success_repeat_threshold
        if not is_failed_result(result):
            s_count = self.success_counts.get(call_hash, 0) + 1
            self.success_counts[call_hash] = s_count
            if s_count >= self.config.success_repeat_threshold:
                self.blocked_call_hashes.add(call_hash)
                return (
                    True,
                    f"BLOCKED_SUCCESS_REPEAT: {tool_name} already succeeded with these exact parameters. "
                    "The action is DONE — do NOT call it again. Move on to the next step or say Status: DONE.",
                )
            return (False, "")

        outcome_hash = self._hash_outcome(call_hash, result)
        count = self.outcome_counts.get(outcome_hash, 0) + 1
        self.outcome_counts[outcome_hash] = count
        if count >= self.config.outcome_block_threshold:
            self.blocked_call_hashes.add(call_hash)
            return (
                True,
                "BLOCKED_REPEAT_OUTCOME: same failing result repeated multiple times for the same tool call.",
            )

        return (False, "")

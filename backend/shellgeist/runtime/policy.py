"""Tool execution policy: loop guard, retries, and dangerous command filters."""
from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Awaitable

from shellgeist.runtime.paths import resolve_repo_path
from shellgeist.config import env_int as _env_int

# ---------------------------------------------------------------------------
# Loop Guard (formerly safety/loop_guard.py)
# ---------------------------------------------------------------------------

@dataclass
class LoopGuardConfig:
    warn_threshold: int = 2
    block_threshold: int = 3
    outcome_block_threshold: int = 3
    success_repeat_threshold: int = 2
    global_call_limit: int = 40
    history_size: int = 16


class LoopGuardVerdict:
    ALLOW = "allow"
    BLOCK = "block"
    CIRCUIT = "circuit"


def is_failed_result(res_str: str) -> bool:
    s = (res_str or "").strip()
    if not s: return False
    # Catch both explicit errors, internal blocks, and policy denials
    if (s.startswith("Error") or
        s.startswith("Blocked:") or
        s.startswith("BLOCKED_") or
        s.startswith("POLICY_DENY") or
        s.startswith("CIRCUIT_BREAKER")):
        return True
    if "[exit_code=" in s:
        return True
    # FS/shell error patterns (with or without "Error:" prefix)
    lower = s.lower()
    if any(x in lower for x in (
        "directory not found",
        "file not found",
        "access denied",
        "workspace root is your home",
        "no such file or directory",
        "validation failed for ",
    )):
        return True
    try:
        obj = json.loads(s)
        return isinstance(obj, dict) and obj.get("ok") is False
    except Exception:
        return False


class LoopGuard:
    def __init__(self, config: LoopGuardConfig | None = None) -> None:
        self.config = config or LoopGuardConfig()
        self.total_calls = 0
        self.call_counts: dict[str, int] = {}
        self.outcome_counts: dict[str, int] = {}
        self.success_counts: dict[str, int] = {}
        self.failure_counts: dict[str, int] = {}
        self.blocked_call_hashes: set[str] = set()
        self.recent_calls: deque[str] = deque(maxlen=self.config.history_size)

    def _hash_call(self, tool_name: str, args: dict[str, Any]) -> str:
        try:
            payload = f"{tool_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        except Exception:
            payload = f"{tool_name}:{args}"
        return sha256(payload.encode("utf-8", errors="replace")).hexdigest()

    def _is_validation_like_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name not in ("run_shell", "exec_shell_session"):
            return False
        cmd = (args.get("command") or "").strip()
        if not cmd:
            return False
        return bool(
            re.search(r"\bpython3\s+-m\s+py_compile\b", cmd)
            or re.search(r"\btimeout\s+\S+\s+python3\b", cmd)
            or re.search(r"\bpython3\s+\S+\.py\b", cmd)
        )

    def _is_safe_repeatable_tool(self, tool_name: str) -> bool:
        return tool_name in ("read_file", "list_files", "find_files")

    def check_call(self, tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
        self.total_calls += 1
        if self.total_calls > self.config.global_call_limit:
            return LoopGuardVerdict.CIRCUIT, f"CIRCUIT_BREAKER: exceeded {self.config.global_call_limit} calls."
        
        call_hash = self._hash_call(tool_name, args)
        self.recent_calls.append(call_hash)
        if call_hash in self.blocked_call_hashes:
            hint = ""
            if tool_name == "run_shell":
                cmd = (args.get("command") or "").strip()
                if ".py" in cmd or "python" in cmd:
                    hint = " Use 'python3 <your_script>.py', not './your_script.py'. This call was blocked after too many repeats."
            return LoopGuardVerdict.BLOCK, "BLOCKED_REPEAT_TOOL: Exact call failed repeatedly." + hint
        
        count = self.call_counts.get(call_hash, 0) + 1
        self.call_counts[call_hash] = count
        block_threshold = self.config.block_threshold + (3 if self._is_validation_like_call(tool_name, args) else 0)
        if self._is_safe_repeatable_tool(tool_name):
            block_threshold += 2
        if count >= block_threshold:
            hint = ""
            if tool_name == "run_shell":
                cmd = (args.get("command") or "").strip()
                if ".py" in cmd or "python" in cmd:
                    hint = " Use 'python3 <your_script>.py'. This call was blocked after too many repeats."
            return LoopGuardVerdict.BLOCK, f"BLOCKED_REPEAT_TOOL: {tool_name} repeated {count} times." + hint
        
        return LoopGuardVerdict.ALLOW, ""

    def record_outcome(self, tool_name: str, args: dict[str, Any], result: str) -> tuple[bool, str]:
        call_hash = self._hash_call(tool_name, args)
        is_validation = self._is_validation_like_call(tool_name, args)
        if is_failed_result(result):
            # Track failure count — block after 2 identical failures
            f_count = self.failure_counts.get(call_hash, 0) + 1
            self.failure_counts[call_hash] = f_count
            failure_threshold = 4 if is_validation else 2
            if f_count >= failure_threshold:
                self.blocked_call_hashes.add(call_hash)
                hint = ""
                if tool_name == "run_shell":
                    cmd = (args.get("command") or "").strip()
                    if ".py" in cmd or "python" in cmd:
                        hint = " Use 'python3 <your_script>.py'. Stop retrying the same failing command."
                return True, f"BLOCKED_REPEAT_FAILURE: {tool_name} failed {f_count} times with same args. Stop retrying." + hint
        else:
            s_count = self.success_counts.get(call_hash, 0) + 1
            self.success_counts[call_hash] = s_count
            if (
                not is_validation
                and not self._is_safe_repeatable_tool(tool_name)
                and s_count >= self.config.success_repeat_threshold
            ):
                self.blocked_call_hashes.add(call_hash)
                return True, f"BLOCKED_SUCCESS_REPEAT: {tool_name} already succeeded."
        
        return False, ""


# ---------------------------------------------------------------------------
# Retry Engine (formerly safety/retry.py)
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    max_attempts: int = 3
    max_total_retries: int = 24
    base_backoff_ms: int = 180
    max_backoff_ms: int = 1800

    @classmethod
    def from_env(cls) -> RetryConfig:
        return cls(
            max_attempts=max(1, _env_int("SHELLGEIST_RETRY_MAX_ATTEMPTS", 3)),
            max_total_retries=max(0, _env_int("SHELLGEIST_RETRY_MAX_TOTAL", 24)),
        )


class RetryEngine:
    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig.from_env()
        self.total_retries_used = 0

    async def run_async(
        self,
        *,
        key: str,
        operation: Callable[[int], Awaitable[Any]],
        classify_result: Callable[[Any], tuple[str | None, str]] | None = None,
        on_retry: Callable[[int, str, str, int, Any | None], Awaitable[None]] | None = None,
    ) -> Any:
        attempts = 0
        last_result = None
        while attempts < self.config.max_attempts:
            attempts += 1
            try:
                result = await operation(attempts)
            except Exception as e:
                result = f"Error: {e}"
            
            err_class, reason = classify_result(result) if classify_result else (None, "")
            if err_class == "transient" and attempts < self.config.max_attempts and self.total_retries_used < self.config.max_total_retries:
                self.total_retries_used += 1
                delay_ms = int(100 * (2 ** (attempts - 1)))
                if on_retry:
                    await on_retry(attempts, err_class, reason, delay_ms, result)
                await asyncio.sleep(delay_ms / 1000.0)
                last_result = result
                continue
            return result
        return last_result


# ---------------------------------------------------------------------------
# Blocked Patterns (formerly safety/blocked.py)
# ---------------------------------------------------------------------------

_BLOCKED_PATTERNS = [
    r"(^|\s)rm\s+-rf\s+/",     # rm -rf /
    r"(^|\s)mkfs\b",            # mkfs
    r"(^|\s)dd\s+if=/dev/",     # dd writing to raw dev
    r"(^|\s)mv\s+.*\s+/",       # mv to /
    r"(^|\s)>\s*/dev/",         # writing to raw dev
]

def is_blocked(command: str) -> bool:
    """Return True if the command matches a blocked dangerous pattern."""
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return True
    return False

def classify_result_payload(res: Any, tool_name: str | None = None) -> tuple[str | None, str]:
    """Classify a tool or LLM result as success, failure, or transient for retry."""
    s = str(res or "").lower()
    if "timeout" in s or "connection error" in s or "busy" in s:
        return "transient", "network_timeout"
    if "rate limit" in s:
        return "transient", "rate_limited"
    return None, ""

"""Retry engine: exponential backoff, error classification, budget tracking."""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from shellgeist.config import env_int as _env_int

RetryClass = str


@dataclass
class RetryConfig:
    max_attempts: int = 3
    max_total_retries: int = 24
    max_consecutive_retries_per_key: int = 2
    base_backoff_ms: int = 180
    max_backoff_ms: int = 1800
    jitter_ms: int = 120

    @staticmethod
    def from_env() -> RetryConfig:
        return RetryConfig(
            max_attempts=max(1, _env_int("SHELLGEIST_RETRY_MAX_ATTEMPTS", 3)),
            max_total_retries=max(0, _env_int("SHELLGEIST_RETRY_MAX_TOTAL", 24)),
            max_consecutive_retries_per_key=max(0, _env_int("SHELLGEIST_RETRY_MAX_CONSECUTIVE_PER_KEY", 2)),
            base_backoff_ms=max(0, _env_int("SHELLGEIST_RETRY_BASE_MS", 180)),
            max_backoff_ms=max(0, _env_int("SHELLGEIST_RETRY_MAX_BACKOFF_MS", 1800)),
            jitter_ms=max(0, _env_int("SHELLGEIST_RETRY_JITTER_MS", 120)),
        )


@dataclass
class RetryReport:
    key: str
    attempts: int
    retries: int
    outcome: str
    error_class: RetryClass | None = None
    reason: str = ""


class RetryEngine:
    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig.from_env()
        self._total_retries_used = 0
        self._consecutive_retries_by_key: dict[str, int] = {}
        self._retry_count_by_class: dict[str, int] = {}

    @property
    def total_retries_used(self) -> int:
        return self._total_retries_used

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "total_retries_used": self._total_retries_used,
            "retry_count_by_class": dict(self._retry_count_by_class),
            "consecutive_retries_by_key": dict(self._consecutive_retries_by_key),
            "limits": {
                "max_attempts": self.config.max_attempts,
                "max_total_retries": self.config.max_total_retries,
                "max_consecutive_retries_per_key": self.config.max_consecutive_retries_per_key,
            },
        }

    def _can_retry(self, key: str) -> tuple[bool, str]:
        if self._total_retries_used >= self.config.max_total_retries:
            return False, "retry_budget_exhausted"
        if self._consecutive_retries_by_key.get(key, 0) >= self.config.max_consecutive_retries_per_key:
            return False, "retry_consecutive_limit_reached"
        return True, ""

    def _next_delay_ms(self, attempt: int) -> int:
        exp = self.config.base_backoff_ms * (2 ** max(0, attempt - 1))
        capped: int = min(exp, self.config.max_backoff_ms)
        jitter: int = random.randint(0, self.config.jitter_ms) if self.config.jitter_ms > 0 else 0
        return capped + jitter

    async def run_async(
        self,
        *,
        key: str,
        operation: Callable[[int], Awaitable[Any]],
        classify_result: Callable[[Any], tuple[RetryClass | None, str]] | None = None,
        on_retry: Callable[[int, RetryClass, str, int, Any | None], Awaitable[None] | None] | None = None,
    ) -> tuple[Any | None, RetryReport]:
        attempts = 0
        retries = 0
        last_error_class: RetryClass | None = None
        last_reason = ""

        while attempts < self.config.max_attempts:
            attempts += 1
            try:
                result = await operation(attempts)
                result_class: RetryClass | None = None
                result_reason = ""
                if classify_result is not None:
                    result_class, result_reason = classify_result(result)

                if result_class == "transient":
                    can_retry, retry_stop_reason = self._can_retry(key)
                    if attempts < self.config.max_attempts and can_retry:
                        delay_ms = self._next_delay_ms(attempts)
                        self._total_retries_used += 1
                        retries += 1
                        self._consecutive_retries_by_key[key] = self._consecutive_retries_by_key.get(key, 0) + 1
                        self._retry_count_by_class[result_class] = self._retry_count_by_class.get(result_class, 0) + 1
                        if on_retry is not None:
                            maybe = on_retry(attempts, result_class, result_reason, delay_ms, result)
                            if asyncio.iscoroutine(maybe):
                                await maybe
                        await asyncio.sleep(delay_ms / 1000.0)
                        continue

                    self._consecutive_retries_by_key[key] = 0
                    outcome = "failed"
                    reason = result_reason or retry_stop_reason or "transient_failure"
                    return None, RetryReport(
                        key=key,
                        attempts=attempts,
                        retries=retries,
                        outcome=outcome,
                        error_class="transient",
                        reason=reason,
                    )

                self._consecutive_retries_by_key[key] = 0
                return result, RetryReport(
                    key=key,
                    attempts=attempts,
                    retries=retries,
                    outcome="success",
                    error_class=result_class,
                    reason=result_reason,
                )
            except Exception as exc:
                exc_class, exc_reason = classify_exception(exc)
                last_error_class = exc_class
                last_reason = exc_reason
                can_retry, retry_stop_reason = self._can_retry(key)
                if exc_class == "transient" and attempts < self.config.max_attempts and can_retry:
                    delay_ms = self._next_delay_ms(attempts)
                    self._total_retries_used += 1
                    retries += 1
                    self._consecutive_retries_by_key[key] = self._consecutive_retries_by_key.get(key, 0) + 1
                    self._retry_count_by_class[exc_class] = self._retry_count_by_class.get(exc_class, 0) + 1
                    if on_retry is not None:
                        maybe = on_retry(attempts, exc_class, exc_reason, delay_ms, None)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    await asyncio.sleep(delay_ms / 1000.0)
                    continue

                self._consecutive_retries_by_key[key] = 0
                return None, RetryReport(
                    key=key,
                    attempts=attempts,
                    retries=retries,
                    outcome="failed",
                    error_class=exc_class,
                    reason=exc_reason or retry_stop_reason,
                )

        self._consecutive_retries_by_key[key] = 0
        return None, RetryReport(
            key=key,
            attempts=attempts,
            retries=retries,
            outcome="failed",
            error_class=last_error_class,
            reason=last_reason or "max_attempts_reached",
        )


def classify_exception(exc: Exception) -> tuple[RetryClass, str]:
    msg = str(exc or "").strip().lower()
    transient_hints = (
        "timeout",
        "timed out",
        "tempor",
        "connection",
        "unavailable",
        "econn",
        "reset by peer",
        "broken pipe",
        "stream failed",
        "provider_error",
    )
    if isinstance(exc, TimeoutError):
        return "transient", "timeout"
    if any(h in msg for h in transient_hints):
        return "transient", msg[:200]
    return "permanent", msg[:200] or "exception"


def classify_result_payload(result: Any, *, tool_name: str | None = None) -> tuple[RetryClass | None, str]:
    # Empty list/dict from tools like find_files/list_files is a valid result, not transient
    if isinstance(result, list | dict):
        return None, ""
    text = str(result or "").strip()
    if not text:
        return "transient", "empty_result"

    upper = text.upper()
    if "POLICY_DENY" in upper or "POLICY_APPROVAL_REQUIRED" in upper:
        return "policy", "policy_denied"
    if "TOOL_SCHEMA_ERROR" in upper or "PARSE_ERROR" in upper:
        return "schema", "schema_error"
    if "BLOCKED: UNSAFE" in upper or "UNSAFE COMMAND" in upper:
        return "safety", "safety_block"
    if "INVALID_ACTION" in upper:
        return "permanent", "invalid_action"

    lower = text.lower()
    if "timed out" in lower or "timeout" in lower or "connection failed" in lower:
        return "transient", "provider_timeout_or_connection"

    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("ok") is False:
            err = str(obj.get("error") or "").strip().lower()
            if err in {"invalid_session_id", "session_not_found", "session_terminated", "exec_failed", "read_failed", "write_failed", "start_failed", "close_failed", "list_failed"}:
                return "transient", err
            if "schema" in err or "validation" in err or err == "missing_session_id":
                return "schema", err or "schema_error"
            if "policy" in err:
                return "policy", err
            if "blocked" in err or "unsafe" in err:
                return "safety", err
            return "permanent", err or "tool_error"
    except Exception:
        pass

    if text.startswith("Error:"):
        return "permanent", (tool_name or "tool") + "_error"

    return None, ""

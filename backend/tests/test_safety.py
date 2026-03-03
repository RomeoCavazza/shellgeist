"""Tests for the safety subsystem."""
from __future__ import annotations

from shellgeist.safety import is_blocked
from shellgeist.safety.loop_guard import LoopGuard, LoopGuardConfig
from shellgeist.safety.retry import RetryConfig


def test_is_blocked_basics():
    assert is_blocked("rm -rf /") is True
    assert is_blocked("ls -la") is False
    assert is_blocked("echo hello") is False


def test_loop_guard_respects_limit():
    guard = LoopGuard(LoopGuardConfig(global_call_limit=3))
    verdict, _ = guard.check_call("test_tool", {"arg": "val"})
    assert verdict != "circuit"
    guard.total_calls = 4  # simulate exceeding limit
    verdict, _ = guard.check_call("test_tool", {"arg": "val"})
    assert verdict == "circuit"


def test_retry_config_defaults():
    cfg = RetryConfig()
    assert cfg.max_attempts >= 1
    assert cfg.base_backoff_ms > 0

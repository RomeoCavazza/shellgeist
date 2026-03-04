"""Tests for the loop guard — the main safety net against tool-call loops."""
from __future__ import annotations

from shellgeist.safety.loop_guard import (
    LoopGuard,
    LoopGuardConfig,
    LoopGuardVerdict,
    is_failed_result,
)

# ---------------------------------------------------------------------------
# is_failed_result
# ---------------------------------------------------------------------------

class TestIsFailedResult:
    def test_empty_string(self):
        assert not is_failed_result("")

    def test_success_message(self):
        assert not is_failed_result("Successfully wrote to README.md")

    def test_error_prefix(self):
        assert is_failed_result("Error: file not found")

    def test_blocked_prefix(self):
        assert is_failed_result("Blocked: policy denied")

    def test_blocked_repeat(self):
        assert is_failed_result("BLOCKED_REPEAT_TOOL: write_file called 3 times")

    def test_exit_code(self):
        assert is_failed_result("command failed [exit_code=1]")

    def test_json_ok_false(self):
        assert is_failed_result('{"ok": false, "error": "fail"}')

    def test_json_ok_true(self):
        assert not is_failed_result('{"ok": true}')


# ---------------------------------------------------------------------------
# check_call — identical call detection
# ---------------------------------------------------------------------------

class TestCheckCall:
    def test_first_call_allowed(self):
        guard = LoopGuard()
        verdict, _ = guard.check_call("write_file", {"path": "a.txt", "content": "hello"})
        assert verdict == LoopGuardVerdict.ALLOW

    def test_block_after_threshold(self):
        guard = LoopGuard(LoopGuardConfig(block_threshold=3))
        args = {"path": "a.txt", "content": "hello"}
        for _ in range(2):
            v, _ = guard.check_call("write_file", args)
            assert v == LoopGuardVerdict.ALLOW
        v, msg = guard.check_call("write_file", args)
        assert v == LoopGuardVerdict.BLOCK
        assert "BLOCKED_REPEAT_TOOL" in msg

    def test_different_args_not_blocked(self):
        guard = LoopGuard(LoopGuardConfig(block_threshold=2))
        guard.check_call("write_file", {"path": "a.txt", "content": "v1"})
        guard.check_call("write_file", {"path": "a.txt", "content": "v2"})
        v, _ = guard.check_call("write_file", {"path": "a.txt", "content": "v3"})
        assert v == LoopGuardVerdict.ALLOW

    def test_blocked_hash_stays_blocked(self):
        guard = LoopGuard(LoopGuardConfig(block_threshold=2))
        args = {"path": "a.txt", "content": "x"}
        guard.check_call("write_file", args)
        guard.check_call("write_file", args)  # hits threshold → blocked
        v, msg = guard.check_call("write_file", args)  # hash now in blocked set
        assert v == LoopGuardVerdict.BLOCK

    def test_circuit_breaker(self):
        guard = LoopGuard(LoopGuardConfig(global_call_limit=3))
        for i in range(3):
            guard.check_call("run_shell", {"command": f"echo {i}"})
        v, msg = guard.check_call("run_shell", {"command": "echo 4"})
        assert v == LoopGuardVerdict.CIRCUIT
        assert "CIRCUIT_BREAKER" in msg


# ---------------------------------------------------------------------------
# record_outcome — success repeat detection (the write_file ×5 fix)
# ---------------------------------------------------------------------------

class TestRecordOutcome:
    def test_failed_outcome_tracked(self):
        guard = LoopGuard(LoopGuardConfig(outcome_block_threshold=2))
        args = {"command": "false"}
        guard.check_call("run_shell", args)
        guard.record_outcome("run_shell", args, "Error: command failed")
        blocked, msg = guard.record_outcome("run_shell", args, "Error: command failed")
        assert blocked
        assert "BLOCKED_REPEAT_OUTCOME" in msg

    def test_success_not_treated_as_failure(self):
        guard = LoopGuard()
        args = {"path": "a.txt", "content": "ok"}
        guard.check_call("write_file", args)
        blocked, _ = guard.record_outcome("write_file", args, "Successfully wrote to a.txt")
        assert not blocked  # first success is fine

    def test_success_repeat_blocks_after_threshold(self):
        """The critical test: identical successful calls get blocked after success_repeat_threshold."""
        guard = LoopGuard(LoopGuardConfig(success_repeat_threshold=2))
        args = {"path": "README.md", "content": "# Hello"}
        # First call + success
        guard.check_call("write_file", args)
        blocked, _ = guard.record_outcome("write_file", args, "Successfully wrote to README.md")
        assert not blocked
        # Second identical call + same success → should block
        guard.check_call("write_file", args)
        blocked, msg = guard.record_outcome("write_file", args, "Successfully wrote to README.md")
        assert blocked
        assert "BLOCKED_SUCCESS_REPEAT" in msg
        assert "DONE" in msg

    def test_success_repeat_adds_to_blocked_hashes(self):
        """After success repeat block, subsequent check_call is also blocked."""
        guard = LoopGuard(LoopGuardConfig(success_repeat_threshold=2))
        args = {"path": "x.txt", "content": "data"}
        guard.check_call("write_file", args)
        guard.record_outcome("write_file", args, "Successfully wrote to x.txt")
        guard.check_call("write_file", args)
        guard.record_outcome("write_file", args, "Successfully wrote to x.txt")
        # Now check_call itself should block
        v, msg = guard.check_call("write_file", args)
        assert v == LoopGuardVerdict.BLOCK

    def test_different_success_results_not_blocked(self):
        guard = LoopGuard(LoopGuardConfig(success_repeat_threshold=2))
        args = {"path": "a.txt", "content": "v1"}
        guard.check_call("write_file", args)
        guard.record_outcome("write_file", args, "Successfully wrote to a.txt (new file)")
        args2 = {"path": "a.txt", "content": "v2"}
        guard.check_call("write_file", args2)
        blocked, _ = guard.record_outcome("write_file", args2, "Successfully wrote to a.txt\n\nDiff:...")
        assert not blocked  # different args → different hash


# ---------------------------------------------------------------------------
# Ping-pong detection
# ---------------------------------------------------------------------------

class TestPingPong:
    def test_abab_detected(self):
        guard = LoopGuard()
        guard.check_call("read_file", {"path": "a.txt"})
        guard.check_call("write_file", {"path": "a.txt", "content": "x"})
        guard.check_call("read_file", {"path": "a.txt"})
        v, msg = guard.check_call("write_file", {"path": "a.txt", "content": "x"})
        assert v == LoopGuardVerdict.BLOCK
        assert "PING_PONG" in msg

    def test_aabb_not_detected(self):
        guard = LoopGuard()
        guard.check_call("read_file", {"path": "a.txt"})
        guard.check_call("read_file", {"path": "a.txt"})
        guard.check_call("write_file", {"path": "a.txt", "content": "x"})
        v, _ = guard.check_call("write_file", {"path": "a.txt", "content": "x"})
        # This is A-A-B-B, not A-B-A-B. Blocked by block_threshold instead.
        assert v != LoopGuardVerdict.BLOCK or "PING_PONG" not in _


# ---------------------------------------------------------------------------
# write_file content dedup (in fs.py)
# ---------------------------------------------------------------------------

class TestWriteFileDedup:
    def test_skip_when_content_identical(self, tmp_path):
        """write_file should short-circuit when file already has exact content."""
        from shellgeist.tools.fs import write_file

        target = tmp_path / "test.md"
        target.write_text("# Hello", encoding="utf-8")
        result = write_file(path="test.md", content="# Hello", root=str(tmp_path))
        assert "NO_CHANGE" in result
        assert "already contains" in result

    def test_write_when_content_differs(self, tmp_path):
        from shellgeist.tools.fs import write_file

        target = tmp_path / "test.md"
        target.write_text("# Old", encoding="utf-8")
        result = write_file(path="test.md", content="# New", root=str(tmp_path))
        assert "Successfully wrote" in result
        assert target.read_text() == "# New"

    def test_write_new_file(self, tmp_path):
        from shellgeist.tools.fs import write_file

        result = write_file(path="new.md", content="# Created", root=str(tmp_path))
        assert "Successfully wrote" in result
        assert "(new file)" in result

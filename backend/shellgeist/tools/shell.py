"""Shell tools: run_shell, exec_shell_session, run_nix_python."""
from __future__ import annotations

import atexit
import errno
import json
import os
import re
import select
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from shellgeist.runtime.policy import is_blocked
from shellgeist.tools.base import registry


class ShellCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str


class StartShellSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    shell: str = "bash -i"
    command: str | None = None
    cwd: str | None = None


class WriteShellSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    input: str
    append_newline: bool = True


class ReadShellSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    timeout_ms: int = 250
    max_bytes: int = 65536


class ExecShellSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    command: str
    wait_ms: int = 350
    max_bytes: int = 65536


class CloseShellSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    force: bool = False


class ListShellSessionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class RunNixPythonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    python_packages: list[str] = []
    system_packages: list[str] = []
    pure: bool = False


@dataclass
class PTYShellSession:
    session_id: str
    process: subprocess.Popen[bytes]
    master_fd: int
    cwd: str
    shell: str
    created_at: float


@dataclass
class PTYShellSessionTombstone:
    session_id: str
    exit_code: int | None
    ended_at: float
    cwd: str
    shell: str
    reason: str


def _json_result(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _build_shell_env(root: str) -> dict[str, str]:
    env = os.environ.copy()
    runtime_bin = Path(sys.executable).resolve().parent
    venv_bin = Path(root) / ".venv" / "bin"
    path_parts: list[str] = [str(runtime_bin)]
    if venv_bin.exists() and venv_bin.is_dir():
        path_parts.append(str(venv_bin))
    path_parts.append(env.get("PATH", ""))
    env["PATH"] = ":".join([p for p in path_parts if p])
    return env


def _resolve_session_cwd(root: str, cwd: str | None) -> Path:
    root_path = Path(root).expanduser().resolve()
    if cwd is None or str(cwd).strip() == "":
        return root_path

    candidate = Path(cwd).expanduser()
    if not candidate.is_absolute():
        candidate = (root_path / candidate).resolve()
    else:
        candidate = candidate.resolve()

    try:
        candidate.relative_to(root_path)
    except ValueError:
        raise ValueError(f"cwd must stay inside repository root: {root_path}")

    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"cwd does not exist or is not a directory: {candidate}")

    return candidate


def _normalize_session_id(session_id: str | None) -> str:
    if session_id is None or session_id.strip() == "":
        return f"sh_{uuid.uuid4().hex[:12]}"
    sid = session_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", sid):
        raise ValueError("invalid session_id (allowed: A-Z a-z 0-9 _ . -; max 64 chars)")
    return sid


def _normalize_existing_session_id(session_id: str | None) -> tuple[str | None, dict | None]:
    if session_id is None or str(session_id).strip() == "":
        return None, {
            "ok": False,
            "error": "missing_session_id",
            "detail": "session_id is required for this tool. Start a session first and reuse its session_id.",
        }
    try:
        return _normalize_session_id(session_id), None
    except ValueError as e:
        return None, {
            "ok": False,
            "error": "invalid_session_id",
            "detail": str(e),
        }


def _normalize_command_text(command_text: str) -> str:
    # Some models include shell prompt prefix ('$ cmd'). Strip that prefix.
    return re.sub(r"^\s*\$\s+", "", command_text or "").strip()


def _is_valid_nix_attr_token(token: str) -> bool:
    # Supports simple nix attribute path tokens (e.g. pyglet, ffmpeg, xorg.libX11, nodejs_22).
    return bool(re.fullmatch(r"[A-Za-z0-9_.+-]+", token or ""))


def _read_pty_output(fd: int, timeout_ms: int, max_bytes: int) -> str:
    timeout_ms = max(0, min(timeout_ms, 120_000))
    max_bytes = max(1, min(max_bytes, 1_000_000))
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    chunks: list[bytes] = []
    total = 0
    got_any = False

    while total < max_bytes:
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            break
        # Once output starts, stop shortly after stream becomes idle.
        wait_time = min(remaining, 0.05 if got_any else remaining)
        ready, _, _ = select.select([fd], [], [], wait_time)
        if not ready:
            if got_any:
                break
            continue
        try:
            data = os.read(fd, min(4096, max_bytes - total))
        except BlockingIOError:
            continue
        except OSError as e:
            # PTY often raises EIO when the slave side closes.
            if e.errno == errno.EIO:
                break
            raise
        if not data:
            break
        got_any = True
        chunks.append(data)
        total += len(data)

    if not chunks:
        return ""
    return b"".join(chunks).decode("utf-8", errors="replace")


def _strip_ansi(s: str) -> str:
    # Remove common ANSI CSI escapes to simplify marker parsing.
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", s)


def _wait_process_exit_code(proc: subprocess.Popen[bytes], timeout_ms: int) -> int | None:
    if timeout_ms <= 0:
        return proc.poll()
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            return rc
        time.sleep(0.02)
    return proc.poll()


def _validate_shell_input_for_safety(command_text: str) -> tuple[bool, str | None]:
    lines = command_text.splitlines() or [command_text]
    for line in lines:
        check = line.strip()
        if not check:
            continue
        if is_blocked(check):
            return False, check
    return True, None


class PTYShellManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PTYShellSession] = {}
        self._tombstones: dict[str, PTYShellSessionTombstone] = {}
        self._tombstone_order: list[str] = []
        self._lock = threading.Lock()

    def _record_tombstone_locked(self, session: PTYShellSession, reason: str) -> None:
        tomb = PTYShellSessionTombstone(
            session_id=session.session_id,
            exit_code=session.process.poll(),
            ended_at=time.time(),
            cwd=session.cwd,
            shell=session.shell,
            reason=reason,
        )
        self._tombstones[session.session_id] = tomb
        self._tombstone_order = [sid for sid in self._tombstone_order if sid != session.session_id]
        self._tombstone_order.append(session.session_id)
        while len(self._tombstone_order) > 64:
            old = self._tombstone_order.pop(0)
            self._tombstones.pop(old, None)

    def _missing_session_error_locked(self, session_id: str) -> dict:
        tomb = self._tombstones.get(session_id)
        if tomb is not None:
            return {
                "ok": False,
                "error": "session_terminated",
                "session_id": session_id,
                "exit_code": tomb.exit_code,
                "ended_at": tomb.ended_at,
                "cwd": tomb.cwd,
                "shell": tomb.shell,
                "reason": tomb.reason,
            }
        return {
            "ok": False,
            "error": "session_not_found",
            "session_id": session_id,
            "active_sessions": sorted(self._sessions.keys()),
        }

    def _collect_dead_sessions_locked(self) -> None:
        dead_ids = [sid for sid, s in self._sessions.items() if s.process.poll() is not None]
        for sid in dead_ids:
            session = self._sessions.pop(sid)
            self._record_tombstone_locked(session, reason="process_exited")
            try:
                os.close(session.master_fd)
            except OSError:
                pass

    def start(self, *, root: str, session_id: str | None, shell: str, cwd: str | None) -> dict:
        sid = _normalize_session_id(session_id)
        shell_cmd = _normalize_command_text(shell or "") or "bash -i"
        if re.match(r"^\s*nix-shell\b", shell_cmd) and "--run" in shell_cmd:
            return {
                "ok": False,
                "error": "invalid_persistent_shell_command",
                "detail": (
                    "start_shell_session is for persistent interactive shells. "
                    "Do not use `nix-shell ... --run ...` here. "
                    "Use `nix-shell -p ...` for persistent sessions, or use run_shell for one-shot commands."
                ),
                "shell": shell_cmd,
            }
        argv = shlex.split(shell_cmd)
        if not argv:
            raise ValueError("empty shell command")
        token0 = os.path.basename(argv[0])
        allowed_roots = {"bash", "zsh", "fish", "sh", "nix-shell"}
        if token0 not in allowed_roots:
            return {
                "ok": False,
                "error": "invalid_persistent_shell_command",
                "detail": (
                    "start_shell_session is only for interactive/persistent shells "
                    "(`bash -i`, `zsh -i`, `nix-shell -p ...`). "
                    "Use run_shell (one-shot) or exec_shell_session (existing session) for normal commands."
                ),
                "shell": shell_cmd,
            }
        if argv[0] in {"bash", "zsh", "fish"} and all(a not in {"-i", "--interactive"} for a in argv[1:]):
            argv.append("-i")

        target_cwd = _resolve_session_cwd(root, cwd)
        env = _build_shell_env(root)

        with self._lock:
            self._collect_dead_sessions_locked()
            if sid in self._sessions:
                return {
                    "ok": False,
                    "error": "session_exists",
                    "detail": f"session '{sid}' already exists",
                }

            master_fd, slave_fd = os.openpty()
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(target_cwd),
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    text=False,
                    start_new_session=True,
                    close_fds=True,
                )
            finally:
                os.close(slave_fd)

            os.set_blocking(master_fd, False)
            session = PTYShellSession(
                session_id=sid,
                process=proc,
                master_fd=master_fd,
                cwd=str(target_cwd),
                shell=shell_cmd,
                created_at=time.time(),
            )
            self._sessions[sid] = session

        initial_output = _read_pty_output(master_fd, timeout_ms=200, max_bytes=65536)
        # Some failing shells (e.g., bad nix-shell command) can exit slightly after spawn.
        # Grace window prevents reporting "ok" then immediate "session_terminated".
        exit_code = _wait_process_exit_code(proc, timeout_ms=600)
        if exit_code is not None:
            with self._lock:
                dead = self._sessions.pop(sid, None)
                if dead is not None:
                    self._record_tombstone_locked(dead, reason="start_exited_immediately")
            try:
                os.close(master_fd)
            except OSError:
                pass
            return {
                "ok": False,
                "error": "session_start_failed",
                "session_id": sid,
                "exit_code": exit_code,
                "cwd": str(target_cwd),
                "shell": shell_cmd,
                "initial_output": initial_output,
            }
        return {
            "ok": True,
            "session_id": sid,
            "pid": proc.pid,
            "cwd": str(target_cwd),
            "shell": shell_cmd,
            "initial_output": initial_output,
        }

    def write(self, *, session_id: str, input_text: str, append_newline: bool) -> dict:
        sid, sid_err = _normalize_existing_session_id(session_id)
        if sid_err is not None:
            return sid_err
        assert sid is not None
        with self._lock:
            self._collect_dead_sessions_locked()
            session = self._sessions.get(sid)
            if session is None:
                return self._missing_session_error_locked(sid)

        payload = _normalize_command_text(input_text or "")
        if append_newline:
            payload += "\n"
        safe, blocked_line = _validate_shell_input_for_safety(payload)
        if not safe:
            return {
                "ok": False,
                "error": "blocked_command",
                "detail": f"unsafe command pattern detected: {blocked_line}",
            }
        data = payload.encode("utf-8", errors="replace")
        total = 0
        while total < len(data):
            written = os.write(session.master_fd, data[total:])
            total += written
        return {"ok": True, "session_id": sid, "written": total}

    def read(self, *, session_id: str, timeout_ms: int, max_bytes: int) -> dict:
        sid, sid_err = _normalize_existing_session_id(session_id)
        if sid_err is not None:
            return sid_err
        assert sid is not None
        with self._lock:
            self._collect_dead_sessions_locked()
            session = self._sessions.get(sid)
            if session is None:
                return self._missing_session_error_locked(sid)
            proc = session.process

        output = _read_pty_output(session.master_fd, timeout_ms=timeout_ms, max_bytes=max_bytes)
        exit_code = proc.poll()
        return {
            "ok": True,
            "session_id": sid,
            "alive": exit_code is None,
            "exit_code": exit_code,
            "output": output,
        }

    def exec(self, *, session_id: str, command: str, wait_ms: int, max_bytes: int) -> dict:
        sid, sid_err = _normalize_existing_session_id(session_id)
        if sid_err is not None:
            return sid_err
        assert sid is not None
        check_cmd = _normalize_command_text(command or "")
        if not check_cmd:
            return {"ok": False, "error": "empty_command"}
        safe, blocked_line = _validate_shell_input_for_safety(check_cmd)
        if not safe:
            return {
                "ok": False,
                "error": "blocked_command",
                "detail": f"unsafe command pattern detected: {blocked_line}",
            }
        marker = f"__SG_RC_{uuid.uuid4().hex[:12]}__"
        wrapped = f"{check_cmd}\n__sg_rc=$?\nprintf '\\n{marker}%s\\n' \"$__sg_rc\""
        write_res = self.write(session_id=sid, input_text=wrapped, append_newline=True)
        if not write_res.get("ok"):
            return write_res

        with self._lock:
            self._collect_dead_sessions_locked()
            session = self._sessions.get(sid)
            if session is None:
                return self._missing_session_error_locked(sid)
            proc = session.process
            fd = session.master_fd

        remaining_bytes = max(1, min(max_bytes, 1_000_000))
        deadline = time.monotonic() + (max(10, min(wait_ms, 120_000)) / 1000.0)
        chunks: list[str] = []
        found_marker = False

        while remaining_bytes > 0:
            now = time.monotonic()
            if now >= deadline:
                break
            step_ms = int(max(1, min(250, (deadline - now) * 1000)))
            chunk = _read_pty_output(fd, timeout_ms=step_ms, max_bytes=min(remaining_bytes, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining_bytes -= len(chunk.encode("utf-8", errors="replace"))
            if marker in _strip_ansi("".join(chunks)):
                found_marker = True
                break

        raw_output = "".join(chunks)
        parsed = _strip_ansi(raw_output)
        rc = None
        m = re.search(re.escape(marker) + r"(\d+)", parsed)
        if m:
            try:
                rc = int(m.group(1))
            except ValueError:
                rc = None

        if rc is not None:
            # Remove marker line from displayed output (both ANSI-stripped and raw variants).
            parsed = re.sub(rf"\n?{re.escape(marker)}\d+\n?", "\n", parsed)
            raw_output = re.sub(rf"\n?{re.escape(marker)}\d+\n?", "\n", raw_output)

        result = {
            "ok": rc in (None, 0),
            "session_id": sid,
            "alive": proc.poll() is None,
            "exit_code": proc.poll(),
            "command_exit_code": rc,
            "command_completed": bool(rc is not None or found_marker),
            "output": raw_output,
            "output_clean": parsed,
        }
        if rc not in (None, 0):
            result["error"] = "command_failed"
            result["detail"] = "command_failed_but_session_alive" if result["alive"] else "command_failed_and_session_exited"
        return result

    def close(self, *, session_id: str, force: bool) -> dict:
        sid, sid_err = _normalize_existing_session_id(session_id)
        if sid_err is not None:
            return sid_err
        assert sid is not None
        with self._lock:
            self._collect_dead_sessions_locked()
            session = self._sessions.pop(sid, None)
        if session is None:
            with self._lock:
                return self._missing_session_error_locked(sid)

        proc = session.process
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0 if not force else 0.2)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        try:
            os.close(session.master_fd)
        except OSError:
            pass
        with self._lock:
            self._record_tombstone_locked(session, reason="closed_by_tool")
        return {
            "ok": True,
            "session_id": sid,
            "exit_code": proc.poll(),
            "closed": True,
        }

    def list(self) -> dict:
        with self._lock:
            self._collect_dead_sessions_locked()
            sessions = []
            for sid, session in self._sessions.items():
                sessions.append(
                    {
                        "session_id": sid,
                        "pid": session.process.pid,
                        "alive": session.process.poll() is None,
                        "cwd": session.cwd,
                        "shell": session.shell,
                        "created_at": session.created_at,
                    }
                )
            terminated = []
            for sid in reversed(self._tombstone_order[-10:]):
                tomb = self._tombstones.get(sid)
                if tomb is None:
                    continue
                terminated.append(
                    {
                        "session_id": tomb.session_id,
                        "exit_code": tomb.exit_code,
                        "ended_at": tomb.ended_at,
                        "cwd": tomb.cwd,
                        "shell": tomb.shell,
                        "reason": tomb.reason,
                    }
                )
        return {"ok": True, "sessions": sessions, "terminated": terminated}

    def close_all(self) -> None:
        with self._lock:
            session_ids = list(self._sessions.keys())
        for sid in session_ids:
            self.close(session_id=sid, force=True)


_PTY_MANAGER = PTYShellManager()
atexit.register(_PTY_MANAGER.close_all)


@registry.register(
    description="Execute a shell command in the repository root.",
    input_model=ShellCommandInput
)
def run_shell(command: str, root: str, **kwargs: Any) -> str:
    """
    Execute a shell command and return its output.
    """
    # Guard: refuse to run in home directory (likely misconfigured root)
    home = str(Path.home().resolve())
    if str(Path(root).resolve()) == home:
        return (
            "Error: WORKSPACE ROOT is your HOME directory. "
            "Open Neovim inside a project folder first."
        )

    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command"

    # This tool executes each command in a fresh shell process.
    # A bare nix-shell opens a subshell that cannot persist across tool calls.
    if re.match(r"^\s*nix-shell\b", cmd) and "--run" not in cmd:
        return (
            "Error: nix-shell without --run is non-persistent in this tool. "
            "Use one-shot form, e.g. `nix-shell -p python3 --run 'python3 --version'`."
        )

    if is_blocked(cmd):
        return "Blocked: unsafe command pattern detected."

    try:
        env = _build_shell_env(root)

        p = subprocess.run(
            ["bash", "-lc", cmd],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        out = p.stdout or ""
        if p.returncode != 0:
            extra_hint = ""
            if p.returncode == 127 and re.match(r"^\s*(python3?|python|pip3?|pip)\b", cmd):
                extra_hint = (
                    "\nHint: interpreter not found in this stateless shell call. "
                    "Use one-shot Nix (`nix-shell -p python3 --run 'python3 ...'`) "
                    "or a persistent PTY session (`start_shell_session` with `shell: \"nix-shell -p ...\"`)."
                )
            suffix = "" if out.endswith("\n") or out == "" else "\n"
            return f"{out}{suffix}[exit_code={p.returncode}]{extra_hint}"
        return out if out else "Success"
    except subprocess.TimeoutExpired:
        return "Error executing command: timeout after 60s"
    except Exception as e:
        return f"Error executing command: {e}"


@registry.register(
    description=(
        "Start a persistent PTY shell session. "
        "Use for multi-step commands where state must persist (nix-shell, cd, exports, venv)."
    ),
    input_model=StartShellSessionInput,
)
def start_shell_session(
    session_id: str | None = None,
    shell: str = "bash -i",
    command: str | None = None,
    cwd: str | None = None,
    root: str = "",
    **kwargs: Any,
) -> str:
    try:
        # Be tolerant to LLM argument drift: some models send `command` instead of `shell`.
        shell_value = (shell or "").strip()
        if (not shell_value or shell_value == "bash -i") and command and str(command).strip():
            shell_value = str(command).strip()
        if not shell_value:
            shell_value = "bash -i"
        result = _PTY_MANAGER.start(root=root, session_id=session_id, shell=shell_value, cwd=cwd)
        return _json_result(result)
    except Exception as e:
        return _json_result({"ok": False, "error": "start_failed", "detail": str(e)})


@registry.register(
    description="Write input into a persistent PTY shell session (optionally appending newline).",
    input_model=WriteShellSessionInput,
)
def write_shell_session(
    session_id: str,
    input: str,
    append_newline: bool = True,
    root: str = "",
    **kwargs: Any,
) -> str:
    del root
    try:
        result = _PTY_MANAGER.write(
            session_id=session_id,
            input_text=input,
            append_newline=append_newline,
        )
        return _json_result(result)
    except Exception as e:
        return _json_result({"ok": False, "error": "write_failed", "detail": str(e)})


@registry.register(
    description="Read output from a persistent PTY shell session.",
    input_model=ReadShellSessionInput,
)
def read_shell_session(
    session_id: str,
    timeout_ms: int = 250,
    max_bytes: int = 65536,
    root: str = "",
    **kwargs: Any,
) -> str:
    del root
    try:
        result = _PTY_MANAGER.read(
            session_id=session_id,
            timeout_ms=timeout_ms,
            max_bytes=max_bytes,
        )
        return _json_result(result)
    except Exception as e:
        return _json_result({"ok": False, "error": "read_failed", "detail": str(e)})


@registry.register(
    description=(
        "Run one command inside a persistent PTY shell session and read output. "
        "Preferred over run_shell when command state must persist."
    ),
    input_model=ExecShellSessionInput,
)
def exec_shell_session(
    session_id: str,
    command: str,
    wait_ms: int = 350,
    max_bytes: int = 65536,
    root: str = "",
    **kwargs: Any,
) -> str:
    del root
    try:
        result = _PTY_MANAGER.exec(
            session_id=session_id,
            command=command,
            wait_ms=wait_ms,
            max_bytes=max_bytes,
        )
        return _json_result(result)
    except Exception as e:
        return _json_result({"ok": False, "error": "exec_failed", "detail": str(e)})


@registry.register(
    description="Close a persistent PTY shell session.",
    input_model=CloseShellSessionInput,
)
def close_shell_session(session_id: str, force: bool = False, root: str = "", **kwargs: Any) -> str:
    del root
    try:
        result = _PTY_MANAGER.close(session_id=session_id, force=force)
        return _json_result(result)
    except Exception as e:
        return _json_result({"ok": False, "error": "close_failed", "detail": str(e)})


@registry.register(
    description="List active persistent PTY shell sessions.",
    input_model=ListShellSessionsInput,
)
def list_shell_sessions(root: str = "", **kwargs: Any) -> str:
    del root
    try:
        result = _PTY_MANAGER.list()
        return _json_result(result)
    except Exception as e:
        return _json_result({"ok": False, "error": "list_failed", "detail": str(e)})


@registry.register(
    description=(
        "Run a one-shot Python command inside a Nix environment with optional Python and system packages. "
        "Use this instead of crafting nix-shell syntax manually."
    ),
    input_model=RunNixPythonInput,
)
def run_nix_python(
    command: str,
    python_packages: list[str] | None = None,
    system_packages: list[str] | None = None,
    pure: bool = False,
    root: str = "",
    **kwargs: Any,
) -> str:
    cmd = _normalize_command_text(command)
    if not cmd:
        return "Error: empty command"
    if is_blocked(cmd):
        return "Blocked: unsafe command pattern detected."

    py_pkgs = list(python_packages or [])
    sys_pkgs = list(system_packages or [])
    for p in py_pkgs:
        if not _is_valid_nix_attr_token(p):
            return f"Error: invalid python package token '{p}'"
    for p in sys_pkgs:
        if not _is_valid_nix_attr_token(p):
            return f"Error: invalid system package token '{p}'"

    py_expr = "python3.withPackages (p: with p; [ " + " ".join(py_pkgs) + " ])"
    package_args = [py_expr, *sys_pkgs]
    nix_cmd = "nix-shell"
    if pure:
        nix_cmd += " --pure"
    nix_cmd += " -p " + " ".join(shlex.quote(p) for p in package_args)
    nix_cmd += " --run " + shlex.quote(cmd)
    return str(run_shell(nix_cmd, root=root))

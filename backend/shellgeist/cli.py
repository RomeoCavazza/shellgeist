#!/usr/bin/env python3
"""ShellGeist CLI: unified command-line interface.

Entry point for the ``shellgeist`` console script defined in pyproject.toml.
Subcommands: agent, daemon, debug, edit-plan, ping, version.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

SOCKET_PATH = os.path.expanduser("~/.cache/shellgeist.sock")


def _jprint(obj: Any) -> None:
    """Print JSON to stdout."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ── agent chat ──────────────────────────────────────────────────────────


async def _run_agent_chat(goal: str) -> int:
    """Connect to daemon and stream execution events to terminal."""
    if not os.path.exists(SOCKET_PATH):
        print("[!] Daemon not running. Start with: shellgeist daemon")
        return 1

    reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
    payload = {"cmd": "agent_task", "goal": goal, "root": os.getcwd()}
    writer.write((json.dumps(payload) + "\n").encode())
    await writer.drain()

    def _print_event(event: dict[str, Any]) -> None:
        channel = str(event.get("channel", ""))
        content = str(event.get("content", ""))
        phase = str(event.get("phase", ""))
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}

        if channel == "status":
            return
        if channel == "reasoning":
            print(f"\033[90mThinking: {content}\033[0m")
            return
        if channel == "tool_call":
            print(f"\033[32mAction: {content}\033[0m")
            return
        if channel == "tool_result":
            print(f"Observation: {content}")
            return
        if channel == "error":
            print(f"\033[31mError: {content}\033[0m")
            return
        if channel == "response":
            if isinstance(meta, dict) and meta.get("chunk"):
                print(content, end="", flush=True)
            else:
                print(content)
            if phase == "done":
                print("")

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            ev = json.loads(line)
            if ev["type"] == "execution_event":
                event = ev.get("event")
                if isinstance(event, dict):
                    _print_event(event)
                continue
            if ev["type"] == "result":
                if ev["ok"]:
                    print("\033[36mGoal achieved!\033[0m")
                else:
                    print(f"\033[31mError: {ev.get('error')}\033[0m")
                break
    finally:
        writer.close()
        await writer.wait_closed()

    return 0


# ── subcommands ─────────────────────────────────────────────────────────


def cmd_agent(args: argparse.Namespace) -> int:
    """Run a task via the agent daemon."""
    return asyncio.run(_run_agent_chat(args.goal))


def cmd_daemon(args: argparse.Namespace) -> int:
    """Start the background daemon."""
    from shellgeist.sgd import main as daemon_main

    return asyncio.run(daemon_main())


def cmd_debug(args: argparse.Namespace) -> int:
    """Print environment/debug info."""
    from shellgeist.llm.client import get_client

    try:
        client_fast, model_fast = get_client("fast")
        client_smart, model_smart = get_client("smart")
        models_ok = True
    except Exception as e:
        model_fast = model_smart = f"error: {e}"
        models_ok = False

    _jprint({
        "ok": True,
        "python": sys.executable,
        "version": sys.version,
        "cwd": str(Path.cwd()),
        "SOCKET_PATH": SOCKET_PATH,
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1"),
        "OPENAI_API_KEY_set": bool(os.getenv("OPENAI_API_KEY")),
        "SHELLGEIST_MODEL_FAST": model_fast,
        "SHELLGEIST_MODEL_SMART": model_smart,
        "models_ok": models_ok,
    })
    return 0


def cmd_edit_plan(args: argparse.Namespace) -> int:
    """Generate edit plan for a file."""
    from shellgeist.tools.coder import edit_plan

    root = Path(args.root).resolve() if args.root else Path.cwd()
    result = edit_plan(args.file, args.instruction, root=root)
    d = result.to_dict()
    _jprint(d)
    return 0 if d.get("ok") else 1


def cmd_ping(args: argparse.Namespace) -> int:
    """Ping the daemon."""
    import socket

    sock_path = os.path.expanduser(args.socket)

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        sock.sendall(b'{"cmd":"ping"}\n')
        response = sock.recv(4096).decode()
        sock.close()

        data = json.loads(response)
        _jprint(data)
        return 0 if data.get("ok") else 1
    except FileNotFoundError:
        _jprint({"ok": False, "error": "daemon_not_running", "socket": sock_path})
        return 1
    except Exception as e:
        _jprint({"ok": False, "error": str(e)})
        return 1


def cmd_version(_args: argparse.Namespace) -> int:
    """Print version."""
    from shellgeist import __version__

    print(f"shellgeist {__version__}")
    return 0


# ── parser & entry point ───────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="shellgeist",
        description="ShellGeist - AI-powered code editing assistant for Neovim",
    )
    p.add_argument("--version", "-V", action="store_true", help="Print version")

    sub = p.add_subparsers(dest="cmd")

    # agent
    sp = sub.add_parser("agent", help="Run a task via the agent")
    sp.add_argument("goal", help="Task goal")
    sp.set_defaults(fn=cmd_agent)

    # daemon
    sp = sub.add_parser("daemon", help="Start the background daemon")
    sp.set_defaults(fn=cmd_daemon)

    # debug
    sp = sub.add_parser("debug", help="Print environment/debug info (JSON)")
    sp.set_defaults(fn=cmd_debug)

    # edit-plan
    sp = sub.add_parser("edit-plan", help="Generate unified diff for file edit")
    sp.add_argument("file", help="File path relative to root")
    sp.add_argument("instruction", help="Edit instruction")
    sp.add_argument("--root", "-r", help="Project root (default: cwd)")
    sp.set_defaults(fn=cmd_edit_plan)

    # ping
    sp = sub.add_parser("ping", help="Ping the ShellGeist daemon")
    sp.add_argument(
        "--socket", "-s",
        default="~/.cache/shellgeist.sock",
        help="Daemon socket path",
    )
    sp.set_defaults(fn=cmd_ping)

    # version
    sp = sub.add_parser("version", help="Print version")
    sp.set_defaults(fn=cmd_version)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        return cmd_version(args)

    if not args.cmd:
        parser.print_help()
        return 0

    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())


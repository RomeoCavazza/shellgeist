#!/usr/bin/env python3
"""ShellGeist CLI - Command-line interface for debugging and testing."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _jprint(obj: Any) -> None:
    """Print JSON to stdout."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_debug(args: argparse.Namespace) -> int:
    """Print environment/debug info."""
    from shellgeist.models import get_client

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
    _jprint(result)
    return 0 if result.get("ok") else 1


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


def cmd_version(args: argparse.Namespace) -> int:
    """Print version."""
    print("shellgeist 0.1.0")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="shellgeist",
        description="ShellGeist - AI-powered code editing assistant for Neovim",
    )
    p.add_argument("--version", "-V", action="store_true", help="Print version")

    sub = p.add_subparsers(dest="cmd")

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


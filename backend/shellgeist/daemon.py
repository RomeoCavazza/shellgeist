#!/usr/bin/env python3
"""ShellGeist Daemon - Unix socket server for RPC communication."""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from typing import Any

from shellgeist.protocol import handle_request

DEFAULT_SOCKET = os.path.expanduser("~/.cache/shellgeist.sock")


def _send(writer: asyncio.StreamWriter, ev: dict) -> None:
    """Send JSON event to client."""
    writer.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))


async def _safe_drain(writer: asyncio.StreamWriter) -> bool:
    """Drain writer, handle disconnects gracefully."""
    try:
        await writer.drain()
        return True
    except (ConnectionResetError, BrokenPipeError):
        return False


async def client_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a single client connection."""
    try:
        while True:
            data = await reader.readline()
            if not data:
                break

            try:
                req: Any = json.loads(data)
            except Exception:
                _send(writer, {"type": "result", "ok": False, "error": "bad_json"})
                if not await _safe_drain(writer):
                    break
                continue

            try:
                resp = await handle_request(req)
                _send(writer, resp)
            except Exception as e:
                _send(writer, {
                    "type": "result",
                    "ok": False,
                    "error": "handler_crash",
                    "detail": str(e),
                })

            if not await _safe_drain(writer):
                break

    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_server(socket_path: str) -> int:
    """Run the daemon server."""
    os.makedirs(os.path.dirname(socket_path), exist_ok=True)
    
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    server = await asyncio.start_unix_server(client_handler, path=socket_path)
    print(f"[ShellGeist] daemon listening: {socket_path}")

    try:
        async with server:
            await server.serve_forever()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        try:
            server.close()
            await server.wait_closed()
        except Exception:
            pass
        try:
            if os.path.exists(socket_path):
                os.unlink(socket_path)
        except Exception:
            pass

    return 0


def main(argv: list[str] | None = None) -> int:
    """Daemon entry point."""
    parser = argparse.ArgumentParser(
        prog="sgd",
        description="ShellGeist Daemon - AI code editing server",
    )
    parser.add_argument(
        "--socket", "-s",
        default=DEFAULT_SOCKET,
        help=f"Socket path (default: {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--version", "-V",
        action="store_true",
        help="Print version",
    )
    
    args = parser.parse_args(argv)

    if args.version:
        print("sgd 0.1.0")
        return 0

    try:
        return asyncio.run(run_server(args.socket))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


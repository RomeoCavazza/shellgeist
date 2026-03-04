#!/usr/bin/env python3
"""ShellGeist Daemon - Unix socket server for RPC communication."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from shellgeist.io.transport import safe_drain, send_json
from shellgeist.protocol import handle_request

DEFAULT_SOCKET = os.path.expanduser("~/.cache/shellgeist.sock")


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
                send_json(writer, {"type": "result", "ok": False, "error": "bad_json"})
                if not await safe_drain(writer):
                    break
                continue

            try:
                resp = await handle_request(req, writer=writer, reader=reader)
                send_json(writer, resp)
            except Exception as e:
                send_json(writer, {
                    "type": "result",
                    "ok": False,
                    "error": "handler_crash",
                    "detail": str(e),
                })

            if not await safe_drain(writer):
                break

    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_server(socket_path: str) -> int:
    """Start Unix socket server and serve forever."""
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


async def main(socket_path: str | None = None) -> int:
    """Async entry point (used by cli.py daemon subcommand)."""
    return await run_server(socket_path or DEFAULT_SOCKET)


def cli_main(argv: list[str] | None = None) -> None:
    """CLI entry point with argparse."""
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
        from shellgeist import __version__
        print(f"sgd {__version__}")
        raise SystemExit(0)

    raise SystemExit(asyncio.run(run_server(args.socket)))


if __name__ == "__main__":
    try:
        cli_main()
    except KeyboardInterrupt:
        raise SystemExit(0)

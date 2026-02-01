#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from shellgeist.protocol import handle_request

SOCKET = os.path.expanduser("~/.cache/shellgeist.sock")


def _send(writer: asyncio.StreamWriter, ev: dict) -> None:
    """
    Best-effort send. StreamWriter.write() itself is usually non-raising;
    the failure will surface on drain(), but we keep this helper simple.
    """
    writer.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))


async def _safe_drain(writer: asyncio.StreamWriter) -> bool:
    """
    Drain, but treat client disconnects as normal.
    Returns False if the connection is gone and we should stop.
    """
    try:
        await writer.drain()
        return True
    except (ConnectionResetError, BrokenPipeError):
        return False


async def client_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
                _send(writer, {"type": "result", "ok": False, "error": "handler_crash", "detail": str(e)})

            if not await _safe_drain(writer):
                break

    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main() -> int:
    os.makedirs(os.path.dirname(SOCKET), exist_ok=True)
    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass

    server = await asyncio.start_unix_server(client_handler, path=SOCKET)
    print("[ShellGeist] daemon listening:", SOCKET)

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
            if os.path.exists(SOCKET):
                os.unlink(SOCKET)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)

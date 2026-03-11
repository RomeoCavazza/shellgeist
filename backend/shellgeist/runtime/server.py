"""RPC server and request handler: routes JSON commands to agent and tools."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from shellgeist.config import socket_path
from shellgeist.runtime.protocol import SGRequest, SGResult
from shellgeist.runtime.transport import safe_drain, send_json
from shellgeist.tools.git_utils import git

_agent_cache: dict[str, Any] = {}


def _resolve_root(root_str: str | None) -> Path:
    """Resolve and validate the workspace root for a request.

    Enforces that:
    - a root is provided and exists
    - the root is a directory
    """
    if not root_str:
        raise ValueError("missing_root")

    p = Path(root_str).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError("invalid_root")

    return p


async def handle_request(raw_req: dict, writer: asyncio.StreamWriter | None = None, reader: asyncio.StreamReader | None = None) -> dict:
    """Route JSON commands to agent, tools, and session."""
    try:
        req: Any = TypeAdapter(SGRequest).validate_python(raw_req)
    except ValidationError as e:
        return SGResult(ok=False, error="validation_error", detail=str(e)).model_dump()

    cmd = req.cmd

    try:
        if cmd == 'ping':
            server_file = Path(__file__).resolve()
            # .../backend/shellgeist/runtime/server.py -> repo root is parents[3]
            repo_root = str(server_file.parents[3]) if len(server_file.parents) >= 4 else str(server_file.parent)
            package_root = str(server_file.parents[2]) if len(server_file.parents) >= 3 else str(server_file.parent)
            return SGResult(
                ok=True,
                data={
                    "repo_root": repo_root,
                    "package_root": package_root,
                    "server_file": str(server_file),
                },
            ).model_dump()

        if cmd == 'git_status':
            root = _resolve_root(req.root)
            rc, out = git(root, ['status', '--porcelain'])
            if rc != 0:
                return SGResult(ok=True, data={"inside_git": False, "porcelain": []}).model_dump()
            lines = [ln for ln in (out or '').splitlines() if ln.strip()]
            return SGResult(ok=True, data={"inside_git": True, "porcelain": lines}).model_dump()

        if cmd == 'git_add':
            root = _resolve_root(req.root)
            rc, out = git(root, ['add', '--', req.file])
            if rc != 0:
                return SGResult(ok=False, error='git_add_failed', detail=(out or '')[:8000]).model_dump()
            return SGResult(ok=True, data={"file": req.file, "staged": True}).model_dump()

        if cmd == 'git_restore':
            root = _resolve_root(req.root)
            rc, out = git(root, ['restore', '--', req.file])
            if rc != 0:
                return SGResult(ok=False, error='git_restore_failed', detail=(out or '')[:8000]).model_dump()
            return SGResult(ok=True, data={"file": req.file, "restored": True}).model_dump()

        if cmd == 'agent_task':
            root = _resolve_root(req.root)
            session_id = req.session_id or "default"
            fresh = getattr(req, 'fresh_conversation', False)
            if fresh and session_id in _agent_cache:
                del _agent_cache[session_id]
            if session_id not in _agent_cache or str(_agent_cache[session_id].root) != str(root):
                from shellgeist.agent.loop import Agent
                _agent_cache[session_id] = Agent(root=str(root))

            agent = _agent_cache[session_id]
            res = await agent.run_task(
                req.goal,
                writer=writer,
                session_id=session_id,
                mode=req.mode,
                reader=reader,
                fresh_conversation=fresh,
            )
            return SGResult(ok=res["ok"], data=res).model_dump()

        if cmd == 'reset_session':
            session_id = getattr(req, 'session_id', None) or "default"
            if session_id in _agent_cache:
                del _agent_cache[session_id]
            return SGResult(ok=True).model_dump()

        if cmd == 'edit':
            from shellgeist.tools.edit import edit_plan
            root = _resolve_root(req.root)
            edit_result = edit_plan(req.file, req.instruction, root=root)
            return SGResult(ok=True, data=edit_result.to_dict()).model_dump()

        if cmd == 'edit_apply':
            from shellgeist.tools.edit import apply_edit
            root = _resolve_root(req.root)
            patch_result = apply_edit(
                req.file, req.patch,
                root=root,
                instruction=req.instruction or "apply",
                stage=req.stage,
                backup=req.backup,
            )
            return SGResult(ok=True, data=patch_result).model_dump()

        if cmd == 'edit_apply_full':
            from shellgeist.tools.edit import apply_full_replace
            root = _resolve_root(req.root)
            replace_result = apply_full_replace(
                req.file, req.text,
                root=root,
                instruction=req.instruction or "apply_full",
                stage=req.stage,
                backup=req.backup,
            )
            return SGResult(ok=True, data=replace_result).model_dump()

        if cmd == "get_history":
            from shellgeist.runtime.session import get_session_history
            hist = get_session_history(req.session_id, for_ui=True)
            return SGResult(ok=True, data={"history": hist}).model_dump()

        return SGResult(ok=False, error='unknown_cmd').model_dump()

    except ValueError as e:
        return SGResult(ok=False, error=str(e)).model_dump()
    except Exception as e:
        return SGResult(ok=False, error='internal_error', detail=str(e)).model_dump()


async def client_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle a single client connection."""
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            try:
                req = json.loads(data)
                resp = await handle_request(req, writer=writer, reader=reader)
                send_json(writer, resp)
            except Exception as e:
                send_json(writer, {"ok": False, "error": "server_error", "detail": str(e)})
            
            if not await safe_drain(writer):
                break
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_server(socket_path_arg: str | None = None) -> int:
    """Start Unix socket server and serve forever."""
    path = socket_path_arg or socket_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

    server = await asyncio.start_unix_server(client_handler, path=path)
    print(f"[ShellGeist] daemon listening: {path}")
    async with server:
        await server.serve_forever()
    return 0

"""RPC request handler: routes JSON commands to agent, tools, and session."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from shellgeist.protocol.models import SGRequest, SGResult
from shellgeist.tools.coder import apply_edit, apply_full_replace, edit_plan
from shellgeist.util_git import git


def _resolve_root(root_str: str | None) -> Path:
    if not root_str:
        raise ValueError('missing_root')
    p = Path(root_str).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError('invalid_root')
    return p


# Global cache for agents to avoid re-scanning and re-summarizing on every request
_agent_cache: dict[str, Any] = {}


async def _handle_agent_task(req: Any, writer: Any | None, reader: Any | None) -> dict:
    """Run an agent task (the most complex RPC handler)."""
    root = _resolve_root(req.root)
    if not req.goal:
        raise ValueError("missing_goal")

    session_id = req.session_id or "default"
    if session_id not in _agent_cache:
        from shellgeist.agent import Agent
        _agent_cache[session_id] = Agent(root=str(root))
    else:
        # Avoid reusing an agent pinned to a different project root
        cached_agent = _agent_cache[session_id]
        if str(cached_agent.root) != str(root):
             from shellgeist.agent import Agent
             _agent_cache[session_id] = Agent(root=str(root))

    agent = _agent_cache[session_id]
    res = await agent.run_task(req.goal, writer=writer, session_id=session_id, mode=req.mode, reader=reader)
    return SGResult(ok=res["ok"], data=res).model_dump()


async def _handle_edit_apply(req: Any) -> dict:
    """Apply a pre-computed patch."""
    root = _resolve_root(req.root)
    if not req.file:
        raise ValueError("missing_file")
    if not req.patch:
        raise ValueError("missing_patch")
    patch_result = apply_edit(
        req.file, req.patch,
        root=root,
        instruction=req.instruction or "apply",
        stage=req.stage,
        backup=req.backup,
    )
    return SGResult(ok=True, data=patch_result).model_dump()


async def _handle_edit_apply_full(req: Any) -> dict:
    """Apply a full file replacement."""
    root = _resolve_root(req.root)
    if not req.file:
        raise ValueError("missing_file")
    if not req.text:
        raise ValueError("missing_text")
    replace_result = apply_full_replace(
        req.file, req.text,
        root=root,
        instruction=req.instruction or "apply_full",
        stage=req.stage,
        backup=req.backup,
    )
    return SGResult(ok=True, data=replace_result).model_dump()


async def handle_request(raw_req: dict, writer: Any | None = None, reader: Any | None = None) -> dict:
    try:
        req: Any = TypeAdapter(SGRequest).validate_python(raw_req)
    except ValidationError as e:
        return SGResult(ok=False, error="validation_error", detail=str(e)).model_dump()

    cmd = req.cmd

    try:
        # ---------------- basic ----------------
        if cmd == 'ping':
            return SGResult(ok=True).model_dump()

        # ---------------- git status ----------------
        if cmd == 'git_status':
            root = _resolve_root(req.root)
            rc, out = git(root, ['status', '--porcelain'])
            if rc != 0:
                return SGResult(ok=True, data={"inside_git": False, "porcelain": []}).model_dump()
            lines = [ln for ln in (out or '').splitlines() if ln.strip()]
            return SGResult(ok=True, data={"inside_git": True, "porcelain": lines}).model_dump()

        # ---------------- git add / restore ----------------
        if cmd == 'git_add':
            root = _resolve_root(req.root)
            if not req.file:
                raise ValueError("missing_file")
            rc, out = git(root, ['add', '--', req.file])
            if rc != 0:
                return SGResult(ok=False, error='git_add_failed', detail=(out or '')[:8000]).model_dump()
            return SGResult(ok=True, data={"file": req.file, "staged": True}).model_dump()

        if cmd == 'git_restore':
            root = _resolve_root(req.root)
            if not req.file:
                raise ValueError("missing_file")
            rc, out = git(root, ['restore', '--', req.file])
            if rc != 0:
                return SGResult(ok=False, error='git_restore_failed', detail=(out or '')[:8000]).model_dump()
            return SGResult(ok=True, data={"file": req.file, "restored": True}).model_dump()

        # ---------------- existing features ----------------
        if cmd == 'plan':
            root = _resolve_root(req.root)
            if not req.goal:
                raise ValueError("missing_goal")
            # Placeholder — real planning is done by the agent loop.
            return SGResult(ok=True, data={"steps": [
                {"kind": "edit", "file": "README.md", "instruction": f"Add Roadmap about: {req.goal}"},
                {"kind": "shell", "command": "mkdir -p docs"},
            ]}).model_dump()

        if cmd == 'shell':
            # Legacy stub — shell planning is handled by the agent loop now.
            return SGResult(ok=False, error='deprecated_use_agent_task').model_dump()

        if cmd == 'chat':
            text = (req.text or "").strip()
            if not text:
                raise ValueError("missing_text")
            return SGResult(
                ok=True,
                data={
                    "answer": "Endpoint chat minimal actif. Utilise :SGAgent pour le mode autonome.",
                    "echo": text,
                },
            ).model_dump()

        if cmd == 'edit':
            root = _resolve_root(req.root)
            if not req.file:
                raise ValueError("missing_file")
            if not req.instruction:
                raise ValueError("missing_instruction")
            edit_result = edit_plan(req.file, req.instruction, root=root)
            return SGResult(ok=True, data=edit_result.to_dict()).model_dump()

        # ---------------- edit apply (patch) ----------------
        if cmd == 'edit_apply':
            return await _handle_edit_apply(req)

        # ---------------- edit apply full replace ----------------
        if cmd == 'edit_apply_full':
            return await _handle_edit_apply_full(req)

        # ---------------- agent task (NEW) ----------------
        if cmd == 'agent_task':
            return await _handle_agent_task(req, writer, reader)

        if cmd == "get_history":
            from shellgeist.session.store import get_session_history
            hist = get_session_history(req.session_id, for_ui=True)
            return SGResult(ok=True, data={"history": hist}).model_dump()

        return SGResult(ok=False, error='unknown_cmd').model_dump()

    except ValueError as e:
        return SGResult(ok=False, error=str(e)).model_dump()
    except Exception as e:
        return SGResult(ok=False, error='internal_error', detail=str(e)).model_dump()

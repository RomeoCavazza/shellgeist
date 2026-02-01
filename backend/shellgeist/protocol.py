from __future__ import annotations

# Additional imports and function definitions...
import subprocess
from pathlib import Path
from typing import Any

from shellgeist.safety import is_blocked
from shellgeist.tools.coder import apply_edit, apply_full_replace, edit_plan
from shellgeist.tools.planner import plan
from shellgeist.tools.shell import plan_shell


def _require_root(req: dict) -> Path:
    root = req.get('root')
    if not root or not isinstance(root, str):
        raise ValueError('missing_root')
    p = Path(root).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError('invalid_root')
    return p

def _require_file(req: dict) -> str:
    f = req.get('file')
    if not isinstance(f, str) or not f.strip():
        raise ValueError('missing_file')
    return f


def _git(root: Path, args: list[str]) -> tuple[int, str]:
    p = subprocess.run(
        ['git', '-C', str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return p.returncode, p.stdout


def _result(**payload: Any) -> dict:
    # Centralize protocol envelope
    return {'type': 'result', **payload}

async def handle_request(req: dict) -> dict:
    cmd = req.get('cmd')

    try:
        # ---------------- basic ----------------
        if cmd == 'ping':
            return _result(ok=True)

        # ---------------- git status ----------------
        if cmd == 'git_status':
            root = _require_root(req)

            rc, out = _git(root, ['status', '--porcelain'])
            if rc != 0:
                # UI-friendly: outside git repo (or any failure) => ok=True
                return _result(ok=True, inside_git=False, porcelain=[])

            lines = [ln for ln in (out or '').splitlines() if ln.strip() != {}]
            return _result(ok=True, inside_git=True, porcelain=lines)

        # ---------------- git add / restore ----------------
        if cmd == 'git_add':
            root = _require_root(req)
            file = _require_file(req)

            rc, out = _git(root, ['add', '--', file])
            if rc != 0:
                return _result(ok=False, error='git_add_failed', detail=(out or '')[:8000])
            return _result(ok=True)

        if cmd == 'git_restore':
            root = _require_root(req)
            file = _require_file(req)

            rc, out = _git(root, ['restore', '--', file])
            if rc != 0:
                return _result(ok=False, error='git_restore_failed', detail=(out or '')[:8000])
            return _result(ok=True)

        # ---------------- existing features ----------------
        if cmd == 'chat':
            # keep it structured even if TODO
            return _result(ok=True, answer='TODO streaming LLM')

        if cmd == 'plan':
            root = _require_root(req)
            goal = req.get('goal')
            if not isinstance(goal, str) or not goal.strip():
                raise ValueError('missing_goal')
            return _result(ok=True, steps=plan(goal, root=root))

        if cmd == 'edit':
            root = _require_root(req)
            file = _require_file(req)
            instruction = req.get('instruction')
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError('missing_instruction')

            out = edit_plan(file, instruction, root=root)
            # edit_plan already returns a dict; wrap into protocol envelope
            return _result(**out)

        # ---------------- edit apply (patch) ----------------
        if cmd == 'edit_apply':
            root = _require_root(req)
            file = _require_file(req)

            patch = req.get('patch')
            if not isinstance(patch, str) or '@@' not in patch:
                raise ValueError('missing_patch')

            instruction = req.get('instruction')
            if not isinstance(instruction, str) or not instruction.strip():
                instruction = 'apply'

            stage = bool(req.get('stage', False))
            backup = bool(req.get('backup', True))

            out = apply_edit(
                file,
                patch,
                root=root,
                instruction=instruction,
                stage=stage,
                backup=backup,
            )
            return _result(**out)

        # ---------------- edit apply full replace ----------------
        if cmd == 'edit_apply_full':
            root = _require_root(req)
            file = _require_file(req)

            text = req.get('text')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('missing_text')

            instruction = req.get('instruction')
            if not isinstance(instruction, str) or not instruction.strip():
                instruction = 'apply_full'

            stage = bool(req.get('stage', False))
            backup = bool(req.get('backup', True))

            out = apply_full_replace(
                file,
                text,
                root=root,
                instruction=instruction,
                stage=stage,
                backup=backup,
            )
            return _result(**out)

        # ---------------- shell plan ----------------
        if cmd == 'shell':
            root = _require_root(req)
            task = req.get('task')
            if not isinstance(task, str) or not task.strip():
                raise ValueError('missing_task')
            commands = plan_shell(task, root=root)
            blocked = [c for c in commands if is_blocked(c)]
            return _result(ok=True, commands=commands, blocked=blocked)

        return _result(ok=False, error='unknown_cmd')

    except ValueError as e:
        return _result(ok=False, error=str(e))
    except Exception as e:
        return _result(ok=False, error='internal_error', detail=str(e))

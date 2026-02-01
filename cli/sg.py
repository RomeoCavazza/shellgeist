#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
import argparse
import json
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"


def _in_venv() -> bool:
    return sys.prefix != sys.base_prefix


if VENV_PY.exists() and not _in_venv():
    os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])


BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from shellgeist.tools.coder import edit_plan


def jprint(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def cmd_debug(args: argparse.Namespace) -> int:
    jprint(
        {
            "ok": True,
            "python": sys.executable,
            "cwd": str(Path.cwd()),
            "repo_root": str(REPO_ROOT),
            "backend_root": str(BACKEND_ROOT),
            "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", ""),
            "OPENAI_API_KEY_set": bool(os.getenv("OPENAI_API_KEY")),
            "AI_MODEL_FAST": os.getenv("AI_MODEL_FAST", ""),
            "AI_MODEL_SMART": os.getenv("AI_MODEL_SMART", ""),
        }
    )
    return 0


def cmd_edit_plan(args: argparse.Namespace) -> int:
    res = edit_plan(args.file, args.instruction)
    jprint(res)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sg", description="ShellGeist v4 CLI (minimal)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("debug", help="Print environment/debug info (JSON)")
    sp.set_defaults(fn=cmd_debug)

    sp = sub.add_parser("edit-plan", help="Return unified diff + new content (does not write)")
    sp.add_argument("file")
    sp.add_argument("instruction")
    sp.set_defaults(fn=cmd_edit_plan)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())

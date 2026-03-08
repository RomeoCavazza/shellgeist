"""Tool subsystem: registry, executor, parser, policy.

Call :func:`load_tools` once at startup to populate the global *registry*
with the built-in tools (fs, coder, shell).  Previous versions relied on
side-effect imports; this explicit approach makes initialisation order clear
and avoids accidental circular imports.
"""
from __future__ import annotations

from shellgeist.tools.base import Tool, ToolRegistry, registry

_loaded = False


def load_tools() -> None:
    """Import tool modules so their ``@registry.register`` decorators fire.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _loaded
    if _loaded:
        return
    
    modules = [
        "shellgeist.tools.edit",
        "shellgeist.tools.fs",
        "shellgeist.tools.shell",
    ]
    
    import importlib
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            # We don't want to crash everything if one tool fails to load,
            # but we should definitely know about it.
            import sys
            print(f"ERROR: Failed to load tool module {mod_name}: {e}", file=sys.stderr)

    _loaded = True


__all__ = [
    "Tool",
    "ToolRegistry",
    "load_tools",
    "registry",
]

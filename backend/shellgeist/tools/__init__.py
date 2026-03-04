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
    # Each module decorates functions with @registry.register on import.
    import shellgeist.tools.coder as _coder  # noqa: F401
    import shellgeist.tools.fs as _fs  # noqa: F401
    import shellgeist.tools.shell as _shell  # noqa: F401
    _loaded = True


__all__ = [
    "Tool",
    "ToolRegistry",
    "load_tools",
    "registry",
]

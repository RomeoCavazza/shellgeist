"""Tool subsystem: registry, executor, parser, policy.

Importing this package triggers tool registration via side-effect imports
of fs, coder, and shell modules.
"""

from shellgeist.tools.base import Tool, ToolRegistry, registry

# Side-effect imports: each module registers its tools with the global registry.
import shellgeist.tools.fs as _fs  # noqa: F401
import shellgeist.tools.coder as _coder  # noqa: F401
import shellgeist.tools.shell as _shell  # noqa: F401

__all__ = [
    "Tool",
    "ToolRegistry",
    "registry",
]

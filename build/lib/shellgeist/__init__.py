"""ShellGeist - AI-powered code editing assistant for Neovim."""

__all__ = ["__version__"]

try:
    from importlib.metadata import version as _meta_version

    __version__ = _meta_version("shellgeist")
except Exception:  # not installed as package (dev / bash wrapper)
    __version__ = "0.1.0"

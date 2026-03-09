# Contributing to ShellGeist

Thank you for your interest in contributing to ShellGeist! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11+
- Neovim 0.9+ (for plugin testing)
- [nui.nvim](https://github.com/MunifTanjim/nui.nvim) (required by the sidebar)
- Ollama or compatible OpenAI API endpoint

### Installation

```bash
# Clone the repository
git clone https://github.com/RomeoCavazza/shellgeist.git
cd shellgeist

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode with dev dependencies
pip install -e ".[dev]"
```

### Linting

```bash
# Check code style
ruff check backend/

# Auto-fix issues
ruff check backend/ --fix
```

### Type Checking

```bash
mypy backend/shellgeist/ --ignore-missing-imports
```

## Project Structure

Repository layout. See also [README](README.md#project-structure).

```
.
├── assets
│   ├── ascii-logo.txt
│   └── shellgeist.png
├── backend
│   └── shellgeist
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── py.typed
│       ├── agent
│       │   ├── __init__.py
│       │   ├── loop.py
│       │   ├── messages.py
│       │   ├── orchestrator.py
│       │   ├── signals.py
│       │   └── parsing
│       │       ├── __init__.py
│       │       ├── json_utils.py
│       │       ├── normalize.py
│       │       └── parser.py
│       ├── llm
│       │   ├── __init__.py
│       │   ├── client.py
│       │   ├── prompt.py
│       │   ├── rules.py
│       │   └── stream.py
│       ├── runtime
│       │   ├── __init__.py
│       │   ├── paths.py
│       │   ├── policy.py
│       │   ├── protocol.py
│       │   ├── server.py
│       │   ├── session.py
│       │   ├── telemetry.py
│       │   └── transport.py
│       └── tools
│           ├── __init__.py
│           ├── base.py
│           ├── edit.py
│           ├── executor.py
│           ├── fs.py
│           ├── git_utils.py
│           ├── patch.py
│           └── shell.py
├── CONTRIBUTING.md
├── flake.lock
├── flake.nix
├── install.sh
├── LICENSE
├── nvim
│   ├── lua
│   │   └── shellgeist
│   │       ├── conflict.lua
│   │       ├── diff.lua
│   │       ├── init.lua
│   │       ├── rpc.lua
│   │       └── sidebar.lua
│   └── plugin
│       └── shellgeist.lua
├── pyproject.toml
├── README.md
└── shellgeist
```

## Performance

- **Sidebar**: Scroll-to-bottom is debounced (50 ms) during streaming to limit redraws. If the UI feels sluggish, increase the delay in `sidebar.lua` (`vim.defer_fn(..., 50)`).
- **Backend**: Session writes one row per message; history is truncated when loaded. For very long runs, `repair_conversation_history` caps non-system messages.
- **LLM**: For slow or large models, set `SHELLGEIST_HTTP_TIMEOUT` (seconds). Stream idle timeout is in config (`stream_idle_timeout`).

## Code Style

- Follow PEP 8 with 100 character line length
- Use type hints for all function signatures
- Document public functions with docstrings
- Keep functions focused and small

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Commit with clear messages (`git commit -m 'feat: Add amazing feature'`)
5. Push to your fork (`git push origin feature/amazing-feature`)
6. Open a Pull Request

### Commit Message Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `refactor:` Code refactoring
- `chore:` Maintenance tasks

## Reporting Issues

When reporting issues, please include:

- ShellGeist version (`shellgeist --version`)
- Python version (`python --version`)
- Neovim version (`nvim --version`)
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (with `SHELLGEIST_TRACE=1`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.


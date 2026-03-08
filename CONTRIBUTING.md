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

### Running Tests

```bash
pytest backend/tests/ -v
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

```
shellgeist/
├── backend/
│   ├── shellgeist/
│   │   ├── __init__.py       # Package root, __version__
│   │   ├── cli.py            # CLI entry point (shellgeist command)
│   │   ├── config.py         # Env-var config (OPENAI_*, SHELLGEIST_*)
│   │   ├── agent/            # Agent loop & orchestration
│   │   │   ├── loop.py       # Agent class, tool-call loop, small-talk
│   │   │   ├── messages.py   # Message building
│   │   │   ├── signals.py    # UI signals
│   │   │   └── parsing/      # XML/plaintext tool parsing, json_utils, normalize
│   │   ├── llm/              # LLM client
│   │   │   ├── client.py     # OpenAI-compatible client
│   │   │   ├── prompt.py     # System prompt, tool schemas, .shellgeist.md
│   │   │   └── stream.py     # Streaming + retry
│   │   ├── runtime/          # Daemon, protocol, session, policy
│   │   │   ├── server.py     # Unix socket server, request routing
│   │   │   ├── protocol.py   # Pydantic SGRequest/SGResult, JSON-lines
│   │   │   ├── session/      # SQLite history, ops, repair
│   │   │   ├── policy.py     # LoopGuard, RetryEngine
│   │   │   └── paths.py      # resolve_repo_path
│   │   └── tools/            # Tool registry, executor, implementations
│   │       ├── fs.py         # read_file, write_file, list_files, find_files
│   │       ├── edit.py       # edit_file, edit_plan, apply
│   │       ├── patch/        # Unified diff + guards
│   │       └── shell.py       # run_shell, PTY, run_nix_python
│   └── tests/                # Test suite (see docs/AUDIT.md for coverage)
│       └── test_paths_and_fs.py
├── nvim/                     # Neovim plugin (Lua)
│   ├── plugin/shellgeist.lua # Plugin loader
│   └── lua/shellgeist/
│       ├── init.lua          # Setup, commands (SGAgent, SGChat, SGSidebar, etc.)
│       ├── sidebar.lua       # Chat sidebar UI (nui.nvim)
│       ├── rpc.lua           # Unix socket RPC client
│       ├── diff.lua          # Diff preview & apply
│       └── conflict.lua      # Inline accept/reject conflict view
├── docs/
│   ├── AUDIT.md              # Technical and conceptual audit
│   └── VERSION_ANALYSIS.md   # Version history analysis
├── flake.nix                 # Nix flake (develop / run)
├── pyproject.toml            # Python packaging
└── shellgeist                # Wrapper script (bash)
```

## Code Style

- Follow PEP 8 with 100 character line length
- Use type hints for all function signatures
- Document public functions with docstrings
- Keep functions focused and small

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Commit with clear messages (`git commit -m 'feat: Add amazing feature'`)
7. Push to your fork (`git push origin feature/amazing-feature`)
8. Open a Pull Request

### Commit Message Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `test:` Adding tests
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


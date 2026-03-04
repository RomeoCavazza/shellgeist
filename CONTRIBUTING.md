# Contributing to ShellGeist

Thank you for your interest in contributing to ShellGeist! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11+
- Neovim 0.9+ (for plugin testing)
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
│   │   ├── sgd.py            # Daemon entry point (Unix socket server)
│   │   ├── config.py         # Centralised env-var helpers
│   │   ├── util_json.py      # Lenient JSON parser for LLM output
│   │   ├── util_path.py      # resolve_repo_path() — safe path resolution
│   │   ├── util_git.py       # git() subprocess helper
│   │   ├── agent/            # Core agent loop & state
│   │   │   ├── core.py       # Agent class, agentic loop
│   │   │   ├── messages.py   # Message building helpers
│   │   │   ├── orchestrator.py # Multi-step orchestration
│   │   │   └── state.py      # Agent state management
│   │   ├── diff/             # Diff generation & application
│   │   │   ├── apply.py      # Unified diff application
│   │   │   └── guards.py     # Diff safety guardrails
│   │   ├── io/               # I/O primitives
│   │   │   ├── events.py     # UI event emitter (streaming + review)
│   │   │   ├── results.py    # Result formatting
│   │   │   ├── telemetry.py  # Telemetry utilities
│   │   │   └── transport.py  # Socket send_json / safe_drain
│   │   ├── llm/              # LLM client layer
│   │   │   ├── client.py     # OpenAI-compatible client
│   │   │   ├── prompt.py     # System prompt construction
│   │   │   └── stream.py     # Streaming response handler
│   │   ├── protocol/         # JSON-lines RPC protocol
│   │   │   ├── handler.py    # Command dispatcher
│   │   │   ├── helpers.py    # Protocol utilities
│   │   │   └── models.py     # Request/response models (Pydantic)
│   │   ├── safety/           # Safety & guardrails
│   │   │   ├── blocked.py    # Blocked command patterns
│   │   │   ├── loop_guard.py # Infinite loop detection
│   │   │   ├── retry.py      # Exponential-backoff retry engine
│   │   │   └── verify.py     # Output verification
│   │   ├── session/          # Session & history
│   │   │   ├── ops.py        # Session operations
│   │   │   ├── repair.py     # Session repair utilities
│   │   │   └── store.py      # SQLite history store
│   │   └── tools/            # Tool implementations
│   │       ├── __init__.py   # load_tools() — explicit registration
│   │       ├── base.py       # Tool, ToolRegistry, global registry
│   │       ├── coder.py      # Code editing tool (diff/fulltext pipeline)
│   │       ├── executor.py   # Tool executor + review flow
│   │       ├── fs.py         # Filesystem tools (read/write/list/find)
│   │       ├── normalize.py  # LLM output normalisation
│   │       ├── parser.py     # XML tool-call parser
│   │       ├── policy.py     # Per-project tool policy
│   │       ├── preview.py    # Code preview for tool calls
│   │       ├── runtime.py    # Arg normalisation + missing-arg detection
│   │       └── shell.py      # Shell command + PTY sessions
│   └── tests/                # Test suite (84 tests)
│       ├── test_diff_apply.py
│       ├── test_guards.py
│       ├── test_normalize.py
│       ├── test_tool_parser.py
│       └── test_util_json.py
├── nvim/                     # Neovim plugin (Lua)
│   ├── plugin/shellgeist.lua # Plugin loader
│   └── lua/shellgeist/
│       ├── init.lua          # Plugin setup, commands, event dispatch
│       ├── sidebar.lua       # Chat sidebar UI (nui.nvim)
│       ├── rpc.lua           # Unix socket RPC client
│       ├── diff.lua          # Diff preview & apply
│       └── conflict.lua      # Inline accept/reject conflict view
├── docs/                     # Technical documentation
│   ├── ARCHITECTURE.md       # Full architecture & RPC protocol
│   ├── AUDIT.md              # Code audit findings
│   └── ROADMAP.md            # Refactoring roadmap
├── flake.nix                 # Nix flake (develop / run / build)
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


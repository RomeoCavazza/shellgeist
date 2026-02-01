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
│   └── shellgeist/
│       ├── cli.py          # CLI entry point
│       ├── daemon.py       # Daemon entry point
│       ├── protocol.py     # RPC protocol handler
│       ├── models.py       # LLM client
│       ├── tools/          # Core functionality
│       │   ├── coder.py    # Code editing logic
│       │   ├── planner.py  # Task planning
│       │   └── shell.py    # Shell command generation
│       └── diff/           # Diff application
│           ├── apply.py    # Unified diff application
│           └── guards.py   # Safety guardrails
├── nvim/                   # Neovim plugin (Lua)
└── tests/                  # Test suite
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


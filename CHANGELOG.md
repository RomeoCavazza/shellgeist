# Changelog

All notable changes to ShellGeist will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Persistent PTY shell tools: `start_shell_session`, `write_shell_session`, `read_shell_session`, `exec_shell_session`, `close_shell_session`, `list_shell_sessions`
- Agent guidance for choosing stateless `run_shell` vs stateful shell sessions (fixes nix-shell persistence workflows)
- **Review mode**: hunk-level accept/reject via `conflict.lua` before writing edits to disk
- `:SGDiagnostic` command — prints socket, daemon status, env vars
- `:SGReview` command — inline git conflict view
- `ARCHITECTURE.md` — full architecture, RPC protocol, env vars reference
- RPC auto-reconnect on daemon crash (single retry with respawn)

### Changed
- Tool registry: explicit `load_tools()` replaces side-effect imports
- `coder.py` refactored: `EditResult` dataclass, `normalize.py` extracted, cascade deduplicated
- DRY cleanup: `util_path.py`, `util_git.py`, centralised `config.py` helpers
- Diff guards consolidated in `diff/guards.py`
- Dead code removed: `plan_shell`, `SMALL_TALK_REPLY`, `can_complete`, `prefer_v5`, `_backup_file`

### Fixed
- 84 unit tests added (diff/apply, guards, JSON parser, XML parser, normalise)

## [0.1.0] - 2026-02-01

### Added
- **Backend daemon** (`sgd`): Async Unix socket server for RPC communication
- **CLI tool** (`shellgeist`): Command-line interface for debugging and testing
- **Neovim plugin**: Lua client with diff preview and keybindings
- **AI-powered code editing**: Generate and apply unified diffs via LLM
- **Comprehensive guardrails**:
  - Control character blocking
  - Python `__future__` import protection
  - Rewrite violence detection
  - README.md special protection
  - Syntax validation after edits
  - Path traversal prevention
- **Robust JSON parsing**: Auto-repair of common LLM JSON errors
- **Fallback cascade**: diff → repair → fulltext replacement
- **Git integration**: Status, stage, and restore commands
- **OpenAI-compatible API**: Works with Ollama and other compatible endpoints

### Security
- Path safety checks prevent directory traversal attacks
- Blocked patterns for dangerous shell commands

## [0.0.1] - 2024-01-01

### Added
- Initial development version (internal)

[Unreleased]: https://github.com/RomeoCavazza/shellgeist/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RomeoCavazza/shellgeist/releases/tag/v0.1.0
[0.0.1]: https://github.com/RomeoCavazza/shellgeist/releases/tag/v0.0.1

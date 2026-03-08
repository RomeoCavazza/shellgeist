<div align="center">
  <img alt="logo" width="400" src="assets/logo.png" />
  <p><strong>The "Tout Terrain" Autonomous Developer Agent for Neovim</strong></p>
</div>


  ██████  ██░ ██ ▓█████  ██▓     ██▓      ▄████ ▓█████  ██▓  ██████ ▄▄▄█████▓
▒██    ▒ ▓██░ ██▒▓█   ▀ ▓██▒    ▓██▒     ██▒ ▀█▒▓█   ▀ ▓██▒▒██    ▒ ▓  ██▒ ▓▒
░ ▓██▄   ▒██▀▀██░▒███   ▒██░    ▒██░    ▒██░▄▄▄░▒███   ▒██▒░ ▓██▄   ▒ ▓██░ ▒░
  ▒   ██▒░▓█ ░██ ▒▓█  ▄ ▒██░    ▒██░    ░▓█  ██▓▒▓█  ▄ ░██░  ▒   ██▒░ ▓██▓ ░ 
▒██████▒▒░▓█▒░██▓░▒████▒░██████▒░██████▒░▒▓███▀▒░▒████▒░██░▒██████▒▒  ▒██▒ ░ 
▒ ▒▓▒ ▒ ░ ▒ ░░▒░▒░░ ▒░ ░░ ▒░▓  ░░ ▒░▓  ░ ░▒   ▒ ░░ ▒░ ░░▓  ▒ ▒▓▒ ▒ ░  ▒ ░░   
░ ░▒  ░ ░ ▒ ░▒░ ░ ░ ░  ░░ ░ ▒  ░░ ░ ▒  ░  ░   ░  ░ ░  ░ ▒ ░░ ░▒  ░ ░    ░    
░  ░  ░   ░  ░░ ░   ░     ░ ░     ░ ░   ░ ░   ░    ░    ▒ ░░  ░  ░    ░      
      ░   ░  ░  ░   ░  ░    ░  ░    ░  ░      ░    ░  ░ ░        ░           

<p align="center">
  <a href="https://neovim.io/" target="_blank"><img src="https://img.shields.io/static/v1?style=flat-square&label=Neovim&message=v0.9%2b&logo=neovim&labelColor=282828&logoColor=8faa80&color=414b32" alt="Neovim: v0.9+" /></a>
  <a href="https://www.python.org/" target="_blank"><img src="https://img.shields.io/static/v1?style=flat-square&label=Python&message=3.11%2b&logo=python&logoColor=3776ab&labelColor=282828&color=347D39" alt="Python: 3.11+" /></a>
</p>

**ShellGeist** is no longer just a plugin—it's a fully autonomous AI coding assistant. It reasons, plans, and executes code changes directly in your repository using a native tool-calling loop.

## 🚀 Key Features

- **Autonomous Agentic Loop**: ShellGeist uses an "Iteration" loop (Thought -> Action -> Observation) to solve complex goals.
- **Dual-Model Routing**: 
  - **Fast (3B/7B)**: Instant planning and light iterations.
  - **Smart (32B+)**: Surgical precision for code edits and verification.
- **Global Project Context**: The agent sees your whole repo via `get_repo_map`. No more manual file traversal.
- **Persistent Terminal Sessions**: Built-in PTY shell sessions for stateful workflows (`nix-shell`, `export`, `cd`, virtualenv activation).
- **Magical Setup**: No manual daemon starting. Neovim auto-spawns the backend when you need it.
- **Persistent History**: All chats are saved in SQLite (`~/.cache/shellgeist/history.db`).
- **Unified CLI**: Use `./shellgeist "Goal"` in the terminal or `:SGAgent` in Neovim.

## 📦 Instant Installation

Clone and run the magic installer:

```bash
git clone https://github.com/your-username/shellgeist
cd shellgeist
./install.sh
source ~/.bashrc
```

ShellGeist is now globally available as `shellgeist`.

## 🛠️ Configuration

All settings are driven by environment variables (see `backend/shellgeist/config.py`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_BASE_URL` | `http://127.0.0.1:11434/v1` | Ollama/OpenAI-compatible API base |
| `OPENAI_API_KEY` | `ollama` | API key (Ollama ignores it) |
| `SHELLGEIST_MODEL` | `qwen2.5-coder:7b` | Preferred model (7b = fast; use 32b for heavier tasks) |
| `SHELLGEIST_HTTP_TIMEOUT` | `300` | HTTP timeout in seconds (increase for slow models) |
| `SHELLGEIST_MODEL_FALLBACK_KEYWORDS` | `coder,qwen,llama,mistral` | Comma-separated keywords for model discovery |

Example in `~/.bashrc` or `~/.zshrc`:

```bash
export SHELLGEIST_MODEL="qwen2.5-coder:7b"   # Faster, smaller model
export SHELLGEIST_HTTP_TIMEOUT=600           # For slow/large models
```

## ⌨️ Neovim Usage

| Command | Description |
|---------|-------------|
| `:SGAgent <goal>` | Start an autonomous task |
| `:SGAgent` | Open sidebar (no goal = chat mode) |
| `:SGSidebar` | Toggle the chat sidebar |
| `:SGEdit <file> <instruction>` | Single-file edit via LLM |
| `:SGReview [file]` | Review git changes (inline conflict view) |
| `:SGStatus` | Show `git status` |
| `:SGDiagnostic` | Print daemon / env diagnostic info |
| `:SGPing` | Ping daemon health-check |

Default keymaps (set in your config):

| Keymap | Description |
|--------|-------------|
| `<leader>as` | Toggle sidebar |
| `<leader>ag` | Direct prompt (floating input) |

## 🛡️ Guardrails

ShellGeist includes "Crisis-Proof" safety:
- **Syntax Validation**: Python code is compiled before being saved.
- **Rewrite Detection**: Blocks violent rewrites unless explicitly intended.
- **Future Import Protection**: Keeps Python `__future__` imports where they belong.
- **Atomic Writes**: Zero risk of file corruption during edits.

## 🏗️ Architecture

```
Neovim (Lua)  ←─ Unix socket ─→  Daemon (Python/Asyncio)  ──→  Ollama / OpenAI
  sidebar.lua                       handler.py → Agent loop
  conflict.lua (review)             → tools (edit, shell, fs)
  rpc.lua (client)                  → SQLite history
```

- **14 registered tools**: `read_file`, `write_file`, `edit_file`, `run_shell`, PTY sessions, `run_nix_python`, …
- **Review mode**: hunk-level accept/reject before writing (avante-style inline conflict view)
- **Safety**: command blocklist, loop guard, retry engine, `__future__` import protection, atomic writes

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full protocol reference (RPC commands, streaming events, env vars).

---
*Built for developers who want a local, free, and powerful AI teammate.*

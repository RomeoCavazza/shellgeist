<div align="center">

<pre>
  ██████  ██░ ██ ▓█████  ██▓     ██▓      ▄████ ▓█████  ██▓  ██████ ▄▄▄█████▓
▒██    ▒ ▓██░ ██▒▓█   ▀ ▓██▒    ▓██▒     ██▒ ▀█▒▓█   ▀ ▓██▒▒██    ▒ ▓  ██▒ ▓▒
░ ▓██▄   ▒██▀▀██░▒███   ▒██░    ▒██░    ▒██░▄▄▄░▒███   ▒██▒░ ▓██▄   ▒ ▓██░ ▒░
  ▒   ██▒░▓█ ░██ ▒▓█  ▄ ▒██░    ▒██░    ░▓█  ██▓▒▓█  ▄ ░██░  ▒   ██▒░ ▓██▓ ░ 
▒██████▒▒░▓█▒░██▓░▒████▒░██████▒░██████▒░▒▓███▀▒░▒████▒░██░▒██████▒▒  ▒██▒ ░ 
▒ ▒▓▒ ▒ ░ ▒ ░░▒░▒░░ ▒░ ░░ ▒░▓  ░░ ▒░▓  ░ ░▒   ▒ ░░ ▒░ ░░▓  ▒ ▒▓▒ ▒ ░  ▒ ░░   
░ ░▒  ░ ░ ▒ ░▒░ ░ ░ ░  ░░ ░ ▒  ░░ ░ ▒  ░  ░   ░  ░ ░  ░ ▒ ░░ ░▒  ░ ░    ░    
░  ░  ░   ░  ░░ ░   ░     ░ ░     ░ ░   ░ ░   ░    ░    ▒ ░░  ░  ░    ░      
      ░   ░  ░  ░   ░  ░    ░  ░    ░  ░      ░    ░  ░ ░        ░           
</pre>

</div>

<p align="center">
  <a href="https://github.com/RomeoCavazza/shellgeist/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" /></a>
  <a href="https://neovim.io/"><img src="https://img.shields.io/badge/Neovim-0.9+-57A143?logo=neovim" alt="Neovim 0.9+" /></a>
  <a href="https://www.lua.org/"><img src="https://img.shields.io/badge/Lua-5.1+-2C2D72?logo=lua" alt="Lua 5.1+" /></a>
  <a href="https://nixos.org/"><img src="https://img.shields.io/badge/Nix-flake-5277C3?logo=nixos" alt="Nix flake" /></a>
  <a href="https://github.com/RomeoCavazza/shellgeist/actions"><img src="https://img.shields.io/github/actions/workflow/status/RomeoCavazza/shellgeist/ci.yml?branch=main" alt="CI" /></a>
  <a href="https://github.com/ollama/ollama"><img src="https://img.shields.io/badge/Ollama-compatible-000000" alt="Ollama" /></a>
  <a href="https://openai.com/api/"><img src="https://img.shields.io/badge/OpenAI--API-compatible-412991" alt="OpenAI API" /></a>
</p>

---

## Overview

ShellGeist is an AI-powered code assistant that runs inside Neovim. It connects your editor to an LLM backend (Ollama or any OpenAI-compatible API), runs tools in your workspace (read/write files, shell commands, diffs), and streams responses into a sidebar. Edit, review, and apply changes without leaving the buffer.

- **Daemon + plugin**: Python backend (Unix socket server) and Lua Neovim plugin; one process per workspace.
- **Tool-first**: The model calls tools (e.g. `list_files`, `read_file`, `run_shell`); you see results and can approve in review mode.
- **Streaming UI**: Chat sidebar with [Response] / [Request], inline diff review, and conflict resolution.

<p align="center">
  <img src="assets/shellgeist.png" alt="ShellGeist" width="720" style="display: block; margin-left: auto; margin-right: auto;" />
</p>

---

## Project structure

```
shellgeist/
├── backend/
│   └── shellgeist/
│       ├── cli.py              # CLI and daemon entry
│       ├── config.py           # Env and config
│       ├── agent/              # Agent loop, orchestration, parsing
│       ├── llm/                # Client, prompt, streaming
│       ├── runtime/            # Server, protocol, session, policy
│       └── tools/              # read_file, write_file, list_files, shell, edit, patch, git
├── nvim/
│   ├── plugin/shellgeist.lua   # Plugin loader
│   └── lua/shellgeist/
│       ├── init.lua            # Commands, event dispatch
│       ├── sidebar.lua         # Chat UI (nui.nvim)
│       ├── rpc.lua             # Unix socket RPC client
│       ├── diff.lua            # Diff preview and apply
│       └── conflict.lua         # Inline accept/reject
├── assets/
│   ├── ascii-logo.txt
│   └── shellgeist.png
├── flake.nix                   # Nix flake (dev shell, run, build)
├── pyproject.toml              # Python package
├── install.sh                  # Wrapper (venv / nix develop / nix-shell)
└── shellgeist                  # Bash entry script
```

---

## Architecture

```mermaid
flowchart LR
  subgraph Editor
    N["Neovim"]
    UI["Lua plugin\n(sidebar, diff, RPC)"]
    N --> UI
  end

  subgraph Backend
    S["Python daemon\n(Unix socket)"]
    A["Agent loop"]
    T["Tools\n(fs, shell, edit)"]
    S --> A
    A --> T
  end

  subgraph LLM
    O["Ollama / OpenAI API"]
  end

  UI <-->|"JSON-lines RPC"| S
  A <-->|"streaming"| O
  T -->|"read/write/run"| FS["Workspace"]
```

- Neovim plugin opens a Unix socket and sends JSON-lines (goal, mode). Backend runs the agent loop, calls tools, streams tool calls and results back. Plugin renders sidebar, tool cards, and diff review.

### Request flow

```mermaid
sequenceDiagram
  participant U as User
  participant N as Neovim
  participant RPC as Lua RPC
  participant D as Daemon
  participant A as Agent
  participant LLM as LLM API
  participant T as Tools

  U->>N: :SGAgent "fix the bug"
  N->>RPC: goal + mode
  RPC->>D: JSON-lines (agent_task)
  D->>A: run_task()
  loop Agent loop
    A->>LLM: messages + tool schemas
    LLM-->>A: stream (text / tool_calls)
    alt tool_calls
      A->>T: execute (read_file, run_shell, …)
      T-->>A: result
      A->>RPC: stream tool result (event)
      RPC->>N: update sidebar
    else text only
      A->>RPC: stream chunk
      RPC->>N: append to response
    end
  end
  A->>D: done
  D->>RPC: result
  RPC->>N: close stream
```

### Agent loop (backend)

```mermaid
flowchart TB
  subgraph loop["Agent loop"]
    P["Build messages\n+ tool schemas"]
    L["LLM stream"]
    X{"Tool calls\nin output?"}
    E["Execute tools\n(fs, shell, edit)"]
    A["Append result\nto messages"]
    D["Done\n(final answer)"]

    P --> L
    L --> X
    X -->|yes| E
    E --> A
    A --> P
    X -->|no| D
  end
```

---

## Commands

| Command | Description |
|--------|-------------|
| `shellgeist` / `sgd` | Start the daemon (or run one-off with args). |
| `:SGChat` | Open the chat sidebar and focus the request input. |
| `:SGAgent <goal>` | Send a one-shot goal and stream the response. |
| `:SGReview` | Open the review panel for current diff / conflicts. |
| `:SGEdit <file> <instruction>` | Edit a file with a natural-language instruction. |
| `:SGMode auto` / `:SGMode review` | Set auto vs manual tool approval. |

**Install (plugin)**  
Point your Neovim config to the `nvim/` directory (e.g. with lazy.nvim or as a local path). Ensure the `shellgeist` (or `install.sh`) script is on your `PATH` or that the daemon is started with `PYTHONPATH=backend python -m shellgeist.cli`.

**Run backend**

```bash
# With Nix
nix develop --command shellgeist

# Or venv + pip
pip install -e .
shellgeist
```

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, linting, and the pull request process.

---

## License

[MIT](LICENSE)

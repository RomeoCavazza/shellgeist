# ShellGeist — Technical and Conceptual Audit

Full repository audit (backend, nvim plugin, CLI, docs, CI). Purpose: assess maturity, identify gaps and risks, and inform next steps (benchmark, then optional: LangChain, Doom, release, mini-site).

---

## Part 1 — Technical Audit

### 1.1 Backend (`backend/shellgeist`)

**Entry points**
- **CLI:** `shellgeist.cli:main` (subcommands: agent, daemon, debug, edit-plan, ping, version).
- **Daemon:** `shellgeist daemon` → `runtime/server.py` (Unix socket `~/.cache/shellgeist.sock`).
- **Agent:** RPC `agent_task` → `Agent.run_task()` in `agent/loop.py`.

**Architecture**
- **agent/:** Loop, orchestration (small-talk, tool-call extraction, decide_no_tool_action), messages, signals, parsing (XML + plaintext, json_utils, normalize).
- **llm/:** OpenAI-compatible client, prompt builder (tool schemas, `.shellgeist.md`), stream with retry.
- **runtime/:** Server (request routing), protocol (Pydantic SGRequest/SGResult), transport (send_json, UIEventEmitter), session (SQLite `~/.cache/shellgeist/history.db`), policy (LoopGuard, RetryEngine), paths (resolve_repo_path), telemetry.
- **tools/:** Registry, executor; fs (read_file, write_file, list_files, find_files, get_repo_map), edit (edit_file, edit_plan, apply), patch (unified diff + guards), shell (run_shell, PTY sessions, run_nix_python), git_utils.

**Dependencies**
- Py: `anyio>=4.0`, `pydantic>=2.0`. No async HTTP lib (streaming via thread + queue).
- Config: `config.py` (OPENAI_*, SHELLGEIST_*); policy retry env; session DB and socket path hardcoded.

**Tests**
- **Present:** `backend/tests/test_paths_and_fs.py` only (2 tests: resolve_repo_path rejection, read_file/list_files relative paths).
- **Missing:** No tests for agent loop, orchestrator, parser, patch, session repair, RPC handler, or integration. CONTRIBUTING.md references test_diff_apply, test_guards, test_normalize, test_tool_parser, test_util_json — **none exist**.

**Risks and gaps**
- **Unimplemented RPCs:** Protocol defines `plan`, `shell`, `chat`; server has **no handlers** — Neovim commands `:SGPlan`, `:SGShell`, `:SGChat` send these and get `unknown_cmd`.
- **Env naming:** Lua diagnostic shows `SHELLGEIST_MODEL_FAST` / `SHELLGEIST_MODEL_SMART`; backend only has `SHELLGEIST_MODEL`.
- **Duplication:** `.shellgeist.md` / `.shellgeist/rules.md` loaded in both `prompt.py` and `agent/loop.py`; could be shared.
- **Socket path:** Daemon uses fixed `~/.cache/shellgeist.sock`; no flag or env to run multiple daemons or custom socket for `agent` (only `ping` accepts `--socket`).
- **Client “fast”/“smart”:** `get_client("fast")` / `get_client("smart")` used in debug; config has a single model.

---

### 1.2 Neovim plugin (`nvim/`)

**Entry points**
- `plugin/shellgeist.lua` → `require("shellgeist").setup()`.

**Files**
- **init.lua:** setup(), commands (SGAgent, SGChat, SGSidebar, SGEdit, SGReview, SGPlan, SGShell, SGStatus, SGPing, SGDiagnostic), daemon spawn, project_root, event handling.
- **sidebar.lua:** NUI Layout/Input/Popup, chat buffer, streaming, render_* (user, response, thinking, action, code, observation, error, diff_review, approval), banner, highlights, keymaps.
- **rpc.lua:** Unix pipe connect, JSON write/read, streaming vs one-shot, approval/review reply_fn.
- **diff.lua:** Diff preview tab, apply_patch, apply_full, git_stage, git_restore.
- **conflict.lua:** Inline conflict UI (markers, choose ours/theirs/both, reject, jump).

**Dependencies**
- **nui.nvim** — required for sidebar; not declared in README/CONTRIBUTING.

**Tests**
- None (no Lua test harness).

**Risks and gaps**
- **:SGPlan / :SGShell / :SGChat** send RPCs the server does not handle → always `unknown_cmd` unless backend adds handlers or plugin stops sending.
- **Daemon path:** Uses `debug.getinfo(1).source` to find project root for spawn; depends on load path.

---

### 1.3 CLI and scripts

- **shellgeist (bash):** Wrapper; PYTHONPATH=backend, fallback nix develop / nix-shell.
- **install.sh:** Symlink to `~/.local/bin`, optional PATH in `.bashrc`; no zsh/fish, no dependency check.
- **Socket:** Daemon always default path; CLI `agent` does not accept alternate socket (only `ping` does).

---

### 1.4 Build and CI

- **pyproject.toml:** Python 3.11+, setuptools, package dir `backend`; scripts shellgeist/sgd; dev: pytest, pytest-asyncio, ruff, mypy. No coverage.
- **flake.nix:** python313, devShell with nixd; PYTHONPATH=./backend. Minor skew with pyproject 3.11+.
- **.github/workflows/ci.yml:** test (pytest backend/tests/), lint (ruff), typecheck (mypy, continue-on-error). No Nix job, no Neovim/nui test.

---

### 1.5 Documentation

- **README.md:** Up to date (Overview, structure, Mermaid, commands, install).
- **CONTRIBUTING.md:** Commands (test/lint/typecheck) OK; **project structure and file list are obsolete** (sgd.py, util_*.py, agent/core.py, diff/, io/, protocol/, safety/, session/ layout; “84 tests” and test_*.py names that do not exist).
- **docs/:** `docs/VERSION_ANALYSIS.md` and this `docs/AUDIT.md` exist; CONTRIBUTING still references ARCHITECTURE.md, ROADMAP.md — **missing** (or to be added).

---

## Part 2 — Conceptual Audit

### 2.1 Product vision and positioning

- **What it is:** Local AI coding agent inside Neovim: tool-calling loop (read/write/list/run_shell/edit), streaming UI, diff review, optional approval. Targets developers who want an in-editor agent without SaaS.
- **Strengths:** Clear “daemon + plugin” model; tool-first design; review mode; Nix/venv support; single-model config; no LangChain/agent framework dependency (lightweight).
- **Alignment:** README and UX (sidebar, [Response]/[Request], hidden status/tool_use) align with “pro, publication-ready” and “maturity” narrative.

### 2.2 UX and consistency

- **Sidebar:** User vs Assistant clearly separated; tool blocks and Status hidden; colors configurable. Coherent.
- **Commands:** :SGAgent and :SGChat are the main entry points; :SGPlan/:SGShell/:SGChat (as distinct RPCs) are **conceptually** present in the UI but **not implemented** on the server — either implement or remove from UI to avoid confusion.
- **Diagnostic:** Displays MODEL_FAST/MODEL_SMART while backend has one model — misleading; align or document.

### 2.3 Conceptual gaps

- **Plan / Shell / Chat as commands:** Protocol and Neovim expose them; server ignores. Decision needed: implement (e.g. plan → agent with goal “plan only”, shell → run_shell flow, chat → agent_task) or drop from protocol and UI.
- **Multi-workspace:** One daemon, one socket; session_id separates chats but root is per request. Fine for single project; document if “multiple projects” is out of scope.
- **Observability:** No metrics/APM; telemetry is retry-only. Acceptable for current stage; benchmark and audit can inform whether to add.

### 2.4 Maturity assessment

| Dimension        | Status   | Note |
|------------------|----------|------|
| Feature set      | Mature   | Agent, tools, review, PTY, history. |
| Code structure   | Mature   | Clear agent/llm/runtime/tools split. |
| Documentation    | Partial  | README good; CONTRIBUTING and referenced docs stale. |
| Tests            | Weak     | Two tests only; no agent/tools/RPC coverage. |
| CI               | OK       | pytest, ruff, mypy; mypy optional; no Nix/Neovim. |
| Security/safety  | Good     | Path containment, loop guard, retry, guards on patch. |
| UX consistency   | Good     | Sidebar and prompts aligned; plan/shell/chat RPCs inconsistent. |

**Verdict:** Project is **promising and in maturation phase**. Solid for daily use and public release **after**: (1) benchmark and full audit (this doc), (2) fixing critical doc/RPC gaps, (3) optional test expansion.

---

## Part 3 — Recommended next steps (before “demain”)

1. **Align CONTRIBUTING.md** with current tree and test count; add or remove references to ARCHITECTURE.md / ROADMAP.md.
2. **Decide on plan/shell/chat:** Either implement server handlers (e.g. map to agent_task or dedicated flows) or remove from protocol and Neovim commands.
3. **Document nui.nvim** in README (plugin dependencies).
4. **Unify config:** Either add SHELLGEIST_MODEL_FAST/SMART to backend or stop showing them in SGDiagnostic.
5. **Optional:** Add tests for agent/orchestrator, parser, paths, and one integration path (e.g. agent_task → tool → result) before release.

---

## Part 4 — Ideas for “demain” (after benchmark and audit)

- **LangChain:** Not required for current design; agent loop and tools are custom. If you want chains or more tools later, a thin integration (e.g. “tool adapter” from LangChain tools to ShellGeist registry) could be added without rewriting the core.
- **Doom (Emacs):** Implies a second client (Elisp) talking to the same daemon over the same socket and protocol. Doable if protocol is stable and documented; RPC and events would need a clear spec (e.g. ARCHITECTURE.md).
- **Release + video + mini-site:** Fits after the above cleanup and optional test expansion; README already supports a “pro” launch.

---

*Audit date: 2026-03-09. Repo state: 49 commits, main branch.*

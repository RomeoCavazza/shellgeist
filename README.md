# ShellGeist 👻

> **Your transparent, cyberpunk AI terminal agent.**

ShellGeist is a Terminal User Interface (TUI) agent powered by LLMs (local or remote) designed to help developers code, navigate, and automate tasks directly within their terminal environment.

![ShellGeist Preview](https://placehold.co/800x400/000000/00ff00?text=ShellGeist+TUI)

## ✨ Features

*   **Cyberpunk Dashboard**: A transparent, grid-based layout inspired by modern IDEs and sci-fi interfaces.
*   **100% Transparent**: Seamlessly integrates with your terminal's background (wallpaper, blur).
*   **Dual Mode Brain**:
    *   **FAST**: Uses a smaller/faster model for quick queries.
    *   **SMART**: Uses a powerful model (e.g., Llama3, GPT-4) for complex planning.
*   **Autonomous Agent**:
    *   `/auto <goal>`: Breaks down high-level goals into executable steps (edit files, run shell commands).
    *   `/edit <file> <instruction>`: Smart file editing with diff preview.
    *   `/sh <task>`: Generates and executes shell commands safely.
*   **Real-time Monitoring**: CPU/RAM usage and agent status indicators.
*   **Nerd Fonts Integration**: Beautiful file icons and UI elements.

## 🚀 Getting Started

### Prerequisites

*   **Python 3.11+**
*   **Nerd Font** installed in your terminal (recommended for icons).
*   **Ollama** running locally (default) or an OpenAI-compatible API key.

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/RomeoCavazza/shellgeist.git
    cd shellgeist
    ```

2.  Install dependencies (using Nix or Pip):
    ```bash
    # With Nix (Recommended)
    nix develop

    # With Pip
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

3.  Set up environment variables (optional, defaults to localhost Ollama):
    ```bash
    export OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
    export OPENAI_API_KEY="ollama"
    ```

### Usage

Run the agent:

```bash
python agent.py
```

### Commands

| Command | Description |
| :--- | :--- |
| `/chat <msg>` | Chat with the AI (default behavior without prefix). |
| `/auto <goal>` | Autonomous agent mode: plans and executes complex tasks. |
| `/edit <file> <instr>` | Edit a specific file with an instruction. |
| `/sh <task>` | Generate and run shell commands. |
| `/ls` | List files in the current directory. |
| `/quit` | Exit ShellGeist. |

## 🛠️ Configuration

You can toggle between **FAST** and **SMART** models directly in the UI by clicking the status panel.

To configure models via env vars:
```bash
export AI_MODEL_SMART="llama3"
export AI_MODEL_FAST="mistral"
```

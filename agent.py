# agent.py
import json
import time
from pathlib import Path
from datetime import datetime
import psutil
import threading
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.widgets import Header, Footer, Static, Input, Label, Button, DataTable, Sparkline, RichLog
from textual.reactive import reactive
from textual import work
from textual.message import Message
from textual.screen import ModalScreen

from rich.syntax import Syntax
from rich.text import Text
from rich.panel import Panel
from rich.markdown import Markdown
from rich.align import Align
from rich import box
from rich.table import Table
from rich.layout import Layout

# Imports de l'agent
from modules.logger import log_event
from modules.repo_tools import list_files, read_file, grep_pattern, edit_file, unified_diff
from modules.chat import chat_app, ChatState
from modules.auto_agent import plan_auto
from modules.shell_tools import plan_commands, run_command
from modules.safety import is_blocked
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Configuration
LOG_FILE = "ai_lab_events.jsonl"


class SystemMonitor(Static):
    """
    Widget style htop/btm affichant CPU/RAM.
    """
    def on_mount(self) -> None:
        self.set_interval(1.0, self.update_stats)

    def update_stats(self) -> None:
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        
        # Construction d'un tableau compact style dashboard
        grid = Table.grid(expand=True)
        grid.add_column(justify="right", ratio=1)
        grid.add_column(justify="left", ratio=4)
        grid.add_column(justify="right", ratio=1)
        
        # Barre CPU
        bars_cpu = "|" * int(cpu / 5)
        color_cpu = "green" if cpu < 50 else "yellow" if cpu < 80 else "red"
        grid.add_row(
            "[bold]CPU[/]", 
            f"[{color_cpu}]{bars_cpu}[/]", 
            f"{cpu:.1f}%"
        )
        
        # Barre RAM
        bars_mem = "|" * int(mem / 5)
        color_mem = "green" if mem < 60 else "yellow" if mem < 90 else "red"
        grid.add_row(
            "[bold]MEM[/]", 
            f"[{color_mem}]{bars_mem}[/]", 
            f"{mem:.1f}%"
        )

        self.update(Panel(grid, title="[bold cyan]SYSTEM[/bold cyan]", border_style="cyan", box=box.HEAVY))


class AgentStatus(Static):
    """
    Affiche l'état de l'agent et permet de switcher le mode (FAST/SMART).
    """
    status = reactive("IDLE")
    model = reactive("Ollama/Llama3")
    mode = reactive("SMART")

    def on_click(self) -> None:
        self.mode = "FAST" if self.mode == "SMART" else "SMART"
        # TODO: Propager le changement de mode au backend

    def render(self):
        color = "green" if self.status == "IDLE" else "yellow" if self.status == "THINKING" else "red"
        
        # Styles pour le toggle
        fast_style = "[bold green]FAST[/]" if self.mode == "FAST" else "[dim]FAST[/]"
        smart_style = "[bold green]SMART[/]" if self.mode == "SMART" else "[dim]SMART[/]"
        
        grid = Table.grid(expand=True)
        grid.add_column()
        grid.add_row(f"STATUS: [{color} bold]{self.status}[/]")
        grid.add_row(f"MODEL : [blue]{self.model}[/]")
        grid.add_row(f"MODE  : {fast_style} | {smart_style}")
        
        return Panel(grid, title="[bold cyan]AGENT[/bold cyan]", border_style="cyan", box=box.HEAVY)


class FileExplorer(Static):
    """
    Liste les fichiers du projet (style Tree/NerdTree).
    """
    # Mapping Nerd Fonts (Nécessite une police compatible installée)
    NERD_ICONS = {
        ".py": "",  # Python
        ".md": "",  # Markdown
        ".json": "", # JSON
        ".jsonl": "",
        ".nix": "", # NixOS
        ".lock": "", # Lock
        ".txt": "", # Text
        "dir": "",   # Folder
        "default": "" # Default
    }

    def on_mount(self):
        self.refresh_files()

    def refresh_files(self):
        root = Path(".").resolve()
        # Simulation simple d'arbre pour l'instant
        tree_txt = ""
        for path in sorted(root.rglob("*")):
            if ".git" in path.parts or ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            depth = len(path.relative_to(root).parts)
            prefix = "  " * (depth - 1)
            
            if path.is_dir():
                icon = self.NERD_ICONS["dir"]
                color = "blue"
            else:
                icon = self.NERD_ICONS.get(path.suffix, self.NERD_ICONS["default"])
                color = "white"
                
            name = path.name
            tree_txt += f"{prefix}{icon} [{color}]{name}[/{color}]\n"
            
        self.update(Panel(tree_txt, title="[bold cyan]FILES[/bold cyan]", border_style="cyan", box=box.HEAVY))


class MainLog(RichLog):
    """
    Le flux principal d'exécution (style terminal log).
    """
    pass


class CommandInput(Input):
    """
    Prompt style terminal.
    """
    pass


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, message: str, details: str = ""):
        super().__init__()
        self.message = message
        self.details = details

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label(f"[bold red]⚠️ CONFIRMATION REQUIRED[/bold red]\n\n{self.message}", id="question")
            if self.details:
                yield Static(Panel(self.details, title="DETAILS", border_style="yellow"), id="details")
            with Horizontal(id="buttons"):
                yield Button(" [Y] YES ", variant="success", id="yes")
                yield Button(" [N] NO ", variant="error", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


class AiLabTui(App):
    CSS = """
    /* --- GLOBAL TRANSPARENCY --- */
    * {
        background: rgba(0, 0, 0, 0);
    }

    Screen {
        background: rgba(0, 0, 0, 0);
        layout: grid;
        grid-size: 2 3;
        grid-columns: 25% 1fr;
        grid-rows: 10 1fr 3;
        grid-gutter: 0;
        overflow: hidden;  /* Pas de scroll sur l'écran principal */
        height: 100%;      /* Force la hauteur du viewport */
        width: 100%;
    }

    /* --- SIDEBAR (Row 1-2, Col 1) --- */
    #sidebar {
        row-span: 2;
        column-span: 1;
        height: 100%;
        width: 100%;
        border-right: heavy cyan;
        overflow-y: auto;
        scrollbar-size: 1 1;
        scrollbar-color: cyan;
        scrollbar-background: transparent;
    }

    /* --- HEADER AREA (Row 1, Col 2) --- */
    #header {
        row-span: 1;
        column-span: 1;
        height: 100%;
        width: 100%;
        layout: horizontal;
        overflow: hidden;
        background: transparent;
    }
    
    SystemMonitor {
        width: 1fr;
        height: 100%;
        background: transparent;
    }
    AgentStatus {
        width: 35;
        height: 100%;
        background: transparent;
    }

    /* --- MAIN CONTENT (Row 2, Col 2) --- */
    #main_log {
        row-span: 1;
        column-span: 1;
        height: 100%;
        width: 100%;
        border-top: heavy cyan;
        padding: 0 1;
        overflow-y: auto;
        scrollbar-size: 1 1;
        scrollbar-color: cyan;
        scrollbar-background: transparent;
        background: transparent;
    }

    /* --- FOOTER / INPUT (Row 3, Spans all) --- */
    #footer_area {
        row-span: 1;
        column-span: 2;
        width: 100%;
        border-top: heavy cyan;
        height: 100%; /* Remplit la row définie à 3 cellules */
        background: transparent;
    }

    Input {
        border: none;
        color: #00ff00; /* Terminal Green */
        height: 100%;
        background: rgba(0, 0, 0, 0);
    }
    Input:focus {
        border: none;
        background: rgba(0, 0, 0, 0);
    }

    /* --- DIALOG --- */
    #dialog {
        width: 60%;
        height: auto;
        background: #111;
        border: heavy red;
        padding: 2;
        align: center middle;
    }
    
    Button {
        min-width: 10;
        margin: 1;
        background: #333;
        color: white;
        border: none;
    }
    Button:hover {
        background: white;
        color: black;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        # Layout IDE : Sidebar Left, Main Right (Header+Log), Input Bottom
        
        # Row 1-2, Col 1 : Sidebar
        with Vertical(id="sidebar"):
            yield FileExplorer()

        # Row 1, Col 2 : Header
        with Horizontal(id="header"):
            yield SystemMonitor()
            yield AgentStatus()
            
        # Row 2, Col 2 : Main Log
        yield MainLog(id="main_log", markup=True, highlight=True)

        # Row 3, Col 1-2 : Footer Input
        with Container(id="footer_area"):
            yield CommandInput(placeholder="> Enter command...", id="cmd_input")

    def on_mount(self):
        # Force transparency at runtime
        self.screen.styles.background = "transparent"
        
        self.repo_root = Path(".").resolve()
        self.chat_state: ChatState = {"messages": []}
        self.query_one(Input).focus()
        
        log = self.query_one(MainLog)
        
        # ASCII Intro
        ghost_art = """
⠀⠀⠀⠀⠀⠀⠀⢀⠤⠐⠀⠀⠒⠂⠄⡀⠀⠀
⠀⠀⠀⠀⠀⠀⡠⠁⠀⠀⠀⠀⠀⠀⠀⢸⣦⠀
⠀⠀⠀⠀⠀⠰⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⢃
⠀⠀⠀⠀⢠⠃⠀⠀⠀⠀⢠⣄⠀⠀⠀⢀⣀⠘
⠀⠀⠀⠀⠌⠀⠀⠀⠀⢀⠘⢍⠀⣄⠤⡨⠯⠀
⠀⠀⠀⡐⠀⠀⠀⠀⠀⠈⠈⠃⠀⠀⠀⠑⠒⠸
⠀⠀⢠⣱⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠇
⠀⡠⠻⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡌⠀
⠔⠁⣼⣐⡄⠀⠀⠀⢠⠀⠀⠀⠀⡄⠀⡜⠀⠀
⠑⠚⠉⠀⡄⠀⢀⠴⡏⠀⠀⢠⢞⣧⠌⠀⠀⠀
⠀⠀⠀⠀⠑⠚⠓⠉⠗⠧⠒⠉⠉⠀⠀⠀⠀⠀
"""
        welcome_text = Text()
        welcome_text.append(ghost_art, style="bold white")
        welcome_text.append("\nHello, I'm ShellGeist.\n", style="bold cyan")
        welcome_text.append("The ghost haunting your terminal.", style="italic dim white")
        
        log.write(Panel(Align.center(welcome_text), border_style="cyan", box=box.HEAVY))
        log.write(f"[dim]CWD: {self.repo_root}[/dim]")
        log.write("-" * 40)

    async def on_input_submitted(self, event: Input.Submitted):
        cmd = event.value.strip()
        if not cmd: return
        self.query_one(Input).value = ""
        
        log = self.query_one(MainLog)
        log.write(f"\n[bold magenta]USER >[/bold magenta] {cmd}")
        
        self.query_one(AgentStatus).status = "THINKING"
        
        # Dispatch simple
        if cmd.startswith("/ls"):
            self.run_ls()
        elif cmd == "/quit":
            self.exit()
        elif cmd.startswith("/auto"):
            self.run_auto(cmd[6:])
        elif cmd.startswith("/edit"):
            # Ex: /edit README.md Add title
            parts = cmd.split(" ", 2)
            if len(parts) < 3:
                log.write("[red]Usage: /edit <file> <instruction>[/red]")
            else:
                self.run_edit(parts[1], parts[2])
        elif cmd.startswith("/sh") or cmd.startswith("/shell"):
            # Ex: /sh git status
            task = cmd.split(" ", 1)[1] if " " in cmd else ""
            if not task:
                log.write("[red]Usage: /sh <command_or_task>[/red]")
            else:
                self.run_sh(task)
        else:
            self.run_chat(cmd)
            
        self.query_one(AgentStatus).status = "IDLE"

    @work(exclusive=True)
    async def run_ls(self):
        res = list_files(self.repo_root)
        self.query_one(MainLog).write(Panel(res, title="LS", border_style="blue"))

    @work(exclusive=True)
    async def run_chat(self, msg):
        # Récupération du mode (FAST/SMART) depuis l'UI
        mode = self.query_one(AgentStatus).mode.lower()
        
        self.chat_state["messages"].append(HumanMessage(content=msg))
        self.chat_state["model_type"] = mode
        
        try:
            res = chat_app.invoke(self.chat_state)
            ans = res["messages"][-1].content
            self.query_one(MainLog).write(f"[bold cyan]AI ({mode.upper()}) >[/bold cyan] {ans}")
        except Exception as e:
            self.query_one(MainLog).write(f"[red]Error:[/red] {e}")

    @work(exclusive=True)
    async def run_edit(self, filename, instruction):
        self.query_one(AgentStatus).status = "CODING"
        log = self.query_one(MainLog)
        log.write(f"[dim]Editing {filename}...[/dim]")
        
        # Confirmation avant écriture
        diff = edit_file(filename, instruction, dry_run=True)
        if not diff:
            log.write("[red]No changes proposed or error.[/red]")
            return
            
        log.write(Syntax(diff, "diff", theme="monokai", line_numbers=True))
        
        if await self.app.push_screen_wait(ConfirmScreen(f"Apply this edit to {filename}?")):
            edit_file(filename, instruction, dry_run=False)
            log.write(f"[bold green]File {filename} updated.[/bold green]")
        else:
            log.write("[yellow]Edit cancelled.[/yellow]")
            
        self.query_one(AgentStatus).status = "IDLE"

    @work(exclusive=True)
    async def run_sh(self, task):
        self.query_one(AgentStatus).status = "PLANNING"
        log = self.query_one(MainLog)
        log.write(f"[dim]Shell planning: {task}[/dim]")
        
        cmds = plan_commands(task)
        if not cmds:
            log.write("[red]No commands generated.[/red]")
            return
            
        cmd_list = "\n".join([f"- {c}" for c in cmds])
        log.write(Panel(cmd_list, title="SHELL PLAN", border_style="magenta"))
        
        if await self.app.push_screen_wait(ConfirmScreen(f"Run {len(cmds)} shell commands?")):
            self.query_one(AgentStatus).status = "EXECUTING"
            for cmd in cmds:
                log.write(f"[bold]$ {cmd}[/bold]")
                out = run_command(cmd)
                log.write(out)
            log.write("[bold green]Commands executed.[/bold green]")
        else:
            log.write("[yellow]Shell task cancelled.[/yellow]")
            
        self.query_one(AgentStatus).status = "IDLE"

    @work(exclusive=True)
    async def run_auto(self, goal):
        self.query_one(AgentStatus).status = "PLANNING"
        log = self.query_one(MainLog)
        
        # Récupération du mode (FAST/SMART) depuis l'UI
        mode = self.query_one(AgentStatus).mode.lower()
        log.write(f"[dim]Planning: {goal} (mode={mode})[/dim]")
        
        steps = plan_auto(goal, model_type=mode)
        if not steps:
            log.write("[red]Plan failed[/red]")
            return
            
        # Affiche le plan comme un bloc technique
        table = Table(show_header=True, header_style="bold magenta", box=box.MINIMAL)
        table.add_column("#", style="dim", width=3)
        table.add_column("Type", width=8)
        table.add_column("Action")
        
        for i, s in enumerate(steps):
            kind = s['kind']
            icon = "📝" if kind == "edit" else "🐚" if kind == "shell" else "💬"
            color = "blue" if kind == "edit" else "red" if kind == "shell" else "green"
            action = s.get('command') or s.get('instruction') or s.get('message') or ""
            table.add_row(str(i+1), f"[{color}]{icon} {kind}[/]", action)
            
        log.write(Panel(table, title="EXECUTION PLAN", border_style="yellow"))
        
        if not await self.app.push_screen_wait(ConfirmScreen(f"Execute {len(steps)} steps?")):
            log.write("[red]ABORTED[/red]")
            return
            
        self.query_one(AgentStatus).status = "EXECUTING"
        for s in steps:
            log.write(f"[bold]Running {s['kind']}...[/bold]")
            # ... (logique d'exécution simplifiée pour validation layout)
            if s['kind'] == 'shell':
                run_command(s['command'])
                log.write(f"[green]OK[/green]")
        
        log.write("[bold green]DONE[/bold green]")

if __name__ == "__main__":
    app = AiLabTui()
    app.run()

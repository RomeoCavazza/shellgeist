# modules/shell_tools.py
import json
import subprocess
from typing import TypedDict, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.graph import StateGraph
from modules.models import get_client
from modules.logger import log_event


class ShellState(TypedDict):
    messages: List[BaseMessage]


def _call_planner(state: ShellState) -> ShellState:
    """
    Noeud LangGraph : demande au LLM une liste JSON de commandes shell.
    """
    messages = state["messages"]
    client, model = get_client("fast")

    system_prompt = (
        "Tu es un planificateur de commandes shell.\n"
        "L'utilisateur décrit une tâche. Tu dois répondre STRICTEMENT avec un JSON "
        "qui est une liste de chaînes de caractères, chaque chaîne étant une commande "
        "shell POSIX.\n\n"
        "Exemple de réponse valide :\n"
        '  [\"ls -la\", \"echo \\"Done\\"\"]\n\n'
        "Contraintes :\n"
        "- Ne mets AUCUNE explication en dehors du JSON.\n"
        "- Pas de texte avant ou après le JSON.\n"
        "- Évite les commandes dangereuses.\n"
    )

    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if isinstance(m, HumanMessage):
            openai_messages.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            openai_messages.append({"role": "assistant", "content": m.content})

    resp = client.chat.completions.create(
        model=model,
        messages=openai_messages,
    )
    answer = resp.choices[0].message.content

    messages.append(AIMessage(content=answer))
    return {"messages": messages}


_graph = StateGraph(ShellState)
_graph.add_node("planner", _call_planner)
_graph.set_entry_point("planner")
_graph.set_finish_point("planner")
_planner_app = _graph.compile()


def _extract_json_array(raw: str) -> str:
    """
    Essaye d'extraire un JSON de liste à partir d'une réponse potentiellement bruitée :
    - supprime les ```json ... ``` éventuels
    - prend le segment entre le premier '[' et le dernier ']'.
    """
    txt = raw.strip()

    if txt.startswith("```"):
        lines = txt.splitlines()
        if len(lines) >= 2:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            txt = "\n".join(lines).strip()

    start = txt.find("[")
    end = txt.rfind("]")
    if start != -1 and end != -1 and end > start:
        return txt[start : end + 1]

    return txt


def plan_commands(task: str) -> Optional[list[str]]:
    """
    Prend une tâche en langage naturel, renvoie une liste de commandes shell (ou None).
    """
    state: ShellState = {"messages": [HumanMessage(content=task)]}
    result = _planner_app.invoke(state)
    last = result["messages"][-1]
    raw = last.content.strip()
    cleaned = _extract_json_array(raw)

    try:
        commands = json.loads(cleaned)
        if not isinstance(commands, list):
            raise ValueError("Le JSON n'est pas une liste.")
        cmds = [c for c in commands if isinstance(c, str)]
        log_event("shell_plan", task=task, commands=cmds)
        return cmds
    except Exception:
        log_event("shell_plan_error", task=task, raw=raw)
        return None


def run_command(cmd: str) -> None:
    """
    Exécute une commande shell (avec shell=True).
    À utiliser uniquement après confirmation humaine.
    """
    try:
        print(f"\n[EXEC] {cmd}\n")
        log_event("shell_exec", cmd=cmd)
        subprocess.run(cmd, shell=True, check=False)
    except Exception as e:
        log_event("shell_exec_error", cmd=cmd, error=str(e))
        print(f"[ERREUR] Commande échouée : {e}")

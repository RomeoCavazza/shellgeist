# modules/auto_agent.py
import json
from typing import TypedDict, List, Optional

from modules.models import get_client


class AutoStep(TypedDict, total=False):
    kind: str          # "edit" | "shell" | "chat"
    file: str          # pour kind == "edit"
    instruction: str   # pour kind == "edit"
    command: str       # pour kind == "shell"
    message: str       # pour kind == "chat"
    note: str          # optionnel


def _strip_code_fences(text: str) -> str:
    """
    Supprime éventuellement les ```json ... ``` autour de la réponse du LLM.
    """
    s = text.strip()
    if s.startswith("```"):
        # On cherche la première fin de ligne après ```...
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        # On enlève un éventuel ``` final
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def plan_auto(goal: str) -> Optional[List[AutoStep]]:
    """
    Prend un objectif en langage naturel,
    renvoie une liste d'étapes structurées (edit/shell/chat) ou None en cas d'erreur.
    """
    client, model = get_client("smart")

    system_prompt = (
        "Tu es un planificateur d'actions pour un agent CLI.\n"
        "L'utilisateur te donne un OBJECTIF. Tu dois le découper en une suite d'étapes, "
        "chacune étant de l'une des formes suivantes :\n\n"
        "1) Étape d'édition de fichier :\n"
        "   {\"kind\": \"edit\", \"file\": \"chemin/relatif.py\", \"instruction\": \"...\"}\n\n"
        "2) Étape shell :\n"
        "   {\"kind\": \"shell\", \"command\": \"commande shell POSIX\"}\n\n"
        "3) Étape de discussion :\n"
        "   {\"kind\": \"chat\", \"message\": \"message à envoyer au LLM\"}\n\n"
        "Tu dois répondre STRICTEMENT avec un JSON qui est une liste de ces objets, par exemple :\n"
        "[\n"
        "  {\"kind\": \"edit\", \"file\": \"agent_cli.py\", \"instruction\": \"simplifier le help\"},\n"
        "  {\"kind\": \"shell\", \"command\": \"pytest\"}\n"
        "]\n\n"
        "Contraintes :\n"
        "- Pas de texte avant ou après le JSON.\n"
        "- Pas de commentaires, pas de markdown, pas de ```.\n"
        "- Les chemins de fichier sont relatifs à la racine du repo.\n"
        "- Si tu n'as pas d'idée pertinente, renvoie simplement [].\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal},
    ]

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    raw = resp.choices[0].message.content or ""
    raw = _strip_code_fences(raw)

    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Le JSON renvoyé n'est pas une liste.")
        steps: List[AutoStep] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind not in ("edit", "shell", "chat"):
                continue
            step: AutoStep = {"kind": kind}
            if kind == "edit":
                step["file"] = str(item.get("file", "")).strip()
                step["instruction"] = str(item.get("instruction", "")).strip()
            elif kind == "shell":
                step["command"] = str(item.get("command", "")).strip()
            elif kind == "chat":
                step["message"] = str(item.get("message", "")).strip()
            note = item.get("note")
            if isinstance(note, str) and note.strip():
                step["note"] = note.strip()
            steps.append(step)
        return steps
    except Exception:
        return None


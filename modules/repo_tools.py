# modules/repo_tools.py
from pathlib import Path
from typing import Tuple, Optional
import textwrap
import json
import difflib

from modules.models import get_client


IGNORED_DIRS = {".git", ".venv", "__pycache__"}


def list_files(root: Path, max_depth: int = 3) -> str:
    lines: list[str] = []

    def walk(p: Path, depth: int):
        if depth > max_depth:
            return
        rel = p.relative_to(root)
        prefix = "  " * depth
        if p.is_dir():
            if rel.name in IGNORED_DIRS:
                return
            lines.append(f"{prefix}{rel}/")
            for child in sorted(p.iterdir()):
                walk(child, depth + 1)
        else:
            lines.append(f"{prefix}{rel}")

    walk(root, 0)
    return "\n".join(lines)


def read_file(root: Path, relpath: str, max_chars: int = 8000) -> str:
    file_path = (root / relpath).resolve()
    try:
        file_path.relative_to(root.resolve())
    except ValueError:
        return "Refus : chemin en dehors du repo."

    if not file_path.exists():
        return f"Fichier introuvable: {relpath}"

    if file_path.is_dir():
        return f"{relpath} est un dossier, pas un fichier."

    content = file_path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        content = content[:max_chars] + "\n[... contenu tronqué ...]"
    return content


def grep_pattern(root: Path, pattern: str, max_results: int = 50) -> str:
    """
    Cherche 'pattern' dans les fichiers texte du repo.
    """
    results: list[str] = []

    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            if pattern in line:
                rel = path.relative_to(root)
                results.append(f"{rel}:{i}: {line}")
                if len(results) >= max_results:
                    return "\n".join(results)

    if not results:
        return f"Aucun match pour '{pattern}'."
    return "\n".join(results)


def _extract_json_object(raw: str) -> str:
    """
    Essaye d'extraire un JSON d'objet à partir d'une réponse potentiellement bruitée :
    - supprime les ```json ... ``` éventuels
    - prend le segment entre le premier '{' et le dernier '}'.
    """
    txt = raw.strip()

    # Enlever les fences ```...```
    if txt.startswith("```"):
        # enlever la première ligne ```json ou ```
        lines = txt.splitlines()
        if len(lines) >= 2:
            # supprime première et éventuellement dernière ligne ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            txt = "\n".join(lines).strip()

    # Isoler un objet JSON
    start = txt.find("{")
    end = txt.rfind("}")
    if start != -1 and end != -1 and end > start:
        return txt[start : end + 1]

    return txt  # on tente tel quel


def edit_file(
    root: Path,
    relpath: str,
    instruction: str,
    model_type: str = "smart",
) -> Tuple[Optional[str], str]:
    """
    Demande au LLM de proposer une nouvelle version complète du fichier.

    Retourne (ancien_contenu, nouveau_contenu)
    ou (None, message_d_erreur).
    """
    file_path = (root / relpath).resolve()
    try:
        file_path.relative_to(root.resolve())
    except ValueError:
        return None, "Refus : chemin en dehors du repo."

    if not file_path.exists():
        return None, f"Fichier introuvable: {relpath}"
    if file_path.is_dir():
        return None, f"{relpath} est un dossier, pas un fichier."

    old_content = file_path.read_text(encoding="utf-8", errors="replace")

    client, model = get_client(model_type)

    system_prompt = (
        "Tu es un assistant d'édition de code.\n"
        "On te donne le contenu COMPLET d'un fichier et une instruction.\n"
        "Tu dois répondre STRICTEMENT avec un JSON de la forme :\n"
        '{\"new_content\": \"...\"}\n\n'
        "- new_content doit contenir le contenu COMPLET du fichier après modification.\n"
        "- Ne mets AUCUN autre texte en dehors du JSON.\n"
        "- Si l'instruction est vague (ex: 'simplifie le help'), NE TOUCHE PAS "
        "à la structure du programme :\n"
        "    * modifie uniquement les messages d'aide, les commentaires ou les docstrings,\n"
        "    * ne supprime pas de blocs de code entiers,\n"
        "    * ne remplace pas tout le fichier par une ligne.\n"
        "- Essaie de MINIMISER les modifications : garde le maximum de code identique.\n"
    )

    user_prompt = (
        f"Instruction d'édition : {instruction}\n\n"
        f"Contenu actuel du fichier {relpath} :\n"
        "-----\n"
        f"{old_content}\n"
        "-----\n"
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw = resp.choices[0].message.content.strip()
    cleaned = _extract_json_object(raw)

    try:
        data = json.loads(cleaned)
        new_content = data.get("new_content")
        if not isinstance(new_content, str):
            return None, "Réponse JSON invalide: 'new_content' manquant ou non texte."
    except Exception as e:
        return None, f"Impossible de parser la réponse JSON: {e}"

    return old_content, new_content


def unified_diff(old: str, new: str, relpath: str) -> str:
    """
    Retourne un diff unifié lisible entre old et new.
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{relpath} (ancien)",
        tofile=f"{relpath} (nouveau)",
    )
    return "".join(diff) or "[INFO] Aucun changement proposé."

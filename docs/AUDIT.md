# Audit ShellGeist — Version unifiée

Document unique regroupant les audits techniques : bugs de flux, appels d’outils (parsing / fallbacks), et qualité de code (anti-slop). Dernière mise à jour : état du code actuel.

**Références code** : `backend/shellgeist/agent/loop.py`, `backend/shellgeist/agent/orchestrator.py`, `backend/shellgeist/agent/parsing/parser.py`, `backend/shellgeist/runtime/policy.py`, `backend/shellgeist/runtime/paths.py`, `backend/shellgeist/llm/prompt.py`.

---

## Partie 1 — Bugs de flux (scénario utilisateur)

Contexte : scénario « hey → liste dossiers → lis README/loop.py → affiche README → crée ping.py → exécute-le → … ».

### 1.1 « Exécute-le et renvoie-moi la sortie »

| Élément | Détail |
|--------|--------|
| **Problème** | Après création de `ping.py`, l’utilisateur demande d’exécuter le script. Le modèle enchaînait `list_files` + `read_file` au lieu de `run_shell("python3 ping.py")`. |
| **Cause** | Pas de règle explicite pour « après un write_file sur un .py, si l’utilisateur dit exécute/run it → run_shell avec python3 <ce fichier> ». |
| **Statut** | ✅ **Fait** — Règle 6 dans le prompt (`llm/prompt.py`) : si l’utilisateur dit "execute it", "run it", "exécute-le" ou "exécute-le et renvoie la sortie" après l’écriture d’un .py, appeler run_shell avec `python3 <that file>.py` et ne pas utiliser list_files/read_file pour ça. |
| **Optionnel** | Chemin déterministe dans la boucle agent : détecter (dernier outil = write_file sur .py + goal contient « exécute ») et injecter automatiquement `run_shell(python3 <fichier>)`. |

### 1.2 Chemin ambigu : `read_file("shellgeist/__init__.py")`

| Élément | Détail |
|--------|--------|
| **Problème** | Plusieurs fichiers correspondent (backend/shellgeist/__init__.py, backend/shellgeist/runtime/__init__.py, …). Le backend lève « Ambiguous file path. Possible matches: … ». |
| **Cause** | `resolve_existing_repo_file` (paths.py) lève une exception quand il y a plusieurs candidats ; le modèle n’avait pas de consigne pour désambiguïser. |
| **Statut** | ✅ **Fait** — (1) Règle 7 dans le prompt : en cas de chemin ambigu, utiliser le chemin relatif complet depuis la racine (ex. `backend/shellgeist/__init__.py`). (2) Dans `policy.py`, `is_ambiguous_path_result()` + dans `record_outcome` : ne pas compter cette erreur comme échec, pas d’ajout à `blocked_call_hashes` → le modèle peut réessayer avec un chemin complet. |

### 1.3 BLOCKED_REPEAT_TOOL après erreurs « Ambiguous »

| Élément | Détail |
|--------|--------|
| **Problème** | Après plusieurs échecs dont « Ambiguous file path », le runtime renvoyait BLOCKED_REPEAT_TOOL et la tâche s’arrêtait. |
| **Cause** | `is_failed_result()` comptait « validation failed » (donc « Ambiguous file path ») comme échec ; après 2 échecs le call était bloqué. |
| **Statut** | ✅ **Fait** — Dans `record_outcome`, si `is_ambiguous_path_result(result)` alors on ne met pas à jour les compteurs d’échec ni `blocked_call_hashes` ; retour `(False, "")` pour laisser l’agent continuer. |

---

## Partie 2 — Appels d’outils (parsing, fallbacks)

Constat initial : le modèle répondait parfois en pseudo-code ou markdown au lieu de `<tool_use>` (ex. `write_file{"path": "cube.py", "content": "..."}` dans un bloc ` ```python `).

### 2.1 Comparaison avec avante.nvim et OpenCode

| Aspect | avante.nvim | OpenCode | ShellGeist (actuel) |
|--------|-------------|----------|----------------------|
| Source des appels | API tool calling | Texte + repair | Texte uniquement |
| Fallbacks | Limités (API) | Alias + repair | Canonical + XML + plaintext + tool_name{ + blocs ``` |
| Format mal formé | Erreur API | Réparation | Reconnu (fallbacks implémentés) |
| Bloc ```python | N/A | N/A | Fouillé et parsé |

- **avante.nvim** : tool calling natif ; en text completion il faut des fallbacks et alias robustes.
- **OpenCode** : chaîne exact → normalisé → alias ; schémas souples pour paramètres.

### 2.2 Format attendu et parsing actuel

- **Canonical** : `<tool_use>{"name": "...", "arguments": {...}}</tool_use>`.
- **Parsing** : (1) Canonical, (2) XML et variantes + blocs ` ```tool_use `, (3) Plaintext `ToolName: { json }` ou `ToolName( { json } )`, (4) **tool_name{ json }** sans séparateur, (5) contenu des blocs **` ```python `** et **` ``` `** réinjecté dans la chaîne de parsing.

### 2.3 Correctifs implémentés

| Piste | Description | Statut |
|-------|-------------|--------|
| Fallback **tool_name{** | Regex `(write_file|read_file|list_files|run_shell|find_files|edit_file)\s*\{` + extraction JSON brace-balanced. | ✅ **Fait** — `orchestrator.py` : `_PLAINTEXT_TOOL_NO_SEP_RE` + `_extract_plaintext_tool_calls_impl` (1a). |
| Blocs **```python** / **```** | Extraire le contenu des blocs et appliquer les mêmes parsers. | ✅ **Fait** — En tête de `extract_plaintext_tool_calls`, boucle sur ` ```python ... ``` ` et ` ``` ... ``` `, puis `_extract_plaintext_tool_calls_impl(inner)`. |
| Rappel dans le prompt | « Only output <tool_use>...; no ``` code blocks for tool calls ». | ✅ Déjà présent (RULES 4). |
| Alias / repair | `writefile` → `write_file`, `cmd` → `command`, etc. | ✅ Déjà en place dans `_normalize_tool_payload` et `_CLASS_TO_TOOL`. |

### 2.4 Parser canonique : JSON imbriqué (arguments avec `{ }`)

Quand le modèle renvoie `<tool_use>{"name": "write_file", "arguments": {"path": "cube.py", "content": "..."}}</tool_use>`, le regex canonique `\{.*?\}` (non greedy) s’arrêtait au **premier** `}`, donc le corps extrait était tronqué, `loads_obj` échouait, et le message était renvoyé comme réponse au lieu d’exécuter l’outil → cube.py jamais créé, puis read_file("cube.py") → File not found. **Correctif** : dans `parser.py`, extraction du corps JSON par **accolades équilibrées** (`_extract_brace_balanced_body`) en ignorant les `{`/`}` à l’intérieur des chaînes. `parse_canonical_tool_use` utilise cette extraction pour parser correctement les appels avec `"arguments": { ... }`.

---

## Partie 3 — Qualité de code (anti-slop)

Rapport ciblé : duplication exacte, verbosité évitable.

### 3.1 policy.py — Hint run_shell répété 3 fois

| Élément | Détail |
|--------|--------|
| **Constat** | Lignes ~111–115, ~124–128, ~146–150 : même bloc `hint = ""` puis `if tool_name == "run_shell": cmd = ...; if ".py" in cmd: hint = " Use 'python3 <your_script>.py'..."`. |
| **Action recommandée** | Extraire une fonction `_run_shell_python_hint(tool_name, args, for_failure=False)` et l’appeler aux 3 endroits. |
| **Statut** | ⏳ **À faire** (optionnel) — Réduit ~15 lignes et évite la dérive si on change le message. |

### 3.2 loop.py — Résumés et messages d’échec

| Élément | Détail |
|--------|--------|
| **_summarize_read_observation** | Grosse branche pour les .py (docstring, classes, fonctions, plusieurs variantes de phrase). Action : 1–2 templates (ex. `_py_summary_label(...)`). |
| **_summarize_failure_for_user** | Plusieurs blocs `if ... return (f"...\n\nDétail technique : {raw}")`. Action : factoriser en `_failure_message(label, short_explanation, raw)`. |
| **Statut** | ⏳ **À faire** (optionnel) — Améliore la maintenabilité sans changer le comportement. |

### 3.3 Complexité justifiée (pas du slop)

- **parser.py** : nombreuses regex et fallbacks nécessaires pour la robustesse.
- **orchestrator.py** : `classify_model_turn` et `_looks_like_final_response` — chaque branche a un rôle distinct.
- **loop.py** : les helpers `_*` sont utilisés ; pas de fonction morte identifiée.

---

## Synthèse des statuts

| Domaine | Fait | À faire / optionnel |
|---------|------|----------------------|
| **Bugs de flux** | Règles 6 et 7 du prompt ; `is_ambiguous_path_result` + `record_outcome` pour chemins ambigus | Optionnel : chemin déterministe « exécute-le » après write_file .py |
| **Tool calls** | Fallback tool_name{ ; blocs ```python/``` ; alias déjà présents | — |
| **Transcript (Partie 4)** | `is_no_change_result` + record_outcome pour NO_CHANGE ; message fs.py | Injecter hint « Run python3 <file> » ; renforcer règle 1 ; éviter placeholder new_content |
| **Anti-slop** | — | Factoriser hint run_shell dans policy.py ; simplifier _summarize_read_observation et _summarize_failure_for_user dans loop.py |

---

## Fichiers concernés

| Fichier | Rôle |
|---------|------|
| `backend/shellgeist/agent/loop.py` | Boucle agent, résumés, détection list-only / strict target. |
| `backend/shellgeist/agent/orchestrator.py` | Classification du tour, extraction plaintext + tool_name{ + blocs ```. |
| `backend/shellgeist/agent/parsing/parser.py` | Canonical et XML tool_use. |
| `backend/shellgeist/runtime/policy.py` | LoopGuard, `is_failed_result`, `is_ambiguous_path_result`, `is_no_change_result`, `record_outcome`. |
| `backend/shellgeist/runtime/paths.py` | `resolve_existing_repo_file`, erreur « Ambiguous file path ». |
| `backend/shellgeist/tools/fs.py` | write_file, message NO_CHANGE. |
| `backend/shellgeist/llm/prompt.py` | Règles 1–7 (dont exécute-le et chemins ambigus). |

---

## Partie 4 — Audit d’un transcript réel (chaque sortie)

Transcript : utilisateur demande « Exécute-le et renvoie-moi la sortie » après création de `ping.py`, puis le flux dérape jusqu’à `BLOCKED_REPEAT_TOOL: Exact call failed repeatedly`.

### 4.1 Sorties observées (dans l’ordre)

| # | Outil | Sortie / effet | Problème |
|---|--------|-----------------|----------|
| 1 | list_files | backend/ | Inutile pour « exécute ping.py ». |
| 2 | list_files | Arborescence backend/shellgeist/ | Idem : le modèle explore au lieu d’exécuter. |
| 3 | run_shell | **Contenu de CONTRIBUTING.md** (pip install, Contributing, License) | Mauvaise commande : probablement `cat CONTRIBUTING.md` au lieu de `python3 ping.py`. La règle 6 du prompt n’a pas été suivie ou le « dernier fichier .py écrit » n’est pas assez explicite dans le contexte. |
| 4 | list_files | nvim/ | Dérapage : le modèle quitte la tâche « exécute ping.py » et part sur nvim. |
| 5 | read_file | conflict.lua | Hors sujet. |
| 6 | write_file | path=`nvim/lua/shellgeist/conflict.lua.new`, content=`{{"new_content"}}\n\n-- This is the new content...` | Contenu template littéral `{{"new_content"}}` (placeholder non remplacé). Confusion possible avec le schéma/résultat d’`edit_file` (champ `new_content`) ou hallucination. |
| 7 | read_file | conflict.lua (original) | Relecture inutile. |
| 8 | write_file | idem path + contenu similaire | Même appel qu’en 6. |
| 9 | read_file | conflict.lua | Boucle. |
| 10 | write_file | (répétition) | Même call_hash qu’en 6/8. |
| 11 | write_file | **KO** | `BLOCKED_REPEAT_TOOL: Exact call failed repeatedly.` |

### 4.2 Chaîne de causes

1. **run_shell avec la mauvaise commande** — Le modèle n’a pas appelé `run_shell("python3 ping.py")`. Soit la règle 6 est insuffisante, soit le contexte (dernier write_file = ping.py) n’est pas mis en avant (pas de hint « Run the script you just wrote: python3 ping.py »).

2. **Dérive de tâche** — Après la sortie erronée (CONTRIBUTING), le modèle enchaîne sur list_files(nvim), read_file(conflict.lua), write_file(conflict.lua.new). Aucun ancrage fort sur « répondre à la dernière demande utilisateur : exécuter ping.py ».

3. **write_file avec contenu template** — Le champ `content` contient littéralement `{{"new_content"}}`. À clarifier : exemple dans le prompt, schéma d’edit_file affiché au modèle, ou confusion entre write_file et edit_file.

4. **Répétition du même write_file → BLOCKED** — Premier write_file(path=conflict.lua.new, content=X) → succès. Deuxième write_file(mêmes path + content) → retour `NO_CHANGE: ... already contains this exact content`. `NO_CHANGE` était compté comme **succès** par le LoopGuard → success_count = 2 → BLOCKED_SUCCESS_REPEAT → call_hash dans blocked_call_hashes. Appel suivant → check_call refuse → « Exact call failed repeatedly ».

### 4.3 Correctifs appliqués (code)

| Problème | Fichier | Correction |
|----------|---------|------------|
| NO_CHANGE compté comme succès → blocage après retry | `policy.py` | `is_no_change_result(result)` : si vrai, on ne met pas à jour success_counts dans record_outcome (retour (False, "")). Un retry write_file qui renvoie NO_CHANGE ne déclenche plus BLOCKED_SUCCESS_REPEAT. |
| Message NO_CHANGE | `fs.py` | « Do NOT call write_file again **with the same content**. » |

### 4.4 Pistes restantes (prompt / boucle)

| Problème | Piste |
|----------|--------|
| run_shell avec mauvaise commande | En cas de goal « exécute-le » / « run it » et dernier outil = write_file sur un .py : **injecter** dans le contexte une ligne du type : « Run the script you just wrote: `python3 ping.py` » (nom de fichier déduit du dernier write_file). |
| Dérive (list nvim, edit conflict.lua) | Renforcer la règle 1 : « Do only what the user asked in their **last** message. Do not switch to other files or tasks. » Ou rappeler le goal courant en tête du tour. |
| Contenu `{{"new_content"}}` | Vérifier les exemples/schémas envoyés au LLM (write_file vs edit_file) ; éviter tout placeholder de type `{{"new_content"}}` dans les exemples de write_file. |

### 4.5 Boucle agent : pas en background, mais « thinking » trop tôt

**Vérification** : la boucle n’est pas appelée en arrière-plan. Chaque message utilisateur déclenche un seul `run_task` (server.py → `agent.run_task(goal, ...)`). Pour les tâches simples (list_only, read_only) un **chemin déterministe** existe (`_build_deterministic_batch_if_possible` → `list_files` ou `read_file` sans appel LLM), et après exécution on retourne sans rappeler le LLM (court-circuit list-only vers 1393–1407, read_only vers 1365–1373).

**Problème** : le statut « busy / thinking » (`ui.status(True)`) était envoyé au **début de chaque itération**, avant de savoir si on allait appeler le LLM. Pour un « Liste le contenu du répertoire », l’UI affichait donc une phase « thinking » alors qu’on ne faisait qu’un `list_files` déterministe.

**Correctif** : `await ui.status(True)` est déplacé **à l’intérieur du bloc `else`**, juste avant `run_llm_stream_with_retry`. Ainsi, pour list_only / read_only déterministes, aucun indicateur « thinking » n’est envoyé ; il n’apparaît que lorsqu’on appelle réellement le LLM.

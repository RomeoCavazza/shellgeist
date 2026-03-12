# Technical Documentation Portal

Bienvenue dans la documentation technique approfondie de **ShellGeist**. Ce projet est à la fois un outil de productivité pour Neovim et un **objet d'étude** sur les workflows agentiques.

---

## Structure de `docs/`

```text
docs/
├── README.md           # Ce portail
├── cloc-report.md      # Statistiques du code (Généré par cloc)
├── specification.txt   # Dictionnaire technique dense
└── diagrams/           # Sources et exports visuels
    ├── *.puml          # Sources PlantUML
    └── png/            # Exports images (.png)
```

---

## 1. Dictionnaire Technique

[**specification.txt**](./specification.txt) — Une vue condensée de l'architecture, des variables clés, des modules backend et frontend, et des flux logiques de l'agent.

---

## 2. Statistiques du Code (CLOC)

| Language | files | blank | comment | code |
| :--- | :--- | :--- | :--- | :--- |
| Python | 32 | 1148 | 664 | 5923 |
| Lua | 6 | 317 | 295 | 2774 |
| **SUM** | **38** | **1465** | **959** | **8697** |

> [!TIP]
> Pour régénérer ce rapport :
> ```bash
> cloc . --exclude-dir=.git,node_modules,result,.direnv --md --out=docs/cloc-report.md
> ```

Rapport complet : [**cloc-report.md**](./cloc-report.md).

---

## 3. Architecture & Logique

### Cycle de Vie de l'Agent
Le schéma suivant détaille comment l'agent bascule entre décisions probabilistes et chemins déterministes.

```mermaid
flowchart TD
    Start([Start]) --> LoadContext[LoadContext]
    LoadContext --> ClassifyIntent[ClassifyIntent]
    
    ClassifyIntent --> IsModel{Intent Type?}
    IsModel -- Model --> ModelDecide[ModelDecide]
    ModelDecide --> VB[ValidateBatch]
    IsModel -- Deterministic --> DP[DeterministicPath]
    
    VB --> EB[ExecuteBatch]
    DP --> EB
    
    EB --> OR[ObserveResult]
    OR --> IsSuccess{Success?}
    
    IsSuccess -- Yes --> FT1[FinalizeTurn]
    FT1 --> Stop([Stop])
    
    IsSuccess -- No / Error --> RepairOnce[RepairOnce]
    RepairOnce --> EB2[ExecuteBatch]
    EB2 --> RetryCheck{Retry Limit?}
    
    RetryCheck -- Remaining --> OR
    RetryCheck -- Exhausted --> FT2[FinalizeTurn]
    FT2 --> Stop
```

### Architecture Système
Couplage lâche entre le daemon Python et le plugin Lua.

```mermaid
graph LR
    subgraph NV ["Frontend (Neovim)"]
        UI["Sidebar UI (Lua)"]
        RPC["RPC Client (Lua)"]
        Conflict["Conflict Resolver (Lua)"]
    end
    
    subgraph BE ["Backend (Daemon)"]
        Server["Server (Python)"]
        Agent["Agent Loop (Python)"]
        Orch["Orchestrator (Python)"]
        Tools["Tools (Python)"]
    end
    
    subgraph WS ["Workspace"]
        FS[("File System")]
        Git[("Git Repo")]
    end
    
    subgraph LLM ["LLM Provider"]
        LLM_Node["Ollama / OpenAI"]
    end
    
    UI <--> RPC
    RPC <-->|JSON-lines / Unix Socket| Server
    Server <--> Agent
    Agent <--> Orch
    Agent <--> Tools
    Tools <--> FS
    Tools <--> Git
    Agent <-->|HTTPS / Streaming| LLM_Node
```

### Séquence d'Éxécution
Flux de données typique lors d'une requête utilisateur.

```mermaid
sequenceDiagram
    actor User
    participant Neovim
    participant Sidebar
    participant Server as Daemon Server
    participant Agent as Agent Loop
    participant LLM
    participant Tools
    
    User->>Neovim: :SGAgent "fix bug"
    Neovim->>Server: Request (JSON goal)
    Server->>Agent: run_task(goal)
    
    loop LLM Cycle
        Agent->>LLM: Prompt (history + schemas)
        LLM-->>Agent: Delta (streaming response)
        Agent->>Sidebar: response_draft (UI updates)
        
        alt Tool Call Detected
            Agent->>Tools: execute_tool_call(args)
            Tools-->>Agent: Result (stdout/stderr)
            Agent->>Sidebar: observation (card UI)
        else Final Answer
            Agent->>Sidebar: response (done)
        end
    end
    
    Agent->>Server: Task Result
    Server->>Neovim: RPC response
```

---

## 4. Diagrammes (PlantUML)

Sources : `diagrams/*.puml`. Les sources sont conservées pour archive, mais les diagrammes ci-dessus utilisent Mermaid pour un rendu dynamique.

Pour régénérer les images PNG (optionnel) :
```bash
nix shell nixpkgs#plantuml -c plantuml -tpng -odocs/diagrams/png docs/diagrams/*.puml
```

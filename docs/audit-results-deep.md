# Audit approfondi — docs/results.md (run avec README sauvegardé)

## Contexte

- **README** : Tu as sauvegardé le README ; l’agent le voit maintenant quand il est traqué. Dans ce transcript les tours « Lis README » affichent encore « fichier vide » (run peut-être avant la sauvegarde ou session antérieure).
- **Problèmes récurrents** : erreurs de syntaxe Python générées par le modèle, pas de bilan pour l’utilisateur quand c’est bloqué, et mauvais enchaînements d’outils (run_shell au lieu d’éditer d’abord).

---

## 1. Erreurs de syntaxe observées

### ping.py

| Étape | Erreur | Cause probable |
|-------|--------|----------------|
| Premier write | `return str(e)}` | **Accolade en trop** `}` à la fin (résidu JSON / f-string). |
| Repair | Corrigé par hasard en ajoutant ` \` et un bloc `if __name__` (ligne coupée). | — |
| Réécris ping.py | `print(f"Failed to connect to {host}: {e}}")` | **Double `}}`** : en f-string un `}` seul ferme l’expression ; pour afficher une accolade littérale il faut `}}`. Ici `{e}}` → expression `e` puis `}` littéral → erreur « single '}' is not allowed ». Il faut **`{e}`** (un seul `}`). |
| Après erreur | Aucun write_file de correction | Le modèle a relancé **run_shell deux fois** (même erreur) puis le run a été arrêté. Il n’a **jamais corrigé** le fichier. |

### cube.py

| Étape | Erreur | Cause probable |
|-------|--------|----------------|
| Premier write | `print(..., matrix])))` | **Une parenthèse en trop** à la fin. |
| Premier write | `int/projected_vertices[edge[0]][1]` | **Typo** : slash `/` au lieu de `(`. |
| Premier write | `int(projected_vertices[edge[0]][1]) + 10)` | **Parenthèse en trop** : le dernier `)` ne correspond à rien (il faut soit `int(...+ 10)` soit `int(...) + 10` sans `)` en trop). |
| Repair | Corrige `matrix])))` → `matrix]))` | OK. |
| Repair | Introduit `int(projected_vertices[edge[0]][1]) + 10)` | **Même type d’erreur** : une `)` en trop à la fin de la ligne. |

**Pattern** : le modèle produit souvent des erreurs de ponctuation (une `)` ou `}` en trop, ou `/` au lieu de `(`). En repair il en corrige une mais en introduit ou laisse une autre.

---

## 2. Pas de bilan pour l’utilisateur quand c’est bloqué

**Constat** : après plusieurs échecs (validation qui échoue, repair qui n’y arrive pas, ou BLOCKED_REPEAT), l’agent **ne revient pas vers l’utilisateur** avec un résumé clair du type :

- ce qui a échoué (fichier, commande, erreur),
- ce qui a été tenté (N tentatives de correction),
- une piste de solution (ex. « Corriger la ligne X : utiliser `{e}` et non `{e}}` dans l’f-string »).

Aujourd’hui le message final est technique (« Le modèle n’a pas terminé correctement la correction… », « Détail technique : … ») et ne met pas en forme un **bilan** explicite (nombre de tentatives, erreur principale, action recommandée).

**Piste** : enrichir le message d’erreur final (celui envoyé à l’UI / à l’utilisateur) avec un court **bilan** quand `repair_attempts >= 1` ou quand on arrête après BLOCKED_REPEAT : « Tentatives de correction : N. Dernière erreur : … Recommandation : … »

---

## 3. Mauvais enchaînements d’outils

**Constat** :

- Pour **ping.py** après la SyntaxError : le modèle a fait **run_shell** deux fois au lieu de **write_file** pour corriger `{e}}` → `{e}`.
- En repair, il arrive qu’il enchaîne **read_file** puis d’autres outils avant **write_file**, ce qui retarde la vraie correction.

**Piste** : dans le feedback de repair (REPAIR_REQUIRED), renforcer la consigne du type : « Corriger l’erreur avec **write_file** sur le fichier cible ; n’appeler **run_shell** qu’après avoir corrigé. Éviter les appels inutiles (read_file en boucle, autres outils). » et éventuellement ajouter une aide ciblée pour les SyntaxError (f-string, parenthèses).

---

## 4. Correctifs prévus côté backend

1. **Bilan utilisateur** : dans `_summarize_failure_for_user`, quand `turn` est connu et `repair_attempts >= 1`, ajouter une phrase du type : « Tentatives de correction : N. » et garder « Détail technique » en dessous.
2. **Feedback repair** : dans le message REPAIR_REQUIRED, ajouter une phrase du type : « Prochaine action : un **write_file** avec le code corrigé (corriger l’erreur ci-dessus), puis relancer la validation. Éviter read_file en boucle et les run_shell avant d’avoir corrigé. »
3. **Aide SyntaxError** : dans `_repair_guidance_for_failure`, si l’observation contient "syntaxerror" / "f-string" / "unmatched ')'", ajouter une courte indication (ex. « En f-string utiliser `{e}` et non `{e}}`. Vérifier que chaque `(` a bien une `)` correspondante. »).

---

## 5. Verdicts rapides ce run

| Scénario | Verdict |
|----------|---------|
| smalltalk, list dirs, read loop.py | OK |
| Lis / Affiche / cat README | KO (fichier vide dans ce transcript) |
| Crée ping.py + run | OK après repair (syntaxe corrigée par hasard) |
| Réécris ping.py + run | KO (f-string `{e}}` non corrigée, pas de write_file après l’erreur) |
| cube simple | OK (write + pas de py_compile/python3 dans le tour — inchangé) |
| cube 3D | KO (syntaxe : parenthèses / typo ; repair corrige une erreur, en laisse une autre) |

---

## 6. Résumé

- **Erreurs** : surtout **syntaxe** (accolade/parenthèse en trop, typo `/` au lieu de `(`). Le modèle en corrige une partie en repair mais en laisse ou en introduit d’autres.
- **Bilan utilisateur** : absent ; à ajouter dans le message final quand il y a eu plusieurs tentatives ou blocage.
- **Outils** : le modèle relance **run_shell** sans corriger le fichier avant, ou multiplie **read_file** au lieu d’aller à **write_file**. À décourager dans le feedback repair.

Les correctifs 1–3 ci‑dessus sont implémentables dans le backend (loop.py) pour améliorer le message à l’utilisateur et le comportement en repair.

---

## 7. Détection succès / échec run_shell

Quand un script exit 0 mais imprime une erreur (ex. `[Errno -5] No address associated with hostname`), on ne renvoyait pas `[exit_code=]` donc on marquait succès à tort. **Correctif** : `is_failed_result` traite comme échec les sorties contenant `[errno `, `no address associated`, `name or service not known`, `traceback`, `syntaxerror`, etc. Hint repair : ne pas copier le message d’erreur littéralement dans le code.

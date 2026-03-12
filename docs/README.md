# ShellGeist — Documentation

| Fichier | Rôle |
|---------|------|
| **AUDIT.md** | Audit technique unifié : bugs de flux, parsing outils, anti-slop, transcript, dernier run. Référence principale pour les correctifs et le statut. |
| **results.md** | Transcript du dernier run (sidebar / export) + section **Audit / Verdicts** en bas (tableau verdicts, points positifs, problèmes). |
| **audit-results-deep.md** | Analyse détaillée d’anciens runs : erreurs de syntaxe récurrentes, bilan utilisateur, enchaînements d’outils, détection succès/échec run_shell. Complète AUDIT.md. |
| **specification.txt** | Spécification technique : architecture, fichiers backend, rôles et variables. |

**Pour auditer un nouveau run** : coller le transcript dans `results.md`, ajouter ou mettre à jour la section « Audit / Verdicts » en bas, puis mettre à jour la Partie 5 de `AUDIT.md` si besoin.

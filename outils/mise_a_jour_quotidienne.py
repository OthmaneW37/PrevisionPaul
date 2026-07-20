# -*- coding: utf-8 -*-
"""
Mise à jour quotidienne automatique — enchaîne, dans l'ordre :
  1. export des ventes depuis la base SQL d'Elyx (outils/exporter_ventes_sql.py),
  2. prévisions journalières  (python -m paul_forecast.forecast_journalier),
  3. pipeline mensuel complet (python main.py : prévisions, MRP, plan sécurisé).

C'est l'équivalent du bouton « Relancer le calcul » du dashboard, précédé de
la récupération des ventes fraîches. Conçu pour le Planificateur de tâches
Windows (tâche « PrevisionPaul - MAJ quotidienne », chaque nuit) : tout est
journalisé dans logs/auto_AAAAMMJJ.log et le code retour est non nul dès
qu'une étape échoue (visible dans l'historique du Planificateur).

Lancement manuel : python outils/mise_a_jour_quotidienne.py
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------

import os
import subprocess
import sys
from datetime import datetime

RACINE = _RACINE
LOGS = os.path.join(RACINE, "logs")

ETAPES = [
    ("Export ventes SQL (Elyx)",
     [sys.executable, os.path.join(RACINE, "outils", "exporter_ventes_sql.py")]),
    ("Prévisions journalières",
     [sys.executable, "-m", "paul_forecast.forecast_journalier"]),
    ("Pipeline mensuel (MRP + plan sécurisé)",
     [sys.executable, os.path.join(RACINE, "main.py")]),
]


def executer():
    os.makedirs(LOGS, exist_ok=True)
    chemin_log = os.path.join(LOGS, f"auto_{datetime.now():%Y%m%d}.log")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    with open(chemin_log, "a", encoding="utf-8") as log:
        def ecrire(txt):
            ligne = f"{datetime.now():%H:%M:%S} | {txt}"
            print(ligne)
            log.write(ligne + "\n")
            log.flush()

        ecrire("=" * 60)
        ecrire("MISE À JOUR QUOTIDIENNE — démarrage")
        for nom, commande in ETAPES:
            ecrire(f"[Étape] {nom} …")
            res = subprocess.run(commande, cwd=RACINE, env=env,
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 timeout=3600)
            for flux in (res.stdout, res.stderr):
                for l in (flux or "").splitlines():
                    if l.strip():
                        log.write("    " + l + "\n")
            log.flush()
            if res.returncode != 0:
                ecrire(f"[ÉCHEC] {nom} (code {res.returncode}) — arrêt. "
                       f"Détails dans {chemin_log}")
                return res.returncode
            ecrire(f"[OK] {nom}")
        ecrire("MISE À JOUR QUOTIDIENNE — terminée avec succès")
    return 0


if __name__ == "__main__":
    sys.exit(executer())

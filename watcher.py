"""
watcher.py — Relance automatique des calculs quand les ventes changent.

Surveille le dossier donnees_ventes/ (exports .txt, CSV journalier, xlsx
mensuels). Dès qu'un fichier y est ajouté ou modifié — et une fois la copie
terminée (taille stable) — relance dans l'ordre :
  1. python -m paul_forecast.forecast_journalier   (prévisions du jour)
  2. python main.py                                (pipeline mensuel + MRP)
soit exactement ce que fait le bouton « Relancer le calcul » du dashboard.

Usage : python watcher.py   (ou double-clic sur lancer.bat)
"""

import subprocess
import sys
import time
import os
from datetime import datetime

# Consoles Windows cp850/cp1252 : évite UnicodeEncodeError sur ✓ ✗ ═ ║ …
for _flux in (sys.stdout, sys.stderr):
    try:
        if _flux is not None and hasattr(_flux, "reconfigure"):
            _flux.reconfigure(errors="replace")
    except Exception:
        pass

RACINE       = os.path.dirname(os.path.abspath(__file__))
DOSSIER_SUIVI = os.path.join(RACINE, "donnees_ventes")
EXTENSIONS   = (".txt", ".csv", ".xlsx")
POLL_DELAY   = 2.0   # secondes entre deux vérifications
DELAI_STABLE = 5.0   # secondes sans changement avant de lancer (copie terminée)


def etat_dossier():
    """Empreinte {chemin: (mtime, taille)} des fichiers de ventes suivis."""
    etat = {}
    for dossier, _, fichiers in os.walk(DOSSIER_SUIVI):
        for f in fichiers:
            if f.startswith("~$") or not f.lower().endswith(EXTENSIONS):
                continue
            p = os.path.join(dossier, f)
            try:
                st = os.stat(p)
                etat[p] = (st.st_mtime, st.st_size)
            except OSError:
                continue
    return etat


def horodatage():
    return datetime.now().strftime("%H:%M:%S")


def lancer(convertir=False):
    debut = time.time()
    etapes = [
        ("Prévisions journalières", [sys.executable, "-u", "-m", "paul_forecast.forecast_journalier"]),
        ("Pipeline mensuel (MRP)",  [sys.executable, "-u", os.path.join(RACINE, "main.py")]),
    ]
    if convertir:
        # Un export brut ProduitParJour*.txt a changé : reconstruire d'abord le
        # CSV journalier consolidé, que les deux pipelines relisent ensuite.
        etapes.insert(0, ("Conversion des exports bruts (txt → csv)",
                          [sys.executable, "-u",
                           os.path.join(RACINE, "outils", "convertir_ventes_journalieres.py")]))
    ok = True
    for nom, cmd in etapes:
        print(f"\n{'═'*60}\n  [{horodatage()}] {nom}\n{'═'*60}\n")
        result = subprocess.run(cmd, cwd=RACINE)
        if result.returncode != 0:
            print(f"\n[{horodatage()}] ✗ {nom} : erreur (code {result.returncode})")
            ok = False
            break
    duree = time.time() - debut
    statut = "✓ Calculs terminés" if ok else "✗ Erreur — voir ci-dessus"
    print(f"\n[{horodatage()}] {statut} en {duree:.0f}s — en attente de nouveaux fichiers…\n")


# ── Démarrage ────────────────────────────────────────────────────────────────
print(f"""
╔══════════════════════════════════════════════════════════╗
║   WATCHER — PAUL Prévisions                              ║
║   Surveille : donnees_ventes\\ (txt, csv, xlsx)           ║
║   Dépose un nouveau fichier de ventes pour relancer      ║
║   automatiquement les calculs. Ctrl+C pour arrêter.      ║
╚══════════════════════════════════════════════════════════╝
""")

if not os.path.isdir(DOSSIER_SUIVI):
    print(f"[Watcher] Dossier introuvable : {DOSSIER_SUIVI}")
    sys.exit(1)

dernier_etat = etat_dossier()

# Première exécution immédiate (met tout à jour au démarrage)
lancer()
dernier_etat = etat_dossier()

# Boucle de surveillance (avec délai de stabilité : copie de gros fichiers)
try:
    while True:
        time.sleep(POLL_DELAY)
        etat = etat_dossier()
        if etat == dernier_etat:
            continue
        print(f"[{horodatage()}] Changement détecté dans donnees_ventes\\ — "
              f"attente de la fin de copie…")
        # attendre que l'état soit stable DELAI_STABLE secondes
        avant = dernier_etat
        stable_depuis = time.time()
        dernier_etat = etat
        while time.time() - stable_depuis < DELAI_STABLE:
            time.sleep(POLL_DELAY)
            etat = etat_dossier()
            if etat != dernier_etat:
                dernier_etat = etat
                stable_depuis = time.time()
        modifies = [p for p, v in dernier_etat.items() if avant.get(p) != v]
        txt_change = any(p.lower().endswith(".txt") for p in modifies)
        lancer(convertir=txt_change)
        dernier_etat = etat_dossier()
except KeyboardInterrupt:
    print("\n[Watcher] Arrêté.")

# -*- coding: utf-8 -*-
"""
Configuration du logging du projet.

Remplace les `print()` dispersés par un logger unique qui écrit simultanément :
  - dans la console (niveau INFO),
  - dans un fichier horodaté `logs/run_YYYYMMDD_HHMMSS.log` (niveau DEBUG),
afin de garder une trace de chaque exécution (utile en production / la nuit).
"""

import os
import sys
import logging
from datetime import datetime

from . import config

_LOGGER_NOM = "paul"
_DEJA_CONFIGURE = False


def _securiser_console():
    """Rend stdout/stderr tolérants aux caractères non encodables.

    Les consoles Windows en cp850/cp1252 ne connaissent pas certains caractères
    (✓, →, ═…) : sans cela, un simple print() fait planter le programme
    (UnicodeEncodeError) au lancement — vu avec dashboard.py et watcher.py.
    """
    for flux in (sys.stdout, sys.stderr):
        try:
            if flux is not None and hasattr(flux, "reconfigure"):
                flux.reconfigure(errors="replace")
        except Exception:
            pass


def configurer_logging(niveau_console=logging.INFO, niveau_fichier=logging.DEBUG):
    """Configure et retourne le logger racine du projet (idempotent)."""
    global _DEJA_CONFIGURE
    logger = logging.getLogger(_LOGGER_NOM)

    if _DEJA_CONFIGURE:
        return logger

    _securiser_console()

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                            datefmt="%H:%M:%S")

    # --- Console ---
    console = logging.StreamHandler()
    console.setLevel(niveau_console)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # --- Fichier horodaté ---
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        chemin_log = os.path.join(config.LOG_DIR, f"run_{horodatage}.log")
        fichier = logging.FileHandler(chemin_log, encoding="utf-8")
        fichier.setLevel(niveau_fichier)
        fichier.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        logger.addHandler(fichier)
        logger.info("Journal d'exécution : %s", chemin_log)
    except OSError as e:
        logger.warning("Impossible de créer le fichier de log : %s", e)

    _DEJA_CONFIGURE = True
    return logger


def get_logger():
    """Retourne le logger du projet (le configure au premier appel)."""
    if not _DEJA_CONFIGURE:
        return configurer_logging()
    return logging.getLogger(_LOGGER_NOM)

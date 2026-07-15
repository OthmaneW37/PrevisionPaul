# -*- coding: utf-8 -*-
"""
Prédiction AUTOMATIQUE de l'effet des matchs de l'équipe nationale.

Plutôt que de saisir un boost manuel pour chaque match, on tient un simple
CALENDRIER de dates de matchs (data/matchs.json). Le système :
  - MESURE l'uplift réel par catégorie sur les jours de match PASSÉS présents dans
    l'historique (via calibration_fetes.mesurer_uplift_jours),
  - APPLIQUE automatiquement ce profil aux matchs À VENIR (dans l'horizon), modulé
    par un coefficient d'importance (poule < élimination directe < finale).

L'utilisateur n'a donc qu'à donner la DATE d'un match : l'impact est appris des
données. La saisie d'événements manuels (evenements.json) reste disponible en plus.
"""

import os
import json

import pandas as pd

from . import config
from . import calibration_fetes
from .logging_setup import get_logger

logger = get_logger()

# Les matchs sont stockés comme des événements de type "match" dans evenements.json
# (source unique : un seul endroit pour ajouter/supprimer, cf onglet « Événements »).
EVT_PATH = os.path.join(config.DATA_DIR, "evenements.json")


def charger_matchs():
    """Matchs = événements de type 'match' [{date, adversaire, importance, …}]."""
    try:
        with open(EVT_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    evs = doc.get("evenements", []) if isinstance(doc, dict) else (doc or [])
    return [e for e in evs if e.get("type") == "match"]


def _dates(matchs=None):
    matchs = matchs if matchs is not None else charger_matchs()
    out = []
    for m in matchs:
        try:
            out.append(pd.Timestamp(m["date"]))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def profil_match():
    """Uplift moyen par catégorie {famille: ratio} mesuré sur les jours de match PASSÉS.

    « Passés » = jours de match couverts par l'historique de ventes journalières.
    Bornes 0.5–2.5. {} si aucun match mesurable.
    """
    dates = _dates()
    if not dates:
        return {}
    profil = calibration_fetes.mesurer_uplift_jours(dates)
    return {fam: max(0.5, min(2.5, r)) for fam, r in profil.items()}


def table_boost(dates_horizon, familles):
    """Multiplicateur boost[(date, famille)] pour les jours de match dans l'horizon.

    ratio_appliqué = 1 + (uplift_mesuré[famille] - 1) × importance.
    """
    profil = profil_match()
    if not profil:
        return {}
    cal = {pd.Timestamp(m["date"]): float(m.get("importance", 1.0) or 1.0)
           for m in charger_matchs() if m.get("date")}
    glob = sum(profil.values()) / len(profil)
    boost = {}
    for d in dates_horizon:
        imp = cal.get(pd.Timestamp(d))
        if imp is None:
            continue
        for fam in familles:
            r = profil.get(str(fam), glob)
            mult = 1.0 + (r - 1.0) * imp
            if mult != 1.0:
                boost[(d, str(fam))] = mult
    return boost

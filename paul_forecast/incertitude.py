# -*- coding: utf-8 -*-
"""
Fourchette de prévision et stock de sécurité.

Une prévision ponctuelle ne suffit pas pour commander : il faut une fourchette
et une quantité qui évite la rupture. Pour chaque produit on estime l'écart-type
de l'erreur de prévision à 1 mois (à partir des MAE mesurées au banc d'essai —
benchmark_modeles.csv — sinon une marge relative par défaut), puis :

    Quantité recommandée (h)  = prévision(h) + z(service) × σ(h)
    Fourchette                = prévision(h) ± z(service) × σ(h)   (bornée ≥ 0)

où σ(h) = σ(1 mois) × √h (l'incertitude croît avec l'horizon).
Pour une erreur ~normale : σ ≈ MAE / 0.8 ≈ 1.25 × MAE.
"""
import os
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import config
from .logging_setup import get_logger

logger = get_logger()

_MAE_VERS_SIGMA = 1.2533   # σ ≈ MAE / E|N(0,1)|


def z_service(niveau=None):
    """Quantile normal unilatéral pour un niveau de service (0<niveau<1)."""
    niveau = niveau if niveau is not None else config.NIVEAU_SERVICE
    niveau = min(max(float(niveau), 0.5), 0.999)
    return NormalDist().inv_cdf(niveau)


def sigma_par_produit(chemin="benchmark_modeles.csv"):
    """σ(1 mois) par produit, dérivé des MAE du banc d'essai. {} si absent."""
    p = os.path.join(config.RACINE_PROJET, chemin)
    if not os.path.exists(p):
        logger.info("[Incertitude] %s absent — marge relative par défaut utilisée.", chemin)
        return {}
    try:
        df = pd.read_csv(p, sep=";").set_index("produit")
        col = "mae_best" if "mae_best" in df.columns else None
        if col is None:
            return {}
        return {str(k): float(v) * _MAE_VERS_SIGMA
                for k, v in df[col].items() if np.isfinite(v)}
    except Exception as e:
        logger.warning("[Incertitude] lecture %s impossible : %s", chemin, e)
        return {}


def fiabilite_par_produit(chemin="benchmark_modeles.csv"):
    """
    Indice de confiance par produit : erreur relative = MAE / volume moyen.
    Retourne {produit: {"err_rel": x, "niveau": "Fiable|Moyen|Incertain"}}.
    {} si le banc d'essai est absent.

    Les produits ABSENTS du banc (< 30 mois d'historique mensuel : références
    récentes comme JUS ORANGE 16 CL) sont complétés par la fiabilité JOURNALIÈRE
    (backtest glissant 28 j d'exports/previsions_journalieres.csv, qui n'exige que
    quelques semaines de données) — mieux qu'un « Hist. court » systématique alors
    que le produit se vend depuis un an.
    """
    p = os.path.join(config.RACINE_PROJET, chemin)
    if not os.path.exists(p):
        return _fiabilite_journaliere()
    try:
        df = pd.read_csv(p, sep=";").set_index("produit")
    except Exception:
        return _fiabilite_journaliere()
    if "mae_best" not in df.columns or "volume" not in df.columns:
        return _fiabilite_journaliere()
    out = {}
    for prod, row in df.iterrows():
        vol, mae = row["volume"], row["mae_best"]
        if not np.isfinite(vol) or vol <= 0 or not np.isfinite(mae):
            continue
        er = mae / vol
        niveau = ("Fiable" if er <= config.FIABILITE_SEUIL_BON else
                  "Moyen" if er <= config.FIABILITE_SEUIL_MOYEN else "Incertain")
        out[str(prod)] = {"err_rel": round(float(er), 3), "niveau": niveau}
    for prod, info in _fiabilite_journaliere().items():
        out.setdefault(prod, info)
    return out


def _fiabilite_journaliere(chemin=os.path.join("exports", "previsions_journalieres.csv")):
    """Fiabilité par produit lue dans l'export JOURNALIER (repli pour les produits
    trop récents pour le banc mensuel). {} si l'export n'existe pas encore.

    Seuls les niveaux mesurés (Fiable/Moyen/Incertain) sont repris : « Peu vendu »
    et « Hist. court » journaliers n'apportent rien de plus au plan mensuel.
    err_rel est None (l'erreur hebdo n'est pas homogène au MAE/volume mensuel).
    """
    p = os.path.join(config.RACINE_PROJET, chemin)
    if not os.path.exists(p):
        return {}
    try:
        df = pd.read_csv(p, sep=";", usecols=["Produit", "Fiabilite"])
    except Exception:
        return {}
    niveaux = df.dropna().groupby("Produit")["Fiabilite"].last()
    return {str(prod): {"err_rel": None, "niveau": str(niv)}
            for prod, niv in niveaux.items()
            if str(niv) in ("Fiable", "Moyen", "Incertain")}


def ajouter_intervalles(dict_prevision_prod, sigma_map=None, niveau=None,
                        col="Qty_Prev_Selection", prefixe="Qty_Selection"):
    """
    Ajoute à chaque DataFrame produit les colonnes :
      <prefixe>_Bas, <prefixe>_Haut (= quantité recommandée au niveau de service).
    σ par produit : MAE du banc d'essai si dispo, sinon marge relative par défaut.
    """
    sigma_map = sigma_map if sigma_map is not None else sigma_par_produit()
    z = z_service(niveau)
    marge = config.MARGE_INCERTITUDE_DEFAUT
    for prod, df in dict_prevision_prod.items():
        if col not in df.columns:
            continue
        f = df[col].to_numpy(dtype=float)
        s1 = sigma_map.get(str(prod))
        if s1 is None or not np.isfinite(s1) or s1 <= 0:
            s1 = marge * (f[0] if len(f) else 0.0)   # repli : % de la prévision
        sig = s1 * np.sqrt(np.arange(1, len(f) + 1))
        df[f"{prefixe}_Bas"] = np.clip(f - z * sig, 0, None)
        df[f"{prefixe}_Haut"] = f + z * sig
    return dict_prevision_prod

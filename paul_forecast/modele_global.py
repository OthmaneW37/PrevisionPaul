# -*- coding: utf-8 -*-
"""
Modèle GLOBAL de prévision (gradient boosting entraîné sur TOUS les produits).

Contrairement aux modèles par série (ETS, Theta…), le GBM apprend les motifs
PARTAGÉS entre produits : effet du mois, des fêtes (fractions Ramadan/Aïd du
mois), de la famille, de la dynamique récente (lags normalisés par le niveau).
C'est l'approche standard du retail moderne : elle aide surtout les produits à
historique court/bruité, qui « empruntent » l'information des autres.

Usage pipeline : `previsions_globales(series, familles, dates_futures)` retourne
{produit: array(n_mois)} par prédiction récursive (h=1 réinjecté dans les lags).
"""
import os

import numpy as np
import pandas as pd

# joblib/loky (utilisé par scikit-learn) ne sait pas compter les cœurs physiques
# sur certains Windows et émet un gros UserWarning — on fixe la valeur nous-mêmes.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))

from . import config
from . import saisonnalite_fetes as sf
from .logging_setup import get_logger

logger = get_logger()

MIN_HIST = 14          # mois d'historique requis pour une ligne d'entraînement
CLIP_RATIO = 6.0       # borne du ratio cible (anti-valeurs aberrantes)


def _fractions_fetes(dates):
    fen = sf._fenetres_par_type()
    return {d: tuple(sf._fraction_du_mois(d, fen.get(t, []))
                     for t in ("ramadan", "aid_fitr", "aid_adha")) for d in dates}


def _features(y, t, date, fr, fam_code):
    """Vecteur de features pour prédire l'indice t (base = niveau 12 mois)."""
    base = float(np.mean(y[max(0, t - 12):t]))
    if base <= 0:
        return None, None
    lag = lambda k: (y[t - k] / base) if t - k >= 0 else 1.0
    rec3 = y[max(0, t - 3):t]
    f_ram, f_fitr, f_adha = fr[date]
    feats = [lag(1), lag(2), lag(3), lag(6), lag(12),
             float(np.mean(rec3)) / base, float(np.std(rec3)) / base,
             float(date.month), f_ram, f_fitr, f_adha,
             float(fam_code), float(np.log1p(base))]
    return feats, base


def entrainer(series, dates_par_prod, fam_codes, fr):
    """Entraîne le GBM sur tout l'historique. Retourne le modèle (ou None)."""
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError:
        logger.warning("[GBM] scikit-learn absent — modèle global indisponible.")
        return None
    X, Y = [], []
    for p, y in series.items():
        dts, fc = dates_par_prod[p], fam_codes.get(p, -1)
        for t in range(MIN_HIST, len(y)):
            feats, base = _features(y, t, dts[t], fr, fc)
            if feats is None:
                continue
            X.append(feats)
            Y.append(float(np.clip(y[t] / base, 0.0, CLIP_RATIO)))
    if len(X) < 500:
        logger.warning("[GBM] Trop peu de données (%d lignes) — modèle global ignoré.", len(X))
        return None
    gbm = HistGradientBoostingRegressor(
        loss="absolute_error", max_iter=350, learning_rate=0.06,
        l2_regularization=1.0, categorical_features=[7, 11], random_state=42)
    gbm.fit(np.array(X), np.array(Y))
    logger.info("[GBM] Modèle global entraîné sur %d observations (%d produits).",
                len(X), len(series))
    return gbm


def previsions_globales(series, familles, dates_futures):
    """
    Prévisions récursives {produit: np.array(len(dates_futures))} pour tous les
    produits ayant assez d'historique. {} si le modèle n'a pas pu être entraîné.
    """
    dates_futures = [pd.Timestamp(d) for d in dates_futures]
    fam_liste = sorted(set(str(f) for f in familles.values()))
    fam_codes_map = {f: i for i, f in enumerate(fam_liste)}
    fam_codes = {p: fam_codes_map.get(str(familles.get(p, "")), -1) for p in series}

    # dates par produit : séries alignées sur la même fin (dernier mois du panel)
    fin = dates_futures[0] - pd.offsets.MonthEnd(1)
    dates_par_prod = {p: pd.date_range(end=fin, periods=len(y), freq="ME")
                      for p, y in series.items()}
    toutes = pd.date_range(end=fin, periods=max(len(y) for y in series.values()), freq="ME")
    fr = _fractions_fetes(list(toutes) + dates_futures)

    gbm = entrainer(series, dates_par_prod, fam_codes, fr)
    if gbm is None:
        return {}

    out = {}
    for p, y in series.items():
        if len(y) < MIN_HIST:
            continue
        fc = fam_codes.get(p, -1)
        y_ext = list(map(float, y))
        preds = []
        for h, d in enumerate(dates_futures):
            t = len(y_ext)
            feats, base = _features(np.asarray(y_ext), t, d, fr, fc)
            if feats is None:
                preds.append(0.0); y_ext.append(0.0); continue
            ratio = float(gbm.predict(np.array([feats]))[0])
            v = max(0.0, ratio * base)
            preds.append(v)
            y_ext.append(v)     # récursif : la prédiction nourrit les lags suivants
        out[p] = np.array(preds)
    return out

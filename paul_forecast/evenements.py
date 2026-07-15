# -*- coding: utf-8 -*-
"""
Couche d'ajustement « événements » des prévisions.

Au-delà des fêtes religieuses récurrentes (voir saisonnalite_fetes.py), certains
ÉVÉNEMENTS PONCTUELS dopent la fréquentation un jour précis : match de l'équipe
nationale, jour férié, concert, festival, épisode météo, promotion… Ils ne sont
pas dans l'historique (ou noyés dans des totaux mensuels), donc on les anticipe
par un BOOST attendu, saisi par l'utilisateur dans le dashboard.

Modèle (identique aux fêtes, au prorata des jours) :
    mult_famille(F, mois M) = 1 + Σ_event (ratio_event[F] - 1) * (jours_event_dans_M / jours_M)
où ratio_event[F] = override de la famille F si défini, sinon le boost global.

En granularité MENSUELLE, l'effet d'un événement d'un jour est dilué (1 jour / ~30).
La structure est conçue pour rester valable en JOURNALIER : le facteur de prorata
devient simplement 1/1 le jour J. Données : data/evenements.json (config.EVENEMENTS).
"""

import pandas as pd

from . import config
from .logging_setup import get_logger

logger = get_logger()


def evenements_normalises():
    """Liste d'événements exploitables : dates Timestamp + multiplicateurs (global + par famille)."""
    data = config.EVENEMENTS
    items = data.get("evenements", []) if isinstance(data, dict) else (data or [])
    out = []
    for e in items:
        if e.get("type") == "match":
            continue  # les matchs sont gérés (impact appris) par le module matchs
        try:
            debut = pd.Timestamp(e["date"])
        except (KeyError, ValueError, TypeError):
            continue
        try:
            fin = pd.Timestamp(e.get("date_fin") or e["date"])
        except (ValueError, TypeError):
            fin = debut
        if fin < debut:
            debut, fin = fin, debut
        try:
            glob = 1.0 + float(e.get("impact_pct", 0) or 0) / 100.0
        except (TypeError, ValueError):
            glob = 1.0
        fam = {}
        for k, v in (e.get("familles_pct") or {}).items():
            try:
                fam[str(k)] = 1.0 + float(v) / 100.0
            except (TypeError, ValueError):
                continue
        out.append({"nom": e.get("nom", ""), "type": e.get("type", ""),
                    "debut": debut, "fin": fin, "global": glob, "familles": fam})
    return out


def _jours_dans_mois(date_fin_mois, debut, fin):
    """(jours de l'événement tombant dans le mois, nombre de jours du mois)."""
    fin_m = pd.Timestamp(date_fin_mois)
    jours = pd.date_range(fin_m.replace(day=1),
                          fin_m.replace(day=1) + pd.offsets.MonthEnd(0), freq="D")
    if len(jours) == 0:
        return 0, 0
    couverts = ((jours >= debut) & (jours <= fin)).sum()
    return int(couverts), len(jours)


def fenetres_periode(date_min, date_max):
    """Événements chevauchant l'intervalle (pour annotation du dashboard)."""
    dmin, dmax = pd.Timestamp(date_min), pd.Timestamp(date_max)
    return [e for e in evenements_normalises() if e["fin"] >= dmin and e["debut"] <= dmax]


def ajuster_previsions_evenements(dict_prevision_prod, produit_famille):
    """
    Applique le boost de chaque événement aux prévisions par produit, au prorata
    des jours concernés dans le mois. Multiplie les colonnes Qty_Prev_* / Rev_Prev_*.
    Retourne la liste des mois (Timestamp) ajustés.
    """
    evs = evenements_normalises()
    if not evs or not dict_prevision_prod:
        return []

    exemple = next(iter(dict_prevision_prod.values()))
    dates = [pd.Timestamp(d) for d in exemple["Date"]]

    # Pour chaque mois : liste (event, fraction_jours) des événements présents.
    actifs = {}
    for d in dates:
        for e in evs:
            jours, total = _jours_dans_mois(d, e["debut"], e["fin"])
            if jours > 0 and total > 0:
                actifs.setdefault(d, []).append((e, jours / total))
    if not actifs:
        return []

    def mult_famille(famille, date):
        m = 1.0
        for e, frac in actifs.get(date, []):
            r = e["familles"].get(famille, e["global"])
            m += (r - 1.0) * frac
        return m

    for prod, df_fc in dict_prevision_prod.items():
        famille = str(produit_famille.get(prod, "")).strip()
        cols = [c for c in df_fc.columns if c.startswith(("Qty_Prev", "Rev_Prev"))]
        for i, d in enumerate(df_fc["Date"]):
            d = pd.Timestamp(d)
            if d not in actifs:
                continue
            m = mult_famille(famille, d)
            if m == 1.0:
                continue
            for c in cols:
                df_fc.iloc[i, df_fc.columns.get_loc(c)] *= m

    for d in sorted(actifs):
        noms = ", ".join(f"{e['nom']} ({frac*100:.0f}% du mois)" for e, frac in actifs[d])
        logger.info("[Événements] %s : %s → prévisions ajustées.", d.strftime("%Y-%m"), noms)
    return sorted(actifs)

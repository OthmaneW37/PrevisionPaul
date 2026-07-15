# -*- coding: utf-8 -*-
"""
Couche d'ajustement « fêtes » des prévisions.

La saisonnalité par mois civil (Holt-Winters) ne capte pas les fêtes lunaires
qui se décalent chaque année (Ramadan, Aïd el-Fitr/Adha, Achoura, Mawlid). Or
ces périodes déforment fortement le MIX produits (ex. Ramadan : menus de jour en
chute, boulangerie/cuisine/pâtisserie du ftour en hausse).

Pour chaque mois de prévision, ce module applique — au prorata du nombre de jours
de chaque fête dans le mois — les ratios par famille de data/profils_fetes.json.
Les besoins matières premières (MRP) en héritent automatiquement : on est ainsi
« prêt le jour J » avec les bonnes quantités.

Dates : fournies par fetes_maroc.json (rafraîchissable via fetes_api / Aladhan).
Ratios : profils MESURÉS sur les ventes journalières réelles (profils_fetes_mesures.json,
via calibration_fetes) en priorité, avec repli sur les hypothèses de profils_fetes.json.
"""

import pandas as pd

from . import config
from . import calibration_fetes
from .logging_setup import get_logger

logger = get_logger()


def _profils_effectifs():
    """Profils par type/famille : profils MESURÉS (calibration_fetes, prioritaires)
    fusionnés sur les hypothèses de config.PROFILS_FETES. Harmonise le pipeline
    mensuel avec le forecasting journalier (qui utilise déjà les mesurés)."""
    hyp = config.PROFILS_FETES or {}
    try:
        mes = calibration_fetes.profils_mesures() or {}
    except Exception:
        mes = {}
    types = {t for t in (set(hyp) | set(mes)) if not str(t).startswith("_")}
    out = {}
    for t in types:
        ratios = dict(hyp.get(t, {}).get("ratios", {}))
        ratios.update(mes.get(t, {}).get("ratios", {}))   # mesuré écrase l'hypothèse
        if ratios:
            out[t] = {"ratios": ratios}
    return out


def _type_fete(fete):
    """Type de la fête : champ 'type' explicite, sinon déduit du nom (rétrocompat)."""
    if fete.get("type"):
        return fete["type"]
    nom = str(fete.get("nom", "")).lower()
    if "ramadan" in nom:        return "ramadan"
    if "fitr" in nom:           return "aid_fitr"
    if "adha" in nom:           return "aid_adha"
    if "achoura" in nom or "ashura" in nom: return "achoura"
    if "mawlid" in nom or "mouloud" in nom: return "mawlid"
    return None


def _fenetres_par_type():
    """{type_fete: [(debut, fin), ...]} pour les types ayant un profil défini."""
    profils = _profils_effectifs()
    fenetres = {}
    for fete in config.FETES_MAROCAINES:
        typ = _type_fete(fete)
        if typ is None or typ not in profils:
            continue
        try:
            fenetres.setdefault(typ, []).append(
                (pd.Timestamp(fete["debut"]), pd.Timestamp(fete["fin"])))
        except (KeyError, ValueError):
            continue
    return fenetres


def _fraction_du_mois(date_fin_mois, fenetres):
    """Fraction des jours du mois couverts par une liste de fenêtres (0..1)."""
    fin = pd.Timestamp(date_fin_mois)
    jours = pd.date_range(fin.replace(day=1), fin.replace(day=1) + pd.offsets.MonthEnd(0), freq="D")
    if len(jours) == 0:
        return 0.0
    couverts = pd.Series(False, index=jours)
    for deb, fn in fenetres:
        couverts |= (jours >= deb) & (jours <= fn)
    return float(couverts.sum()) / float(len(jours))


def ajuster_previsions_fetes(dict_prevision_prod, produit_famille):
    """
    Ajuste les prévisions par produit pour les mois de fête.

    Pour un produit de famille F et un mois M, le multiplicateur est :
        mult = 1 + Σ_type (ratio_type[F] - 1) * fraction_type(M)
    (somme sur les types de fête, dont les jours ne se chevauchent pas).
    Multiplie toutes les colonnes Qty_Prev_* et Rev_Prev_*. Retourne un journal.
    """
    profils = _profils_effectifs()
    fenetres = _fenetres_par_type()
    if not profils or not fenetres:
        logger.info("[Fêtes] Aucun profil/date de fête — pas d'ajustement.")
        return []

    exemple = next(iter(dict_prevision_prod.values()))
    dates = [pd.Timestamp(d) for d in exemple["Date"]]
    # fraction par (type, date)
    frac = {typ: {d: _fraction_du_mois(d, fen) for d in dates}
            for typ, fen in fenetres.items()}
    mois_actifs = {d: {typ: frac[typ][d] for typ in fenetres if frac[typ][d] > 0}
                   for d in dates}
    mois_actifs = {d: t for d, t in mois_actifs.items() if t}
    if not mois_actifs:
        logger.info("[Fêtes] Aucun mois de prévision ne chevauche une fête.")
        return []

    ratios = {typ: profils[typ].get("ratios", {}) for typ in fenetres}

    def mult_famille(famille, date):
        m = 1.0
        for typ, f in mois_actifs.get(date, {}).items():
            r = ratios[typ].get(famille)
            if r is not None:
                m += (r - 1.0) * f
        return m

    for prod, df_fc in dict_prevision_prod.items():
        famille = str(produit_famille.get(prod, "")).strip()
        if not famille:
            continue
        cols = [c for c in df_fc.columns if c.startswith(("Qty_Prev", "Rev_Prev"))]
        for i, d in enumerate(df_fc["Date"]):
            d = pd.Timestamp(d)
            if d not in mois_actifs:
                continue
            m = mult_famille(famille, d)
            if m == 1.0:
                continue
            for c in cols:
                df_fc.iloc[i, df_fc.columns.get_loc(c)] *= m

    for d in sorted(mois_actifs):
        details = ", ".join(f"{typ} {f*100:.0f}%" for typ, f in mois_actifs[d].items())
        logger.info("[Fêtes] %s : %s → mix ajusté par famille.",
                    d.strftime("%Y-%m"), details)
    return sorted(mois_actifs)

# -*- coding: utf-8 -*-
"""
Explication des pics et creux des séries (CA, quantité, par famille…).

Pour chaque variation marquée, attribue une cause lisible :
  - une FÊTE (Ramadan, Aïd…) si le mois la chevauche,
  - la SAISON (haute/basse) d'après le profil saisonnier mensuel,
  - sinon « tendance / autre ».

Utilisé par le dashboard pour ombrer les périodes de fête et annoter les points
remarquables des graphiques.
"""

import pandas as pd

from . import config

# Libellés lisibles + couleur d'ombrage par type de fête (palette PAUL).
_FETES_AFFICHAGE = {
    "ramadan":  ("Ramadan",      "rgba(111,78,55,0.10)"),
    "aid_fitr": ("Aïd el-Fitr",  "rgba(74,123,86,0.13)"),
    "aid_adha": ("Aïd el-Adha",  "rgba(168,67,47,0.12)"),
    "achoura":  ("Achoura",      "rgba(184,144,74,0.10)"),
    "mawlid":   ("Mawlid",       "rgba(184,144,74,0.10)"),
}

# Mois à ombrer en priorité (les autres restent disponibles via annotations).
_FETES_OMBREES = {"ramadan", "aid_fitr", "aid_adha"}


def _type_fete(fete):
    if fete.get("type"):
        return fete["type"]
    nom = str(fete.get("nom", "")).lower()
    for cle in _FETES_AFFICHAGE:
        if cle.split("_")[0] in nom or ("fitr" in nom and cle == "aid_fitr") \
           or ("adha" in nom and cle == "aid_adha"):
            return cle
    return None


def fenetres_periode(date_min, date_max):
    """Fêtes (debut, fin, type, label) chevauchant l'intervalle donné."""
    dmin, dmax = pd.Timestamp(date_min), pd.Timestamp(date_max)
    out = []
    for fete in config.FETES_MAROCAINES:
        typ = _type_fete(fete)
        if typ is None:
            continue
        try:
            deb, fin = pd.Timestamp(fete["debut"]), pd.Timestamp(fete["fin"])
        except (KeyError, ValueError):
            continue
        if fin >= dmin and deb <= dmax:
            label = _FETES_AFFICHAGE.get(typ, (fete.get("nom", typ), ""))[0]
            out.append({"debut": deb, "fin": fin, "type": typ, "label": label})
    return out


def _fraction_fete_mois(date_fin_mois, fenetres):
    """Pour un mois, (type dominant, fraction de jours en fête)."""
    fin = pd.Timestamp(date_fin_mois)
    jours = pd.date_range(fin.replace(day=1), fin.replace(day=1) + pd.offsets.MonthEnd(0), freq="D")
    meilleur, best_frac = None, 0.0
    for f in fenetres:
        n = ((jours >= f["debut"]) & (jours <= f["fin"])).sum()
        frac = n / len(jours)
        if frac > best_frac:
            best_frac, meilleur = frac, f
    return meilleur, best_frac


def _raison_saison(mois):
    fac = config.PROFIL_SAISONNIER_MENSUEL.get(mois, 1.0)
    if mois in (7, 8):
        return "Saison haute (été)", fac
    if mois == 12:
        return "Saison haute (fêtes de fin d'année)", fac
    if mois in (4, 5, 6):
        return "Saison haute (printemps)", fac
    if mois in (1, 2):
        return "Saison basse (hiver)", fac
    if mois == 11:
        return "Saison basse", fac
    return None, fac


def expliquer_points(dates, valeurs, seuil=0.12, max_points=8):
    """
    Repère les pics/creux notables et donne leur cause.
    Retourne une liste de dicts {date, valeur, sens, cause, ecart}.
    """
    s = pd.Series(list(valeurs), index=pd.to_datetime(list(dates))).astype(float)
    if len(s) < 3:
        return []
    base = s.rolling(window=5, center=True, min_periods=2).median()
    fen = fenetres_periode(s.index.min(), s.index.max())
    n = len(s)

    points = []
    for i, (d, v) in enumerate(s.items()):
        b = base.get(d)
        if not b or b == 0:
            continue
        ecart = (v - b) / b
        if abs(ecart) < seuil:
            continue
        sens = "pic" if ecart > 0 else "creux"
        fete, frac = _fraction_fete_mois(d, fen)
        rs, fac = _raison_saison(d.month)
        saison_ok = rs and ((sens == "pic" and fac >= 1.04) or (sens == "creux" and fac <= 0.96))
        if fete and frac >= 0.25:
            cause = f"{fete['label']} ({frac*100:.0f}% du mois)"
        elif saison_ok:
            cause = rs
        elif i == 0:
            cause = "Début de série (montée en charge)"
        elif i == n - 1:
            cause = "Fin d'horizon de prévision"
        else:
            cause = "Tendance / autre"
        points.append({"date": d, "valeur": v, "sens": sens,
                       "cause": cause, "ecart": ecart})
    points.sort(key=lambda p: -abs(p["ecart"]))
    return points[:max_points]


def decorer_figure(fig, dates, valeurs=None, annoter_points=True):
    """
    Ajoute à une figure Plotly :
      - des bandes ombrées + labels pour les périodes de fête,
      - (si valeurs fournies) des annotations sur les pics/creux notables.
    """
    if dates is None or len(list(dates)) == 0:
        return fig
    dts = pd.to_datetime(list(dates))
    fen = fenetres_periode(dts.min(), dts.max())
    for f in fen:
        if f["type"] not in _FETES_OMBREES:
            continue
        couleur = _FETES_AFFICHAGE[f["type"]][1]
        fig.add_vrect(x0=f["debut"], x1=f["fin"], fillcolor=couleur,
                      line_width=0, layer="below",
                      annotation_text=f["label"], annotation_position="top left",
                      annotation=dict(font_size=10, font_color="#8b949e"))

    if annoter_points and valeurs is not None:
        for p in expliquer_points(dates, valeurs):
            haut = p["sens"] == "pic"
            fleche = "▲" if haut else "▼"
            fig.add_annotation(
                x=p["date"], y=p["valeur"],
                text=f"{fleche} {p['cause']}",
                showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=1,
                arrowcolor="#b8904a", ax=0, ay=(-40 if haut else 40),
                font=dict(size=10, color=("#3d7a3d" if haut else "#a8432f"),
                          family="Inter, sans-serif"),
                bgcolor="rgba(251,248,241,0.95)", bordercolor="#cda85f", borderwidth=1)
    return fig

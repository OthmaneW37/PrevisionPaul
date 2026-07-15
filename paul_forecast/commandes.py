# -*- coding: utf-8 -*-
"""
Commandes clients exceptionnelles (B2B) : détection et planification.

Certaines entreprises (fast-food, traiteur, bureaux…) passent ponctuellement de
GROSSES commandes (ex. des centaines de flûtes pour leurs sandwichs). Ces ventes
ne relèvent pas de la demande « boutique » habituelle :

  - dans le PASSÉ, elles créent des pics isolés qui fausseraient le modèle
    (niveau récent, profil jour de semaine, ancre annuelle). On les DÉTECTE
    (« possible commande », affiché dans le dashboard) et on les NEUTRALISE
    dans la série d'apprentissage ;
  - dans le FUTUR, quand une commande est connue à l'avance (« le 07/07 on
    livre 500 flûtes »), elle est saisie via l'onglet Commandes du dashboard
    (data/commandes_clients.json) et AJOUTÉE telle quelle aux prévisions
    journalières ET mensuelles — donc aussi au plan de production et aux
    besoins matières premières (BOM). Pas de marge de sécurité dessus : la
    quantité est certaine.

Détection : un jour est un pic « type commande » si la vente dépasse nettement
le niveau local du produit (médiane glissante centrée ±7 j) :
    vente > médiane × COMMANDE_RATIO_SEUIL  ET  vente − médiane ≥ COMMANDE_EXCES_MIN.
La médiane glissante suit les changements de régime DURABLES (ex. un client
récurrent qui commande tous les jours) : seuls les pics isolés sont neutralisés.
Les jours de fête, d'événement ou de match ne sont jamais marqués (pics
légitimes de la demande boutique).
"""

import os
import json

import pandas as pd

from . import config
from .logging_setup import get_logger

logger = get_logger()

COMMANDES_PATH = os.path.join(config.DATA_DIR, "commandes_clients.json")

# Fenêtre (jours) de la médiane glissante centrée servant de niveau « attendu ».
FENETRE_MEDIANE = 15


# ── Commandes planifiées (saisies via le dashboard) ───────────────────────────
def charger_commandes():
    """Commandes planifiées brutes [{id, date, produit, quantite, client}]."""
    try:
        with open(COMMANDES_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return doc.get("commandes", []) if isinstance(doc, dict) else (doc or [])


def commandes_normalisees():
    """[{date: Timestamp, produit, quantite, client}] — entrées valides uniquement."""
    out = []
    for c in charger_commandes():
        try:
            d = pd.Timestamp(c["date"]).normalize()
            q = float(c["quantite"])
            p = str(c["produit"]).strip()
        except (KeyError, ValueError, TypeError):
            continue
        if not p or q <= 0:
            continue
        out.append({"date": d, "produit": p, "quantite": q,
                    "client": str(c.get("client", "") or "").strip()})
    return out


# ── Détection des pics « possible commande » ──────────────────────────────────
def jours_proteges():
    """Jours couverts par une fête, un événement ou un match : leurs pics sont
    de la demande boutique légitime — jamais marqués ni neutralisés."""
    jours = set()
    for fete in config.FETES_MAROCAINES:
        try:
            deb, fin = pd.Timestamp(fete["debut"]), pd.Timestamp(fete["fin"])
        except (KeyError, ValueError, TypeError):
            continue
        jours.update(pd.date_range(deb, fin))
    data = config.EVENEMENTS
    items = data.get("evenements", []) if isinstance(data, dict) else (data or [])
    for e in items:
        try:
            deb = pd.Timestamp(e["date"])
            fin = pd.Timestamp(e.get("date_fin") or e["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if fin < deb:
            deb, fin = fin, deb
        jours.update(pd.date_range(deb, fin))
    return jours


def _pics_serie(s, ratio_seuil=None, exces_min=None):
    """Pics « type commande » d'une série journalière (index dates, 0 comblés).

    Retourne un DataFrame indexé par date : Quantite, Attendu, Exces.
    Attendu = médiane glissante centrée (suit les changements de régime durables,
    donc un client récurrent n'est pas re-signalé tous les jours).
    """
    vide = pd.DataFrame(columns=["Quantite", "Attendu", "Exces"])
    ratio = ratio_seuil if ratio_seuil is not None else config.COMMANDE_RATIO_SEUIL
    exces = exces_min if exces_min is not None else config.COMMANDE_EXCES_MIN
    if s is None or s.empty:
        return vide
    attendu = s.rolling(FENETRE_MEDIANE, center=True, min_periods=5).median()
    attendu = attendu.fillna(s.median()).clip(lower=0.0)
    masque = (s > attendu * ratio) & ((s - attendu) >= exces)
    if not masque.any():
        return vide
    return pd.DataFrame({"Quantite": s[masque].astype(float),
                         "Attendu": attendu[masque].astype(float),
                         "Exces": (s[masque] - attendu[masque]).astype(float)})


def nettoyer_serie(s, proteges=None, ratio_seuil=None, exces_min=None):
    """Série d'apprentissage sans les pics « type commande ».

    Les jours détectés sont ramenés à leur niveau attendu (médiane locale) :
    le modèle apprend la demande boutique, l'excès étant attribué à une commande
    ponctuelle. Les jours `proteges` (fêtes/événements/matchs) sont conservés.
    """
    pics = _pics_serie(s, ratio_seuil, exces_min)
    if proteges is not None and not pics.empty:
        pics = pics[~pics.index.isin(proteges)]
    if pics.empty:
        return s
    s = s.copy()
    s.loc[pics.index] = pics["Attendu"]
    return s


def detecter_pics(df, ratio_seuil=None, exces_min=None):
    """Jours « possible commande » sur tout l'historique journalier.

    df : ventes journalières (colonnes Date, Produit, Famille, Quantite).
    Retourne [Date, Produit, Famille, Quantite, Attendu, Exces] trié du plus
    récent au plus ancien. Jours de fête/événement/match exclus.
    """
    colonnes = ["Date", "Produit", "Famille", "Quantite", "Attendu", "Exces"]
    if df is None or df.empty:
        return pd.DataFrame(columns=colonnes)
    exces = exces_min if exces_min is not None else config.COMMANDE_EXCES_MIN
    proteges = jours_proteges()
    fin = df["Date"].max()
    lignes = []
    for prod, g in df.groupby("Produit"):
        if g["Quantite"].max() < exces:      # trop petit pour contenir un pic
            continue
        s = g.groupby("Date")["Quantite"].sum()
        s = s.reindex(pd.date_range(s.index.min(), fin), fill_value=0.0)
        pics = _pics_serie(s, ratio_seuil, exces_min)
        pics = pics[~pics.index.isin(proteges)]
        fam = str(g["Famille"].iloc[-1]) if "Famille" in g.columns else ""
        for d, r in pics.iterrows():
            lignes.append({"Date": d, "Produit": str(prod), "Famille": fam,
                           "Quantite": float(r["Quantite"]),
                           "Attendu": float(r["Attendu"]),
                           "Exces": float(r["Exces"])})
    if not lignes:
        return pd.DataFrame(columns=colonnes)
    return (pd.DataFrame(lignes, columns=colonnes)
            .sort_values("Date", ascending=False).reset_index(drop=True))


# ── Injection des commandes planifiées dans les prévisions ────────────────────
def ajouter_commandes_journalier(prev):
    """Ajoute les commandes planifiées aux prévisions JOURNALIÈRES.

    prev : DataFrame (Date, Code, Produit, Famille, Qty_Prev, Qty_Recommandee, …).
    Ajoute la colonne Qty_Commande puis incrémente Qty_Prev et Qty_Recommandee
    du montant exact (pas de marge : la quantité est connue). Une commande sur
    un produit absent de l'horizon crée sa propre ligne (Fiabilite « Commande »).
    """
    if prev is None or prev.empty:
        return prev
    prev["Qty_Commande"] = 0.0
    cmds = commandes_normalisees()
    if not cmds:
        return prev
    dmin, dmax = prev["Date"].min(), prev["Date"].max()
    produits = prev["Produit"].astype(str)
    nouvelles = []
    for c in cmds:
        if not (dmin <= c["date"] <= dmax):
            continue
        masque = (produits == c["produit"]) & (prev["Date"] == c["date"])
        if masque.any():
            prev.loc[masque, "Qty_Commande"] += c["quantite"]
        else:
            # produit sans prévision (pas d'historique) : la commande est quand
            # même à produire ce jour-là.
            nouvelles.append({"Date": c["date"], "Code": "", "Produit": c["produit"],
                              "Famille": "Autres", "Qty_Prev": 0.0,
                              "Qty_Recommandee": 0.0, "Fiabilite": "Commande",
                              "Qty_Commande": c["quantite"]})
        logger.info("[Commandes] +%.0f × « %s » le %s ajouté aux prévisions journalières.",
                    c["quantite"], c["produit"], c["date"].date())
    if nouvelles:
        prev = pd.concat([prev, pd.DataFrame(nouvelles)], ignore_index=True)
    m = prev["Qty_Commande"] > 0
    prev.loc[m, "Qty_Prev"] += prev.loc[m, "Qty_Commande"]
    prev.loc[m, "Qty_Recommandee"] += prev.loc[m, "Qty_Commande"]
    return prev


def ajouter_commandes_mensuelles(dict_prevision_prod):
    """Ajoute les commandes planifiées aux prévisions MENSUELLES (pipeline).

    Incrémente toutes les colonnes de quantité (Qty_Prev_* et fourchettes
    Qty_Selection_Bas/Haut) du mois contenant chaque commande : le plan de
    production et les besoins matières premières (BOM) en héritent. À appeler
    APRÈS incertitude.ajouter_intervalles (pas de marge sur une quantité connue).
    Un produit inconnu du pipeline mensuel est signalé mais ignoré (il reste
    couvert côté journalier). Retourne la liste des commandes appliquées.
    """
    cmds = commandes_normalisees()
    if not cmds or not dict_prevision_prod:
        return []
    par_nom = {str(p): df for p, df in dict_prevision_prod.items()}
    appliquees = []
    for c in cmds:
        df_fc = par_nom.get(c["produit"])
        if df_fc is None:
            logger.warning("[Commandes] Produit « %s » inconnu du pipeline mensuel — "
                           "commande du %s non ajoutée aux besoins matières.",
                           c["produit"], c["date"].date())
            continue
        masque = pd.to_datetime(df_fc["Date"]).dt.to_period("M") == c["date"].to_period("M")
        if not masque.any():
            continue        # hors horizon de prévision (passée ou trop lointaine)
        cols = [col for col in df_fc.columns
                if col.startswith("Qty_Prev") or col in ("Qty_Selection_Bas",
                                                         "Qty_Selection_Haut")]
        for col in cols:
            df_fc.loc[masque, col] = df_fc.loc[masque, col].astype(float) + c["quantite"]
        appliquees.append(c)
        logger.info("[Commandes] +%.0f × « %s » ajouté au mois %s (production + matières).",
                    c["quantite"], c["produit"], c["date"].strftime("%Y-%m"))
    return appliquees

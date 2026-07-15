# -*- coding: utf-8 -*-
"""
Prévision JOURNALIÈRE par produit (« combien produire demain / chaque jour »).

Modèle (validé en backtest : ~7-8 % de MAPE journalier sur mois normaux) :

    prév(produit p, jour d) = niveau_saison(p, d) × croissance(p) × poids_jour(p, dow(d))
                              × boost_événements_fêtes(famille, d)

  - niveau_saison : niveau désaisonnalisé (par jour de semaine) observé à la MÊME
    période l'an dernier (±13 j autour de d-364) → capte la saison annuelle ;
  - croissance    : ventes des 28 derniers jours / mêmes 28 jours un an plus tôt
    (bornée 0.6–1.8) → capte la tendance récente ;
  - poids_jour    : profil jour-de-semaine du produit (week-end plus fort, etc.) ;
  - boost         : événements (data/evenements.json) et fêtes appliqués PILE le jour.

L'apprentissage se fait sur une série NETTOYÉE des pics « type commande client »
(grosse commande B2B isolée, cf. module commandes) ; les commandes planifiées
connues à l'avance sont ensuite AJOUTÉES telles quelles (colonne Qty_Commande).

Source : donnees_ventes/ventes_journalieres.csv (cf outils/convertir_ventes_journalieres.py).
Sortie : exports/previsions_journalieres.csv (Date ; Code ; Produit ; Famille ;
         Qty_Prev ; Qty_Base ; Qty_Recommandee ; Fiabilite ; Qty_Commande).
         Qty_Base = demande boutique HABITUELLE (ancre annuelle × croissance
         d'avant-rupture) : ne diffère de Qty_Prev que pour un produit en rupture
         de niveau HAUSSE (ex. fast-food qui commande tous les jours) — l'écart
         est la part attribuable au nouveau régime.
"""

import os
import json

import numpy as np
import pandas as pd

from . import config
from . import evenements as mod_evenements
from . import matchs as mod_matchs
from . import commandes as mod_commandes
from . import calibration_fetes
from .logging_setup import get_logger

logger = get_logger()

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(RACINE, "donnees_ventes", "ventes_journalieres.csv")

HORIZON_JOURS = 62        # ~2 mois : permet de lire « demain » comme le mois complet
FENETRE_CROISSANCE = 28   # jours pour la tendance récente (YoY)
DEMI_FENETRE_LY = 13      # ± jours autour de d-364 pour le niveau saisonnier
POIDS_RECENT = 0.4        # mélange niveau récent (β) vs ancre annuelle (1-β) ; suit les changements de niveau

# Rupture de niveau DURABLE (changement de régime : nouveau client récurrent,
# perte d'un débouché…). Quand elle est détectée, on relève β pour suivre le
# niveau récent au lieu de rester ancré sur l'an dernier (que le plafond YoY de
# g_yoy empêche de rattraper). Ne se déclenche que si la divergence est forte ET
# stable sur les deux dernières fenêtres → pas sur un simple à-coup.
# Deux voies : LENTE (2×14 j — rupture modérée mais installée) et RAPIDE
# (2×7 j à seuils plus stricts + confirmation sur 14 j — rupture FORTE, ex. un
# fast-food qui se met à commander ×6 tous les jours, détectée ~1 sem plus tôt).
POIDS_RECENT_RUPTURE = 0.9
SEUIL_RUPTURE_HAUT   = 1.4    # niveau récent ≥ 1.4× l'attendu annuel plafonné → hausse
SEUIL_RUPTURE_BAS    = 0.6    # niveau récent ≤ 0.6× l'attendu annuel → baisse
SEUIL_RUPTURE_FORT   = 1.6    # voie rapide : semaine récente ≥ 1.6× l'attendu


# ── Boost événements / fêtes au jour le jour ──────────────────────────────────
def _fetes_normalisees():
    """[(debut, fin, ratios_par_famille)] depuis FETES_MAROCAINES.

    Profils MESURÉS (calibration_fetes) prioritaires, repli sur les hypothèses
    de config.PROFILS_FETES par type puis par famille.
    """
    mesures = calibration_fetes.profils_mesures() or {}
    hypotheses = config.PROFILS_FETES or {}
    profils = {}
    for typ in set(mesures) | set(hypotheses):
        if typ.startswith("_"):
            continue
        ratios = dict(hypotheses.get(typ, {}).get("ratios", {}))
        ratios.update(mesures.get(typ, {}).get("ratios", {}))  # mesuré écrase l'hypothèse
        if ratios:
            profils[typ] = {"ratios": ratios}
    out = []
    for fete in config.FETES_MAROCAINES:
        typ = fete.get("type")
        if not typ:
            nom = str(fete.get("nom", "")).lower()
            typ = ("ramadan" if "ramadan" in nom else
                   "aid_fitr" if "fitr" in nom else
                   "aid_adha" if "adha" in nom else
                   "achoura" if "achoura" in nom else
                   "mawlid" if ("mawlid" in nom or "mouloud" in nom) else None)
        if typ is None or typ not in profils:
            continue
        try:
            deb, fin = pd.Timestamp(fete["debut"]), pd.Timestamp(fete["fin"])
        except (KeyError, ValueError):
            continue
        out.append((deb, fin, profils[typ].get("ratios", {})))
    return out


def _combiner_boosts(mults):
    """Combine plusieurs multiplicateurs SANS les empiler.

    On prend la plus forte HAUSSE et la plus forte BAISSE (au lieu du produit) : deux
    sources qui décrivent le même pic (ex. un match saisi à la fois comme match et
    comme événement) ne se cumulent pas, mais une baisse (ex. Ramadan) reste appliquée.
    """
    hausse = max([m for m in mults if m > 1.0], default=1.0)
    baisse = min([m for m in mults if m < 1.0], default=1.0)
    return hausse * baisse


def _table_boost(dates, familles):
    """Multiplicateur boost[(date, famille)] (1.0 par défaut) : fêtes + événements + matchs."""
    fetes = _fetes_normalisees()
    evs = mod_evenements.evenements_normalises()
    boost_matchs = mod_matchs.table_boost(dates, familles)
    boost = {}
    for d in dates:
        for fam in familles:
            mults = [ratios[fam] for deb, fin, ratios in fetes
                     if deb <= d <= fin and fam in ratios]
            mults += [e["familles"].get(fam, e["global"]) for e in evs
                      if e["debut"] <= d <= e["fin"]]
            mm = boost_matchs.get((d, fam))
            if mm is not None:
                mults.append(mm)
            m = _combiner_boosts(mults)
            if m != 1.0:
                boost[(d, fam)] = m
    return boost


# ── Modèle ────────────────────────────────────────────────────────────────────
def charger_ventes():
    if not os.path.exists(SOURCE):
        return None
    df = pd.read_csv(SOURCE, sep=";", parse_dates=["Date"])
    df["Quantite"] = df["Quantite"].clip(lower=0)
    return df


def _parametres(s, fin):
    """Paramètres du modèle pour une série jusqu'à `fin` : profil jour, croissance, niveau, marge."""
    positifs = s[(s.index <= fin) & (s > 0)]
    base = positifs.mean() if len(positifs) else 0.0
    if base <= 0:
        return None
    st = s[s.index <= fin]
    w = (st.groupby(st.index.dayofweek).mean() / base).reindex(range(7)).fillna(1.0).to_dict()
    a = s.reindex(pd.date_range(fin - pd.Timedelta(days=FENETRE_CROISSANCE - 1), fin)).sum()
    b = s.reindex(pd.date_range(fin - pd.Timedelta(days=FENETRE_CROISSANCE - 1 + 364),
                                fin - pd.Timedelta(days=364))).sum()
    g_yoy = float(np.clip(a / b, 0.6, 1.8)) if b > 0 else 1.0
    niveau = s.reindex(pd.date_range(fin - pd.Timedelta(days=FENETRE_CROISSANCE - 1), fin)).mean()

    # ── Détection d'une rupture de niveau DURABLE ─────────────────────────────
    # On compare le niveau des dernières fenêtres à l'attendu de l'ancre annuelle
    # plafonnée (an dernier × g_yoy borné). Si les DEUX fenêtres consécutives sont
    # fortement au-dessus (ou en dessous), c'est un changement de régime que le
    # plafond YoY ne peut pas exprimer → on bascule sur un niveau réactif. Les
    # pics de commande isolés sont déjà retirés en amont (module commandes) : ils
    # ne faussent pas ces moyennes. La voie RAPIDE (2×7 j, seuils stricts,
    # confirmée sur 14 j pour ne pas réagir à une simple semaine de fête) capte
    # les ruptures fortes ~1 semaine plus tôt que la voie lente (2×14 j).
    demi = FENETRE_CROISSANCE // 2
    l_recent = s.reindex(pd.date_range(fin - pd.Timedelta(days=demi - 1), fin)).mean()
    l_avant  = s.reindex(pd.date_range(fin - pd.Timedelta(days=FENETRE_CROISSANCE - 1),
                                       fin - pd.Timedelta(days=demi))).mean()
    sem_recent = s.reindex(pd.date_range(fin - pd.Timedelta(days=6), fin)).mean()
    sem_avant  = s.reindex(pd.date_range(fin - pd.Timedelta(days=13),
                                         fin - pd.Timedelta(days=7))).mean()
    ly_niveau = b / FENETRE_CROISSANCE if b > 0 else 0.0
    beta, niveau_ref, rupture_hausse = POIDS_RECENT, niveau, False
    if ly_niveau > 0 and np.isfinite(l_recent) and np.isfinite(l_avant):
        attendu = ly_niveau * g_yoy
        rapide = (np.isfinite(sem_recent) and np.isfinite(sem_avant)
                  and sem_recent >= attendu * SEUIL_RUPTURE_FORT
                  and sem_avant >= attendu * 1.3
                  and l_recent >= attendu * SEUIL_RUPTURE_HAUT)
        hausse = l_recent >= attendu * SEUIL_RUPTURE_HAUT and l_avant >= attendu * 1.2
        baisse = l_recent <= attendu * SEUIL_RUPTURE_BAS and l_avant <= attendu * 0.8
        if rapide:
            # rupture FORTE et confirmée : la semaine en cours EST le nouveau
            # régime — suivre son niveau sans dilution. Plafonné à 2× la semaine
            # précédente : une vraie rampe (client récurrent) monte semaine après
            # semaine et n'est pas bridée ; un pic d'UNE semaine qui explose de
            # nulle part (événement non saisi, grosse commande) l'est.
            beta = POIDS_RECENT_RUPTURE
            niveau_ref = min(sem_recent, sem_avant * 2.0)
            rupture_hausse = True
        elif hausse or baisse:
            # rupture modérée : suivi réactif mais prudent (moyenne 28 j et 14 j
            # mélangées — les produits volatils déclenchent parfois cette voie
            # sur du bruit, un niveau non dilué sur-réagirait).
            beta = 0.85
            niveau_ref = 0.5 * niveau + 0.5 * l_recent
            rupture_hausse = bool(hausse)

    # Croissance « d'avant » : mesurée sur la fenêtre 28 j finissant il y a 28 j
    # (donc hors régime récent). Sert de base « demande habituelle » quand une
    # rupture HAUSSE est détectée : la croissance g_yoy, gonflée par le nouveau
    # régime lui-même (plafonnée à 1.8), surestimerait la demande boutique.
    g_base = g_yoy
    if rupture_hausse:
        a2 = s.reindex(pd.date_range(fin - pd.Timedelta(days=2 * FENETRE_CROISSANCE - 1),
                                     fin - pd.Timedelta(days=FENETRE_CROISSANCE))).sum()
        b2 = s.reindex(pd.date_range(fin - pd.Timedelta(days=2 * FENETRE_CROISSANCE - 1 + 364),
                                     fin - pd.Timedelta(days=FENETRE_CROISSANCE + 364))).sum()
        g_base = float(np.clip(a2 / b2, 0.6, 1.8)) if b2 > 0 else 1.0

    rec = s.reindex(pd.date_range(fin - pd.Timedelta(days=55), fin))
    cv = (rec.std() / rec.mean()) if rec.mean() > 0 else 0.25
    marge = float(np.clip(cv, 0.10, 0.40))
    return {"w": w, "g_yoy": g_yoy, "niveau": niveau, "niveau_ref": niveau_ref,
            "beta": beta, "marge": marge, "n_positifs": int((st > 0).sum()),
            "rupture_hausse": rupture_hausse, "g_base": g_base}


def _prevoir_jours(s, jours, p, retour_base=False):
    """Prévision (avant boost) pour une liste de jours.

    Niveau = mélange du NIVEAU RÉCENT (suit les flambées/chutes) et de l'ANCRE
    ANNUELLE (même période l'an dernier × croissance, porte la saisonnalité) :
        niveau = β·récent + (1−β)·annuel.
    β vaut POIDS_RECENT en régime normal ; il est relevé (et le niveau récent
    rendu plus réactif) quand _parametres a détecté une rupture de niveau durable.

    Si `retour_base` : retourne (prévisions, base) où base = composante ANCRE
    ANNUELLE seule (annuel × poids jour) — la « demande habituelle » du produit,
    hors changement de régime récent. Sert à décomposer la prévision d'un produit
    en rupture : part boutique habituelle vs part du nouveau régime.
    """
    w, g_yoy, niv, beta = p["w"], p["g_yoy"], p["niveau_ref"], p["beta"]
    # facteur ramenant l'ancre annuelle à la croissance « d'avant-rupture »
    # (g_yoy est gonflé par le nouveau régime lui-même) — 1.0 hors rupture.
    ratio_base = (p.get("g_base", g_yoy) / g_yoy) if g_yoy > 0 else 1.0
    out, base = {}, {}
    for d in jours:
        ly = s[(s.index >= d - pd.Timedelta(days=364 + DEMI_FENETRE_LY)) &
               (s.index <= d - pd.Timedelta(days=364 - DEMI_FENETRE_LY))]
        if len(ly) == 0 or ly.sum() == 0:
            annuel = niv
            annuel_ok = False
        else:
            f = ly.index.dayofweek.map(w).to_numpy(dtype=float)
            f[f <= 0] = np.nan
            annuel = np.nanmean(ly.to_numpy() / f) * g_yoy
            annuel_ok = bool(np.isfinite(annuel))
        if not np.isfinite(annuel):
            annuel = niv
        niveau = beta * niv + (1 - beta) * annuel
        out[d] = max(niveau * w[d.dayofweek], 0.0)
        # sans ancre annuelle fiable, pas de décomposition possible : base = prévision
        base[d] = max(annuel * ratio_base * w[d.dayofweek], 0.0) if annuel_ok else out[d]
    return (out, base) if retour_base else out


def _fiabilite(s, fin_hist, holdout_jours=28):
    """Niveau de fiabilité d'un produit par backtest GLISSANT des `holdout_jours` derniers jours.

    Le modèle est RÉAJUSTÉ au début de chaque semaine du holdout (origine
    glissante) puis prédit 7 jours — comme en production, où il est recalé chaque
    jour. Un backtest à origine FIGÉE (paramètres calés une seule fois 28 j en
    arrière) notait « Incertain » des produits que le modèle réel suit très bien
    (ex. changement de régime en cours de fenêtre, rattrapé en production).
    Mesure : wMAPE des TOTAUX HEBDOMADAIRES (Σ|prév_sem − réel_sem| / Σréel) —
    c'est le volume hebdo qui compte pour la production, pas le bruit jour à jour.

    Labels : Fiable / Moyen / Incertain (mesuré) ;
    « Peu vendu »  = quasi aucune vente sur les 4 dernières semaines (l'enjeu de
                     production est nul, l'erreur relative n'a pas de sens) ;
    « Hist. court » = pas assez d'historique pour au moins 2 semaines de test.
    """
    jours_recents = pd.date_range(fin_hist - pd.Timedelta(days=holdout_jours - 1), fin_hist)
    if s.reindex(jours_recents).fillna(0.0).sum() < holdout_jours * 0.5:
        return "Peu vendu", None                 # produit quasi inexistant récemment
    err_abs = reel = 0.0
    n_semaines = 0
    for k in range(holdout_jours // 7, 0, -1):
        origine = fin_hist - pd.Timedelta(days=7 * k)
        p = _parametres(s, origine)
        if p is None or p["n_positifs"] < 20:
            continue                             # trop peu d'historique à cette origine
        jours = pd.date_range(origine + pd.Timedelta(days=1),
                              origine + pd.Timedelta(days=7))
        pred = sum(_prevoir_jours(s, jours, p).values())
        act = float(s.reindex(jours).fillna(0.0).sum())
        err_abs += abs(pred - act)
        reel += act
        n_semaines += 1
    if n_semaines < 2 or reel <= 0:
        return "Hist. court", None
    err = err_abs / reel
    niveau = "Fiable" if err < 0.25 else "Moyen" if err < 0.45 else "Incertain"
    return niveau, err


def prevoir(horizon_jours=HORIZON_JOURS):
    """Retourne Date ; Code ; Produit ; Famille ; Qty_Prev ; Qty_Recommandee ;
    Fiabilite ; Qty_Commande (part commande client incluse dans Qty_Prev)."""
    df = charger_ventes()
    if df is None or df.empty:
        logger.warning("[Journalier] Source introuvable : %s", SOURCE)
        return None

    df = df.groupby(["Produit", "Famille", "Code", "Date"], as_index=False)["Quantite"].sum()
    fin_hist = df["Date"].max()
    horizon = pd.date_range(fin_hist + pd.Timedelta(days=1), periods=horizon_jours)

    meta = (df.sort_values("Date").groupby("Produit")
              .agg(Famille=("Famille", "last"), Code=("Code", "last")))

    proteges = mod_commandes.jours_proteges()
    lignes = []
    for prod, g in df.groupby("Produit"):
        s = g.groupby("Date")["Quantite"].sum()
        # Reindexer jusqu'à fin_hist (pas seulement la dernière vente DU PRODUIT) :
        # sinon un produit qui n'a rien vendu dans les 28 derniers jours a une
        # série qui s'arrête avant la fenêtre récente → reindex hors plage → NaN.
        s = s.reindex(pd.date_range(s.index.min(), fin_hist), fill_value=0.0)
        # Neutraliser les pics « type commande client » : un jour de grosse
        # commande B2B isolée ne doit gonfler ni le niveau récent, ni le profil
        # jour de semaine, ni l'ancre annuelle. Un changement de régime durable
        # (client récurrent) est conservé.
        s = mod_commandes.nettoyer_serie(s, proteges)
        p = _parametres(s, fin_hist)
        if p is None:
            continue
        niveau, _ = _fiabilite(s, fin_hist)
        qmap, qbase = _prevoir_jours(s, horizon, p, retour_base=True)
        fam, code = meta.loc[prod, "Famille"], meta.loc[prod, "Code"]
        for d in horizon:
            # Qty_Base = demande boutique HABITUELLE (ancre annuelle × croissance
            # d'avant-rupture). Ne diffère de Qty_Prev que pour un produit en
            # rupture HAUSSE (ex. nouveau client récurrent type fast-food) :
            # l'écart = part attribuable au nouveau régime. Cf. notes_produits.json
            # pour la cause confirmée quand elle est connue.
            base = min(qbase[d], qmap[d]) if p["rupture_hausse"] else qmap[d]
            lignes.append({"Date": d, "Code": code, "Produit": prod, "Famille": fam,
                           "Qty_Prev": qmap[d], "Qty_Base": base,
                           "_marge": p["marge"], "Fiabilite": niveau})

    prev = pd.DataFrame(lignes)
    if prev.empty:
        return prev

    # boosts événements / fêtes (au jour le jour) — la demande boutique de base
    # est boostée pareil (un match augmente aussi les ventes hors client B2B)
    boost = _table_boost(set(prev["Date"]), set(prev["Famille"].astype(str)))
    if boost:
        facteurs = prev.apply(
            lambda r: boost.get((r["Date"], str(r["Famille"])), 1.0), axis=1)
        prev["Qty_Prev"] *= facteurs
        prev["Qty_Base"] *= facteurs

    # corrections manuelles par produit (data/ajustements_produits.json)
    for a in charger_overrides():
        prod = a.get("produit")
        if not prod:
            continue
        masque = prev["Produit"].astype(str) == str(prod)
        if not masque.any():
            continue
        try:
            val = float(a.get("valeur"))
        except (TypeError, ValueError):
            continue
        if a.get("mode") == "fixe":
            prev.loc[masque, "Qty_Prev"] = max(val, 0.0)
        else:  # facteur multiplicatif
            prev.loc[masque, "Qty_Prev"] *= max(val, 0.0)
            prev.loc[masque, "Qty_Base"] *= max(val, 0.0)

    prev["Qty_Recommandee"] = (prev["Qty_Prev"] * (1 + prev["_marge"])).round()
    prev["Qty_Prev"] = prev["Qty_Prev"].round()
    # la base ne peut pas dépasser la prévision (ex. après une correction « fixe »)
    prev["Qty_Base"] = np.minimum(prev["Qty_Base"], prev["Qty_Prev"]).round()
    prev = prev.drop(columns="_marge")

    # commandes clients planifiées (data/commandes_clients.json) : ajoutées
    # telles quelles APRÈS la marge — la quantité est connue, pas d'incertitude.
    prev = mod_commandes.ajouter_commandes_journalier(prev)
    return prev


# ── Corrections manuelles + alertes de niveau ─────────────────────────────────
OVERRIDES_PATH = os.path.join(config.DATA_DIR, "ajustements_produits.json")


def charger_overrides():
    """Corrections manuelles [{produit, mode: 'facteur'|'fixe', valeur}]."""
    try:
        with open(OVERRIDES_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return doc.get("ajustements", []) if isinstance(doc, dict) else (doc or [])


def alertes_niveau(min_volume=20, seuil_haut=1.6, seuil_bas=0.65):
    """Produits dont le niveau RÉCENT (28 j) diffère fortement de l'an dernier.

    Repère les produits qui ont changé de régime (flambée/chute) — ceux où la
    prévision est la plus délicate. Retourne un DataFrame trié par ampleur.
    """
    df = charger_ventes()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.groupby(["Produit", "Famille", "Date"], as_index=False)["Quantite"].sum()
    fin = df["Date"].max()
    recent = pd.date_range(fin - pd.Timedelta(days=27), fin)
    an_dernier = pd.date_range(fin - pd.Timedelta(days=27 + 364), fin - pd.Timedelta(days=364))
    rows = []
    for prod, g in df.groupby("Produit"):
        s = g.groupby("Date")["Quantite"].sum()
        s = s.reindex(pd.date_range(s.index.min(), fin), fill_value=0.0)
        rec = s.reindex(recent).mean()
        ly = s.reindex(an_dernier).mean()
        if pd.isna(rec) or pd.isna(ly) or rec < min_volume or ly <= 0:
            continue
        ratio = rec / ly
        if ratio >= seuil_haut or ratio <= seuil_bas:
            rows.append({"Produit": prod, "Famille": g["Famille"].iloc[-1],
                         "Niveau récent/j": int(round(rec)), "Il y a 1 an/j": int(round(ly)),
                         "Variation": f"{'+' if ratio >= 1 else ''}{(ratio - 1) * 100:.0f}%",
                         "_ampleur": abs(ratio - 1)})
    if not rows:
        return pd.DataFrame()
    return (pd.DataFrame(rows).sort_values("_ampleur", ascending=False)
            .drop(columns="_ampleur").reset_index(drop=True))


def generer_csv(chemin=None):
    chemin = chemin or os.path.join(RACINE, "exports", "previsions_journalieres.csv")
    # Rafraîchit les profils de fêtes mesurés avant de prévoir.
    try:
        calibration_fetes.calibrer(ecrire=True, rapport=False)
    except Exception as e:
        logger.warning("[Journalier] Calibration fêtes ignorée : %s", e)
    prev = prevoir()
    if prev is None or prev.empty:
        logger.warning("[Journalier] Rien à exporter.")
        return None
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    prev.to_csv(chemin, sep=";", index=False, encoding="utf-8")
    logger.info("[Journalier] %d lignes (%s → %s) → %s",
                len(prev), prev["Date"].min().date(), prev["Date"].max().date(), chemin)

    # Suivi « prévu vs réel » (reconstitution hors-échantillon des jours récents).
    # Import local : suivi dépend de ce module (évite un cycle d'import).
    try:
        from . import suivi
        suivi.generer_csv()
    except Exception as e:
        logger.warning("[Journalier] Suivi prévu/réel ignoré : %s", e)
    return chemin


if __name__ == "__main__":
    generer_csv()

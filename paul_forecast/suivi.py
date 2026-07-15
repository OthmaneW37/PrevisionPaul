# -*- coding: utf-8 -*-
"""
Suivi quotidien « prévu vs réel » du forecast journalier.

Le projet ne conserve pas d'archive des prévisions passées : on ne peut donc pas
relire « la prévision affichée hier ». À la place, on RECONSTITUE honnêtement ce
que le modèle aurait prédit pour chacun des N derniers jours en ne l'entraînant
que sur les données ANTÉRIEURES à ce jour. L'origine est GLISSANTE, ré-estimée
tous les `pas` jours (par défaut chaque semaine) : chaque jour évalué est donc
prédit par un modèle « frais » d'au plus `pas` jours — fidèle à un forecast
régénéré périodiquement, et non figé au début de la fenêtre (ce qui sous-estimait
fortement les produits en hausse récente). Comparé au réel jour par jour et
produit par produit.

Sert à deux choses :
  - vérifier globalement que la prévision colle au réel (écart quotidien) ;
  - repérer les produits à BIAIS SYSTÉMATIQUE (toujours sur- ou sous-produits),
    le signal le plus actionnable pour corriger un réglage ou une recette.

Le réel comparé est la vente BRUTE (ce qui s'est vraiment vendu). Les commandes
B2B ponctuelles sont retirées de l'apprentissage (comme en production) mais pas
du réel : un jour de grosse commande non anticipée apparaît donc logiquement en
sous-production. Les ajustements manuels (ajustements_produits.json) ne sont PAS
appliqués : on mesure le MODÈLE, pas les corrections à la main.

Sortie : exports/suivi_prevu_reel.csv (Date ; Produit ; Famille ; Prev ; Reel).
"""

import os

import numpy as np
import pandas as pd

from . import forecast_journalier as fj
from . import commandes as mod_commandes
from .logging_setup import get_logger

logger = get_logger()

FENETRE_SUIVI = 28   # nombre de jours récents rejoués
PAS_REESTIMATION = 7  # ré-estime le modèle tous les N jours (origine glissante)


def _blocs(cut, fin, pas):
    """Découpe (cut, fin] en blocs de `pas` jours : [(origine, jours_du_bloc)].

    Chaque bloc est prédit par un modèle entraîné à `origine` (= veille du bloc)."""
    blocs, o = [], cut
    while o < fin:
        o2 = min(o + pd.Timedelta(days=pas), fin)
        blocs.append((o, pd.date_range(o + pd.Timedelta(days=1), o2)))
        o = o2
    return blocs


def comparer_prevu_reel(n_jours=FENETRE_SUIVI, pas=PAS_REESTIMATION, df=None):
    """Reconstitue prévu vs réel sur les `n_jours` derniers jours (origine glissante).

    Retourne un DataFrame [Date, Produit, Famille, Prev, Reel] ou None.
    """
    df = df if df is not None else fj.charger_ventes()
    if df is None or df.empty:
        return None
    df = df.groupby(["Produit", "Famille", "Code", "Date"], as_index=False)["Quantite"].sum()
    fin = df["Date"].max()
    cut = fin - pd.Timedelta(days=n_jours)
    blocs = _blocs(cut, fin, pas)
    if not blocs:
        return None

    proteges = mod_commandes.jours_proteges()
    meta = df.groupby("Produit").agg(Famille=("Famille", "last"))
    lignes = []
    for prod, g in df.groupby("Produit"):
        s = g.groupby("Date")["Quantite"].sum()
        s = s.reindex(pd.date_range(s.index.min(), fin), fill_value=0.0)
        # série d'apprentissage nettoyée des pics de commande (comme en production)
        s_clean = mod_commandes.nettoyer_serie(s, proteges)
        fam = str(meta.loc[prod, "Famille"])
        for origine, jours_bloc in blocs:
            # modèle entraîné UNIQUEMENT sur l'avant du bloc (aucune fuite)
            p = fj._parametres(s_clean, origine)
            if p is None:
                continue
            pred = fj._prevoir_jours(s_clean, jours_bloc, p)
            for d in jours_bloc:
                lignes.append({"Date": d, "Produit": str(prod), "Famille": fam,
                               "Prev": max(pred[d], 0.0),
                               "Reel": float(s.reindex([d]).iloc[0])})   # réel BRUT
    comp = pd.DataFrame(lignes)
    if comp.empty:
        return comp

    # mêmes boosts fêtes/événements/matchs qu'en production, appliqués au jour
    boost = fj._table_boost(set(comp["Date"]), set(comp["Famille"].astype(str)))
    if boost:
        comp["Prev"] *= comp.apply(
            lambda r: boost.get((r["Date"], str(r["Famille"])), 1.0), axis=1)
    comp["Prev"] = comp["Prev"].round(1)
    return comp


def resume_journalier(comp):
    """Écart quotidien agrégé (toutes catégories) : Prev, Reel, Ecart, Ecart_pct."""
    if comp is None or comp.empty:
        return pd.DataFrame(columns=["Date", "Prev", "Reel", "Ecart", "Ecart_pct"])
    g = (comp.groupby("Date").agg(Prev=("Prev", "sum"), Reel=("Reel", "sum"))
         .reset_index())
    g["Ecart"] = g["Prev"] - g["Reel"]
    g["Ecart_pct"] = np.where(g["Reel"] > 0, g["Ecart"] / g["Reel"] * 100.0, np.nan)
    return g


def metriques_globales(comp):
    """Indicateurs de tête : wMAPE global, biais global (%), n jours, n produits."""
    if comp is None or comp.empty:
        return {"wmape": None, "biais_pct": None, "n_jours": 0, "n_produits": 0}
    reel = float(comp["Reel"].sum())
    prev = float(comp["Prev"].sum())
    wmape = (float((comp["Prev"] - comp["Reel"]).abs().sum()) / reel) if reel > 0 else None
    biais = ((prev - reel) / reel * 100.0) if reel > 0 else None
    return {"wmape": wmape, "biais_pct": biais,
            "n_jours": int(comp["Date"].nunique()),
            "n_produits": int(comp["Produit"].nunique())}


def biais_par_produit(comp, min_volume_jour=5.0, seuil_biais=15.0):
    """Produits à biais SYSTÉMATIQUE sur la fenêtre (sur/sous-production).

    Retourne [Produit, Famille, Reel/j, Prev/j, Biais, wMAPE, Sens], trié par
    impact (|écart total|). `min_volume_jour` filtre les produits trop petits ;
    `seuil_biais` (%) définit à partir de quand on parle de biais.
    """
    colonnes = ["Produit", "Famille", "Réel/j", "Prév/j", "Biais", "wMAPE", "Sens"]
    if comp is None or comp.empty:
        return pd.DataFrame(columns=colonnes)
    rows = []
    for prod, g in comp.groupby("Produit"):
        n = len(g)
        reel = float(g["Reel"].sum())
        prev = float(g["Prev"].sum())
        if n == 0 or reel < min_volume_jour * n:
            continue
        biais = (prev - reel) / reel * 100.0
        wmape = float((g["Prev"] - g["Reel"]).abs().sum()) / reel
        sens = ("Sur-production" if biais >= seuil_biais else
                "Sous-production" if biais <= -seuil_biais else "OK")
        rows.append({"Produit": str(prod), "Famille": str(g["Famille"].iloc[-1]),
                     "Réel/j": int(round(reel / n)), "Prév/j": int(round(prev / n)),
                     "Biais": f"{'+' if biais >= 0 else ''}{biais:.0f}%",
                     "wMAPE": f"{wmape*100:.0f}%", "Sens": sens,
                     "_impact": abs(prev - reel)})
    if not rows:
        return pd.DataFrame(columns=colonnes)
    df = pd.DataFrame(rows).sort_values("_impact", ascending=False)
    return df.drop(columns="_impact").reset_index(drop=True)


def generer_csv(chemin=None, n_jours=FENETRE_SUIVI):
    """Calcule et écrit exports/suivi_prevu_reel.csv. Retourne le chemin ou None."""
    chemin = chemin or os.path.join(fj.RACINE, "exports", "suivi_prevu_reel.csv")
    comp = comparer_prevu_reel(n_jours)
    if comp is None or comp.empty:
        logger.warning("[Suivi] Rien à comparer (données insuffisantes).")
        return None
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    comp.to_csv(chemin, sep=";", index=False, encoding="utf-8")
    m = metriques_globales(comp)
    logger.info("[Suivi] %d jours × %d produits — wMAPE global %.0f%%, biais %+.0f%% → %s",
                m["n_jours"], m["n_produits"],
                (m["wmape"] or 0) * 100, (m["biais_pct"] or 0), chemin)
    return chemin


if __name__ == "__main__":
    generer_csv()

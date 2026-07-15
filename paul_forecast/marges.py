# -*- coding: utf-8 -*-
"""
Marge matière par produit : prix de vente moyen constaté − coût matières.

  - Prix de vente : moyenne pondérée RÉELLE des ventes récentes
    (CA_TTC / Quantité sur les `FENETRE_PRIX` derniers jours de
    donnees_ventes/ventes_journalieres.csv) — reflète les prix actuels.
  - Coût matières : recette exacte (BOM) ou détectée, éclatée en matières de
    base (exploser_psf) puis chiffrée avec les prix estimés (couts).

Limites assumées (affichées dans le dashboard) : prix TTC (TVA non déduite),
prix matières ESTIMÉS, recettes provisoires → la marge est une marge MATIÈRE
indicative, pas une marge nette (ni main-d'œuvre, ni énergie, ni emballage).
"""
import os

import numpy as np
import pandas as pd

from . import config
from . import bom
from . import couts
from .logging_setup import get_logger

logger = get_logger()

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(RACINE, "donnees_ventes", "ventes_journalieres.csv")

FENETRE_PRIX = 120     # jours récents pour le prix de vente moyen
MIN_QTE = 10           # volume minimal sur la fenêtre pour un prix fiable


def cout_matiere_produit(prod):
    """(coût MAD/unité, source 'exact'|'auto'|None) pour un produit."""
    recette = config.BOM.get(str(prod))
    source = "exact" if recette else None
    if not recette:
        recette = bom.detecter_bom_produit(str(prod))
        source = "auto" if recette else None
    if not recette:
        return None, None
    base = bom.normaliser_bom(bom.exploser_psf(recette))
    couts_lignes = [couts.cout_ligne(n, q) for n, q in base.items()]
    connus = [c for c in couts_lignes if c is not None]
    if not connus:
        return None, source
    return float(sum(connus)), source


def table_marges(fenetre_jours=FENETRE_PRIX):
    """DataFrame par produit : volume, prix de vente moyen, coût matière, marge, food-cost."""
    if not os.path.exists(SOURCE):
        return None
    df = pd.read_csv(SOURCE, sep=";", parse_dates=["Date"])
    df["Quantite"] = pd.to_numeric(df["Quantite"], errors="coerce").fillna(0).clip(lower=0)
    df["CA_TTC"] = pd.to_numeric(df["CA_TTC"], errors="coerce").fillna(0).clip(lower=0)
    fin = df["Date"].max()
    rec = df[df["Date"] >= fin - pd.Timedelta(days=fenetre_jours - 1)]

    g = (rec.groupby(["Produit", "Famille"], as_index=False)
            .agg(Quantite=("Quantite", "sum"), CA=("CA_TTC", "sum")))
    g = g[(g["Quantite"] >= MIN_QTE) & (g["CA"] > 0)]
    g["Prix_vente"] = g["CA"] / g["Quantite"]

    lignes = []
    for _, r in g.iterrows():
        cout, source = cout_matiere_produit(r["Produit"])
        if cout is None:
            continue
        marge = r["Prix_vente"] - cout
        fc = 100 * cout / r["Prix_vente"] if r["Prix_vente"] > 0 else np.nan
        # Alerte : prix de vente quasi nul (offert/lot) ou food-cost anormal.
        if r["Prix_vente"] < 1.0:
            alerte = "prix quasi nul (offert/lot ?)"
        elif fc > 45:
            alerte = "food-cost élevé — vérifier recette/prix"
        else:
            alerte = ""
        lignes.append({
            "Produit": r["Produit"], "Famille": r["Famille"],
            "Volume_recent": int(r["Quantite"]),
            "Prix_vente_MAD": round(float(r["Prix_vente"]), 2),
            "Cout_matiere_MAD": round(cout, 2),
            "Marge_MAD": round(float(marge), 2),
            "Marge_totale_MAD": round(float(marge) * float(r["Quantite"]), 0),
            "FoodCost_pct": round(fc, 1) if np.isfinite(fc) else np.nan,
            "Source_recette": source,
            "Alerte": alerte,
        })
    if not lignes:
        return None
    out = pd.DataFrame(lignes).sort_values("Volume_recent", ascending=False).reset_index(drop=True)
    return out


def generer_csv(chemin=None, fenetre_jours=FENETRE_PRIX):
    """Écrit exports/marges_produits.csv. Retourne le chemin ou None."""
    t = table_marges(fenetre_jours)
    if t is None or t.empty:
        logger.warning("[Marges] Rien à exporter (ventes journalières absentes ?).")
        return None
    chemin = chemin or os.path.join(RACINE, "exports", "marges_produits.csv")
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    t.to_csv(chemin, sep=";", index=False, encoding="utf-8")
    logger.info("[Marges] %d produits chiffrés → %s", len(t), chemin)
    return chemin


if __name__ == "__main__":
    generer_csv()

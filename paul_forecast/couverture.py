# -*- coding: utf-8 -*-
"""
Couverture des recettes : quels produits ont une recette EXACTE, et lesquels
reposent encore sur une estimation (détection auto par nom, ou recette générique
de famille) ou sur RIEN.

Le bon de commande matières premières n'est fiable que là où la recette est
exacte. Ailleurs, il est estimé (food-cost annoncé comme un « plancher »). Ce
module classe chaque produit et les PRIORISE par volume prévu × poids matière,
pour que la saisie des vraies recettes (par le chef) commence par les produits
qui pèsent le plus sur les achats — les 15-20 recettes qui font 80 % du besoin.

Priorité de détection identique au pipeline MRP (cf. _calculer_besoins_mrp) :
    recette exacte (data/recettes_exactes.json)
      > détection auto par motif du nom (bom.detecter_bom_produit)
      > recette générique de famille (bom.recette_generique_famille)
      > aucune (produit revendu tel quel, ou trou à combler).
"""

import pandas as pd

from . import config
from . import bom
from .logging_setup import get_logger

logger = get_logger()

SOURCES = ("exacte", "auto", "générique", "aucune")


def source_recette(produit, famille=None):
    """Classe un produit : 'exacte' | 'auto' | 'générique' | 'aucune'."""
    if config.BOM.get(produit):
        return "exacte"
    if bom.detecter_bom_produit(produit):
        return "auto"
    if bom.recette_generique_famille(produit, famille):
        return "générique"
    return "aucune"


def _poids_matiere_unitaire(produit, famille=None):
    """Poids matière (g) par unité vendue, selon la recette disponible.

    Somme des ingrédients en (g) après éclatement des semi-finis. Sert d'estimation
    d'impact sur les achats (0.0 si produit revendu tel quel / sans recette).
    """
    recette = (config.BOM.get(produit)
               or bom.detecter_bom_produit(produit)
               or bom.recette_generique_famille(produit, famille))
    if not recette:
        return 0.0
    eclatee = bom.normaliser_bom(bom.exploser_psf(recette))
    total = 0.0
    for ing, qte in eclatee.items():
        if "(g)" in str(ing):
            try:
                total += float(qte)
            except (TypeError, ValueError):
                continue
    return total


def rapport_couverture(volumes):
    """Classe et priorise les produits par impact sur les achats.

    volumes : DataFrame [Produit, Famille, Volume] (Volume = quantité prévue
    cumulée sur l'horizon, ex. somme de Qty_Prev).
    Retourne un DataFrame trié : Produit, Famille, Volume, Source,
    Poids_matiere_kg (Volume × poids unitaire), Prioritaire (source ≠ exacte
    et volume matière non nul), trié par Poids_matiere_kg décroissant.
    """
    colonnes = ["Produit", "Famille", "Volume", "Source", "Poids_matiere_kg", "Prioritaire"]
    if volumes is None or volumes.empty:
        return pd.DataFrame(columns=colonnes)
    lignes = []
    for _, r in volumes.iterrows():
        prod = str(r["Produit"])
        fam = str(r.get("Famille", "") or "")
        vol = float(r.get("Volume", 0.0) or 0.0)
        src = source_recette(prod, fam)
        poids_kg = vol * _poids_matiere_unitaire(prod, fam) / 1000.0
        lignes.append({
            "Produit": prod, "Famille": fam, "Volume": round(vol),
            "Source": src, "Poids_matiere_kg": round(poids_kg, 1),
            "Prioritaire": src != "exacte" and poids_kg > 0,
        })
    df = pd.DataFrame(lignes, columns=colonnes)
    return df.sort_values(["Prioritaire", "Poids_matiere_kg"],
                          ascending=[False, False]).reset_index(drop=True)


def synthese(volumes):
    """Compteurs par source + part du poids matière couverte par une recette exacte.

    Retourne {n_produits, par_source: {...}, poids_total_kg, poids_exact_kg,
    couverture_poids} — 'couverture_poids' = part des kg matières issue de
    recettes EXACTES (le reste est estimé).
    """
    rap = rapport_couverture(volumes)
    if rap.empty:
        return {"n_produits": 0, "par_source": {}, "poids_total_kg": 0.0,
                "poids_exact_kg": 0.0, "couverture_poids": 0.0}
    par_source = rap["Source"].value_counts().to_dict()
    poids_total = float(rap["Poids_matiere_kg"].sum())
    poids_exact = float(rap.loc[rap["Source"] == "exacte", "Poids_matiere_kg"].sum())
    couv = (poids_exact / poids_total) if poids_total > 0 else 0.0
    return {"n_produits": int(len(rap)),
            "par_source": {s: int(par_source.get(s, 0)) for s in SOURCES},
            "poids_total_kg": round(poids_total, 1),
            "poids_exact_kg": round(poids_exact, 1),
            "couverture_poids": round(couv, 3)}


def volumes_depuis_journalier(dfj):
    """Construit le DataFrame [Produit, Famille, Volume] depuis l'export journalier.

    Volume = somme de Qty_Prev sur l'horizon (demande prévue, hors commandes B2B
    ponctuelles qui ne reflètent pas la recette). Retourne None si indisponible.
    """
    if dfj is None or dfj.empty or "Produit" not in dfj.columns:
        return None
    df = dfj.copy()
    base = "Qty_Prev"
    if "Qty_Commande" in df.columns and base in df.columns:
        # ne pas gonfler le volume « récurrent » d'un produit avec une commande unique
        df["_v"] = pd.to_numeric(df[base], errors="coerce").fillna(0.0) \
                   - pd.to_numeric(df["Qty_Commande"], errors="coerce").fillna(0.0)
        df["_v"] = df["_v"].clip(lower=0.0)
    else:
        df["_v"] = pd.to_numeric(df.get(base, 0.0), errors="coerce").fillna(0.0)
    g = df.groupby("Produit").agg(Famille=("Famille", "last"), Volume=("_v", "sum"))
    return g.reset_index()

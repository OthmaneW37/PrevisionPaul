# -*- coding: utf-8 -*-
"""
Audit de la couverture des recettes par catégorie (BOULANGERIE, CUISINE,
PATISSERIE, VIENNOISERIE).

Pour chaque catégorie :
  - produits vendus (12 derniers mois de donnees_ventes/ventes_journalieres.csv)
  - croisement avec data/recettes_exactes.json + recettes_exactes_provenance.json
  - comptage {recette_exacte, estimation, manquante} et détail par source
  - % du volume vendu couvert par chaque niveau de fiabilité

Sorties :
  - tableau récapitulatif à l'écran
  - exports/audit_recettes_categories.csv (récap)
  - exports/produits_sans_recette.csv (produits sans recette, triés par volume)

Usage : python outils/audit_recettes_categories.py
"""

import os
import sys
import json

import pandas as pd

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)

from paul_forecast import bom, config          # noqa: E402

VENTES_CSV = os.path.join(_RACINE, "donnees_ventes", "ventes_journalieres.csv")
PROVENANCE_JSON = os.path.join(_RACINE, "data", "recettes_exactes_provenance.json")
EXPORTS = os.path.join(_RACINE, "exports")

# Mêmes règles que le pipeline (cf. paul_forecast/config.py).
SOURCES_EXACTES = config.SOURCES_RECETTE_EXACTE
CATEGORIES_AUDIT = config.CATEGORIES_BESOINS


def _charger_provenance():
    try:
        with open(PROVENANCE_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def classifier_produit(produit, famille, provenance):
    """Retourne (statut, source) : statut ∈ {recette_exacte, estimation, manquante}."""
    if produit in config.BOM:
        prov = provenance.get(produit)
        source = (prov.get("source") if isinstance(prov, dict) else str(prov or "")) \
                 or "sans provenance"
        statut = "recette_exacte" if source in SOURCES_EXACTES else "estimation"
        return statut, source
    if bom.detecter_bom_produit(produit):
        return "estimation", "détection par motif du nom"
    if bom.recette_generique_famille(produit, famille):
        return "estimation", "recette générique famille"
    return "manquante", "aucune"


def auditer(mois_historique=12):
    ventes = pd.read_csv(VENTES_CSV, sep=";", parse_dates=["Date"])
    fin = ventes["Date"].max()
    debut = fin - pd.DateOffset(months=mois_historique)
    ventes = ventes[ventes["Date"] >= debut]

    provenance = _charger_provenance()

    # Volume par produit (famille = la plus fréquente si plusieurs)
    vol = (ventes.groupby(["Produit", "Famille"], as_index=False)["Quantite"].sum()
           .sort_values("Quantite", ascending=False)
           .drop_duplicates("Produit"))

    lignes_produits = []
    for _, r in vol.iterrows():
        fam = str(r["Famille"]).strip().upper()
        if fam not in CATEGORIES_AUDIT:
            continue
        statut, source = classifier_produit(r["Produit"], fam, provenance)
        lignes_produits.append({"Categorie": fam, "Produit": r["Produit"],
                                "Volume_12m": r["Quantite"],
                                "Statut": statut, "Source": source})
    dfp = pd.DataFrame(lignes_produits)

    # ── Récapitulatif par catégorie ──────────────────────────────────────────
    recap = []
    for cat in CATEGORIES_AUDIT:
        d = dfp[dfp["Categorie"] == cat]
        tot_v = d["Volume_12m"].sum()
        ligne = {"Categorie": cat, "Nb_produits": len(d)}
        for statut in ("recette_exacte", "estimation", "manquante"):
            m = d["Statut"] == statut
            ligne[f"Nb_{statut}"] = int(m.sum())
            ligne[f"%vol_{statut}"] = round(
                d.loc[m, "Volume_12m"].sum() / tot_v * 100, 1) if tot_v else 0.0
        recap.append(ligne)
    df_recap = pd.DataFrame(recap)

    # ── Détail par source de provenance ──────────────────────────────────────
    df_sources = (dfp.groupby(["Categorie", "Source"], as_index=False)
                  .agg(Nb_produits=("Produit", "count"),
                       Volume_12m=("Volume_12m", "sum"))
                  .sort_values(["Categorie", "Volume_12m"], ascending=[True, False]))

    # ── Produits sans recette, priorisés par volume ───────────────────────────
    df_manquants = (dfp[dfp["Statut"] == "manquante"]
                    .sort_values(["Categorie", "Volume_12m"], ascending=[True, False])
                    [["Categorie", "Produit", "Volume_12m"]])

    return df_recap, df_sources, df_manquants, dfp


def main():
    df_recap, df_sources, df_manquants, _ = auditer()

    print("\n=== COUVERTURE RECETTES PAR CATÉGORIE (12 derniers mois) ===")
    print(df_recap.to_string(index=False))
    print("\n=== DÉTAIL PAR SOURCE ===")
    print(df_sources.to_string(index=False))
    print(f"\n=== PRODUITS SANS RECETTE ({len(df_manquants)}) — top 15 par volume ===")
    print(df_manquants.head(15).to_string(index=False))

    os.makedirs(EXPORTS, exist_ok=True)
    df_recap.to_csv(os.path.join(EXPORTS, "audit_recettes_categories.csv"),
                    index=False, sep=";", encoding="utf-8")
    df_manquants.to_csv(os.path.join(EXPORTS, "produits_sans_recette.csv"),
                        index=False, sep=";", encoding="utf-8")
    print(f"\nExports écrits : {EXPORTS}\\audit_recettes_categories.csv, "
          f"produits_sans_recette.csv")


if __name__ == "__main__":
    main()

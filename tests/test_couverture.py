# -*- coding: utf-8 -*-
"""Tests du rapport de couverture des recettes (priorisation de la saisie)."""
import pandas as pd

from paul_forecast import couverture as CV
from paul_forecast import config

# « FICELLE ESSAI » : reconnu par motif (branche flûte/ficelle de detecter_bom_produit)
# mais absent des recettes exactes → source « auto » quelle que soit la config réelle.
AUTO = "FICELLE ESSAI 250GR"


def test_source_recette_exacte(monkeypatch):
    monkeypatch.setattr(config, "BOM", {"PRODUIT EXACT": {"Farine de blé T65 (g)": 100.0}})
    assert CV.source_recette("PRODUIT EXACT", "BOULANGERIE") == "exacte"


def test_source_recette_auto(monkeypatch):
    monkeypatch.setattr(config, "BOM", {})
    assert CV.source_recette(AUTO, "BOULANGERIE") == "auto"


def test_source_recette_generique(monkeypatch):
    monkeypatch.setattr(config, "BOM", {})
    # nom non reconnu mais famille homogène → recette générique de famille
    assert CV.source_recette("ZZZ INCONNU", "BOULANGERIE") == "générique"


def test_source_recette_aucune(monkeypatch):
    monkeypatch.setattr(config, "BOM", {})
    # boisson achetée/revendue : aucune recette de production
    assert CV.source_recette("COCA COLA 33CL", "BEVERAGE") == "aucune"


def test_rapport_priorise_par_poids_matiere(monkeypatch):
    monkeypatch.setattr(config, "BOM", {"PROD EXACT": {"Farine de blé T65 (g)": 100.0}})
    volumes = pd.DataFrame({
        "Produit": [AUTO, "PROD EXACT", "COCA COLA 33CL"],
        "Famille": ["BOULANGERIE", "BOULANGERIE", "BEVERAGE"],
        "Volume": [1000, 1000, 1000],
    })
    rap = CV.rapport_couverture(volumes)
    # le produit exact n'est jamais prioritaire ; la boisson sans recette non plus
    assert rap.loc[rap["Produit"] == "PROD EXACT", "Prioritaire"].iloc[0] == False
    assert rap.loc[rap["Produit"] == "COCA COLA 33CL", "Prioritaire"].iloc[0] == False
    # le produit auto (avec poids matière) est prioritaire et en tête
    assert rap.iloc[0]["Produit"] == AUTO
    assert rap.iloc[0]["Prioritaire"] == True
    assert rap.iloc[0]["Poids_matiere_kg"] > 0


def test_synthese_couverture(monkeypatch):
    monkeypatch.setattr(config, "BOM", {"PROD EXACT": {"Farine de blé T65 (g)": 100.0}})
    volumes = pd.DataFrame({
        "Produit": [AUTO, "PROD EXACT"],
        "Famille": ["BOULANGERIE", "BOULANGERIE"],
        "Volume": [1000, 1000],
    })
    syn = CV.synthese(volumes)
    assert syn["n_produits"] == 2
    assert syn["par_source"]["exacte"] == 1
    assert syn["par_source"]["auto"] == 1
    assert 0.0 <= syn["couverture_poids"] <= 1.0


def test_volumes_exclut_commandes():
    # une commande B2B ponctuelle ne doit pas gonfler le volume récurrent
    dfj = pd.DataFrame({
        "Produit": ["FLUTE 250GR", "FLUTE 250GR"],
        "Famille": ["BOULANGERIE", "BOULANGERIE"],
        "Qty_Prev": [600.0, 100.0],
        "Qty_Commande": [500.0, 0.0],
    })
    vol = CV.volumes_depuis_journalier(dfj)
    # 600-500=100 puis 100-0=100 → 200 (et non 700)
    assert vol.loc[vol["Produit"] == "FLUTE 250GR", "Volume"].iloc[0] == 200.0

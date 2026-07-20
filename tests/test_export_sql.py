# -*- coding: utf-8 -*-
"""Tests de l'export des ventes depuis la base SQL d'Elyx (fusion + familles).

La connexion SQL elle-même n'est pas testée ici (elle dépend du serveur) :
on teste la logique pure — fusion des extraits SQL dans le consolidé et
rattachement Produit/Famille.
"""
import pandas as pd

from outils.exporter_ventes_sql import completer_produit_famille, fusionner, COLONNES


def _consolide():
    return pd.DataFrame([
        {"Date": "2026-06-28", "Code": 2552, "Produit": "FLUTE 250GR",
         "Famille": "BOULANGERIE", "Quantite": 100.0, "CA_TTC": 500.0},
        {"Date": "2026-06-29", "Code": 2552, "Produit": "FLUTE 250GR",
         "Famille": "BOULANGERIE", "Quantite": 110.0, "CA_TTC": 550.0},
        {"Date": "2026-06-29", "Code": 1, "Produit": "CROISSANT BEURRE",
         "Famille": "VIENNOISERIE", "Quantite": 80.0, "CA_TTC": 400.0},
    ])


def test_fusion_remplace_les_dates_sql():
    """Les lignes SQL remplacent TOUTES les lignes du consolidé aux mêmes dates."""
    sql = pd.DataFrame([
        {"Date": "2026-06-29", "Code": 2552, "Produit": "FLUTE 250GR",
         "Famille": "BOULANGERIE", "Quantite": 120.0, "CA_TTC": 600.0},
    ])
    fusion = fusionner(_consolide(), sql)
    j29 = fusion[fusion["Date"] == "2026-06-29"]
    # le 29, seule la ligne SQL subsiste (le croissant historique disparaît :
    # SQL fait foi pour la journée entière)
    assert len(j29) == 1
    assert float(j29["Quantite"].iloc[0]) == 120.0
    # le 28 (hors périmètre SQL) est intact
    j28 = fusion[fusion["Date"] == "2026-06-28"]
    assert len(j28) == 1 and float(j28["Quantite"].iloc[0]) == 100.0


def test_fusion_extrait_vide_sans_effet():
    vide = pd.DataFrame(columns=COLONNES)
    fusion = fusionner(_consolide(), vide)
    assert len(fusion) == len(_consolide())


def test_fusion_idempotente():
    sql = pd.DataFrame([
        {"Date": "2026-06-30", "Code": 2552, "Produit": "FLUTE 250GR",
         "Famille": "BOULANGERIE", "Quantite": 90.0, "CA_TTC": 450.0},
    ])
    une_fois = fusionner(_consolide(), sql)
    deux_fois = fusionner(une_fois, sql)
    assert len(une_fois) == len(deux_fois)


def test_famille_priorite_historique_puis_elyx_puis_autres():
    df_sql = pd.DataFrame([
        {"Date": "2026-06-30", "Code": 2552, "Produit": "",
         "Quantite": 90.0, "CA_TTC": 450.0},      # connu de l'historique
        {"Date": "2026-06-30", "Code": 9001, "Produit": "TARTE NOUVELLE",
         "Quantite": 5.0, "CA_TTC": 100.0},        # connu d'Elyx seulement
        {"Date": "2026-06-30", "Code": 9999, "Produit": "",
         "Quantite": 1.0, "CA_TTC": 10.0},         # inconnu partout
    ])
    referentiel = {9001: ("TARTE NOUVELLE", "PATISSERIE"),
                   9999: ("", None)}
    mapping_histo = {2552: ("FLUTE 250GR", "BOULANGERIE")}
    res = completer_produit_famille(df_sql, referentiel, mapping_histo)

    assert list(res.columns) == COLONNES
    ligne = res.set_index("Code")
    assert ligne.loc[2552, "Produit"] == "FLUTE 250GR"       # nom repris de l'historique
    assert ligne.loc[2552, "Famille"] == "BOULANGERIE"
    assert ligne.loc[9001, "Famille"] == "PATISSERIE"        # famille Elyx
    assert ligne.loc[9999, "Produit"] == "ARTICLE 9999"      # repli
    assert ligne.loc[9999, "Famille"] == "Autres"

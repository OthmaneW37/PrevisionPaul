# -*- coding: utf-8 -*-
"""Tests du chiffrage des matières (prix, unités, budget)."""
import pandas as pd

from paul_forecast import couts


def test_prix_unitaire_farine_et_beurre():
    assert couts.prix_unitaire("Farine de blé T65 (g)") is not None
    assert couts.prix_unitaire("Beurre 84% MG (g)") > couts.prix_unitaire("Sucre (g)")


def test_cout_solide_en_grammes():
    # 1000 g × prix/kg = prix/kg
    p = couts.prix_unitaire("Sucre (g)")
    assert abs(couts.cout_ligne("Sucre (g)", 1000) - p) < 1e-6


def test_cout_liquide_en_ml():
    p = couts.prix_unitaire("Lait entier (ml)")
    assert abs(couts.cout_ligne("Lait entier (ml)", 1000) - p) < 1e-6


def test_sachet_the_est_un_emballage_pas_du_the():
    # régression : « sachet » doit primer sur « thé » (emballage, pas thé en vrac)
    c = couts.cout_ligne("Sachet thé (unité)", 100)
    assert c < 100          # 100 sachets ne coûtent pas 12 000 MAD


def test_chiffrer_besoins_total_positif():
    df = pd.DataFrame({"Ingredient": ["Farine de blé T65 (g)", "Beurre 84% MG (g)", "Truc inconnu (g)"],
                       "Quantite_Requise": [100000.0, 10000.0, 5.0]})
    out, total, couv = couts.chiffrer_besoins(df)
    assert total > 0
    assert 0 < couv <= 1
    assert "Cout_MAD" in out.columns

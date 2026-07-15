# -*- coding: utf-8 -*-
"""Tests du module marges + du repli générique par famille (bom)."""
import pytest

from paul_forecast import bom, marges


# ── Repli générique par famille (longue traîne) ─────────────────────────────────
def test_generique_boulangerie_grammage_dans_le_nom():
    r = bom.recette_generique_famille("PAIN SPECIAL 200GR", "BOULANGERIE")
    assert r["Farine de blé T65 (g)"] == pytest.approx(144.0)   # 0.72 × 200
    assert r["Sel (g)"] == pytest.approx(3.6)


def test_generique_viennoiserie_defaut_70g():
    r = bom.recette_generique_famille("DELICE INCONNU", "VIENNOISERIE")
    assert r["Pâte à croissant"] == pytest.approx(63.0)          # 0.9 × 70


def test_generique_patisserie_contient_les_bases():
    r = bom.recette_generique_famille("ENTREMET MYSTERE", "PATISSERIE")
    assert set(r) >= {"Farine de blé T55 (g)", "Sucre (g)", "Œufs (g)",
                      "Beurre 84% MG (g)"}


def test_generique_cuisine_volontairement_vide():
    # CUISINE trop hétérogène : on préfère ne rien estimer qu'estimer faux.
    assert bom.recette_generique_famille("ACCOMPAGNEMENT NOUILLES", "CUISINE") == {}


def test_generique_famille_inconnue_vide():
    assert bom.recette_generique_famille("X", None) == {}
    assert bom.recette_generique_famille("X", "BEVERAGE") == {}


def test_generique_eclatable_et_chiffrable():
    # La recette générique doit passer la chaîne PSF → normalisation sans erreur.
    r = bom.recette_generique_famille("GATEAU 90GR", "PATISSERIE")
    base = bom.normaliser_bom(bom.exploser_psf(r))
    assert base and all(q > 0 for q in base.values())


# ── Marges ───────────────────────────────────────────────────────────────────
def test_cout_matiere_produit_exact_connu():
    # FLUTE 250GR a une recette exacte → coût > 0, source 'exact'
    cout, source = marges.cout_matiere_produit("FLUTE 250GR")
    assert source == "exact"
    assert cout is not None and 0.1 < cout < 5.0    # ~0.9 MAD, ordre de grandeur


def test_cout_matiere_produit_inconnu():
    cout, source = marges.cout_matiere_produit("PRODUIT QUI N EXISTE PAS 12345")
    assert cout is None


def test_table_marges_structure():
    t = marges.table_marges()
    if t is None:        # ventes journalières absentes sur cette machine
        pytest.skip("ventes_journalieres.csv absent")
    assert {"Produit", "Prix_vente_MAD", "Cout_matiere_MAD", "Marge_MAD",
            "Marge_totale_MAD", "FoodCost_pct", "Alerte"} <= set(t.columns)
    # marge = prix − coût (cohérence interne)
    ligne = t.iloc[0]
    assert ligne["Marge_MAD"] == pytest.approx(
        ligne["Prix_vente_MAD"] - ligne["Cout_matiere_MAD"], abs=0.05)

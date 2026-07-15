# -*- coding: utf-8 -*-
"""Tests de la nomenclature : alias matières, exclusion de l'eau, éclatement PSF."""
from paul_forecast import bom


def test_normaliser_alias_farine():
    r = bom.normaliser_bom({"Farine T65": 100.0, "Sel": 2.0})
    # « Farine T65 » doit être harmonisé vers la forme canonique
    assert any("Farine de blé T65" in k for k in r), r


def test_normaliser_exclut_eau():
    r = bom.normaliser_bom({"Eau": 50.0, "Farine T65": 100.0})
    assert not any(k.strip().lower().startswith("eau") for k in r), r


def test_normaliser_fusionne_doublons():
    # deux variantes de la même matière doivent être sommées en une seule ligne
    r = bom.normaliser_bom({"Farine T65": 100.0, "Farine de blé T65 (g)": 50.0})
    farines = [v for k, v in r.items() if "Farine de blé T65" in k]
    assert len(farines) == 1 and abs(farines[0] - 150.0) < 1e-6


def test_exploser_psf_produit_de_la_farine():
    # une recette contenant un PSF « Pâte à croissant » doit être éclatée en matières de base
    out = bom.exploser_psf({"Pâte à croissant": 100.0})
    assert any("farine" in k.lower() for k in out), out
    assert sum(out.values()) > 0


def test_exploser_psf_matiere_de_base_inchangee():
    # une matière déjà de base (non-PSF) reste telle quelle
    out = bom.exploser_psf({"Sucre (g)": 30.0})
    assert out.get("Sucre (g)") == 30.0

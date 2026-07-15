# -*- coding: utf-8 -*-
"""Tests des fourchettes de prévision / stock de sécurité."""
import pandas as pd

from paul_forecast import incertitude as I


def test_z_service_croissant():
    assert I.z_service(0.90) < I.z_service(0.95) < I.z_service(0.99)


def test_ajouter_intervalles_encadre_et_selargit():
    df = pd.DataFrame({"Date": pd.date_range("2026-01-31", periods=6, freq="ME"),
                       "Qty_Prev_Selection": [100.0] * 6})
    d = {"P": df}
    I.ajouter_intervalles(d, sigma_map={"P": 10.0}, niveau=0.95)
    out = d["P"]
    assert "Qty_Selection_Bas" in out.columns and "Qty_Selection_Haut" in out.columns
    # la prévision est bien encadrée
    assert (out["Qty_Selection_Bas"] <= out["Qty_Prev_Selection"]).all()
    assert (out["Qty_Selection_Haut"] >= out["Qty_Prev_Selection"]).all()
    # bornes jamais négatives
    assert (out["Qty_Selection_Bas"] >= 0).all()
    # l'incertitude s'élargit avec l'horizon (σ·√h)
    largeur = out["Qty_Selection_Haut"] - out["Qty_Selection_Bas"]
    assert largeur.iloc[-1] > largeur.iloc[0]


def test_ajouter_intervalles_repli_sans_sigma():
    """Sans σ mesuré, on retombe sur une marge relative (produit inconnu)."""
    df = pd.DataFrame({"Date": pd.date_range("2026-01-31", periods=3, freq="ME"),
                       "Qty_Prev_Selection": [200.0, 200.0, 200.0]})
    d = {"NOUVEAU": df}
    I.ajouter_intervalles(d, sigma_map={}, niveau=0.95)
    out = d["NOUVEAU"]
    assert out["Qty_Selection_Haut"].iloc[0] > 200.0   # une marge a bien été appliquée

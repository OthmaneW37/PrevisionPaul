# -*- coding: utf-8 -*-
"""Tests de l'explication des pics/creux des courbes."""
import numpy as np
import pandas as pd

from paul_forecast import explications as E


def test_detecte_un_pic():
    dates = pd.date_range("2024-01-31", periods=24, freq="ME")
    val = np.full(24, 100.0)
    val[12] = 200.0                       # pic net au milieu
    pts = E.expliquer_points(dates, val, seuil=0.2)
    pics = [p for p in pts if p["sens"] == "pic"]
    assert pics, "aucun pic détecté"
    assert pd.Timestamp(pics[0]["date"]).month == dates[12].month


def test_detecte_un_creux():
    dates = pd.date_range("2024-01-31", periods=24, freq="ME")
    val = np.full(24, 100.0)
    val[10] = 30.0                        # creux net
    pts = E.expliquer_points(dates, val, seuil=0.2)
    assert any(p["sens"] == "creux" for p in pts)


def test_serie_plate_sans_alerte():
    dates = pd.date_range("2024-01-31", periods=24, freq="ME")
    pts = E.expliquer_points(dates, np.full(24, 100.0), seuil=0.2)
    assert pts == []

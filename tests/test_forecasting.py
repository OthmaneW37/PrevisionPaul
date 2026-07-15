# -*- coding: utf-8 -*-
"""Tests des modèles de prévision supplémentaires (Theta, ARIMA, Croston, Moyenne)."""
import numpy as np

from paul_forecast import forecasting as F


def _serie():
    return np.array([10, 12, 8, 11, 9, 13, 10, 12, 9, 11, 10, 12] * 4, dtype=float)


def test_prevision_modele_longueur_et_positif():
    y = _serie()
    for nom in ("Theta", "ARIMA", "Croston", "Moyenne"):
        r = F.prevision_modele(y, 3, "ME", nom)
        assert r is not None, f"{nom} a renvoyé None"
        assert len(r) == 3
        assert (np.asarray(r) >= 0).all()


def test_prevision_modele_inconnu_renvoie_none():
    assert F.prevision_modele(_serie(), 3, "ME", "Inexistant") is None


def test_moyenne_vaut_la_moyenne():
    y = _serie()
    r = F.prevision_modele(y, 2, "ME", "Moyenne")
    assert np.allclose(r, y.mean())


def test_croston_intermittent_positif_et_lisse():
    y = np.array([0, 0, 5, 0, 0, 0, 3, 0, 0, 4, 0, 0], dtype=float)
    v = F._croston(y)
    assert v > 0
    assert v < 5           # doit lisser (< la plus grosse demande ponctuelle)


def test_croston_tout_zero():
    assert F._croston(np.zeros(10)) == 0.0

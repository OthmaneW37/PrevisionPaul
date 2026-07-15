# -*- coding: utf-8 -*-
"""Tests de la couche fêtes (fraction du mois, fusion des profils mesurés)."""
import pandas as pd

from paul_forecast import saisonnalite_fetes as SF


def test_fraction_mois_pleine():
    fen = [(pd.Timestamp("2026-02-01"), pd.Timestamp("2026-02-28"))]
    assert SF._fraction_du_mois(pd.Timestamp("2026-02-28"), fen) == 1.0


def test_fraction_mois_partielle():
    fen = [(pd.Timestamp("2026-02-01"), pd.Timestamp("2026-02-14"))]
    f = SF._fraction_du_mois(pd.Timestamp("2026-02-28"), fen)
    assert 0.4 < f < 0.6           # 14 jours sur 28


def test_fraction_mois_hors_periode():
    fen = [(pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-10"))]
    assert SF._fraction_du_mois(pd.Timestamp("2026-02-28"), fen) == 0.0


def test_profils_effectifs_ramadan_present():
    p = SF._profils_effectifs()
    assert isinstance(p, dict)
    assert "ramadan" in p and p["ramadan"]["ratios"], "profil ramadan manquant"

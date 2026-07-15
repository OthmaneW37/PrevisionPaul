# -*- coding: utf-8 -*-
"""Tests du suivi quotidien prévu vs réel (reconstitution hors-échantillon)."""
import numpy as np
import pandas as pd

from paul_forecast import suivi as SU
from paul_forecast import forecast_journalier as FJ


def _ventes(produit, quantite, fin="2026-06-28", jours=420, famille="BOULANGERIE"):
    fin = pd.Timestamp(fin)
    dates = pd.date_range(fin - pd.Timedelta(days=jours - 1), fin)
    q = np.full(len(dates), float(quantite)) if np.isscalar(quantite) else quantite
    return pd.DataFrame({"Date": dates, "Code": 1, "Produit": produit,
                         "Famille": famille, "Quantite": q, "CA_TTC": 0.0})


def test_produit_stable_faible_erreur(monkeypatch):
    """Un produit parfaitement stable doit être reconstitué avec un écart minime."""
    df = _ventes("STABLE", 100.0)
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(SU.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(SU.mod_commandes, "jours_proteges", lambda: set())

    comp = SU.comparer_prevu_reel(n_jours=14)
    assert comp is not None and not comp.empty
    assert set(comp.columns) >= {"Date", "Produit", "Famille", "Prev", "Reel"}
    m = SU.metriques_globales(comp)
    assert m["n_jours"] == 14
    assert m["wmape"] < 0.10, f"écart anormal sur un produit stable (wMAPE={m['wmape']:.2f})"
    assert abs(m["biais_pct"]) < 10


def test_pas_de_fuite_temporelle(monkeypatch):
    """Le réel des jours évalués ne doit pas influencer la prévision : un pic
    UNIQUEMENT dans la fenêtre évaluée reste non anticipé (Prev << Reel ce jour)."""
    q = np.full(420, 100.0)
    df = _ventes("PIC", q)
    fin = pd.Timestamp("2026-06-28")
    jour_pic = fin - pd.Timedelta(days=3)
    df.loc[df["Date"] == jour_pic, "Quantite"] = 1000.0     # pic dans la fenêtre
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(SU.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(SU.mod_commandes, "jours_proteges", lambda: set())

    comp = SU.comparer_prevu_reel(n_jours=14)
    ligne = comp[comp["Date"] == jour_pic].iloc[0]
    assert ligne["Reel"] == 1000.0
    assert ligne["Prev"] < 300, "le modèle a « vu » le futur (fuite)"


def test_resume_et_biais():
    """Agrégations déterministes sur une comparaison fabriquée."""
    dates = pd.date_range("2026-06-01", periods=3)
    comp = pd.DataFrame({
        "Date": list(dates) * 2,
        "Produit": ["A"] * 3 + ["B"] * 3,
        "Famille": ["BOULANGERIE"] * 6,
        "Prev": [130, 130, 130, 100, 100, 100],   # A sur-produit, B pile
        "Reel": [100, 100, 100, 100, 100, 100],
    })
    res = SU.resume_journalier(comp)
    assert list(res["Prev"]) == [230, 230, 230]
    assert list(res["Reel"]) == [200, 200, 200]
    assert round(res["Ecart_pct"].iloc[0]) == 15

    biais = SU.biais_par_produit(comp, min_volume_jour=5, seuil_biais=15)
    ra = biais[biais["Produit"] == "A"].iloc[0]
    assert ra["Biais"] == "+30%" and ra["Sens"] == "Sur-production"
    rb = biais[biais["Produit"] == "B"].iloc[0]
    assert rb["Sens"] == "OK"
    # A a le plus gros impact → en tête
    assert biais.iloc[0]["Produit"] == "A"


def test_metriques_vide():
    assert SU.metriques_globales(pd.DataFrame()) == {
        "wmape": None, "biais_pct": None, "n_jours": 0, "n_produits": 0}
    assert SU.resume_journalier(None).empty
    assert SU.biais_par_produit(None).empty

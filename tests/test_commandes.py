# -*- coding: utf-8 -*-
"""Tests du module commandes : détection des pics « possible commande »,
neutralisation dans l'apprentissage, et injection des commandes planifiées
dans les prévisions journalières et mensuelles."""
import numpy as np
import pandas as pd
import pytest

from paul_forecast import commandes as CMD
from paul_forecast import forecast_journalier as FJ


def _serie(jours=90, niveau=100.0, fin="2026-06-28"):
    fin = pd.Timestamp(fin)
    idx = pd.date_range(fin - pd.Timedelta(days=jours - 1), fin)
    return pd.Series(niveau, index=idx)


# ── Détection ──────────────────────────────────────────────────────────────────
def test_pic_isole_detecte():
    s = _serie()
    s.iloc[40] = 500.0                        # grosse commande un jour isolé
    pics = CMD._pics_serie(s, ratio_seuil=2.2, exces_min=30)
    assert list(pics.index) == [s.index[40]]
    assert pics["Quantite"].iloc[0] == 500.0
    assert pics["Attendu"].iloc[0] == pytest.approx(100.0)


def test_changement_de_regime_non_detecte():
    # un client récurrent double le niveau durablement : la médiane glissante
    # suit → aucun jour marqué « possible commande »
    s = _serie(jours=120)
    s.iloc[60:] = 1000.0
    pics = CMD._pics_serie(s, ratio_seuil=2.2, exces_min=30)
    assert pics.empty


def test_produit_rare_grosse_vente_detectee():
    # produit quasi jamais vendu : 40 unités d'un coup = commande probable
    s = _serie(niveau=0.0)
    s.iloc[50] = 40.0
    pics = CMD._pics_serie(s, ratio_seuil=2.2, exces_min=30)
    assert list(pics.index) == [s.index[50]]


def test_petit_pic_ignore():
    # ratio dépassé mais excès < 30 unités : pas signifiant
    s = _serie(niveau=10.0)
    s.iloc[40] = 30.0
    pics = CMD._pics_serie(s, ratio_seuil=2.2, exces_min=30)
    assert pics.empty


# ── Neutralisation ─────────────────────────────────────────────────────────────
def test_nettoyer_serie_ramene_le_pic_au_niveau_attendu():
    s = _serie()
    jour = s.index[40]
    s.loc[jour] = 500.0
    propre = CMD.nettoyer_serie(s, proteges=set())
    assert propre.loc[jour] == pytest.approx(100.0)
    # les autres jours ne bougent pas
    assert (propre.drop(jour) == s.drop(jour)).all()


def test_nettoyer_serie_respecte_jours_proteges():
    s = _serie()
    jour = s.index[40]
    s.loc[jour] = 500.0                       # pic un jour de fête → légitime
    propre = CMD.nettoyer_serie(s, proteges={jour})
    assert propre.loc[jour] == 500.0


def test_detecter_pics_exclut_fetes(monkeypatch):
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=89), fin)
    q = np.full(len(dates), 100.0)
    q[40] = 500.0                             # pic protégé (fête)
    q[60] = 480.0                             # pic libre → détecté
    df = pd.DataFrame({"Date": dates, "Produit": "FLUTE 250GR",
                       "Famille": "BOULANGERIE", "Quantite": q})
    monkeypatch.setattr(CMD, "jours_proteges", lambda: {dates[40]})
    pics = CMD.detecter_pics(df)
    assert list(pics["Date"]) == [dates[60]]
    assert pics["Produit"].iloc[0] == "FLUTE 250GR"


# ── Injection journalière ──────────────────────────────────────────────────────
def _prev_journalier(jours=7, debut="2026-07-01"):
    dates = pd.date_range(debut, periods=jours)
    return pd.DataFrame({"Date": list(dates), "Code": "2552",
                         "Produit": "FLUTE 250GR", "Famille": "BOULANGERIE",
                         "Qty_Prev": 100.0, "Qty_Recommandee": 120.0,
                         "Fiabilite": "Fiable"})


def test_ajout_commande_journaliere(monkeypatch):
    prev = _prev_journalier()
    monkeypatch.setattr(CMD, "charger_commandes", lambda: [
        {"date": "2026-07-03", "produit": "FLUTE 250GR", "quantite": 500,
         "client": "Fast-food"}])
    out = CMD.ajouter_commandes_journalier(prev)
    jour = out[out["Date"] == pd.Timestamp("2026-07-03")].iloc[0]
    assert jour["Qty_Prev"] == 600.0          # 100 + 500, sans marge sur la commande
    assert jour["Qty_Recommandee"] == 620.0   # 120 + 500
    assert jour["Qty_Commande"] == 500.0
    autres = out[out["Date"] != pd.Timestamp("2026-07-03")]
    assert (autres["Qty_Prev"] == 100.0).all()
    assert (autres["Qty_Commande"] == 0.0).all()


def test_commande_produit_inconnu_cree_une_ligne(monkeypatch):
    prev = _prev_journalier()
    monkeypatch.setattr(CMD, "charger_commandes", lambda: [
        {"date": "2026-07-04", "produit": "PRODUIT SPECIAL", "quantite": 80}])
    out = CMD.ajouter_commandes_journalier(prev)
    ligne = out[out["Produit"] == "PRODUIT SPECIAL"]
    assert len(ligne) == 1
    assert ligne["Qty_Recommandee"].iloc[0] == 80.0
    assert ligne["Fiabilite"].iloc[0] == "Commande"


def test_commande_hors_horizon_ignoree(monkeypatch):
    prev = _prev_journalier()
    monkeypatch.setattr(CMD, "charger_commandes", lambda: [
        {"date": "2026-09-15", "produit": "FLUTE 250GR", "quantite": 500}])
    out = CMD.ajouter_commandes_journalier(prev)
    assert (out["Qty_Commande"] == 0.0).all()
    assert (out["Qty_Prev"] == 100.0).all()


# ── Injection mensuelle (pipeline → plan de production + matières) ────────────
def test_ajout_commande_mensuelle(monkeypatch):
    dates = pd.date_range("2026-07-31", periods=3, freq="ME")
    df_fc = pd.DataFrame({"Date": dates,
                          "Qty_Prev_Selection": [1000.0, 1000.0, 1000.0],
                          "Qty_Prev_Holt_Winters": [900.0, 900.0, 900.0],
                          "Rev_Prev_Selection": [5000.0] * 3,
                          "Qty_Selection_Bas": [800.0] * 3,
                          "Qty_Selection_Haut": [1200.0] * 3})
    monkeypatch.setattr(CMD, "charger_commandes", lambda: [
        {"date": "2026-07-07", "produit": "FLUTE 250GR", "quantite": 500},
        {"date": "2026-07-20", "produit": "INCONNU", "quantite": 99}])
    appliquees = CMD.ajouter_commandes_mensuelles({"FLUTE 250GR": df_fc})
    assert len(appliquees) == 1
    assert df_fc["Qty_Prev_Selection"].tolist() == [1500.0, 1000.0, 1000.0]
    assert df_fc["Qty_Prev_Holt_Winters"].tolist() == [1400.0, 900.0, 900.0]
    assert df_fc["Qty_Selection_Haut"].tolist() == [1700.0, 1200.0, 1200.0]
    # le CA n'est pas modifié (prix de la commande inconnu)
    assert (df_fc["Rev_Prev_Selection"] == 5000.0).all()


# ── Bout en bout : prevoir() ───────────────────────────────────────────────────
def _ventes_stables(fin, jours=420, niveau=10):
    dates = pd.date_range(fin - pd.Timedelta(days=jours - 1), fin)
    return pd.DataFrame({"Date": dates, "Code": 1, "Produit": "STABLE",
                         "Famille": "BOULANGERIE",
                         "Quantite": float(niveau), "CA_TTC": 0.0})


def test_prevoir_insensible_a_un_pic_type_commande(monkeypatch):
    fin = pd.Timestamp("2026-06-28")
    df = _ventes_stables(fin)
    # énorme commande il y a 10 jours : sans neutralisation, le niveau récent
    # (moyenne 28 j) et donc la prévision seraient gonflés
    df.loc[df["Date"] == fin - pd.Timedelta(days=10), "Quantite"] = 600.0
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(CMD, "jours_proteges", lambda: set())
    monkeypatch.setattr(CMD, "charger_commandes", lambda: [])

    prev = FJ.prevoir(horizon_jours=7)
    assert prev is not None and not prev.empty
    # la prévision reste proche du niveau boutique (10/j), pas du pic
    assert prev["Qty_Prev"].max() <= 20


def test_prevoir_ajoute_commande_planifiee(monkeypatch):
    fin = pd.Timestamp("2026-06-28")
    df = _ventes_stables(fin)
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(CMD, "jours_proteges", lambda: set())
    monkeypatch.setattr(CMD, "charger_commandes", lambda: [
        {"date": "2026-07-03", "produit": "STABLE", "quantite": 300,
         "client": "Fast-food"}])

    prev = FJ.prevoir(horizon_jours=7)
    assert "Qty_Commande" in prev.columns
    jour = prev[(prev["Date"] == pd.Timestamp("2026-07-03")) &
                (prev["Produit"] == "STABLE")].iloc[0]
    assert jour["Qty_Commande"] == 300.0
    # la commande est incluse telle quelle dans la prévision et la recommandation
    assert jour["Qty_Prev"] >= 300.0
    assert jour["Qty_Recommandee"] >= jour["Qty_Prev"]
    autres = prev[prev["Date"] != pd.Timestamp("2026-07-03")]
    assert (autres["Qty_Commande"] == 0.0).all()
    assert autres["Qty_Prev"].max() <= 20

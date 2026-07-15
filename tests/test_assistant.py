# -*- coding: utf-8 -*-
"""Tests de l'assistant LOCAL (déterministe, hors ligne) : outils ancrés sur les
données et couche de réponse `interroger`. Aucun appel réseau."""
import pandas as pd
import pytest

from paul_forecast import assistant as A


# ── Données synthétiques ───────────────────────────────────────────────────────
@pytest.fixture
def ventes():
    dates = pd.date_range("2026-05-01", "2026-06-28")
    rows = []
    for d in dates:
        rows.append({"Date": d, "Code": 2552, "Produit": "FLUTE 250GR",
                     "Famille": "BOULANGERIE", "Quantite": 800.0, "CA_TTC": 0.0})
        rows.append({"Date": d, "Code": 1, "Produit": "CROISSANT BEURRE",
                     "Famille": "VIENNOISERIE", "Quantite": 200.0, "CA_TTC": 0.0})
    return pd.DataFrame(rows)


@pytest.fixture
def prev():
    dates = pd.date_range("2026-06-29", periods=7)
    rows = []
    for d in dates:
        rows.append({"Date": d, "Produit": "FLUTE 250GR", "Famille": "BOULANGERIE",
                     "Qty_Prev": 1000.0, "Qty_Recommandee": 1200.0, "Qty_Commande": 0.0,
                     "Fiabilite": "Moyen"})
        rows.append({"Date": d, "Produit": "CROISSANT BEURRE", "Famille": "VIENNOISERIE",
                     "Qty_Prev": 210.0, "Qty_Recommandee": 250.0, "Qty_Commande": 0.0,
                     "Fiabilite": "Fiable"})
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _brancher(monkeypatch, ventes, prev):
    monkeypatch.setattr(A.fj, "charger_ventes", lambda: ventes)
    monkeypatch.setattr(A, "_charger_prev", lambda: prev)


# ── Résolution de produit ──────────────────────────────────────────────────────
def test_resoudre_produit():
    assert A._resoudre_produit("flute 250gr")[0] == "FLUTE 250GR"
    assert A._resoudre_produit("crois")[0] == "CROISSANT BEURRE"   # unique → résolu
    nom, sugg = A._resoudre_produit("zzz introuvable")
    assert nom is None and sugg == []


# ── Outils (données brutes) ────────────────────────────────────────────────────
def test_prevision_produit_7_jours():
    r = A.outil_prevision_produit("flute 250gr")
    assert r["produit"] == "FLUTE 250GR" and len(r["jours"]) == 7
    assert r["jours"][0]["a_produire"] == 1200 and r["jours"][0]["prevision"] == 1000


def test_prevision_date_hors_horizon():
    assert "erreur" in A.outil_prevision_produit("FLUTE 250GR", date="2027-01-01")


def test_production_jour_total_et_categorie():
    r = A.outil_production_jour()
    assert r["total_a_produire"] == 1200 + 250 and r["nb_produits"] == 2
    assert r["top"][0]["produit"] == "FLUTE 250GR"
    rc = A.outil_production_jour(categorie="VIENNOISERIE")
    assert rc["total_a_produire"] == 250


def test_historique_mensuel():
    r = A.outil_historique_produit("flute 250gr", granularite="mois")
    q = {p["periode"]: p["quantite"] for p in r["periodes"]}
    assert q["2026-05"] == 31 * 800


# ── Couche de réponse déterministe (interroger) ────────────────────────────────
def test_interroger_production():
    out = A.interroger("production")
    assert out["texte"].startswith("Le 2026-06-29")
    assert "1 450" in out["texte"]  # 1200 + 250, séparateur espace
    assert out["table"][0]["Produit"] == "FLUTE 250GR"


def test_interroger_prevision_jour():
    out = A.interroger("prevision", produit="flute 250gr", date="2026-06-29")
    assert "1 200" in out["texte"] and "FLUTE 250GR" in out["texte"]
    assert len(out["table"]) == 1


def test_interroger_prevision_sans_produit():
    out = A.interroger("prevision")
    assert "produit" in out["texte"].lower() and out["table"] is None


def test_interroger_produit_inconnu_donne_message():
    out = A.interroger("prevision", produit="zzz")
    assert "introuvable" in out["texte"].lower() and out["table"] is None


def test_interroger_historique():
    out = A.interroger("historique", produit="croissant", granularite="mois")
    assert "CROISSANT BEURRE" in out["texte"]
    assert any(p["Période"] == "2026-05" for p in out["table"])


def test_interroger_types_connus():
    # tous les types sont déclarés et gérés (pas de « type inconnu »)
    for t in A.TYPES_QUESTION:
        out = A.interroger(t, produit="flute 250gr")
        assert "inconnu" not in out["texte"].lower()


def test_interroger_type_inconnu():
    assert "inconnu" in A.interroger("bidon")["texte"].lower()


def test_pas_de_dependance_reseau():
    # garde-fou : l'assistant local ne doit importer aucun client d'API
    import inspect
    src = inspect.getsource(A)
    assert "anthropic" not in src.lower()
    assert "import requests" not in src and "urllib.request" not in src

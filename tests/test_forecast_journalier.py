# -*- coding: utf-8 -*-
"""Test de régression du forecasting journalier.

Garde le bug corrigé le 2026-07-01 : un produit sans vente récente produisait
des NaN (Series.asfreq n'étendait la série que jusqu'à sa dernière vente).
"""
import numpy as np
import pandas as pd

from paul_forecast import forecast_journalier as FJ


def test_prevoir_pas_de_nan_produit_inactif(monkeypatch):
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=400), fin)
    actif = pd.DataFrame({"Date": dates, "Code": 1, "Produit": "ACTIF",
                          "Famille": "BOULANGERIE",
                          "Quantite": np.random.randint(5, 20, len(dates)).astype(float),
                          "CA_TTC": 0.0})
    # produit vendu une seule fois il y a 300 jours, puis plus rien
    inactif = pd.DataFrame({"Date": [fin - pd.Timedelta(days=300)], "Code": 2,
                            "Produit": "INACTIF", "Famille": "CUISINE",
                            "Quantite": [1.0], "CA_TTC": [10.0]})
    df = pd.concat([actif, inactif], ignore_index=True)

    # isole le test de la logique de prévision (pas de boost fêtes/matchs ni overrides)
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])

    prev = FJ.prevoir(horizon_jours=7)
    assert prev is not None and not prev.empty
    assert prev["Qty_Prev"].notna().all(), "des NaN sont réapparus (régression asfreq)"
    assert (prev["Qty_Prev"] >= 0).all()
    # les deux produits doivent être présents dans l'horizon
    assert set(prev["Produit"]) >= {"ACTIF", "INACTIF"}


def test_rupture_de_niveau_durable_rattrapee(monkeypatch):
    """Un produit qui change durablement de régime (×4, au-delà du plafond YoY)
    doit être prévu bien au-dessus de l'ancre annuelle plafonnée, pas scotché
    à l'an dernier. Garde le levier « prévisions réactives »."""
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=400), fin)
    q = np.full(len(dates), 10.0)
    # saut durable et propre à 40/j sur les ~20 derniers jours (pas un pic isolé)
    q[dates >= fin - pd.Timedelta(days=19)] = 40.0
    df = pd.DataFrame({"Date": dates, "Code": 1, "Produit": "RUPTURE",
                       "Famille": "BOULANGERIE", "Quantite": q, "CA_TTC": 0.0})
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "jours_proteges", lambda: set())

    prev = FJ.prevoir(horizon_jours=7)
    niveau_prevu = prev[prev["Produit"] == "RUPTURE"]["Qty_Prev"].mean()
    # ancre annuelle plafonnée : 10 × 1.8 = 18 ; le régime réel est à 40
    assert niveau_prevu > 28, f"rupture non rattrapée (prévu ≈ {niveau_prevu:.0f})"
    assert niveau_prevu <= 45, f"sur-réaction (prévu ≈ {niveau_prevu:.0f})"


def test_produit_stable_non_impacte(monkeypatch):
    """Un produit stable ne doit PAS voir sa prévision bouger : le détecteur de
    rupture ne se déclenche pas, β reste à POIDS_RECENT (~7-8% MAPE préservé)."""
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=400), fin)
    df = pd.DataFrame({"Date": dates, "Code": 1, "Produit": "STABLE",
                       "Famille": "BOULANGERIE", "Quantite": 100.0, "CA_TTC": 0.0})
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "jours_proteges", lambda: set())

    prev = FJ.prevoir(horizon_jours=7)
    niveau_prevu = prev[prev["Produit"] == "STABLE"]["Qty_Prev"].mean()
    assert 90 <= niveau_prevu <= 110, f"produit stable perturbé (prévu ≈ {niveau_prevu:.0f})"


def test_rupture_forte_detectee_vite(monkeypatch):
    """Voie RAPIDE : une rampe forte (×6, type gros client B2B récurrent) doit être
    suivie dès ~2 semaines, sans attendre la confirmation lente sur 2×14 j."""
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=400), fin)
    q = np.full(len(dates), 100.0)
    # rampe : ×2.5 depuis 3 semaines, ×6 depuis 1 semaine (trajectoire type
    # FLUTE 250GR quand le fast-food est monté en charge en juin 2026)
    q[dates >= fin - pd.Timedelta(days=20)] = 250.0
    q[dates >= fin - pd.Timedelta(days=6)] = 600.0
    df = pd.DataFrame({"Date": dates, "Code": 1, "Produit": "RAMPE",
                       "Famille": "BOULANGERIE", "Quantite": q, "CA_TTC": 0.0})
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "jours_proteges", lambda: set())

    prev = FJ.prevoir(horizon_jours=7)
    niveau_prevu = prev[prev["Produit"] == "RAMPE"]["Qty_Prev"].mean()
    # ancre annuelle plafonnée ≈ 180 ; régime réel 600, niveau suivi ≈ min(600, 2×250)=500
    assert niveau_prevu > 300, f"rupture forte non suivie (prévu ≈ {niveau_prevu:.0f})"


def test_pic_une_semaine_pas_pris_pour_regime(monkeypatch):
    """Un pic d'UNE semaine qui explose de nulle part (×8) ne doit pas être pris
    pour un nouveau régime : le plafond 2× semaine précédente le bride."""
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=400), fin)
    q = np.full(len(dates), 50.0)
    q[dates >= fin - pd.Timedelta(days=6)] = 400.0     # pic ×8 sur la seule dernière semaine
    df = pd.DataFrame({"Date": dates, "Code": 1, "Produit": "PIC",
                       "Famille": "BOULANGERIE", "Quantite": q, "CA_TTC": 0.0})
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "jours_proteges", lambda: set())

    prev = FJ.prevoir(horizon_jours=7)
    niveau_prevu = prev[prev["Produit"] == "PIC"]["Qty_Prev"].mean()
    # plafond : niveau_ref ≤ 2 × sem_avant (50) = 100 → prévision bien sous 400
    assert niveau_prevu < 150, f"pic isolé pris pour un régime (prévu ≈ {niveau_prevu:.0f})"


def test_label_peu_vendu(monkeypatch):
    """Un produit avec un long historique mais quasi plus vendu récemment doit être
    étiqueté « Peu vendu » (enjeu nul), pas « Hist. court » (trompeur)."""
    fin = pd.Timestamp("2026-06-28")
    dates = pd.date_range(fin - pd.Timedelta(days=400), fin)
    q = np.full(len(dates), 8.0)
    q[dates >= fin - pd.Timedelta(days=60)] = 0.0      # plus vendu depuis 2 mois
    df = pd.DataFrame({"Date": dates, "Code": 1, "Produit": "DORMANT",
                       "Famille": "CUISINE", "Quantite": q, "CA_TTC": 0.0})
    monkeypatch.setattr(FJ, "charger_ventes", lambda: df)
    monkeypatch.setattr(FJ, "_table_boost", lambda dates_, familles: {})
    monkeypatch.setattr(FJ, "charger_overrides", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "charger_commandes", lambda: [])
    monkeypatch.setattr(FJ.mod_commandes, "jours_proteges", lambda: set())

    prev = FJ.prevoir(horizon_jours=7)
    fiab = prev[prev["Produit"] == "DORMANT"]["Fiabilite"].iloc[0]
    assert fiab == "Peu vendu", f"attendu « Peu vendu », obtenu « {fiab} »"

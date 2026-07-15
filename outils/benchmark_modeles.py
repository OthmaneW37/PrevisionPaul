# -*- coding: utf-8 -*-
"""Banc d'essai des modèles de prévision (quantité), par produit — version
statsmodels (statsforecast segfault sur cet environnement Windows).

Compare en walk-forward (horizon 1 mois, N fenêtres) :
  ETS/Holt-Winters (actuel), SeasonalNaive, Moyenne mobile, Moyenne historique,
  Theta, ARIMA, Croston (demande intermittente) + un ensemble.
Sélectionne le meilleur modèle par produit et chiffre le gain vs « ETS partout ».

Sorties : benchmark_modeles.csv + data/modele_par_produit.json
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------
import json
import warnings

import numpy as np
import pandas as pd

from paul_forecast import data_loader, config

warnings.filterwarnings("ignore")
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.forecasting.theta import ThetaModel
from statsmodels.tsa.arima.model import ARIMA

N_WINDOWS = 6
SEASON = 12
MIN_MOIS = 30          # >= 30 pour que même la 1re fenêtre ait 2 saisons d'entraînement
ENSEMBLE = ["ETS", "Theta", "ARIMA"]
MODELES = ["ETS", "Theta", "ARIMA", "SeasonalNaive", "MoyenneMobile", "Moyenne", "Croston"]


def construire_series():
    """Dict produit -> np.array de QT nette mensuelle (depuis la 1re vente)."""
    df = data_loader.charger_dossier_mensuel(config.DATA_FOLDERS)
    qt = pd.to_numeric(df[config.QTY_COL], errors="coerce").fillna(0)
    ret = pd.to_numeric(df.get(config.QTY_RETURN_COL, 0), errors="coerce").fillna(0)
    d = pd.DataFrame({"p": df[config.PRODUCT_COL].astype(str),
                      "ds": pd.to_datetime(df[config.DATE_COL]),
                      "y": (qt - ret).clip(lower=0)})
    d = d.groupby(["p", "ds"], as_index=False)["y"].sum()
    idx = pd.date_range(d["ds"].min(), d["ds"].max(), freq="ME")
    series = {}
    for p, g in d.groupby("p"):
        s = g.set_index("ds").reindex(idx)["y"]
        nz = s[s > 0]
        if nz.empty:
            continue
        s = s.loc[nz.index[0]:].fillna(0)
        if len(s) >= MIN_MOIS:
            series[p] = s.values.astype(float)
    return series


def croston(y, alpha=0.1):
    z_hat = p_hat = None
    q = 0
    for val in y:
        q += 1
        if val > 0:
            if z_hat is None:
                z_hat, p_hat = val, q
            else:
                z_hat = alpha * val + (1 - alpha) * z_hat
                p_hat = alpha * q + (1 - alpha) * p_hat
            q = 0
    if not z_hat or not p_hat:
        return 0.0
    return z_hat / p_hat


def prevoir(nom, y):
    """Prévision 1 pas du modèle `nom` sur la série y (array)."""
    try:
        if nom == "ETS":
            m = ExponentialSmoothing(y, trend="add", seasonal="add",
                                     seasonal_periods=SEASON, initialization_method="estimated")
            return float(m.fit().forecast(1)[0])
        if nom == "Theta":
            return float(ThetaModel(y, period=SEASON).fit().forecast(1).iloc[0])
        if nom == "ARIMA":
            return float(ARIMA(y, order=(1, 1, 1)).fit().forecast(1)[0])
        if nom == "SeasonalNaive":
            return float(y[-SEASON]) if len(y) >= SEASON else float(y[-1])
        if nom == "MoyenneMobile":
            return float(np.mean(y[-4:]))
        if nom == "Moyenne":
            return float(np.mean(y))
        if nom == "Croston":
            return croston(y)
    except Exception:
        return np.nan
    return np.nan


def main():
    series = construire_series()
    print(f"Produits retenus (>= {MIN_MOIS} mois) : {len(series)}", flush=True)

    lignes = []
    for i, (p, y) in enumerate(series.items(), 1):
        if i % 50 == 0:
            print(f"  ... {i}/{len(series)}", flush=True)
        n = len(y)
        # erreurs absolues cumulées par modèle sur les N fenêtres
        err = {m: [] for m in MODELES + ["Ensemble"]}
        for k in range(N_WINDOWS):
            t = n - N_WINDOWS + k
            train, reel = y[:t], y[t]
            preds = {m: prevoir(m, train) for m in MODELES}
            for m in MODELES:
                pv = preds[m]
                if not np.isnan(pv):
                    err[m].append(abs(reel - max(0.0, pv)))
            ens = [preds[m] for m in ENSEMBLE if not np.isnan(preds[m])]
            if ens:
                err["Ensemble"].append(abs(reel - max(0.0, float(np.mean(ens)))))
        ligne = {"produit": p, "volume": float(np.mean(y))}
        for m in MODELES + ["Ensemble"]:
            ligne[m] = float(np.mean(err[m])) if err[m] else np.nan
        lignes.append(ligne)

    res = pd.DataFrame(lignes).set_index("produit")
    cols = MODELES + ["Ensemble"]
    res["meilleur_modele"] = res[cols].idxmin(axis=1)
    res["mae_best"] = res[cols].min(axis=1)
    res = res.sort_values("volume", ascending=False)
    res.to_csv("benchmark_modeles.csv", sep=";", encoding="utf-8")
    with open("data/modele_par_produit.json", "w", encoding="utf-8") as f:
        json.dump(res["meilleur_modele"].to_dict(), f, ensure_ascii=False, indent=2)

    print("\n=== Qui gagne ? (nb de produits où le modèle est le meilleur) ===")
    print(res["meilleur_modele"].value_counts().to_string())

    w = res["volume"] / res["volume"].sum()
    mae_ets_w = (res["ETS"] * w).sum()
    mae_sel_w = (res["mae_best"] * w).sum()
    print("\n=== Sélection PAR PRODUIT vs ETS (Holt-Winters) partout ===")
    print(f"MAE pondérée — ETS partout       : {mae_ets_w:,.2f}")
    print(f"MAE pondérée — meilleur/produit  : {mae_sel_w:,.2f}")
    print(f"Réduction d'erreur               : {(1 - mae_sel_w / mae_ets_w) * 100:,.1f} %")

    print("\n=== MAE moyenne par modèle (pondérée volume) ===")
    for m in cols:
        print(f"  {m:<14} {(res[m] * w).sum():,.2f}")

    print("\nTop 12 produits (volume) → meilleur modèle :")
    print(res.head(12)[["volume", "meilleur_modele", "mae_best", "ETS"]].to_string())
    print("\nEcrits : benchmark_modeles.csv + data/modele_par_produit.json")


if __name__ == "__main__":
    main()

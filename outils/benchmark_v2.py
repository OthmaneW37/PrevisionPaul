# -*- coding: utf-8 -*-
"""Banc d'essai v2 — pousse la justesse des prévisions mensuelles par produit.

Nouveautés vs _benchmark_modeles :
  - panel COMPLÉTÉ par les ventes journalières (59 mois, Ramadan 2026 inclus) ;
  - 12 fenêtres walk-forward (vs 6) → sélection plus fiable ;
  - GBM GLOBAL (HistGradientBoostingRegressor entraîné sur TOUS les produits :
    lags normalisés, mois, fêtes, famille) — mutualise l'information ;
  - Ensemble MÉDIAN (plus robuste que la moyenne) ;
  - test de la RÉCONCILIATION top-down (caler la somme des produits sur la
    prévision agrégée Holt-Winters).

Sorties : benchmark_v2.csv + data/modele_par_produit.json (mapping v2).
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

warnings.filterwarnings("ignore")

from sklearn.ensemble import HistGradientBoostingRegressor

from benchmark_modeles import construire_series, prevoir, MODELES
from paul_forecast import data_loader, config
from paul_forecast import saisonnalite_fetes as sf

N_WINDOWS = 12
SEASON = 12
MEDIAN_SET = ["ETS", "Theta", "ARIMA", "SeasonalNaive"]
CANDIDATS_FINAUX = MODELES + ["Ensemble", "EnsembleMedian", "GBM"]


# ─────────────────────────────────────────────────────────── features fêtes/mois
def _fractions_fetes(dates):
    """{date: (frac_ramadan, frac_fitr, frac_adha)} pour des fins de mois."""
    fen = sf._fenetres_par_type()
    out = {}
    for d in dates:
        out[d] = tuple(sf._fraction_du_mois(d, fen.get(t, []))
                       for t in ("ramadan", "aid_fitr", "aid_adha"))
    return out


def _familles():
    df = data_loader.charger_dossier_mensuel(config.DATA_FOLDERS)
    return (df.dropna(subset=[config.CATEGORY_COL])
              .drop_duplicates(config.PRODUCT_COL)
              .set_index(config.PRODUCT_COL)[config.CATEGORY_COL].astype(str).to_dict())


# ─────────────────────────────────────────────────────────── dataset GBM
def _lignes_gbm(y, t, dates, fr, fam_code):
    """Features de la cible y[t] (None si historique insuffisant / base nulle)."""
    if t < 14:
        return None
    base = float(np.mean(y[max(0, t - 12):t]))
    if base <= 0:
        return None
    lag = lambda k: (y[t - k] / base) if t - k >= 0 else 1.0
    rec3 = y[max(0, t - 3):t]
    d = dates[t]
    f_ram, f_fitr, f_adha = fr[d]
    feats = [lag(1), lag(2), lag(3), lag(6), lag(12),
             float(np.mean(rec3)) / base, float(np.std(rec3)) / base,
             float(d.month), f_ram, f_fitr, f_adha,
             float(fam_code), float(np.log1p(base))]
    return feats, float(np.clip(y[t] / base, 0.0, 6.0)), base


# ─────────────────────────────────────────────────────────── benchmark
def main():
    series = construire_series()
    n = len(next(iter(series.values())))          # longueur commune (fin alignée)
    # longueur par produit variable : aligner par LA FIN → index absolu t dans
    # chaque série ; on définit les cutoffs en "mois depuis la fin".
    print(f"Produits: {len(series)}", flush=True)

    fam = _familles()
    fam_liste = sorted(set(fam.values()))
    fam_codes_map = {f: i for i, f in enumerate(fam_liste)}
    fam_codes = {p: fam_codes_map.get(fam.get(p, ""), -1) for p in series}

    # dates par produit : la série démarre à la 1re vente et finit au dernier
    # mois global. On reconstruit les dates de chaque série depuis la fin.
    df = data_loader.charger_dossier_mensuel(config.DATA_FOLDERS)
    fin = pd.to_datetime(df[config.DATE_COL]).max()
    fin = pd.Timestamp(fin) + pd.offsets.MonthEnd(0)
    dates_par_prod = {p: pd.date_range(end=fin, periods=len(y), freq="ME")
                      for p, y in series.items()}
    toutes_dates = pd.date_range(end=fin, periods=max(len(y) for y in series.values()), freq="ME")
    fr = _fractions_fetes(toutes_dates)

    # ── modèles statsmodels (walk-forward 12 fenêtres) ────────────────────────
    resultats = {}    # produit -> {modele: [erreurs absolues]}
    reels = {}        # (produit, k) -> y réel ; k = fenêtre 0..11 (0 = la plus ancienne)
    preds_all = {}    # (modele, produit, k) -> prédiction
    for i, (p, y) in enumerate(series.items(), 1):
        if i % 100 == 0:
            print(f"  ... stats {i}/{len(series)}", flush=True)
        ny = len(y)
        err = {m: [] for m in MODELES + ["Ensemble", "EnsembleMedian"]}
        for k in range(N_WINDOWS):
            t = ny - N_WINDOWS + k
            if t < 18:
                continue
            train, reel = y[:t], y[t]
            reels[(p, k)] = reel
            pr = {m: prevoir(m, train) for m in MODELES}
            for m in MODELES:
                v = pr[m]
                if v is not None and not np.isnan(v):
                    preds_all[(m, p, k)] = max(0.0, v)
                    err[m].append(abs(reel - max(0.0, v)))
            ens = [pr[m] for m in ["ETS", "Theta", "ARIMA"]
                   if pr[m] is not None and not np.isnan(pr[m])]
            if ens:
                v = max(0.0, float(np.mean(ens)))
                preds_all[("Ensemble", p, k)] = v
                err["Ensemble"].append(abs(reel - v))
            med = [pr[m] for m in MEDIAN_SET
                   if pr[m] is not None and not np.isnan(pr[m])]
            if med:
                v = max(0.0, float(np.median(med)))
                preds_all[("EnsembleMedian", p, k)] = v
                err["EnsembleMedian"].append(abs(reel - v))
        resultats[p] = err

    # ── GBM global ────────────────────────────────────────────────────────────
    print("GBM global…", flush=True)
    # cutoffs par produit = ny - N_WINDOWS + k ; en indices "communs" on refait
    # le dataset par produit avec ses dates propres.
    X, Y, meta = [], [], []
    for p, y in series.items():
        dts, fc = dates_par_prod[p], fam_codes.get(p, -1)
        for t in range(14, len(y)):
            r = _lignes_gbm(y, t, dts, fr, fc)
            if r is None:
                continue
            X.append(r[0]); Y.append(r[1]); meta.append((p, t, r[2]))
    X, Y = np.array(X), np.array(Y)
    # fenêtre k du produit p ↔ t = len(y)-N_WINDOWS+k → date = dates[t] ; le
    # cutoff temporel commun est LA DATE. Fit sans fuite : train = date < date_k.
    dates_meta = np.array([dates_par_prod[p][t].value for p, t, _ in meta])
    dates_fen = [toutes_dates[len(toutes_dates) - N_WINDOWS + k] for k in range(N_WINDOWS)]
    for k, dk in enumerate(dates_fen):
        train = dates_meta < dk.value
        test = dates_meta == dk.value
        if train.sum() < 500 or not test.any():
            continue
        gbm = HistGradientBoostingRegressor(
            loss="absolute_error", max_iter=350, learning_rate=0.06,
            l2_regularization=1.0, categorical_features=[7, 11], random_state=42)
        gbm.fit(X[train], Y[train])
        yh = gbm.predict(X[test])
        for idx, r in zip(np.where(test)[0], yh):
            p, t, base = meta[idx]
            v = max(0.0, float(r) * base)
            preds_all[("GBM", p, k)] = v
            if (p, k) in reels:
                resultats[p].setdefault("GBM", []).append(abs(reels[(p, k)] - v))
        print(f"  GBM fenêtre {k+1}/{N_WINDOWS} ({dk:%Y-%m}) : {int(train.sum())} pts", flush=True)

    # ── Réconciliation top-down (test) ────────────────────────────────────────
    print("Réconciliation top-down…", flush=True)
    agg = None
    for p, y in series.items():
        s = pd.Series(y, index=dates_par_prod[p])
        agg = s if agg is None else agg.add(s, fill_value=0.0)
    for cible in ["Theta", "EnsembleMedian", "GBM"]:
        cle = f"{cible}+recon"
        for k, dk in enumerate(dates_fen):
            hist = agg[agg.index < dk].values
            hw = prevoir("ETS", hist)
            if hw is None or np.isnan(hw):
                continue
            prods_k = [p for p in series if (cible, p, k) in preds_all]
            somme = sum(preds_all[(cible, p, k)] for p in prods_k)
            if somme <= 0:
                continue
            f = max(0.0, hw) / somme
            f = float(np.clip(f, 0.7, 1.4))
            for p in prods_k:
                v = preds_all[(cible, p, k)] * f
                if (p, k) in reels:
                    resultats[p].setdefault(cle, []).append(abs(reels[(p, k)] - v))

    # ── bilan ────────────────────────────────────────────────────────────────
    vol = {p: float(np.mean(y)) for p, y in series.items()}
    tous_modeles = sorted({m for err in resultats.values() for m in err if err[m]})
    lignes = []
    for p, err in resultats.items():
        ligne = {"produit": p, "volume": vol[p]}
        for m in tous_modeles:
            ligne[m] = float(np.mean(err[m])) if err.get(m) else np.nan
        lignes.append(ligne)
    res = pd.DataFrame(lignes).set_index("produit")
    w = res["volume"] / res["volume"].sum()

    print("\n=== MAE pondérée volume, par modèle (12 fenêtres, panel frais) ===")
    scores = {}
    for m in tous_modeles:
        masque = res[m].notna()
        s = float((res.loc[masque, m] * w[masque]).sum() / w[masque].sum())
        scores[m] = s
        print(f"  {m:<22} {s:8.2f}")

    # sélection par produit (garde 5 % vs ETS), candidats sans les +recon
    choix = {}
    for p, row in res.iterrows():
        cand = {m: row[m] for m in CANDIDATS_FINAUX if m in row and np.isfinite(row[m])}
        if not cand:
            continue
        best = min(cand, key=cand.get)
        ets = row.get("ETS", np.nan)
        choix[p] = best if (not np.isfinite(ets) or cand[best] < 0.95 * ets) else "ETS"
    import collections
    print("\n=== Sélection v2 par produit (garde 5 %) ===")
    for m, c in collections.Counter(choix.values()).most_common():
        print(f"  {m:<16} {c}")

    mae_sel = float(np.nansum([res.loc[p, choix[p]] * w[p] for p in choix]) /
                    np.nansum([w[p] for p in choix]))
    print(f"\nMAE pondérée — ETS partout        : {scores.get('ETS', float('nan')):.2f}")
    print(f"MAE pondérée — sélection v2       : {mae_sel:.2f}")

    res.to_csv("data/benchmark_v2.csv", sep=";", encoding="utf-8")
    with open("data/modele_par_produit.json", "w", encoding="utf-8") as f:
        json.dump(choix, f, ensure_ascii=False, indent=2)
    print("\nEcrits : data/benchmark_v2.csv + data/modele_par_produit.json (v2)")


if __name__ == "__main__":
    main()

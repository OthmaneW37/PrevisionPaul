# -*- coding: utf-8 -*-
"""
Backtesting inter-annuel : entraîne les modèles sur certaines années, prédit une
année cible, et compare aux ventes réelles (MAE / RMSE / MAPE).
"""

import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from . import config
from . import forecasting
from .logging_setup import get_logger

logger = get_logger()


def backtest_annuel(df_nettoye, col_date, col_qty, col_rev, annee_train, annee_test):
    """
    Entraîne sur `annee_train`, prédit `annee_test`, compare au réel.
    Retourne {'df_comparaison', 'metriques'} ou None si données insuffisantes.
    """
    annees_train = [annee_train] if isinstance(annee_train, int) else list(annee_train)
    df_train = df_nettoye[pd.to_datetime(df_nettoye[col_date]).dt.year.isin(annees_train)].copy()
    df_test  = df_nettoye[pd.to_datetime(df_nettoye[col_date]).dt.year == annee_test].copy()

    if df_train.empty or df_test.empty:
        logger.warning("[Backtest] Train (%s) ou test (%s) vide — ignoré.",
                       annees_train, annee_test)
        return None

    def _agg(df):
        df_temp = df[[col_date, col_qty, col_rev]].copy()
        df_temp[col_date] = pd.to_datetime(df_temp[col_date])
        df_temp.set_index(col_date, inplace=True)
        return df_temp.resample("ME").sum().reset_index()

    df_train_agg = _agg(df_train)
    df_test_agg  = _agg(df_test)
    n_test = len(df_test_agg)
    if n_test == 0:
        return None

    logger.info("[Backtest annuel] Train %s (%d mois) → Test %s (%d mois)",
                annees_train, len(df_train_agg), annee_test, n_test)

    resultats = {"Date": df_test_agg[col_date].values}
    y_reel_rev = df_test_agg[col_rev].clip(lower=0).values.astype(float)
    resultats["Reel_CA"] = y_reel_rev

    df_fc_base = forecasting.calculer_previsions_pour_colonne(
        df_train_agg.rename(columns={col_rev: "Valeur", col_qty: "Quantite"}),
        "Valeur", n_test, config.MA_WINDOW, config.AGG_FREQ
    )
    for col_fc, label in [
        ("Prev_Decompo_Saisonniere", "Decompo_Saisonniere"),
        ("Prev_Tendance",            "Tendance_Lineaire"),
        ("Prev_Moyenne_Mobile",      "Moyenne_Mobile"),
        ("Prev_Naif_Saisonnier",     "Naif_Saisonnier"),
        ("Prev_Holt_Winters",        "Holt_Winters"),
    ]:
        if col_fc in df_fc_base.columns:
            resultats[f"Prev_{label}"] = df_fc_base[col_fc].values

    if config.ACTIVER_PROPHET and forecasting.verifier_prophet():
        df_train_prophet = df_train_agg.rename(columns={col_rev: "Valeur", col_qty: "Quantite"})
        res_prophet = forecasting.forecast_prophet(
            df_train_prophet, "Valeur", n_test, config.AGG_FREQ,
            min_historique=config.MIN_HISTORIQUE_PROPHET)
        resultats["Prev_Prophet"] = res_prophet["previsions"] if res_prophet \
            else resultats["Prev_Decompo_Saisonniere"]

    df_comp = pd.DataFrame(resultats)

    metriques = {}
    for col_m in [c for c in df_comp.columns if c.startswith("Prev_")]:
        y_pred = df_comp[col_m].values
        mae  = float(np.mean(np.abs(y_reel_rev - y_pred)))
        rmse = float(np.sqrt(np.mean((y_reel_rev - y_pred) ** 2)))
        mask = y_reel_rev > 0
        mape = float(np.mean(np.abs((y_reel_rev[mask] - y_pred[mask]) / y_reel_rev[mask])) * 100) \
               if mask.sum() > 0 else float("nan")
        nom_modele = col_m.replace("Prev_", "")
        metriques[nom_modele] = {"MAE": round(mae, 0), "RMSE": round(rmse, 0),
                                 "MAPE": round(mape, 1) if not np.isnan(mape) else "N/A"}
        logger.info("  %-25s -> MAE=%s | RMSE=%s | MAPE=%.1f%%",
                    nom_modele, f"{mae:,.0f}", f"{rmse:,.0f}", mape)

    return {"df_comparaison": df_comp, "metriques": metriques}


def tracer_dashboard_backtest(res_backtest, annee_train, annee_test, output_dir):
    """Génère la figure de comparaison prévisions vs réalité du backtest."""
    if res_backtest is None:
        return

    df_comp   = res_backtest["df_comparaison"]
    metriques = res_backtest["metriques"]

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.patch.set_facecolor('#f8f9fa')
    fig.suptitle(f"BACKTESTING | Entraînement {annee_train} → Prédiction {annee_test} vs Réel",
                 fontsize=13, fontweight='bold', color='#2c3e50')

    dates  = pd.to_datetime(df_comp["Date"])
    y_reel = df_comp["Reel_CA"].values

    couleurs_modeles = {
        "Decompo_Saisonniere": ("#8e44ad", "--"),
        "Tendance_Lineaire":   ("#3498db", "-."),
        "Moyenne_Mobile":      ("#e67e22", ":"),
        "Naif_Saisonnier":     ("#95a5a6", ":"),
        "Holt_Winters":        ("#27ae60", "-"),
        "Prophet":             ("#e74c3c", "--"),
    }

    ax = axes[0]
    ax.plot(dates, y_reel, label="Réel", color="#2c3e50", linewidth=2.5, marker="o", markersize=5)
    for modele, (couleur, style) in couleurs_modeles.items():
        col = f"Prev_{modele}"
        if col in df_comp.columns:
            ax.plot(dates, df_comp[col].values, label=modele.replace("_", " "),
                    color=couleur, linewidth=1.8, linestyle=style, marker="s", markersize=4)

    ax.set_title(f"CA mensuel : prévisions vs réalité ({annee_test})", fontsize=11, fontweight='bold')
    ax.set_ylabel("CA (MAD)")
    ax.legend(fontsize=8)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.tick_params(axis="x", rotation=30, labelsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))

    ax2 = axes[1]
    ax2.axis("off")
    modeles_liste = [m for m in metriques if m != "Naif_Saisonnier"]
    data_table = [[m.replace("_", " "), f"{metriques[m]['MAE']:,.0f}",
                   f"{metriques[m]['RMSE']:,.0f}", f"{metriques[m]['MAPE']}%"]
                  for m in modeles_liste]
    data_table.sort(key=lambda r: float(r[3].replace("%", "").replace("N/A", "999")))

    tbl = ax2.table(cellText=data_table,
                    colLabels=["Modèle", "MAE (MAD)", "RMSE (MAD)", "MAPE"],
                    cellLoc="center", loc="center", bbox=[0.05, 0.2, 0.9, 0.65])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    for j in range(4):
        tbl[0, j].set_facecolor("#2c3e50")
        tbl[0, j].set_text_props(color="white", fontweight="bold")
    for j in range(4):
        tbl[1, j].set_facecolor("#d5f5e3")

    ax2.set_title("Métriques d'erreur (meilleur surligné en vert)", fontsize=11, fontweight="bold")

    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    path_out = os.path.join(output_dir, "dashboard_backtest_annuel.png")
    plt.savefig(path_out, dpi=150, bbox_inches="tight", facecolor="#f8f9fa")
    plt.close(fig)
    logger.info("Dashboard backtest sauvegardé : '%s'", path_out)


# ==============================================================================
# VALIDATION GLISSANTE "MOIS PROCHAIN" (walk-forward, horizon = 1 mois)
# ==============================================================================
_MODELES_WF = [
    "Prev_Moyenne_Mobile", "Prev_Tendance", "Prev_Naif_Saisonnier",
    "Prev_Decompo_Saisonniere", "Prev_Holt_Winters",
]


def _metriques_erreur(y_reel, y_pred):
    """MAE / RMSE / MAPE entre deux séries alignées (MAPE sur réel > 0)."""
    y_reel = np.asarray(y_reel, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mae  = float(np.mean(np.abs(y_reel - y_pred)))
    rmse = float(np.sqrt(np.mean((y_reel - y_pred) ** 2)))
    mask = y_reel > 0
    mape = float(np.mean(np.abs((y_reel[mask] - y_pred[mask]) / y_reel[mask])) * 100) \
        if mask.sum() > 0 else float("nan")
    return mae, rmse, mape


def backtest_walkforward_mensuel(df_serie, col_valeur, label, n_validation,
                                 ma_window, freq, min_train):
    """
    Validation glissante à 1 mois d'horizon (rolling origin, horizon = 1).

    Pour chacun des `n_validation` derniers mois : on entraîne les modèles sur
    TOUT l'historique antérieur (le mois cible est exclu → aucune fuite), on
    prédit ce mois unique, puis on compare au réel. Reflète directement la
    question métier « sais-je prédire le mois prochain ? ».

    Retourne {'df_comparaison', 'metriques', 'label', 'meilleur_modele'} ou None.
    """
    df = df_serie[["Date", col_valeur]].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    y = df[col_valeur].values.astype(float)
    N = len(y)

    debut = max(min_train, N - n_validation)
    if debut >= N:
        logger.warning("[Walk-forward %s] Historique insuffisant (%d mois, min_train=%d) "
                       "— validation ignorée.", label, N, min_train)
        return None

    dates_val, reel = [], []
    preds = {m: [] for m in _MODELES_WF}

    for i in range(debut, N):
        df_train = df.iloc[:i][["Date", col_valeur]].rename(columns={col_valeur: "Valeur"})
        df_fc = forecasting.calculer_previsions_pour_colonne(
            df_train, "Valeur", 1, ma_window, freq)
        dates_val.append(df["Date"].iloc[i])
        reel.append(y[i])
        for m in _MODELES_WF:
            preds[m].append(float(df_fc[m].iloc[0]) if m in df_fc.columns else np.nan)

    resultats = {"Date": dates_val, "Reel": reel}
    metriques = {}
    for m in _MODELES_WF:
        resultats[m] = preds[m]
        mae, rmse, mape = _metriques_erreur(reel, preds[m])
        nom = m.replace("Prev_", "")
        metriques[nom] = {"MAE": round(mae, 0), "RMSE": round(rmse, 0),
                          "MAPE": round(mape, 1) if not np.isnan(mape) else "N/A"}
        logger.info("  [%s] %-22s -> MAE=%s | RMSE=%s | MAPE=%.1f%%",
                    label, nom, f"{mae:,.0f}", f"{rmse:,.0f}", mape)

    # Meilleur modèle = plus faible MAPE (N/A traité comme +inf)
    def _mape_num(v):
        return float(v) if v != "N/A" else float("inf")
    meilleur = min(metriques, key=lambda k: _mape_num(metriques[k]["MAPE"]))

    return {
        "df_comparaison": pd.DataFrame(resultats),
        "metriques": metriques,
        "label": label,
        "meilleur_modele": meilleur,
    }


def tracer_dashboard_walkforward(res_ca, res_qte, output_dir):
    """Dashboard de la validation glissante mensuelle (CA + quantité)."""
    blocs = [r for r in (res_ca, res_qte) if r is not None]
    if not blocs:
        return

    couleurs_modeles = {
        "Decompo_Saisonniere": ("#8e44ad", "--"),
        "Tendance":            ("#3498db", "-."),
        "Moyenne_Mobile":      ("#e67e22", ":"),
        "Naif_Saisonnier":     ("#95a5a6", ":"),
        "Holt_Winters":        ("#27ae60", "-"),
    }

    fig, axes = plt.subplots(len(blocs), 2, figsize=(16, 6 * len(blocs)),
                             squeeze=False)
    fig.patch.set_facecolor('#f8f9fa')
    fig.suptitle("VALIDATION GLISSANTE « MOIS PROCHAIN » (horizon 1 mois, sans fuite)",
                 fontsize=13, fontweight='bold', color='#2c3e50')

    for ligne, res in enumerate(blocs):
        df_comp   = res["df_comparaison"]
        metriques = res["metriques"]
        label     = res["label"]
        dates     = pd.to_datetime(df_comp["Date"])
        y_reel    = df_comp["Reel"].values

        ax = axes[ligne][0]
        ax.plot(dates, y_reel, label="Réel", color="#2c3e50",
                linewidth=2.5, marker="o", markersize=5)
        for modele, (couleur, style) in couleurs_modeles.items():
            col = f"Prev_{modele}"
            if col in df_comp.columns:
                ax.plot(dates, df_comp[col].values, label=modele.replace("_", " "),
                        color=couleur, linewidth=1.6, linestyle=style,
                        marker="s", markersize=3)
        ax.set_title(f"{label} : prédiction à 1 mois vs réel", fontsize=11, fontweight='bold')
        ax.set_ylabel(label)
        ax.legend(fontsize=8)
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.tick_params(axis="x", rotation=30, labelsize=8)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:,.0f}'))

        ax2 = axes[ligne][1]
        ax2.axis("off")
        modeles_liste = [m for m in metriques if m != "Naif_Saisonnier"]
        data_table = [[m.replace("_", " "), f"{metriques[m]['MAE']:,.0f}",
                       f"{metriques[m]['RMSE']:,.0f}", f"{metriques[m]['MAPE']}%"]
                      for m in modeles_liste]
        data_table.sort(key=lambda r: float(r[3].replace("%", "").replace("N/A", "999")))

        tbl = ax2.table(cellText=data_table,
                        colLabels=["Modèle", "MAE", "RMSE", "MAPE (1 mois)"],
                        cellLoc="center", loc="center", bbox=[0.05, 0.2, 0.9, 0.65])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(10)
        for j in range(4):
            tbl[0, j].set_facecolor("#2c3e50")
            tbl[0, j].set_text_props(color="white", fontweight="bold")
        for j in range(4):
            tbl[1, j].set_facecolor("#d5f5e3")
        ax2.set_title(f"{label} — erreur à 1 mois (meilleur en vert : {res['meilleur_modele'].replace('_',' ')})",
                      fontsize=11, fontweight="bold")

    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    path_out = os.path.join(output_dir, "dashboard_validation_mensuelle.png")
    plt.savefig(path_out, dpi=150, bbox_inches="tight", facecolor="#f8f9fa")
    plt.close(fig)
    logger.info("Dashboard validation mensuelle sauvegardé : '%s'", path_out)

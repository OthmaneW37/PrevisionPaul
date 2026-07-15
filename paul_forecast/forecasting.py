# -*- coding: utf-8 -*-
"""
Modèles de prévision de séries temporelles.

Modèles de base (sans dépendance lourde) :
  - Moyenne mobile, Tendance linéaire, Naïf saisonnier,
  - Décomposition saisonnière additive, Holt-Winters (si statsmodels présent).

Modèle optionnel : Prophet (Meta) — capture finement les pics Ramadan/Aïd.
"""

import numpy as np
import pandas as pd

from . import config
from .logging_setup import get_logger

logger = get_logger()


# ==============================================================================
# CORRECTION SAISONNIÈRE & WEEKEND (historique court)
# ==============================================================================
def correction_saisonniere_et_weekend(dates_futures, previsions_base, coef_weekend=None):
    """
    Applique le profil saisonnier mensuel + une correction de densité weekend
    sur des prévisions futures. Utilisé quand l'historique est trop court pour
    que la décomposition détecte elle-même la saisonnalité. Résultat toujours ≥ 0.
    """
    coef_wd = coef_weekend if coef_weekend is not None else config.COEF_WEEKEND
    WD_MOYEN = 8.7   # moyenne des jours samedi+dimanche par mois

    previsions_corrigees = np.array(previsions_base, dtype=float).copy()

    for i, date in enumerate(dates_futures):
        mois = date.month
        facteur_saison = config.PROFIL_SAISONNIER_MENSUEL.get(mois, 1.0)

        debut_mois = pd.Timestamp(year=date.year, month=mois, day=1)
        fin_mois   = debut_mois + pd.offsets.MonthEnd(0)
        jours_mois = pd.date_range(debut_mois, fin_mois, freq='D')
        nb_weekend = int((jours_mois.dayofweek >= 5).sum())
        facteur_wd = 1.0 + coef_wd * (nb_weekend - WD_MOYEN) / WD_MOYEN

        previsions_corrigees[i] = max(0.0, previsions_corrigees[i] * facteur_saison * facteur_wd)

    return previsions_corrigees


# ==============================================================================
# MODÈLES DE BASE (5 modèles)
# ==============================================================================
def calculer_previsions_pour_colonne(df_agg, col_nom, n_periodes, ma_window, freq):
    """Calcule des prévisions avancées pour une colonne donnée (Quantité ou CA)."""
    derniere_date = df_agg['Date'].iloc[-1]
    y_raw = df_agg[col_nom].values
    y = np.nan_to_num(y_raw.astype(float), nan=0.0, posinf=0.0, neginf=0.0)
    N = len(y)

    freq_clean = str(freq).upper()
    if freq_clean.startswith('D'):
        S = 7
    elif freq_clean.startswith('W'):
        S = 52
    elif freq_clean.startswith('M'):
        S = 12
    else:
        S = 1

    dates_futures = pd.date_range(start=derniere_date, periods=n_periodes + 1, freq=freq)[1:]
    df_fc = pd.DataFrame({'Date': dates_futures})

    # 1. Moyenne Mobile
    valeur_ma = np.mean(y[-ma_window:]) if N >= ma_window else np.mean(y)
    df_fc['Prev_Moyenne_Mobile'] = float(valeur_ma) if np.isfinite(valeur_ma) else 0.0

    # 2. Tendance Linéaire
    x_train = np.arange(N)
    if N < 2 or np.all(y == y[0]):
        pente, ordonnee = 0.0, float(y[0]) if N > 0 else 0.0
    else:
        try:
            coefs = np.polyfit(x_train, y, 1)
            pente, ordonnee = float(coefs[0]), float(coefs[1])
        except (np.linalg.LinAlgError, ValueError):
            pente, ordonnee = 0.0, float(np.mean(y))
    x_futur = np.arange(N, N + n_periodes)
    df_fc['Prev_Tendance'] = (pente * x_futur + ordonnee)
    df_fc['Prev_Tendance'] = df_fc['Prev_Tendance'].clip(lower=0)

    # 3. Naïf Saisonnier
    prev_snaive = []
    if S > 1 and N >= S:
        for i in range(n_periodes):
            idx_passe = N - S + (i % S)
            while idx_passe >= N:
                idx_passe -= S
            prev_snaive.append(y[idx_passe])
    else:
        prev_snaive = [y[-1]] * n_periodes
    df_fc['Prev_Naif_Saisonnier'] = prev_snaive

    # 4. Décomposition Saisonnière Additive
    prev_decompo = []
    historique_suffisant = (S > 1 and N >= 2 * S)

    if historique_suffisant:
        detrended = y - (pente * x_train + ordonnee)
        indices_saisonniers = np.zeros(S)
        for i in range(S):
            indices_saisonniers[i] = np.mean(detrended[i::S])
        indices_saisonniers -= np.mean(indices_saisonniers)
        for t in x_futur:
            idx_saison = t % S
            prev_decompo.append((pente * t + ordonnee) + indices_saisonniers[idx_saison])
    else:
        prev_decompo = list(pente * x_futur + ordonnee)

    prev_decompo_arr = np.array(prev_decompo, dtype=float).clip(min=0)

    if not historique_suffisant and config.AGG_FREQ.upper().startswith('M'):
        prev_decompo_arr = correction_saisonniere_et_weekend(dates_futures, prev_decompo_arr)

    df_fc['Prev_Decompo_Saisonniere'] = prev_decompo_arr

    # 5. Holt-Winters (Lissage Exponentiel Triple) — fallback = Decompo
    prev_hw_arr = prev_decompo_arr.copy()
    if historique_suffisant:
        try:
            import warnings
            from statsmodels.tsa.holtwinters import ExponentialSmoothing as HW
            with warnings.catch_warnings():
                # Sur 1 000+ produits, les ConvergenceWarning/RuntimeWarning
                # bénins de l'optimiseur noient les logs — on les tait ici
                # (l'échec réel est géré par le except → fallback Decompo).
                warnings.simplefilter("ignore")
                # Tendance AMORTIE (damped) : sans amortissement, la pente
                # récente est extrapolée linéairement sur tout l'horizon et la
                # somme des produits dérive bien au-dessus des niveaux
                # historiques (les hausses ne sont pas bornées, les baisses le
                # sont à 0). L'amortissement ramène la prévision vers un
                # plateau cohérent avec les années précédentes.
                model_hw = HW(y, trend='add', damped_trend=True,
                              seasonal='add', seasonal_periods=S,
                              initialization_method='estimated')
                fit_hw = model_hw.fit(optimized=True, remove_bias=True)
                prev_hw_arr = np.array(fit_hw.forecast(n_periodes), dtype=float).clip(min=0)
        except Exception:
            pass   # statsmodels absent / série trop courte → fallback Decompo
    else:
        if config.AGG_FREQ.upper().startswith('M'):
            base_hw = np.array(list(pente * x_futur + ordonnee), dtype=float).clip(min=0)
            prev_hw_arr = correction_saisonniere_et_weekend(dates_futures, base_hw)

    df_fc['Prev_Holt_Winters'] = prev_hw_arr

    return df_fc


# ==============================================================================
# MODÈLES SUPPLÉMENTAIRES (sélection par produit — cf. banc d'essai)
# ==============================================================================
def _croston(y, alpha=0.1):
    """Méthode de Croston pour la demande intermittente. Retourne la prévision (constante)."""
    z_hat = p_hat = None
    q = 0
    for val in np.asarray(y, dtype=float):
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


def prevision_modele(y, n_periodes, freq, nom):
    """
    Prévision n_periodes d'un modèle « supplémentaire » sur la série y (array) :
    Theta, ARIMA, Croston, Moyenne. Retourne un np.array (≥0) ou None si échec.
    Les modèles ETS/Naïf/Moyenne mobile sont déjà fournis par
    calculer_previsions_pour_colonne (on réutilise ces colonnes).
    """
    import warnings
    y = np.nan_to_num(np.asarray(y, dtype=float), nan=0.0)
    S = 12 if str(freq).upper().startswith("M") else (7 if str(freq).upper().startswith("D") else 1)
    try:
        with warnings.catch_warnings():
            # ARIMA/Theta émettent des avertissements de convergence bénins qui
            # noient les logs — on les tait ici (l'échec réel est géré par except).
            warnings.simplefilter("ignore")
            if nom == "Theta":
                from statsmodels.tsa.forecasting.theta import ThetaModel
                fc = ThetaModel(y, period=S).fit().forecast(n_periodes)
                return np.clip(np.asarray(fc, dtype=float), 0, None)
            if nom == "ARIMA":
                from statsmodels.tsa.arima.model import ARIMA
                fc = ARIMA(y, order=(1, 1, 1)).fit().forecast(n_periodes)
                return np.clip(np.asarray(fc, dtype=float), 0, None)
            if nom == "Croston":
                return np.full(n_periodes, max(0.0, _croston(y)))
            if nom == "Moyenne":
                return np.full(n_periodes, max(0.0, float(np.mean(y)) if len(y) else 0.0))
    except Exception:
        return None
    return None


# ==============================================================================
# PROPHET (optionnel)
# ==============================================================================
def verifier_prophet():
    """Vérifie si Prophet est installé. Logue le message d'installation sinon."""
    try:
        from prophet import Prophet  # noqa: F401
        return True
    except ImportError:
        logger.warning(
            "Module 'prophet' non installé (pip install prophet). "
            "Les colonnes Prev_Prophet retomberont sur la décomposition saisonnière."
        )
        return False


def construire_holidays_prophet():
    """Construit le DataFrame des événements (holidays) Prophet à partir des fêtes."""
    lignes = []
    for fete in config.FETES_MAROCAINES:
        debut = pd.Timestamp(fete["debut"])
        fin   = pd.Timestamp(fete["fin"])
        nom   = fete["nom"].replace(" ", "_")
        mois_couverts = pd.date_range(
            start=debut.to_period("M").to_timestamp("M"),
            end=fin.to_period("M").to_timestamp("M"),
            freq="ME"
        )
        for ts in mois_couverts:
            lignes.append({"holiday": nom, "ds": ts, "lower_window": 0, "upper_window": 0})

    promotions = [
        {"nom": "Promo_Ete",   "date": "2024-07-31"},
        {"nom": "Promo_Ete",   "date": "2025-07-31"},
        {"nom": "Promo_Ete",   "date": "2026-07-31"},
        {"nom": "Promo_Hiver", "date": "2024-12-31"},
        {"nom": "Promo_Hiver", "date": "2025-12-31"},
        {"nom": "Promo_Hiver", "date": "2026-12-31"},
    ]
    for promo in promotions:
        lignes.append({"holiday": promo["nom"], "ds": pd.Timestamp(promo["date"]),
                       "lower_window": -1, "upper_window": 0})

    df_holidays = pd.DataFrame(lignes)
    df_holidays["ds"] = pd.to_datetime(df_holidays["ds"])
    return df_holidays


def forecast_prophet(df_agg, col_nom, n_periodes, freq, categorie=None, min_historique=18):
    """
    Entraîne Prophet et génère n_periodes de prévisions avec IC à 80 %.
    Retourne None si Prophet est indisponible ou l'historique insuffisant.
    """
    if not verifier_prophet():
        return None

    from prophet import Prophet
    import logging
    logging.getLogger("prophet").setLevel(logging.WARNING)
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    N = len(df_agg)
    if N < min_historique:
        logger.info("[Prophet] Historique court (%d < %d) pour '%s' → fallback.",
                    N, min_historique, col_nom)
        return None

    df_p = pd.DataFrame({
        "ds": pd.to_datetime(df_agg["Date"]),
        "y":  df_agg[col_nom].clip(lower=0).values.astype(float)
    })
    df_holidays = construire_holidays_prophet()

    freq_clean = str(freq).upper()
    if freq_clean.startswith("D"):
        yearly, weekly = True, True
    elif freq_clean.startswith("W"):
        yearly, weekly = True, False
    else:
        yearly, weekly = True, False

    scale_factor = min(1.0, N / 24.0)
    _seasonality_prior = max(0.1, 1.0 * scale_factor)
    _holidays_prior    = max(0.5, 5.0 * scale_factor)
    _n_changepoints    = max(3, min(25, N // 3))

    modele = Prophet(
        seasonality_mode="multiplicative",
        yearly_seasonality=yearly,
        weekly_seasonality=weekly,
        daily_seasonality=False,
        holidays=df_holidays,
        interval_width=0.80,
        changepoint_prior_scale=0.05,
        seasonality_prior_scale=_seasonality_prior,
        holidays_prior_scale=_holidays_prior,
        n_changepoints=_n_changepoints,
    )

    try:
        modele.fit(df_p)
    except Exception as e:
        logger.warning("[Prophet] Erreur d'entraînement (%s) : %s → fallback.", col_nom, e)
        return None

    derniere_date = pd.to_datetime(df_agg["Date"].iloc[-1])
    dates_futures = pd.date_range(start=derniere_date, periods=n_periodes + 1, freq=freq)[1:]

    df_forecast = modele.predict(pd.DataFrame({"ds": dates_futures}))
    return {
        "previsions":     df_forecast["yhat"].clip(lower=0).values,
        "yhat_lower":     df_forecast["yhat_lower"].clip(lower=0).values,
        "yhat_upper":     df_forecast["yhat_upper"].clip(lower=0).values,
        "dates_futures":  dates_futures,
        "modele_utilise": "prophet",
    }


def backtesting_prophet(df_agg, col_nom, freq, n_test=6, categorie=None):
    """Évalue Prophet par validation temporelle (walk-forward). Retourne MAE/RMSE/MAPE."""
    N = len(df_agg)
    min_train = 12
    if N < n_test + min_train:
        logger.info("[Backtest Prophet] Historique trop court (%d pts) pour %d périodes.",
                    N, n_test)
        return None

    df_train = df_agg.iloc[:-n_test].copy().reset_index(drop=True)
    df_test  = df_agg.iloc[-n_test:].copy().reset_index(drop=True)

    res = forecast_prophet(df_train, col_nom, n_test, freq, categorie, min_historique=min_train)
    if res is None:
        return None

    y_reel = df_test[col_nom].clip(lower=0).values.astype(float)
    y_pred = res["previsions"]

    mae  = float(np.mean(np.abs(y_reel - y_pred)))
    rmse = float(np.sqrt(np.mean((y_reel - y_pred) ** 2)))
    masque = y_reel > 0
    mape   = float(np.mean(np.abs((y_reel[masque] - y_pred[masque]) / y_reel[masque])) * 100) \
             if masque.sum() > 0 else float("nan")

    df_comp = pd.DataFrame({
        "Date": df_test["Date"].values, "Reel": y_reel,
        "Prophet": y_pred, "Erreur_Abs": np.abs(y_reel - y_pred),
    })
    return {
        "MAE": round(mae, 2), "RMSE": round(rmse, 2),
        "MAPE": round(mape, 2) if not np.isnan(mape) else "N/A",
        "df_comparaison": df_comp,
    }

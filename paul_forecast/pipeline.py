# -*- coding: utf-8 -*-
"""
Orchestration du pipeline de prévision & planification MRP.

Étapes : chargement → validation → nettoyage → prévisions par produit →
consolidation → calcul des besoins ingrédients (BOM) → exports (CSV/Excel/PNG)
→ backtesting inter-annuel optionnel.

Améliorations vs script monolithique d'origine :
  - validation des données en entrée (valider_donnees)
  - gestion d'erreur par produit (un produit en échec n'arrête pas le run)
  - exports horodatés (exports/AAAA-MM-JJ/) pour ne jamais écraser un run
  - Prophet limité aux PROPHET_TOP_N produits les plus vendus
  - journalisation via logging (console + fichier) au lieu de print
"""

import os
from datetime import datetime

import numpy as np
import pandas as pd

from . import config
from . import data_loader
from . import forecasting
from . import bom
from . import backtest
from . import reporting
from . import saisonnalite_fetes
from . import evenements
from . import commandes
from . import fetes_api
from . import incertitude
from .logging_setup import configurer_logging, get_logger

logger = get_logger()


def _preparer_dossier_export():
    """Crée et retourne le sous-dossier d'export horodaté (exports/AAAA-MM-JJ/)."""
    horodatage = datetime.now().strftime("%Y-%m-%d")
    output_dir = os.path.join(config.OUTPUT_DIR, horodatage)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _charger_et_preparer():
    """Charge, valide, nettoie les ventes et calcule les colonnes nettes.

    Retourne (df_nettoye, col_date, col_qty, col_rev) ou None si échec.
    """
    # 1. Chargement
    try:
        if config.USE_DOSSIER_MENSUEL:
            logger.info("Mode : lecture des dossiers mensuels → %s", config.DATA_FOLDERS)
            df_brut = data_loader.charger_dossier_mensuel(config.DATA_FOLDERS)
        else:
            df_brut = data_loader.charger_donnees(config.FILE_PATH, config.EXCEL_SHEET)
    except Exception as e:
        logger.error("Impossible de charger les données : %s", e)
        return None

    # Forward-fill colonnes hiérarchiques + drop lignes de totaux (format export PAUL)
    for col_hier in ["Familles", "Nom Familles", config.CATEGORY_COL]:
        if col_hier in df_brut.columns:
            df_brut[col_hier] = df_brut[col_hier].ffill()
    if config.PRODUCT_COL in df_brut.columns:
        masque_valide = (
            df_brut[config.PRODUCT_COL].notna() &
            (df_brut[config.PRODUCT_COL].astype(str).str.strip() != "") &
            ~df_brut[config.PRODUCT_COL].astype(str).str.contains(
                r"S/T|Total|Sous.total|TOTAL", case=False, na=False, regex=True
            )
        )
        df_brut = df_brut[masque_valide].reset_index(drop=True)

    # Diagnostic des colonnes (utile au premier lancement)
    logger.info("[Diagnostic] %d colonnes : %s", len(df_brut.columns), df_brut.columns.tolist())
    logger.info("[Diagnostic] Dimensions : %d lignes × %d colonnes",
                df_brut.shape[0], df_brut.shape[1])

    # 2. Validation des données en entrée (lève ValueError si structure invalide)
    data_loader.valider_donnees(df_brut)

    # Détection des colonnes de base
    col_date = data_loader.detecter_colonne_date(df_brut, config.DATE_COL)
    col_qty  = data_loader.detecter_colonne_num(
        df_brut, config.QTY_COL, col_date, ["qt", "qty", "quantite", "volume"])
    col_rev  = data_loader.detecter_colonne_num(
        df_brut, config.REV_COL, col_date, ["ca ht", "ca", "ventes", "sales", "valeur"])

    # Nettoyage global
    df_nettoye = data_loader.nettoyer_donnees(df_brut, col_date, col_qty, col_rev)

    # Nettoyage des colonnes de retour (coerce + fillna 0)
    for col_retour in [config.QTY_RETURN_COL, config.CA_RETURN_COL]:
        if col_retour in df_nettoye.columns:
            df_nettoye[col_retour] = pd.to_numeric(df_nettoye[col_retour], errors="coerce").fillna(0)
        else:
            logger.warning("Colonne '%s' absente — retours supposés nuls.", col_retour)
            df_nettoye[col_retour] = 0

    # Quantités et CA nets : ventes brutes - retours (clip ≥ 0)
    df_nettoye["QT_Net"] = (df_nettoye[col_qty] - df_nettoye[config.QTY_RETURN_COL]).clip(lower=0)
    df_nettoye["CA_Net"] = (df_nettoye[col_rev] - df_nettoye[config.CA_RETURN_COL]).clip(lower=0)
    col_qty, col_rev = "QT_Net", "CA_Net"
    logger.info("[OK] Colonnes nettes calculées : QT_Net et CA_Net.")

    if config.PRODUCT_COL not in df_nettoye.columns:
        raise ValueError(
            f"Colonne produit '{config.PRODUCT_COL}' introuvable. "
            "Vérifiez PRODUCT_COL dans la config.")

    # Filtre par famille (optionnel)
    if config.FAMILLES_FILTRE:
        avant = len(df_nettoye)
        df_nettoye = df_nettoye[
            df_nettoye[config.CATEGORY_COL].isin(config.FAMILLES_FILTRE)].reset_index(drop=True)
        logger.info("[Filtre familles] %d → %d lignes conservées (%s)",
                    avant, len(df_nettoye), config.FAMILLES_FILTRE)

    # Filtre seuil d'activité : retirer les produits trop faibles
    if config.SEUIL_QT_MIN > 0:
        qt_par_produit = df_nettoye.groupby(config.PRODUCT_COL)["QT_Net"].sum()
        produits_actifs = qt_par_produit[qt_par_produit >= config.SEUIL_QT_MIN].index
        avant = df_nettoye[config.PRODUCT_COL].nunique()
        df_nettoye = df_nettoye[
            df_nettoye[config.PRODUCT_COL].isin(produits_actifs)].reset_index(drop=True)
        logger.info("[Filtre activité] %d → %d produits (seuil QT_Net ≥ %s)",
                    avant, df_nettoye[config.PRODUCT_COL].nunique(), config.SEUIL_QT_MIN)

    return df_nettoye, col_date, col_qty, col_rev


def _selectionner_produits_prophet(df_nettoye):
    """Retourne l'ensemble des produits éligibles à Prophet (top-N par volume)."""
    if not config.ACTIVER_PROPHET:
        return set()
    if config.PROPHET_TOP_N is None:
        return set(df_nettoye[config.PRODUCT_COL].unique())
    top = (df_nettoye.groupby(config.PRODUCT_COL)["QT_Net"].sum()
           .sort_values(ascending=False).head(config.PROPHET_TOP_N).index)
    logger.info("[Prophet] Limité aux %d produits les plus vendus (PROPHET_TOP_N).",
                len(top))
    return set(top)


def _prevoir_un_produit(prod, df_prod, col_date, col_qty, col_rev,
                        dates_communes, prophet_autorise):
    """Calcule l'historique agrégé et les prévisions d'un produit.

    Retourne (df_agg, df_fc). Lève une exception si le calcul échoue
    (interceptée par l'appelant pour ne pas interrompre tout le run).
    """
    df_agg = data_loader.agreger_serie(
        df_prod, col_date, col_qty, col_rev,
        config.AGG_FREQ, config.AGG_METHOD)

    # Reindexer sur la plage commune : mois manquants → 0
    df_agg = (df_agg.set_index("Date")
              .reindex(dates_communes, fill_value=0)
              .reset_index().rename(columns={"index": "Date"}))

    df_fc_qty = forecasting.calculer_previsions_pour_colonne(
        df_agg, "Quantite", config.FORECAST_PERIODS, config.MA_WINDOW, config.AGG_FREQ)
    df_fc_rev = forecasting.calculer_previsions_pour_colonne(
        df_agg, "Valeur", config.FORECAST_PERIODS, config.MA_WINDOW, config.AGG_FREQ)

    df_fc = pd.DataFrame({"Date": df_fc_qty["Date"]})
    modeles_base = ["Prev_Moyenne_Mobile", "Prev_Tendance", "Prev_Naif_Saisonnier",
                    "Prev_Decompo_Saisonniere", "Prev_Holt_Winters"]
    for col in modeles_base:
        if col in df_fc_qty.columns:
            df_fc[f"Qty_{col}"] = df_fc_qty[col]
            df_fc[f"Rev_{col}"] = df_fc_rev[col]

    # Prophet : uniquement si actif ET produit dans le top-N
    if config.ACTIVER_PROPHET:
        if prophet_autorise:
            categorie_prod = (df_prod[config.CATEGORY_COL].iloc[0]
                              if config.CATEGORY_COL in df_prod.columns else None)
            res_qty_p = forecasting.forecast_prophet(
                df_agg, "Quantite", config.FORECAST_PERIODS, config.AGG_FREQ,
                categorie_prod, config.MIN_HISTORIQUE_PROPHET)
            res_rev_p = forecasting.forecast_prophet(
                df_agg, "Valeur", config.FORECAST_PERIODS, config.AGG_FREQ,
                categorie_prod, config.MIN_HISTORIQUE_PROPHET)
        else:
            res_qty_p = res_rev_p = None

        if res_qty_p is not None:
            df_fc["Qty_Prev_Prophet"]       = res_qty_p["previsions"]
            df_fc["Qty_Prev_Prophet_Lower"] = res_qty_p["yhat_lower"]
            df_fc["Qty_Prev_Prophet_Upper"] = res_qty_p["yhat_upper"]
        else:
            df_fc["Qty_Prev_Prophet"]       = df_fc["Qty_Prev_Decompo_Saisonniere"]
            df_fc["Qty_Prev_Prophet_Lower"] = df_fc["Qty_Prev_Decompo_Saisonniere"] * 0.85
            df_fc["Qty_Prev_Prophet_Upper"] = df_fc["Qty_Prev_Decompo_Saisonniere"] * 1.15

        if res_rev_p is not None:
            df_fc["Rev_Prev_Prophet"]       = res_rev_p["previsions"]
            df_fc["Rev_Prev_Prophet_Lower"] = res_rev_p["yhat_lower"]
            df_fc["Rev_Prev_Prophet_Upper"] = res_rev_p["yhat_upper"]
        else:
            df_fc["Rev_Prev_Prophet"]       = df_fc["Rev_Prev_Decompo_Saisonniere"]
            df_fc["Rev_Prev_Prophet_Lower"] = df_fc["Rev_Prev_Decompo_Saisonniere"] * 0.85
            df_fc["Rev_Prev_Prophet_Upper"] = df_fc["Rev_Prev_Decompo_Saisonniere"] * 1.15

    # ── SÉLECTION DU MODÈLE PAR PRODUIT (banc d'essai) ──────────────────────
    # Colonne *_Prev_Selection = prévision du meilleur modèle pour CE produit.
    _BASE = {"ETS": "Holt_Winters", "SeasonalNaive": "Naif_Saisonnier",
             "MoyenneMobile": "Moyenne_Mobile"}
    nom_sel = config.MODELE_PAR_PRODUIT.get(str(prod), "ETS")

    def _selection(prefixe, y_hist):
        # Modèles déjà calculés → on copie la colonne existante.
        if nom_sel in _BASE:
            return df_fc[f"{prefixe}_Prev_{_BASE[nom_sel]}"].values
        if nom_sel == "Ensemble":
            hw = df_fc[f"{prefixe}_Prev_Holt_Winters"].values
            th = forecasting.prevision_modele(y_hist, config.FORECAST_PERIODS, config.AGG_FREQ, "Theta")
            ar = forecasting.prevision_modele(y_hist, config.FORECAST_PERIODS, config.AGG_FREQ, "ARIMA")
            parts = [a for a in (hw, th, ar) if a is not None]
            return np.mean(parts, axis=0)
        if nom_sel == "EnsembleMedian":
            hw = df_fc[f"{prefixe}_Prev_Holt_Winters"].values
            sn = df_fc[f"{prefixe}_Prev_Naif_Saisonnier"].values
            th = forecasting.prevision_modele(y_hist, config.FORECAST_PERIODS, config.AGG_FREQ, "Theta")
            ar = forecasting.prevision_modele(y_hist, config.FORECAST_PERIODS, config.AGG_FREQ, "ARIMA")
            parts = [a for a in (hw, sn, th, ar) if a is not None]
            return np.median(parts, axis=0)
        if nom_sel == "GBM":
            # Placeholder : la prévision du modèle GLOBAL est calculée une fois
            # pour tous les produits après la boucle (pipeline.run), qui écrase
            # cette colonne. Repli = Holt-Winters si le GBM échoue.
            return df_fc[f"{prefixe}_Prev_Holt_Winters"].values
        fc = forecasting.prevision_modele(y_hist, config.FORECAST_PERIODS, config.AGG_FREQ, nom_sel)
        return fc if fc is not None else df_fc[f"{prefixe}_Prev_Holt_Winters"].values

    df_fc["Qty_Prev_Selection"] = _selection("Qty", df_agg["Quantite"].values)
    df_fc["Rev_Prev_Selection"] = _selection("Rev", df_agg["Valeur"].values)

    return df_agg, df_fc


def _appliquer_modele_global(dict_prevision_prod, dict_historique_prod, produit_famille):
    """
    Prévisions du modèle GLOBAL (GBM, cf. modele_global) pour les produits dont
    le banc d'essai a retenu "GBM" : écrase leurs colonnes *_Prev_Selection.
    Le CA = quantité prévue × prix unitaire moyen des 6 derniers mois actifs.
    """
    cibles = [p for p in dict_prevision_prod
              if config.MODELE_PAR_PRODUIT.get(str(p)) == "GBM"]
    if not cibles:
        return
    try:
        from . import modele_global
        series = {p: h["Quantite"].to_numpy(dtype=float)
                  for p, h in dict_historique_prod.items()}
        dates_futures = list(dict_prevision_prod[cibles[0]]["Date"])
        preds = modele_global.previsions_globales(series, produit_famille, dates_futures)
    except Exception as e:
        logger.warning("[GBM] Modèle global indisponible (%s) — repli Holt-Winters.", e)
        return
    n_ok = 0
    for p in cibles:
        q = preds.get(p)
        if q is None:
            continue
        h = dict_historique_prod[p]
        actifs = h[h["Quantite"] > 0].tail(6)
        prix = (actifs["Valeur"].sum() / actifs["Quantite"].sum()) if len(actifs) else 0.0
        df_fc = dict_prevision_prod[p]
        df_fc["Qty_Prev_Selection"] = q
        df_fc["Rev_Prev_Selection"] = q * max(prix, 0.0)
        n_ok += 1
    logger.info("[GBM] Prévisions globales appliquées à %d/%d produits mappés GBM.",
                n_ok, len(cibles))


def _reconcilier_selection(dict_prevision_prod, dict_historique_prod, dates_communes):
    """
    Réconciliation top-down : multiplie les colonnes *_Prev_Selection de chaque
    produit par un facteur mensuel pour que leur SOMME rejoigne la prévision
    agrégée Holt-Winters (série totale). Facteur borné [0.7, 1.4].

    Les colonnes *_Prev_Holt_Winters sont calées de la même façon : la somme
    de 1 000+ fits produit bruités dérive systématiquement au-dessus de
    l'agrégat (les baisses sont bornées à 0, pas les hausses) et donnait une
    courbe totale incohérente avec les niveaux des années précédentes.
    """
    if not dict_prevision_prod:
        return
    # séries agrégées historiques (mêmes dates pour tous les produits)
    qty_tot = None
    rev_tot = None
    for h in dict_historique_prod.values():
        q = h.set_index("Date")["Quantite"]
        r = h.set_index("Date")["Valeur"]
        qty_tot = q if qty_tot is None else qty_tot.add(q, fill_value=0.0)
        rev_tot = r if rev_tot is None else rev_tot.add(r, fill_value=0.0)
    df_tot = pd.DataFrame({"Date": qty_tot.index, "Quantite": qty_tot.values,
                           "Valeur": rev_tot.values}).sort_values("Date")
    fc_tot = forecasting.calculer_previsions_pour_colonne(
        df_tot, "Quantite", config.FORECAST_PERIODS, config.MA_WINDOW, config.AGG_FREQ)
    fc_rev = forecasting.calculer_previsions_pour_colonne(
        df_tot, "Valeur", config.FORECAST_PERIODS, config.MA_WINDOW, config.AGG_FREQ)
    cible_qty = dict(zip(pd.to_datetime(fc_tot["Date"]), fc_tot["Prev_Holt_Winters"]))
    cible_rev = dict(zip(pd.to_datetime(fc_rev["Date"]), fc_rev["Prev_Holt_Winters"]))

    exemple = next(iter(dict_prevision_prod.values()))
    dates = [pd.Timestamp(d) for d in exemple["Date"]]
    def _facteurs(col_qty, col_rev, borne_bas, borne_haut):
        somme_q = np.zeros(len(dates))
        somme_r = np.zeros(len(dates))
        for df_fc in dict_prevision_prod.values():
            somme_q += np.nan_to_num(df_fc[col_qty].to_numpy(dtype=float))
            somme_r += np.nan_to_num(df_fc[col_rev].to_numpy(dtype=float))
        f_q, f_r = np.ones(len(dates)), np.ones(len(dates))
        for i, d in enumerate(dates):
            if somme_q[i] > 0 and d in cible_qty and np.isfinite(cible_qty[d]):
                f_q[i] = float(np.clip(cible_qty[d] / somme_q[i], borne_bas, borne_haut))
            if somme_r[i] > 0 and d in cible_rev and np.isfinite(cible_rev[d]):
                f_r[i] = float(np.clip(cible_rev[d] / somme_r[i], borne_bas, borne_haut))
        return f_q, f_r

    f_q, f_r = _facteurs("Qty_Prev_Selection", "Rev_Prev_Selection", 0.7, 1.4)
    # HW → HW agrégé : même modèle aux deux niveaux, calage quasi complet
    # (bornes larges, simple garde-fou contre un agrégat aberrant).
    g_q, g_r = _facteurs("Qty_Prev_Holt_Winters", "Rev_Prev_Holt_Winters", 0.5, 1.5)

    for df_fc in dict_prevision_prod.values():
        df_fc["Qty_Prev_Selection"] = df_fc["Qty_Prev_Selection"].to_numpy(dtype=float) * f_q
        df_fc["Rev_Prev_Selection"] = df_fc["Rev_Prev_Selection"].to_numpy(dtype=float) * f_r
        df_fc["Qty_Prev_Holt_Winters"] = df_fc["Qty_Prev_Holt_Winters"].to_numpy(dtype=float) * g_q
        df_fc["Rev_Prev_Holt_Winters"] = df_fc["Rev_Prev_Holt_Winters"].to_numpy(dtype=float) * g_r
    logger.info("[Réconciliation] facteur quantité mois prochain ×%.2f, CA ×%.2f "
                "(somme produits calée sur l'agrégat Holt-Winters).", f_q[0], f_r[0])


def _calculer_besoins_mrp(produits_uniques, dict_prevision_prod, produit_famille=None):
    """Éclate les prévisions produit en besoins ingrédients via les BOM.

    Priorité : recette exacte > détection par motif du nom > recette GÉNÉRIQUE
    de la famille (longue traîne food) > rien (produits revendus tels quels).
    """
    produit_famille = produit_famille or {}
    mod_prev_principal = "Prev_Prophet" if config.ACTIVER_PROPHET else "Prev_Selection"
    liste_besoins = []
    nb_exact = nb_auto = nb_generique = nb_manquant = nb_revendu = 0

    for prod in produits_uniques:
        # Familles achetées/revendues (ex. viennoiserie import) : produit fini,
        # aucune matière première à commander -> exclu du MRP.
        fam_prod_maj = str(produit_famille.get(prod, "")).strip().upper()
        if fam_prod_maj in config.FAMILLES_REVENDUES:
            nb_revendu += 1
            continue

        recette_brute = config.BOM.get(prod)
        if recette_brute:
            nb_exact += 1
            # Exacte seulement si la provenance est validée (fiche chef réelle) ;
            # sinon c'est une estimation stockée dans le même fichier.
            prov = config.PROVENANCE_RECETTES.get(prod)
            source_prov = prov.get("source", "") if isinstance(prov, dict) else str(prov or "")
            source_couv = ("recette_exacte" if source_prov in config.SOURCES_RECETTE_EXACTE
                           else "estimation")
        else:
            recette_brute = bom.detecter_bom_produit(prod)
            if recette_brute:
                nb_auto += 1
                source_couv = "estimation"
            else:
                recette_brute = bom.recette_generique_famille(
                    prod, produit_famille.get(prod))
                if recette_brute:
                    nb_generique += 1
                    source_couv = "estimation"
                else:
                    nb_manquant += 1
                    continue

        recette_eclatee = bom.exploser_psf(recette_brute)   # PSF → matières de base
        recette = bom.normaliser_bom(recette_eclatee)
        if not recette:
            continue

        famille_prod = str(produit_famille.get(prod, "Autres")).strip() or "Autres"
        df_fc_prod = dict_prevision_prod[prod]
        for _, row in df_fc_prod.iterrows():
            date_prev = row["Date"]
            quantite_prevue = row[f"Qty_{mod_prev_principal}"]
            recette_adj = bom.ajuster_bom_ramadan(recette, date_prev)
            for ingredient, ratio in recette_adj.items():
                liste_besoins.append({
                    "Date": date_prev,
                    "Famille": famille_prod,
                    "Produit": prod,
                    "Ingredient": ingredient,
                    "Quantite_Requise": round(quantite_prevue * ratio, 2),
                    "Source_Couverture": source_couv,
                })

    logger.info("[BOM] Exact=%d | Auto=%d | Générique=%d | Sans BOM=%d | Revendu=%d | "
                "Couverts=%d/%d", nb_exact, nb_auto, nb_generique, nb_manquant, nb_revendu,
                nb_exact + nb_auto + nb_generique, len(produits_uniques))

    colonnes_detail = ["Date", "Famille", "Produit", "Ingredient",
                       "Quantite_Requise", "Source_Couverture"]
    df_besoins_bruts = pd.DataFrame(liste_besoins)
    if df_besoins_bruts.empty or "Date" not in df_besoins_bruts.columns:
        logger.info("[Info] Aucune nomenclature renseignée — section MRP ignorée.")
        return (pd.DataFrame(columns=["Date", "Ingredient", "Quantite_Requise"]),
                pd.DataFrame(columns=colonnes_detail))

    def _calibrer(df):
        """Applique CALIBRAGE_MATIERES (farine de production absente des recettes)."""
        for motif, facteur in getattr(config, "CALIBRAGE_MATIERES", []):
            masque = df["Ingredient"].str.contains(motif, case=False, na=False)
            df.loc[masque, "Quantite_Requise"] *= facteur
        return df

    # Détail par produit ET département (traçabilité : « pour faire X ce mois-ci,
    # le département Y a besoin de Z g de … »). Même calibrage que l'agrégé.
    df_detail = (df_besoins_bruts
                 .groupby(["Date", "Famille", "Produit", "Ingredient",
                           "Source_Couverture"], as_index=False)
                 ["Quantite_Requise"].sum())
    df_detail = _calibrer(df_detail)
    df_detail["Quantite_Requise"] = df_detail["Quantite_Requise"].round(2)

    # Agrégé par ingrédient (bon de commande global) — inchangé pour compatibilité.
    df_agg = (df_besoins_bruts.groupby(["Date", "Ingredient"])["Quantite_Requise"]
              .sum().reset_index())
    df_agg = _calibrer(df_agg)
    for motif, facteur in getattr(config, "CALIBRAGE_MATIERES", []):
        n = int(df_agg["Ingredient"].str.contains(motif, case=False, na=False).sum())
        if n:
            logger.info("[Calibrage] '%s' ×%.2f appliqué à %d ligne(s).", motif, facteur, n)
    return df_agg, df_detail


def _exporter_besoins_par_categorie(df_besoins_detail, output_dir):
    """
    Bon de commande SEGMENTÉ par catégorie : un fichier
    besoins_ingredients_<categorie>.csv par catégorie de production
    (cf. config.CATEGORIES_BESOINS), avec la colonne Source_Couverture
    (« recette_exacte » / « estimation ») pour que le gérant puisse
    n'utiliser QUE les chiffres fiables (boulangerie = recettes chef)
    et traiter le reste comme indicatif en attendant les recettes chef.
    """
    if df_besoins_detail is None or df_besoins_detail.empty:
        return
    fam_maj = df_besoins_detail["Famille"].astype(str).str.strip().str.upper()
    for categorie in config.CATEGORIES_BESOINS:
        d = df_besoins_detail[fam_maj == categorie]
        if d.empty:
            logger.info("[Besoins segmentés] %s : aucune ligne — fichier non écrit.",
                        categorie)
            continue
        agg = (d.groupby(["Date", "Ingredient", "Source_Couverture"], as_index=False)
               ["Quantite_Requise"].sum()
               .sort_values(["Date", "Quantite_Requise"], ascending=[True, False]))
        agg["Quantite_Requise"] = agg["Quantite_Requise"].round(2)
        nom = f"besoins_ingredients_{categorie.lower()}.csv"
        agg.to_csv(os.path.join(output_dir, nom),
                   index=False, sep=";", encoding="utf-8")
        part_exacte = (d.loc[d["Source_Couverture"] == "recette_exacte",
                             "Quantite_Requise"].sum()
                       / max(d["Quantite_Requise"].sum(), 1e-9) * 100)
        logger.info("[Besoins segmentés] %s → %s (%.0f%% du besoin en recettes exactes)",
                    categorie, nom, part_exacte)


def _completude_par_categorie(df_besoins_detail):
    """Part du besoin matières (en quantité) issue de recettes exactes, par catégorie.

    Retourne {categorie: % exact}. Approximation volontaire : les quantités
    mélangent g/ml/unités, mais le besoin est dominé par les grammes — suffisant
    comme indicateur de fiabilité du bon de commande.
    """
    if df_besoins_detail is None or df_besoins_detail.empty \
            or "Source_Couverture" not in df_besoins_detail.columns:
        return {}
    fam_maj = df_besoins_detail["Famille"].astype(str).str.strip().str.upper()
    res = {}
    for categorie in config.CATEGORIES_BESOINS:
        d = df_besoins_detail[fam_maj == categorie]
        tot = d["Quantite_Requise"].sum()
        if tot <= 0:
            continue
        exact = d.loc[d["Source_Couverture"] == "recette_exacte",
                      "Quantite_Requise"].sum()
        res[categorie] = exact / tot * 100
    return res


def _validation_terrain_matieres(df_besoins_mrp, output_dir, df_besoins_detail=None):
    """
    Compare le besoin matières NET (issu des ventes) aux repères terrain du chef
    (ex : 3 t de farine / 3 semaines). N'altère PAS les quantités commandées :
    affiche seulement l'écart, car le besoin net ≠ achat réel (surproduction,
    invendus, pertes). Écrit validation_matieres_terrain.txt.
    """
    if df_besoins_mrp is None or df_besoins_mrp.empty or not config.REFERENCES_TERRAIN:
        return
    JOURS_MOIS = 30.44
    premier_mois = sorted(df_besoins_mrp["Date"].unique())[0]
    d = df_besoins_mrp[df_besoins_mrp["Date"] == premier_mois]
    mois_str = pd.to_datetime(premier_mois).strftime("%Y-%m")

    lignes = []
    for nom, ref in config.REFERENCES_TERRAIN.items():
        masque = d["Ingredient"].str.contains(ref["motif"], case=False, na=False)
        total_kg = d.loc[masque, "Quantite_Requise"].sum() / 1000.0
        ref_mois = ref["kg"] / ref["jours"] * JOURS_MOIS
        couv = (total_kg / ref_mois * 100) if ref_mois else 0.0
        lignes.append((nom, total_kg, ref_mois, couv, ref["commentaire"]))

    chemin = os.path.join(output_dir, "validation_matieres_terrain.txt")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("VALIDATION DU BESOIN MATIÈRES vs REPÈRES TERRAIN (CHEF)\n")
        f.write(f"Mois évalué : {mois_str}\n")
        f.write("=" * 70 + "\n\n")
        f.write("Besoin calculé depuis les ventes prévues, AVEC calibrage des\n")
        f.write("matières (cf. CALIBRAGE_MATIERES) calé sur les repères du chef :\n")
        f.write("le facteur intègre la farine de production absente des recettes\n")
        f.write("finies (fleurage, tourage, manipulation). Le mois évalué peut\n")
        f.write("rester sous le repère s'il est creux (saisonnalité, ex. janvier).\n\n")
        for nom, total_kg, ref_mois, couv, comm in lignes:
            f.write(f"[{nom}]\n")
            f.write(f"  Besoin estimé (calibré)      : {total_kg:>9,.0f} kg/mois\n")
            f.write(f"  Repère chef (consommation)   : {ref_mois:>9,.0f} kg/mois  ({comm})\n")
            f.write(f"  Couverture                   : {couv:>9,.0f} %  "
                    f"(écart ×{ref_mois/total_kg:,.2f})\n" if total_kg else
                    "  Couverture                   :       n/a\n")
            f.write("\n")

        couv_cat = _completude_par_categorie(df_besoins_detail)
        if couv_cat:
            f.write("-" * 70 + "\n")
            f.write("FIABILITÉ DU BON DE COMMANDE PAR CATÉGORIE\n")
            f.write("(part du besoin matières issue de recettes EXACTES — fiches chef)\n\n")
            for cat, pct in couv_cat.items():
                if pct >= 95:
                    repere = "recettes chef validées"
                elif pct >= 60:
                    repere = "majoritairement recettes chef — reste à compléter"
                else:
                    repere = "estimations — en attente des recettes chef"
                f.write(f"  {cat:<14}: {pct:>5.0f} % exact  ({repere})\n")
            for fam in sorted(config.FAMILLES_REVENDUES):
                f.write(f"  {fam:<14}:   revendu  (produits achetés — hors bon "
                        f"de commande matières)\n")
            # Fiabilité globale : moyenne des % exacts pondérée par le poids
            # (quantité de matières) de chaque catégorie dans le bon de commande.
            fam_maj = df_besoins_detail["Famille"].astype(str).str.strip().str.upper()
            poids = {c: df_besoins_detail.loc[fam_maj == c, "Quantite_Requise"].sum()
                     for c in couv_cat}
            tot_p = sum(poids.values())
            if tot_p > 0:
                glob = sum(couv_cat[c] * poids[c] for c in couv_cat) / tot_p
                f.write(f"\n  FIABILITÉ GLOBALE DU BON DE COMMANDE : {glob:.0f} % "
                        f"(pondérée par le poids matières de chaque catégorie)\n")
            fiables = [c for c in couv_cat if couv_cat[c] >= 60]
            indicatifs = [c for c in couv_cat if couv_cat[c] < 60]
            if fiables:
                f.write(f"\n  → Fiables (≥60 % exact) : {' + '.join(fiables)} "
                        f"— utilisables tels quels.\n")
            if indicatifs:
                moy = sum(couv_cat[c] for c in indicatifs) / len(indicatifs)
                f.write(f"  → À compléter : {' + '.join(indicatifs)} "
                        f"({moy:.0f} % exact en moyenne) — chiffres indicatifs.\n")
            f.write("\n")
        f.write("=" * 70 + "\n")
        f.write("Lecture : après calibrage, un mois NORMAL doit approcher 100% du\n")
        f.write("repère ; un mois creux (janvier) reste en dessous, un mois de fête\n")
        f.write("au-dessus. Le calibrage (×1.42 farine) sera remplacé/affiné dès\n")
        f.write("réception des vraies recettes du chef.\n")
    logger.info("Validation matières/terrain écrite : '%s'", chemin)
    for nom, total_kg, ref_mois, couv, _ in lignes:
        logger.info("  [%s] besoin net %.0f kg/mois vs repère %.0f kg/mois (%.0f%%)",
                    nom, total_kg, ref_mois, couv)


def _preparation_fetes(dict_prevision_prod, produit_famille, df_besoins_mrp, output_dir):
    """
    Fiche de préparation des mois de fête (Ramadan, Aïd el-Fitr/Adha, Achoura,
    Mawlid) : détail par famille et matières premières clés à prévoir, pour être
    prêt le jour J. Le mix bascule fortement même quand le CA total bouge peu.
    """
    fenetres_par_type = saisonnalite_fetes._fenetres_par_type()
    if not fenetres_par_type or not dict_prevision_prod:
        return
    toutes_fenetres = [w for ws in fenetres_par_type.values() for w in ws]
    exemple = next(iter(dict_prevision_prod.values()))
    fractions, types_par_mois = {}, {}
    for d in exemple["Date"]:
        d = pd.Timestamp(d)
        fractions[d] = saisonnalite_fetes._fraction_du_mois(d, toutes_fenetres)
        types_par_mois[d] = {typ: saisonnalite_fetes._fraction_du_mois(d, ws)
                             for typ, ws in fenetres_par_type.items()
                             if saisonnalite_fetes._fraction_du_mois(d, ws) > 0}
    mois_fete = sorted([d for d, f in fractions.items() if f > 0])
    if not mois_fete:
        return

    qcol, rcol = "Qty_Prev_Holt_Winters", "Rev_Prev_Holt_Winters"
    chemin = os.path.join(output_dir, "preparation_fetes.txt")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("FICHE DE PRÉPARATION — MOIS DE FÊTE (RAMADAN, AÏD, ACHOURA, MAWLID)\n")
        f.write("=" * 72 + "\n")
        f.write("Pendant ces périodes le MIX produits bascule : quantités prévues par\n")
        f.write("famille et matières clés ci-dessous (Holt-Winters ajusté des profils\n")
        f.write("de fête). Ramadan = profil mesuré ; autres fêtes = hypothèses à\n")
        f.write("calibrer (cf. data/profils_fetes.json).\n\n")

        for d in mois_fete:
            libelle = ", ".join(f"{typ} {fr*100:.0f}%"
                                for typ, fr in sorted(types_par_mois[d].items()))
            f.write("█" * 72 + "\n")
            f.write(f" {pd.Timestamp(d).strftime('%B %Y').upper()}  ({libelle})\n")
            f.write("█" * 72 + "\n")

            fam_q, fam_r = {}, {}
            for prod, df_fc in dict_prevision_prod.items():
                row = df_fc[df_fc["Date"] == d]
                if row.empty:
                    continue
                fam = str(produit_famille.get(prod, "Autres")).strip() or "Autres"
                fam_q[fam] = fam_q.get(fam, 0.0) + float(row[qcol].iloc[0])
                fam_r[fam] = fam_r.get(fam, 0.0) + float(row[rcol].iloc[0])

            f.write("\n  Ventes prévues par famille :\n")
            f.write(f"  {'Famille':<26}{'Quantité':>12}{'CA (MAD)':>14}\n")
            f.write("  " + "-" * 52 + "\n")
            for fam in sorted(fam_r, key=lambda x: -fam_r[x]):
                f.write(f"  {fam[:25]:<26}{fam_q[fam]:>12,.0f}{fam_r[fam]:>14,.0f}\n")
            f.write(f"  {'TOTAL':<26}{sum(fam_q.values()):>12,.0f}{sum(fam_r.values()):>14,.0f}\n")

            if df_besoins_mrp is not None and not df_besoins_mrp.empty:
                dm = df_besoins_mrp[df_besoins_mrp["Date"] == d]
                if not dm.empty:
                    f.write("\n  Matières premières clés à prévoir (top 15) :\n")
                    top = dm.sort_values("Quantite_Requise", ascending=False).head(15)
                    for _, r in top.iterrows():
                        q = r["Quantite_Requise"]
                        val = f"{q/1000:,.1f} kg" if q >= 1000 else f"{q:,.0f} g"
                        f.write(f"    - {str(r['Ingredient'])[:42]:<44}{val:>12}\n")
            f.write("\n")
    logger.info("Fiche de préparation des fêtes écrite : '%s'", chemin)


def _synthese_mois_prochain(df_prev_total, res_wf_ca, res_wf_qte, output_dir):
    """Synthèse du livrable clé : CA et quantité prévus pour le mois prochain,
    avec le modèle recommandé par la validation glissante (plus faible erreur à 1 mois).
    """
    if df_prev_total.empty:
        return
    ligne = df_prev_total.iloc[0]
    date_prochaine = pd.to_datetime(ligne["Date"]).strftime("%Y-%m")

    # Garde-fou : si le « mois prochain » est déjà passé, les ventes chargées
    # ne sont plus à jour (dernier mois complet trop ancien).
    if date_prochaine < datetime.now().strftime("%Y-%m"):
        logger.warning("[Données périmées] La prévision cible %s, un mois déjà écoulé : "
                       "mettez à jour donnees_ventes/ventes_journalieres.csv (et les xlsx "
                       "mensuels) puis relancez le calcul.", date_prochaine)

    mod_ca  = res_wf_ca["meilleur_modele"]  if res_wf_ca  else "Holt_Winters"
    mod_qte = res_wf_qte["meilleur_modele"] if res_wf_qte else "Holt_Winters"
    # Chiffres annoncés = colonne Selection (somme des meilleurs modèles par
    # produit, réconciliée sur l'agrégat + fêtes/événements/commandes B2B) :
    # c'est le total que suivent réellement le plan de production, le MRP et le
    # dashboard. Repli sur le meilleur modèle agrégé si la colonne manque.
    col_ca  = "Rev_Prev_Selection" if "Rev_Prev_Selection" in df_prev_total.columns \
              else f"Rev_Prev_{mod_ca}"
    col_qte = "Qty_Prev_Selection" if "Qty_Prev_Selection" in df_prev_total.columns \
              else f"Qty_Prev_{mod_qte}"
    ca_prev  = float(ligne[col_ca])  if col_ca  in df_prev_total.columns else float("nan")
    qte_prev = float(ligne[col_qte]) if col_qte in df_prev_total.columns else float("nan")
    mape_ca  = res_wf_ca["metriques"][mod_ca]["MAPE"]   if res_wf_ca  else "N/A"
    mape_qte = res_wf_qte["metriques"][mod_qte]["MAPE"] if res_wf_qte else "N/A"

    # Fourchette au niveau de service (à partir du RMSE de la validation glissante).
    z = incertitude.z_service()
    rmse_ca  = res_wf_ca["metriques"][mod_ca]["RMSE"]   if res_wf_ca  else None
    rmse_qte = res_wf_qte["metriques"][mod_qte]["RMSE"] if res_wf_qte else None
    ca_bas  = max(0, ca_prev  - z * rmse_ca)  if rmse_ca  else float("nan")
    ca_haut = ca_prev  + z * rmse_ca          if rmse_ca  else float("nan")
    qt_bas  = max(0, qte_prev - z * rmse_qte) if rmse_qte else float("nan")
    qt_haut = qte_prev + z * rmse_qte         if rmse_qte else float("nan")
    svc = int(config.NIVEAU_SERVICE * 100)

    # Le panel (xlsx) est en HORS TAXES : annoncer aussi l'équivalent TTC, sinon
    # le gérant compare au total caisse (TTC) et croit à un gros écart (~10 %).
    ratio_ttc = data_loader.ratio_ttc_ht_estime()
    lignes = [
        "=" * 60,
        f" PRÉVISION DU MOIS PROCHAIN : {date_prochaine}",
        "=" * 60,
        f" Chiffre d'affaires prévu : {ca_prev:,.0f} MAD HT"
        f"   (≈ {ca_prev * ratio_ttc:,.0f} MAD TTC caisse)",
        f"   fourchette : {ca_bas:,.0f} – {ca_haut:,.0f} MAD HT",
        f"   → sélection par produit, calée sur {mod_ca.replace('_', ' ')} "
        f"(erreur ~{mape_ca}% à 1 mois)",
        f" Quantité prévue        : {qte_prev:,.0f} articles",
        f"   fourchette : {qt_bas:,.0f} – {qt_haut:,.0f} articles",
        f"   → sélection par produit, calée sur {mod_qte.replace('_', ' ')} "
        f"(erreur ~{mape_qte}% à 1 mois)",
        "-" * 60,
        f" Fourchettes au niveau de service {svc}% · détail produit par produit",
        f" (quantité à commander pour éviter la rupture) :",
        f"   → plan_production_securise.csv",
        "=" * 60,
    ]
    for ligne_txt in lignes:
        logger.info(ligne_txt)
    chemin = os.path.join(output_dir, "synthese_mois_prochain.txt")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")
    logger.info("Synthèse mois prochain sauvegardée : '%s'", chemin)


def run():
    """Point d'entrée du pipeline complet."""
    configurer_logging()
    logger.info("=" * 60)
    logger.info(" PIPELINE DE PLANIFICATION & PRÉVISIONS INGRÉDIENTS (MRP)")
    logger.info("=" * 60)

    # Rafraîchissement optionnel des dates de fêtes via l'API (sinon cache JSON).
    if config.RAFRAICHIR_FETES_API:
        annee = datetime.now().year
        res_fetes = fetes_api.mettre_a_jour_fetes(range(annee, annee + 3))
        if res_fetes:
            config.FETES_MAROCAINES = res_fetes

    prepare = _charger_et_preparer()
    if prepare is None:
        return
    df_nettoye, col_date, col_qty, col_rev = prepare

    output_dir = _preparer_dossier_export()
    logger.info("Dossier d'export du run : %s", output_dir)

    # ─── MODE BACKTEST UNIQUEMENT ───────────────────────────────────────────
    if config.MODE == "backtest":
        logger.info(" MODE BACKTEST : entraînement %s → évaluation %s",
                    config.ANNEE_TRAIN, config.ANNEE_TEST)
        res_bt = backtest.backtest_annuel(
            df_nettoye, col_date, col_qty, col_rev,
            config.ANNEE_TRAIN, config.ANNEE_TEST)
        if res_bt is not None:
            res_bt["df_comparaison"].to_csv(
                os.path.join(output_dir, "backtest_annuel.csv"),
                index=False, sep=";", encoding="utf-8")
            backtest.tracer_dashboard_backtest(
                res_bt, config.ANNEE_TRAIN, config.ANNEE_TEST, output_dir)
        logger.info(" BACKTEST TERMINÉ")
        return

    produits_uniques = df_nettoye[config.PRODUCT_COL].unique()
    logger.info("Produits à traiter : %d", len(produits_uniques))

    dates_communes = pd.date_range(
        start=pd.to_datetime(df_nettoye[col_date]).min(),
        end=pd.to_datetime(df_nettoye[col_date]).max(),
        freq=config.AGG_FREQ)
    logger.info("[Info] Plage commune : %s → %s (%d périodes)",
                dates_communes[0].date(), dates_communes[-1].date(), len(dates_communes))

    prophet_top = _selectionner_produits_prophet(df_nettoye)

    dict_historique_prod = {}
    dict_prevision_prod = {}
    produits_ok = []
    produits_echec = []

    for i, prod in enumerate(produits_uniques, start=1):
        if i % 100 == 0 or i == len(produits_uniques):
            logger.info("[Prévision] %d/%d produits traités…", i, len(produits_uniques))
        df_prod = df_nettoye[df_nettoye[config.PRODUCT_COL] == prod].copy()
        try:
            df_agg, df_fc = _prevoir_un_produit(
                prod, df_prod, col_date, col_qty, col_rev,
                dates_communes, prod in prophet_top)
        except Exception as e:
            logger.warning("[Produit ignoré] '%s' — échec du calcul : %s", prod, e)
            produits_echec.append(prod)
            continue
        dict_historique_prod[prod] = df_agg
        dict_prevision_prod[prod] = df_fc
        produits_ok.append(prod)

    if not produits_ok:
        logger.error("Aucun produit n'a pu être prévu — arrêt du pipeline.")
        return
    if produits_echec:
        logger.warning("%d produit(s) en échec ignoré(s) : %s",
                       len(produits_echec), produits_echec)

    produits_uniques = produits_ok

    # ─── COUCHE D'AJUSTEMENT « FÊTES » (Ramadan/Aïd : mix produits) ──────────
    # Applique les ratios mesurés par famille aux mois de prévision en Ramadan,
    # pour être prêt le jour J (ex : +55 % boulangerie → +farine, menus −63 %).
    produit_famille = {}
    if config.CATEGORY_COL in df_nettoye.columns:
        produit_famille = (df_nettoye[[config.PRODUCT_COL, config.CATEGORY_COL]]
                           .dropna().drop_duplicates(config.PRODUCT_COL)
                           .set_index(config.PRODUCT_COL)[config.CATEGORY_COL].to_dict())

    # ─── MODÈLE GLOBAL (GBM) pour les produits mappés dessus ─────────────────
    # Entraîné sur TOUS les produits (mutualisation), écrase *_Prev_Selection
    # des produits dont le banc d'essai a retenu "GBM". Repli déjà en place (HW).
    _appliquer_modele_global(dict_prevision_prod, dict_historique_prod, produit_famille)

    # ─── RÉCONCILIATION TOP-DOWN (validée au banc v2 : −5 à −14 % d'erreur) ──
    # Cale la somme des prévisions produit (Selection) sur la prévision agrégée
    # Holt-Winters — la plus fiable au niveau total (~8 % d'erreur à 1 mois).
    _reconcilier_selection(dict_prevision_prod, dict_historique_prod, dates_communes)

    saisonnalite_fetes.ajuster_previsions_fetes(dict_prevision_prod, produit_famille)

    # ─── COUCHE D'AJUSTEMENT « ÉVÉNEMENTS » (match, jour férié, concert…) ─────
    # Boost ponctuel saisi via le dashboard, appliqué au prorata des jours du mois.
    evenements.ajuster_previsions_evenements(dict_prevision_prod, produit_famille)

    # Fourchette de prévision + quantité recommandée (stock de sécurité).
    incertitude.ajouter_intervalles(dict_prevision_prod)
    logger.info("[Incertitude] Fourchettes ajoutées (niveau de service %.0f%%).",
                config.NIVEAU_SERVICE * 100)

    # ─── COMMANDES CLIENTS CONNUES (B2B) : ajout tel quel au mois concerné ────
    # Après les intervalles (quantité certaine → pas de marge dessus), avant la
    # consolidation et le BOM : plan de production et matières premières
    # incluent donc ces commandes.
    commandes.ajouter_commandes_mensuelles(dict_prevision_prod)

    # Consolidation de l'historique global
    df_hist_total = pd.DataFrame({
        "Date": dates_communes,
        "Chiffre_Affaires_Total": 0.0,
        "Quantite_Total": 0.0})
    for prod in produits_uniques:
        df_hist_total["Chiffre_Affaires_Total"] += dict_historique_prod[prod]["Valeur"].values
        df_hist_total["Quantite_Total"]          += dict_historique_prod[prod]["Quantite"].values

    # Interpolation des mois sans données réelles
    masque_vide = df_hist_total["Chiffre_Affaires_Total"] < config.SEUIL_HISTORIQUE
    if masque_vide.any():
        mois_vides = df_hist_total.loc[masque_vide, "Date"].dt.strftime("%Y-%m").tolist()
        logger.info("[Info] Mois sans données → interpolation : %s", mois_vides)
        df_hist_total.loc[masque_vide, "Chiffre_Affaires_Total"] = np.nan
        df_hist_total.loc[masque_vide, "Quantite_Total"]         = np.nan
        df_hist_total["Chiffre_Affaires_Total"] = df_hist_total["Chiffre_Affaires_Total"].interpolate(method="linear")
        df_hist_total["Quantite_Total"]         = df_hist_total["Quantite_Total"].interpolate(method="linear")

    # Consolidation des prévisions
    dates_prevision = dict_prevision_prod[produits_uniques[0]]["Date"]
    df_prev_total = pd.DataFrame({"Date": dates_prevision})
    modeles = ["Moyenne_Mobile", "Tendance", "Naif_Saisonnier", "Decompo_Saisonniere",
               "Holt_Winters", "Selection"]
    if config.ACTIVER_PROPHET:
        modeles.append("Prophet")
    for col in modeles:
        col_rev_k, col_qty_k = f"Rev_Prev_{col}", f"Qty_Prev_{col}"
        df_prev_total[col_rev_k] = 0.0
        df_prev_total[col_qty_k] = 0.0
        for prod in produits_uniques:
            df_p = dict_prevision_prod[prod]
            if col_rev_k in df_p.columns:
                df_prev_total[col_rev_k] += df_p[col_rev_k].values
            if col_qty_k in df_p.columns:
                df_prev_total[col_qty_k] += df_p[col_qty_k].values

    # Besoins ingrédients (MRP) : agrégé (bon de commande global) + détail par
    # produit/département (traçabilité pour chaque département de production).
    df_besoins_mrp, df_besoins_detail = _calculer_besoins_mrp(
        produits_uniques, dict_prevision_prod, produit_famille)

    # Exports CSV
    df_hist_total.to_csv(os.path.join(output_dir, "historique_agrege_global.csv"),
                         index=False, sep=";", encoding="utf-8")
    df_prev_total.to_csv(os.path.join(output_dir, "previsions_global.csv"),
                         index=False, sep=";", encoding="utf-8")
    if not df_besoins_mrp.empty:
        df_besoins_mrp.to_csv(os.path.join(output_dir, "besoins_ingredients_planifies.csv"),
                              index=False, sep=";", encoding="utf-8")
    if not df_besoins_detail.empty:
        _exporter_besoins_par_categorie(df_besoins_detail, output_dir)
        df_besoins_detail.to_csv(os.path.join(output_dir, "besoins_ingredients_detail.csv"),
                                 index=False, sep=";", encoding="utf-8")

    # Prévisions agrégées par famille (pour le dashboard et l'analyse du mix).
    lignes_fam = []
    for prod in produits_uniques:
        fam = str(produit_famille.get(prod, "Autres")).strip() or "Autres"
        dfp = dict_prevision_prod[prod]
        for _, r in dfp.iterrows():
            lignes_fam.append({"Date": r["Date"], "Famille": fam,
                               "Quantite": r.get("Qty_Prev_Selection", r.get("Qty_Prev_Holt_Winters", 0.0)),
                               "CA": r.get("Rev_Prev_Selection", r.get("Rev_Prev_Holt_Winters", 0.0))})
    if lignes_fam:
        (pd.DataFrame(lignes_fam).groupby(["Date", "Famille"], as_index=False).sum()
         .to_csv(os.path.join(output_dir, "previsions_par_famille.csv"),
                 index=False, sep=";", encoding="utf-8"))

    # Plan de production sécurisé du mois prochain (prévision + stock de sécurité).
    fiab = incertitude.fiabilite_par_produit()
    lignes_plan = []
    for prod in produits_uniques:
        r0 = dict_prevision_prod[prod].iloc[0]
        prev = float(r0.get("Qty_Prev_Selection", 0.0))
        info = fiab.get(str(prod))
        lignes_plan.append({
            "Produit": prod,
            "Famille": str(produit_famille.get(prod, "Autres")).strip() or "Autres",
            "Prevision": round(prev),
            "Fourchette_basse": round(float(r0.get("Qty_Selection_Bas", prev))),
            "Quantite_recommandee": round(float(r0.get("Qty_Selection_Haut", prev))),
            "Fiabilite": info["niveau"] if info else "Hist. court",
            "Erreur_rel_%": (round(info["err_rel"] * 100)
                             if info and info.get("err_rel") is not None else None),
            "Modele": config.MODELE_PAR_PRODUIT.get(str(prod), "ETS"),
        })
    if fiab:
        import collections
        cpt = collections.Counter(v["niveau"] for v in fiab.values())
        logger.info("[Fiabilité] Fiable=%d | Moyen=%d | Incertain=%d (sur %d produits évalués)",
                    cpt.get("Fiable", 0), cpt.get("Moyen", 0), cpt.get("Incertain", 0), len(fiab))
    if lignes_plan:
        date_plan = pd.to_datetime(dict_prevision_prod[produits_uniques[0]]["Date"].iloc[0])
        df_plan = pd.DataFrame(lignes_plan).sort_values("Quantite_recommandee", ascending=False)
        df_plan.insert(0, "Mois", date_plan.strftime("%Y-%m"))
        df_plan.to_csv(os.path.join(output_dir, "plan_production_securise.csv"),
                       index=False, sep=";", encoding="utf-8")
        logger.info("Plan de production sécurisé écrit (mois %s, service %.0f%%).",
                    date_plan.strftime("%Y-%m"), config.NIVEAU_SERVICE * 100)

    # Marges matière par produit (prix de vente constaté − coût recette estimé).
    try:
        from . import marges
        marges.generer_csv(os.path.join(output_dir, "marges_produits.csv"))
    except Exception as e:
        logger.warning("[Marges] Export ignoré : %s", e)

    lignes_export_prod = []
    for prod in produits_uniques:
        df_fc_prod = dict_prevision_prod[prod].copy()
        df_fc_prod.insert(0, "Produit", prod)
        df_fc_prod.insert(1, "Famille", str(produit_famille.get(prod, "Autres")).strip() or "Autres")
        lignes_export_prod.append(df_fc_prod)
    pd.concat(lignes_export_prod, ignore_index=True).to_csv(
        os.path.join(output_dir, "previsions_detaillees_par_produit.csv"),
        index=False, sep=";", encoding="utf-8")
    logger.info("[OK] Fichiers CSV exportés dans '%s'.", output_dir)

    # Export Excel (livrable principal)
    reporting.exporter_excel_previsions(
        df_hist_total, df_prev_total, dict_prevision_prod,
        produits_uniques, output_dir, df_besoins_mrp=df_besoins_mrp)

    # Rapport textuel "Bon de Commande"
    if not df_besoins_mrp.empty:
        reporting.generer_rapport_approvisionnement(df_besoins_mrp, output_dir)
        _validation_terrain_matieres(df_besoins_mrp, output_dir, df_besoins_detail)

    # Fiche de préparation des mois de fête (Ramadan, Aïd, Achoura, Mawlid)
    _preparation_fetes(dict_prevision_prod, produit_famille, df_besoins_mrp, output_dir)

    # Dashboards
    logger.info("Génération du Tableau de Bord Gérant...")
    reporting.tracer_dashboard_mrp(
        df_hist_total, df_prev_total, df_besoins_mrp,
        dict_prevision_prod, produits_uniques, output_dir)
    logger.info("Génération du Dashboard Matières Premières...")
    reporting.tracer_dashboard_matieres_premieres(df_besoins_mrp, output_dir)

    # ─── VALIDATION GLISSANTE "MOIS PROCHAIN" (objectif principal du projet) ──
    res_wf_ca = res_wf_qte = None
    if config.ACTIVER_VALIDATION_MENSUELLE:
        logger.info(" VALIDATION GLISSANTE MENSUELLE (horizon 1 mois, %d mois rejoués)",
                    config.N_VALIDATION_WALKFORWARD)
        res_wf_ca = backtest.backtest_walkforward_mensuel(
            df_hist_total, "Chiffre_Affaires_Total", "CA (MAD)",
            config.N_VALIDATION_WALKFORWARD, config.MA_WINDOW, config.AGG_FREQ,
            config.MIN_TRAIN_WALKFORWARD)
        res_wf_qte = backtest.backtest_walkforward_mensuel(
            df_hist_total, "Quantite_Total", "Quantité",
            config.N_VALIDATION_WALKFORWARD, config.MA_WINDOW, config.AGG_FREQ,
            config.MIN_TRAIN_WALKFORWARD)

        for res in (res_wf_ca, res_wf_qte):
            if res is not None:
                nom_csv = f"validation_mensuelle_{res['label'].split()[0].lower()}.csv"
                res["df_comparaison"].to_csv(
                    os.path.join(output_dir, nom_csv),
                    index=False, sep=";", encoding="utf-8")
        backtest.tracer_dashboard_walkforward(res_wf_ca, res_wf_qte, output_dir)

    # ─── SYNTHÈSE "MOIS PROCHAIN" (livrable clé) ─────────────────────────────
    _synthese_mois_prochain(df_prev_total, res_wf_ca, res_wf_qte, output_dir)

    # Backtesting inter-annuel optionnel
    if config.ACTIVER_BACKTEST_ANNUEL and config.ANNEE_TEST is not None:
        logger.info(" BACKTESTING INTER-ANNUEL : train=%s → test=%s",
                    config.ANNEE_TRAIN, config.ANNEE_TEST)
        res_bt = backtest.backtest_annuel(
            df_nettoye, col_date, col_qty, col_rev,
            config.ANNEE_TRAIN, config.ANNEE_TEST)
        if res_bt is not None:
            res_bt["df_comparaison"].to_csv(
                os.path.join(output_dir, "backtest_annuel.csv"),
                index=False, sep=";", encoding="utf-8")
            backtest.tracer_dashboard_backtest(
                res_bt, config.ANNEE_TRAIN, config.ANNEE_TEST, output_dir)

    logger.info("=" * 60)
    logger.info(" TRAITEMENT DE PLANIFICATION TERMINÉ AVEC SUCCÈS")
    logger.info("=" * 60)

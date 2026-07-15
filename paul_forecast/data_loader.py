# -*- coding: utf-8 -*-
"""
Chargement, validation, nettoyage et agrégation des données de ventes PAUL.

Gère deux sources :
  - un dossier (ou plusieurs) de fichiers mensuels xlsx,
  - un fichier unique CSV / Excel.
"""

import os
import re

import numpy as np
import pandas as pd

from . import config
from . import bom
from .logging_setup import get_logger

logger = get_logger()

# Correspondance noms de mois français → numéro de mois
_MOIS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "août": 8, "février": 2, "décembre": 12,
}


# ==============================================================================
# CHARGEMENT — DOSSIERS MENSUELS
# ==============================================================================
def charger_dossier_mensuel(dossier):
    """
    Lit tous les fichiers xlsx d'un ou plusieurs dossiers mensuels et les
    consolide en un DataFrame trié chronologiquement.

    Accepte un str (un dossier) ou une liste de dossiers.
    """
    dossiers = [dossier] if isinstance(dossier, str) else list(dossier)
    morceaux_globaux = []

    for dossier_courant in dossiers:
        if not os.path.isdir(dossier_courant):
            logger.warning("Dossier ignoré (introuvable) : '%s'", dossier_courant)
            continue
        morceaux_globaux.extend(_charger_un_dossier(dossier_courant))

    if not morceaux_globaux:
        raise ValueError(f"Aucun fichier n'a pu être chargé depuis : {dossiers}")

    df_consolide = pd.concat(morceaux_globaux, ignore_index=True)
    df_consolide = df_consolide.sort_values(config.DATE_COL).reset_index(drop=True)
    n_mois = df_consolide[config.DATE_COL].nunique()
    logger.info("%d dossier(s) chargé(s) : %d fichiers → %d lignes, %d mois distincts",
                len(dossiers), len(morceaux_globaux), len(df_consolide), n_mois)

    if getattr(config, "COMPLETER_DEPUIS_JOURNALIER", True):
        df_consolide = completer_depuis_journalier(df_consolide)
    return df_consolide


# Dernier ratio CA TTC (caisse, journalier) / CA HT (panel xlsx) mesuré au
# chargement du panel — sert à afficher l'équivalent TTC des prévisions de CA.
_RATIO_TTC_HT = None


def ratio_ttc_ht_estime():
    """Ratio TTC/HT mesuré sur les mois communs panel/journalier (repli 1.107).

    Le panel mensuel (donc toutes les prévisions de CA du pipeline) est en HORS
    TAXES ; le total caisse que suit le gérant est TTC. Sans cette conversion à
    l'affichage, la prévision paraît ~10 % trop basse.
    """
    return _RATIO_TTC_HT if _RATIO_TTC_HT else 1.107


def completer_depuis_journalier(df_mensuel):
    """
    Complète le panel mensuel avec les mois présents dans les ventes
    JOURNALIÈRES mais absents des xlsx (mois récents pas encore exportés,
    fichiers corrompus). Vérifié : mêmes quantités sur les mois communs
    (écart 0,1 %) ; le CA TTC du journalier est converti en HT via le ratio
    TTC/HT médian par produit (repli : ratio global ~1.107).
    """
    chemin = os.path.join(config.RACINE_PROJET, "donnees_ventes", "ventes_journalieres.csv")
    if not os.path.exists(chemin):
        return df_mensuel
    try:
        dfj = pd.read_csv(chemin, sep=";", parse_dates=["Date"])
    except Exception as e:
        logger.warning("Complément journalier illisible (%s) — panel xlsx seul.", e)
        return df_mensuel

    dfj["Quantite"] = pd.to_numeric(dfj["Quantite"], errors="coerce").fillna(0)
    dfj["CA_TTC"] = pd.to_numeric(dfj.get("CA_TTC", 0), errors="coerce").fillna(0)
    dfj["Mois"] = dfj["Date"].dt.to_period("M")

    mois_xlsx = set(pd.to_datetime(df_mensuel[config.DATE_COL]).dt.to_period("M"))
    manquants = sorted(set(dfj["Mois"]) - mois_xlsx)
    # Ne compléter que les mois COMPLETS (un mois en cours fausserait la série).
    dernier_jour = dfj["Date"].max()
    manquants = [m for m in manquants if m.to_timestamp() + pd.offsets.MonthEnd(0) <= dernier_jour]

    # Ratio TTC/HT par produit sur les mois communs (repli : médiane globale).
    # Calculé même sans mois manquant : il sert aussi à annoncer l'équivalent
    # TTC des prévisions de CA (cf. ratio_ttc_ht_estime).
    dfm = df_mensuel.copy()
    dfm["Mois"] = pd.to_datetime(dfm[config.DATE_COL]).dt.to_period("M")
    dfm["_ca_ht"] = pd.to_numeric(dfm[config.REV_COL], errors="coerce").fillna(0)
    ht = dfm.groupby([config.PRODUCT_COL, "Mois"])["_ca_ht"].sum()
    ttc = (dfj[dfj["Mois"].isin(mois_xlsx)]
           .groupby(["Produit", "Mois"])["CA_TTC"].sum())
    cmp = pd.concat([ht.rename("ht"), ttc.rename("ttc")], axis=1).dropna()
    cmp = cmp[(cmp["ht"] > 0) & (cmp["ttc"] > 0)]
    ratio_prod = (cmp["ttc"] / cmp["ht"]).groupby(level=0).median()
    ratio_global = float((cmp["ttc"] / cmp["ht"]).median()) if len(cmp) else 1.107
    global _RATIO_TTC_HT
    _RATIO_TTC_HT = ratio_global
    if not manquants:
        return df_mensuel

    aj = dfj[dfj["Mois"].isin(manquants)]
    agg = (aj.groupby(["Produit", "Famille", "Mois"], as_index=False)
             .agg(QT=("Quantite", "sum"), CA_TTC=("CA_TTC", "sum")))
    lignes = pd.DataFrame({
        config.DATE_COL: agg["Mois"].dt.to_timestamp() + pd.offsets.MonthEnd(0),
        config.PRODUCT_COL: agg["Produit"],
        config.CATEGORY_COL: agg["Famille"],
        config.QTY_COL: agg["QT"],
        config.REV_COL: agg["CA_TTC"] / agg["Produit"].map(ratio_prod).fillna(ratio_global),
        config.QTY_RETURN_COL: 0,
        config.CA_RETURN_COL: 0,
    })
    out = pd.concat([df_mensuel, lignes], ignore_index=True)
    out = out.sort_values(config.DATE_COL).reset_index(drop=True)
    logger.info("[Complément journalier] %d mois ajoutés depuis les ventes journalières : %s",
                len(manquants), ", ".join(str(m) for m in manquants))
    return out


def _annee_depuis_chemin(chemin_dossier):
    """Extrait l'année depuis le chemin du dossier (ex: '.../2024/12_mois' → 2024)."""
    parties = re.findall(r"(20\d{2})", chemin_dossier.replace("\\", "/"))
    return int(parties[0]) if parties else None


def _charger_un_dossier(dossier):
    """Charge tous les xlsx d'un dossier mensuel (récursif). Retourne une liste de DataFrames."""
    if not os.path.isdir(dossier):
        raise FileNotFoundError(f"Dossier introuvable : '{dossier}'")

    tous_fichiers = []
    for racine, _, fics in os.walk(dossier):
        for f in sorted(fics):
            if f.startswith("~$"):          # fichiers verrou Office → ignorer
                continue
            if f.lower().endswith((".xlsx", ".xls")):
                tous_fichiers.append(os.path.join(racine, f))

    if not tous_fichiers:
        logger.warning("Aucun fichier xlsx dans '%s'", dossier)
        return []

    annee_dossier = _annee_depuis_chemin(dossier)
    morceaux = []

    for chemin in tous_fichiers:
        nom_fichier = os.path.basename(chemin)
        nom_base    = os.path.splitext(nom_fichier)[0].lower()

        # --- Détection du mois ---
        mois_num = None
        for token in re.split(r"[_\-\s\(\)]+", nom_base):
            if token in _MOIS_FR:
                mois_num = _MOIS_FR[token]
                break

        # --- Détection de l'année (nom prioritaire, fallback dossier) ---
        annees_fichier = re.findall(r"(20\d{2})", nom_base)
        if annees_fichier:
            annee_candidate = int(annees_fichier[0])
            if annee_dossier and abs(annee_candidate - annee_dossier) > 1:
                logger.info("'%s' : année fichier=%d ≠ dossier=%d → dossier prioritaire",
                            nom_fichier, annee_candidate, annee_dossier)
                annee = annee_dossier
            else:
                annee = annee_candidate
        else:
            annee = annee_dossier

        if not mois_num:
            logger.warning("Mois non détecté dans '%s' — ignoré.", nom_fichier)
            continue
        if not annee:
            logger.warning("Année non détectée pour '%s' — ignoré.", nom_fichier)
            continue

        # --- Lecture avec détection auto du header ---
        df_mois = None
        for header_row in [config.EXCEL_HEADER_ROW, 0, 1, 2]:
            try:
                df_tmp = pd.read_excel(chemin, sheet_name=config.EXCEL_SHEET, header=header_row)
                cols_lower = [str(c).lower().strip() for c in df_tmp.columns]
                if any(config.PRODUCT_COL.lower() in c for c in cols_lower) or \
                   any("article" in c for c in cols_lower) or \
                   any("produit" in c for c in cols_lower):
                    df_mois = df_tmp
                    break
            except Exception:
                continue

        if df_mois is None:
            try:
                df_mois = pd.read_excel(chemin, sheet_name=config.EXCEL_SHEET,
                                        header=config.EXCEL_HEADER_ROW)
            except Exception as e:
                logger.error("Impossible de lire '%s' : %s", nom_fichier, e)
                continue

        # --- Normaliser les noms de colonnes ---
        df_mois.columns = [str(c).strip() for c in df_mois.columns]

        # --- Harmoniser les variantes de noms de colonnes ---
        for nom_cible, variantes in config.ALIAS_COLONNES.items():
            if nom_cible not in df_mois.columns:
                for var in variantes:
                    if var in df_mois.columns:
                        df_mois = df_mois.rename(columns={var: nom_cible})
                        break

        # --- Forward-fill colonnes hiérarchiques ---
        for col_hier in ["Familles", "Nom Familles", config.CATEGORY_COL]:
            if col_hier in df_mois.columns:
                df_mois[col_hier] = df_mois[col_hier].ffill()

        # --- Filtrer lignes vides / sous-totaux ---
        if config.PRODUCT_COL in df_mois.columns:
            masque_valide = (
                df_mois[config.PRODUCT_COL].notna() &
                (df_mois[config.PRODUCT_COL].astype(str).str.strip() != "") &
                ~df_mois[config.PRODUCT_COL].astype(str).str.contains(
                    r"S/T|Total|Sous.total|TOTAL", case=False, na=False, regex=True
                )
            )
            df_mois = df_mois[masque_valide].copy()

        # --- Vérification CA HT : ignorer le fichier si colonne vide/corrompue ---
        if config.REV_COL in df_mois.columns:
            ca_total = pd.to_numeric(df_mois[config.REV_COL], errors='coerce').fillna(0).sum()
            if ca_total < config.SEUIL_CA_FICHIER:
                logger.warning("'%s' ignoré : CA HT total = %.0f MAD (incomplet/corrompu)",
                               nom_fichier, ca_total)
                continue

        # --- Colonnes manquantes → créer à 0 ---
        for col_opt in [config.QTY_RETURN_COL, config.CA_RETURN_COL]:
            if col_opt not in df_mois.columns:
                df_mois[col_opt] = 0

        # --- Assigner la date (fin de mois) ---
        date_fin_mois = pd.Timestamp(year=annee, month=mois_num, day=1) + pd.offsets.MonthEnd(0)
        df_mois[config.DATE_COL] = date_fin_mois
        logger.debug("'%s' → %s", nom_fichier, date_fin_mois.strftime('%Y-%m'))

        morceaux.append(df_mois)

    logger.info("[%s] %d/%d fichiers chargés", dossier, len(morceaux), len(tous_fichiers))
    return morceaux


# ==============================================================================
# CHARGEMENT — FICHIER UNIQUE
# ==============================================================================
def charger_donnees(chemin_fichier, excel_sheet=0):
    """Charge un fichier CSV ou Excel en DataFrame pandas."""
    if not os.path.exists(chemin_fichier):
        raise FileNotFoundError(
            f"Fichier introuvable : '{chemin_fichier}'. "
            f"Vérifiez FILE_PATH dans config.py. "
            f"Chemin absolu attendu : {os.path.abspath(chemin_fichier)}"
        )

    ext = os.path.splitext(chemin_fichier)[-1].lower()
    logger.info("Chargement du fichier : %s ...", chemin_fichier)

    if ext in [".xlsx", ".xls", ".odf"]:
        try:
            feuilles = pd.ExcelFile(chemin_fichier).sheet_names
            logger.debug("Feuilles disponibles : %s", feuilles)
        except Exception:
            pass
        df = pd.read_excel(chemin_fichier, sheet_name=excel_sheet, header=config.EXCEL_HEADER_ROW)
    elif ext == ".csv":
        for sep in [";", ",", "\t"]:
            try:
                df = pd.read_csv(chemin_fichier, sep=sep, encoding="utf-8")
                if len(df.columns) > 1:
                    logger.info("CSV chargé (séparateur '%s')", sep)
                    return df
            except Exception:
                continue
        df = pd.read_csv(chemin_fichier, encoding="utf-8")
    else:
        raise ValueError(f"Format non supporté : {ext}. Utilisez un CSV ou Excel.")

    logger.info("Fichier chargé : %d lignes × %d colonnes.", df.shape[0], df.shape[1])
    return df


# ==============================================================================
# DÉTECTION & VALIDATION DES COLONNES
# ==============================================================================
def detecter_colonne_date(df, col_config=None):
    """Détecte la colonne de date ou valide celle configurée."""
    if col_config and col_config in df.columns:
        return col_config
    mots_cles = ["date", "time", "jour", "annee", "mois", "period", "facture", "commande"]
    for col in df.columns:
        if any(mc in str(col).lower() for mc in mots_cles):
            return col
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    raise ValueError("Impossible de détecter la colonne de date. Spécifiez DATE_COL.")


def detecter_colonne_num(df, col_config=None, col_date=None, mots_cles=None):
    """Détecte une colonne numérique par mots-clés ou prend la première disponible."""
    if col_config and col_config in df.columns:
        return col_config
    cols_numeriques = df.select_dtypes(include=[np.number]).columns.tolist()
    if col_date in cols_numeriques:
        cols_numeriques.remove(col_date)
    if mots_cles:
        for col in cols_numeriques:
            if any(mc in str(col).lower() for mc in mots_cles):
                return col
    if cols_numeriques:
        return cols_numeriques[0]
    raise ValueError(f"Impossible de détecter une colonne numérique ({mots_cles})")


def valider_donnees(df):
    """
    Vérifie que le DataFrame chargé contient les colonnes indispensables et
    qu'il n'est pas vide. Lève une ValueError explicite sinon — ce qui évite
    des résultats silencieusement faux en aval.
    """
    if df is None or df.empty:
        raise ValueError("Le jeu de données chargé est vide.")

    colonnes = set(df.columns)
    manquantes = []

    # Colonne produit : obligatoire et sans alias acceptable ici.
    if config.PRODUCT_COL not in colonnes:
        manquantes.append(f"produit ('{config.PRODUCT_COL}')")

    # Au moins une colonne de valeur à prévoir (QT ou CA HT).
    if config.QTY_COL not in colonnes and config.REV_COL not in colonnes:
        manquantes.append(f"valeur ('{config.QTY_COL}' ou '{config.REV_COL}')")

    if manquantes:
        raise ValueError(
            "Colonnes indispensables introuvables : " + ", ".join(manquantes) +
            f". Colonnes présentes : {sorted(colonnes)}. "
            "Ajustez les noms dans config.py ou ALIAS_COLONNES."
        )

    logger.info("Validation OK : %d lignes, colonnes clés présentes.", len(df))


# ==============================================================================
# NETTOYAGE & AGRÉGATION
# ==============================================================================
def nettoyer_donnees(df, col_date, col_qty, col_rev):
    """Nettoie et formate les colonnes de date, de quantité et de chiffre d'affaires."""
    df_clean = df.copy()
    df_clean[col_date] = pd.to_datetime(df_clean[col_date], errors='coerce')
    df_clean[col_qty] = pd.to_numeric(df_clean[col_qty], errors='coerce')
    df_clean[col_rev] = pd.to_numeric(df_clean[col_rev], errors='coerce')

    df_clean = df_clean.dropna(subset=[col_date])
    df_clean[col_qty] = df_clean[col_qty].fillna(0)
    df_clean[col_rev] = df_clean[col_rev].fillna(0)

    df_clean = df_clean.sort_values(by=col_date).reset_index(drop=True)
    return df_clean


def agreger_serie(df, col_date, col_qty, col_rev, freq, methode):
    """Agrège la série temporelle (quantité et CA) selon la fréquence et la méthode."""
    df_temp = df[[col_date, col_qty, col_rev]].copy()
    df_temp.set_index(col_date, inplace=True)

    if methode == "sum":
        df_agg = df_temp.resample(freq).sum()
    elif methode == "mean":
        df_agg = df_temp.resample(freq).mean()
    else:
        df_agg = df_temp.resample(freq).first()

    df_agg = df_agg.fillna(0).reset_index()
    df_agg.columns = ['Date', 'Quantite', 'Valeur']
    return df_agg


# ==============================================================================
# GÉNÉRATION DE DONNÉES DE TEST (catalogue PAUL Casablanca)
# ==============================================================================
def generer_donnees_test(chemin_fichier):
    """Génère un fichier de test mensuel 2021 au format métadonnées PAUL."""
    logger.info("Aucun fichier à '%s' — génération d'un historique 2021 de test...",
                chemin_fichier)

    catalogue = [
        {"fam_code": "F01", "fam_nom": "Pains",         "code": "P001", "nom": "Baguette Paul",
         "prix_ht": 10.0,  "vol_base": 4200, "poids_unit": 0.25, "taux_retour": 0.03},
        {"fam_code": "F01", "fam_nom": "Pains",         "code": "P002", "nom": "Pain complet 400g",
         "prix_ht": 26.7,  "vol_base": 850,  "poids_unit": 0.40, "taux_retour": 0.02},
        {"fam_code": "F01", "fam_nom": "Pains",         "code": "P003", "nom": "Baguette 2 Graines",
         "prix_ht": 13.3,  "vol_base": 1700, "poids_unit": 0.27, "taux_retour": 0.02},
        {"fam_code": "F02", "fam_nom": "Viennoiseries", "code": "V001", "nom": "Croissant",
         "prix_ht": 12.5,  "vol_base": 2600, "poids_unit": 0.065, "taux_retour": 0.04},
        {"fam_code": "F02", "fam_nom": "Viennoiseries", "code": "V002", "nom": "Pain au Chocolat",
         "prix_ht": 13.3,  "vol_base": 3000, "poids_unit": 0.075, "taux_retour": 0.04},
        {"fam_code": "F02", "fam_nom": "Viennoiseries", "code": "V003", "nom": "Escargot aux Raisins",
         "prix_ht": 16.7,  "vol_base": 1100, "poids_unit": 0.080, "taux_retour": 0.03},
        {"fam_code": "F03", "fam_nom": "Patisseries",   "code": "T001", "nom": "Tartelette aux Fraises",
         "prix_ht": 35.0,  "vol_base": 650,  "poids_unit": 0.120, "taux_retour": 0.05},
        {"fam_code": "F03", "fam_nom": "Patisseries",   "code": "T002", "nom": "Eclair au Chocolat",
         "prix_ht": 29.2,  "vol_base": 750,  "poids_unit": 0.090, "taux_retour": 0.05},
        {"fam_code": "F04", "fam_nom": "Sandwicherie",  "code": "S001", "nom": "Sandwich Mixte Jambon Beurre",
         "prix_ht": 43.3,  "vol_base": 1500, "poids_unit": 0.200, "taux_retour": 0.02},
        {"fam_code": "F04", "fam_nom": "Sandwicherie",  "code": "S002", "nom": "Sandwich Dieppois Thon",
         "prix_ht": 48.3,  "vol_base": 1300, "poids_unit": 0.210, "taux_retour": 0.02},
        {"fam_code": "F04", "fam_nom": "Sandwicherie",  "code": "S003", "nom": "Salade Cesar",
         "prix_ht": 68.3,  "vol_base": 550,  "poids_unit": 0.250, "taux_retour": 0.03},
        {"fam_code": "F05", "fam_nom": "Boissons",      "code": "B001", "nom": "Cafe Express",
         "prix_ht": 15.0,  "vol_base": 2400, "poids_unit": 0.040, "taux_retour": 0.01},
        {"fam_code": "F05", "fam_nom": "Boissons",      "code": "B002", "nom": "Jus Orange Frais 30cl",
         "prix_ht": 31.7,  "vol_base": 980,  "poids_unit": 0.300, "taux_retour": 0.01},
    ]

    dates_mensuelles = pd.date_range(start="2021-01-31", end="2021-12-31", freq="ME")
    lignes = []
    taux_tva = 1.20

    for dt in dates_mensuelles:
        mois = dt.month
        for item in catalogue:
            fam = item["fam_nom"]
            if fam == "Sandwicherie":
                saison = 1.0 + 0.35 * np.sin(2 * np.pi * (mois - 7) / 12)
            elif fam == "Boissons":
                saison = 1.0 + 0.25 * np.cos(2 * np.pi * (mois - 1) / 12)
            elif fam in ("Viennoiseries", "Pains"):
                saison = 1.0 + 0.15 * np.cos(2 * np.pi * (mois - 1) / 12)
            elif fam == "Patisseries":
                saison = 1.0 + 0.20 * np.sin(2 * np.pi * (mois - 4) / 12)
            else:
                saison = 1.0

            facteur_fete = bom.obtenir_multiplicateur_fete(dt, fam)
            vol_moyen = item["vol_base"] * saison * facteur_fete
            qt        = int(max(0, np.random.poisson(lam=max(1, vol_moyen))))
            qt_retour = int(round(qt * item["taux_retour"] * np.random.uniform(0.5, 1.5)))
            qt_retour = min(qt_retour, qt)

            ca_ht     = round(qt * item["prix_ht"], 2)
            ca_ttc    = round(ca_ht * taux_tva, 2)
            ca_retour = round(qt_retour * item["prix_ht"], 2)
            qt_poids  = round(qt * item["poids_unit"], 3)

            lignes.append({
                "Date":             dt.strftime("%Y-%m-%d"),
                "Familles":         item["fam_code"],
                "Nom Familles":     item["fam_nom"],
                "code article":     item["code"],
                "Nom code article": item["nom"],
                "CA TTC":           ca_ttc,
                "CA HT":            ca_ht,
                "QT":               qt,
                "QT poids/volume":  qt_poids,
                "QT retour":        qt_retour,
                "CA Retour":        ca_retour,
            })

    df_mock = pd.DataFrame(lignes)
    df_mock.loc[df_mock.sample(frac=0.02).index, "QT"]    = np.nan
    df_mock.loc[df_mock.sample(frac=0.02).index, "CA HT"] = np.nan

    df_mock.to_csv(chemin_fichier, index=False, sep=";", encoding="utf-8")
    logger.info("Fichier de test généré : '%s' (%d lignes, %d produits)",
                chemin_fichier, len(df_mock), df_mock['Nom code article'].nunique())

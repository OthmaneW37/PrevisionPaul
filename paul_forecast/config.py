# -*- coding: utf-8 -*-
"""
Configuration centrale du projet de prévision PAUL.

Toutes les constantes paramétrables sont regroupées ici. Les données métier
volumineuses et éditables sans toucher au code (recettes exactes, calendrier des
fêtes marocaines) sont chargées depuis le dossier `data/` au format JSON.
"""

import os
import json

# ==============================================================================
# CHEMINS DE BASE
# ==============================================================================
# Racine du projet = dossier parent de ce package.
RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR      = os.path.join(RACINE_PROJET, "data")
LOG_DIR       = os.path.join(RACINE_PROJET, "logs")

# Dossier racine des exports. Le pipeline crée un sous-dossier horodaté à
# l'intérieur (ex: exports/2026-06-24/) pour ne jamais écraser un run précédent.
OUTPUT_DIR = os.path.join(RACINE_PROJET, "exports")

# ==============================================================================
# SOURCE DE DONNÉES
# ==============================================================================
# Option A : fichier combiné (toute l'année dans un seul xlsx)
FILE_PATH   = os.path.join(RACINE_PROJET, "donnees_ventes", "janvier_decembre",
                           "Janvier_Decembre_2021.xlsx")
EXCEL_SHEET = 0          # indice ou nom de la feuille Excel (0 = première feuille)

# Option B : dossier(s) de fichiers mensuels (un xlsx par mois) — ACTIF
USE_DOSSIER_MENSUEL = True
DATA_FOLDERS = [
    os.path.join(RACINE_PROJET, "donnees_ventes", "2021", "12_mois"),
    os.path.join(RACINE_PROJET, "donnees_ventes", "2022", "12_mois"),
    os.path.join(RACINE_PROJET, "donnees_ventes", "2023", "12_mois"),
    os.path.join(RACINE_PROJET, "donnees_ventes", "2024", "12_mois"),
    os.path.join(RACINE_PROJET, "donnees_ventes", "2025", "12_mois"),
    os.path.join(RACINE_PROJET, "donnees_ventes", "2026", "12_mois"),
]
DATA_FOLDER = DATA_FOLDERS[0]   # compatibilité ascendante

# Ligne d'en-tête réelle dans les fichiers Excel (0 = première ligne).
EXCEL_HEADER_ROW = 1

# Compléter le panel mensuel avec les mois complets présents dans les ventes
# JOURNALIÈRES mais absents des xlsx (mois récents, fichiers corrompus).
# Quantités identiques sur les mois communs ; CA TTC→HT (ratio par produit).
COMPLETER_DEPUIS_JOURNALIER = True

# Seuil minimal de CA HT pour considérer un fichier mensuel comme valide.
SEUIL_CA_FICHIER = 50_000   # MAD — en dessous = fichier incomplet/corrompu

# ==============================================================================
# COLONNES
# ==============================================================================
DATE_COL        = "Date"
PRODUCT_COL     = "Nom code article"
CATEGORY_COL    = "Nom Familles"
QTY_COL         = "QT"
REV_COL         = "CA HT"
QTY_RETURN_COL  = "QT retour"
CA_RETURN_COL   = "CA Retour"

# Harmonisation des variantes de noms de colonnes rencontrées dans les exports.
ALIAS_COLONNES = {
    "Nom Familles":     ["Nom Familles", "NomFamilles", "Famille", "Familles", "Nom_Familles"],
    "Nom code article": ["Nom code article", "Nom Code Article", "Nom_code_article",
                         "Article", "Désignation", "Libellé article", "Libelle article"],
    "QT":        ["QT", "Quantite", "Quantité", "Qty", "QTE", "Qté"],
    "CA HT":     ["CA HT", "CAHT", "CA_HT", "Chiffre affaires HT", "CA Ht"],
    "QT retour": ["QT retour", "QT Retour", "Qtté retour", "Qté retour", "Retour QT"],
    "CA Retour": ["CA Retour", "CA retour", "CA_retour", "Retour CA"],
}

# ==============================================================================
# AGRÉGATION ET PRÉVISION
# ==============================================================================
AGG_FREQ   = "ME"     # 'D' journalier, 'W' hebdo, 'ME' mensuel (fin de mois)
AGG_METHOD = "sum"    # "sum" ou "mean"

FORECAST_PERIODS = 12   # 12 mois → prévisions complètes 2026
MA_WINDOW        = 4    # fenêtre de la moyenne mobile (en périodes)

# Filtre produits (None = tous). Liste de valeurs de CATEGORY_COL sinon.
FAMILLES_FILTRE = None
# Ignorer les produits dont le total QT_Net est < ce seuil sur tout l'historique.
SEUIL_QT_MIN = 1

# ==============================================================================
# MODE D'EXÉCUTION
# ==============================================================================
# "prevision" → pipeline complet ; "backtest" → backtest inter-annuel uniquement
MODE = "prevision"

# Backtest annuel : désactivé par défaut. Il raisonne par année et, avec
# ANNEE_TEST dans ANNEE_TRAIN, souffre d'une fuite de données (le modèle "voit"
# l'année qu'il doit prédire). La validation mensuelle ci-dessous est préférable.
ACTIVER_BACKTEST_ANNUEL = False
ANNEE_TRAIN = [2021, 2022, 2023, 2024]   # exclut l'année de test → pas de fuite
ANNEE_TEST  = 2025

# Validation glissante "mois prochain" (walk-forward, horizon = 1 mois).
# On rejoue les N derniers mois : pour chacun, on entraîne sur tout le passé
# (sans le mois cible → aucune fuite) puis on prédit ce mois et on compare au réel.
# C'est la validation qui reflète le but réel : savoir prédire le mois suivant.
ACTIVER_VALIDATION_MENSUELLE = True
N_VALIDATION_WALKFORWARD     = 12   # nombre de mois rejoués (fenêtre de validation)
MIN_TRAIN_WALKFORWARD        = 24   # mois minimum d'entraînement (2 saisons)

# ──────────────────────────────────────────────────────────────────────────────
# STOCK DE SÉCURITÉ / FOURCHETTE DE PRÉVISION
# ──────────────────────────────────────────────────────────────────────────────
# Niveau de service = probabilité de NE PAS être en rupture. La quantité
# recommandée = prévision + z(service) × écart-type d'erreur. Plus haut = moins
# de ruptures mais plus de surstock. 0.90→z≈1.28 · 0.95→1.645 · 0.98→2.05 · 0.99→2.33
NIVEAU_SERVICE = 0.95
# Incertitude relative par défaut pour les produits sans historique de validation
# (écart-type ≈ 30 % de la prévision).
MARGE_INCERTITUDE_DEFAUT = 0.30

# Seuils d'indice de confiance (erreur relative = erreur validation / volume moyen).
# ≤ bon → 🟢 Fiable · ≤ moyen → 🟠 Moyen · au-delà → 🔴 Incertain.
FIABILITE_SEUIL_BON   = 0.20
FIABILITE_SEUIL_MOYEN = 0.40

# ==============================================================================
# PROPHET (optionnel)
# ==============================================================================
ACTIVER_PROPHET             = False  # lent dans la boucle produit
ACTIVER_BACKTESTING_PROPHET = False
N_TEST_BACKTEST             = 6
MIN_HISTORIQUE_PROPHET      = 6
# Limiter Prophet aux N produits les plus vendus (None = tous). Évite des heures
# de calcul sur 1 000+ produits ; les autres retombent sur la décompo saisonnière.
PROPHET_TOP_N = 30

# ==============================================================================
# SAISONNALITÉ
# ==============================================================================
# Multiplicateurs appliqués quand l'historique est trop court (< 2 saisons) pour
# que la décomposition détecte elle-même la saisonnalité.
PROFIL_SAISONNIER_MENSUEL = {
    1:  0.88, 2:  0.90, 3:  1.02, 4:  1.08, 5:  1.10, 6:  1.05,
    7:  1.18, 8:  1.20, 9:  1.02, 10: 0.98, 11: 0.92, 12: 1.15,
}
# Poids supplémentaire par jour de weekend vs jour de semaine.
COEF_WEEKEND = 0.30

# Seuil (MAD) en dessous duquel un mois est considéré "sans données" et interpolé.
SEUIL_HISTORIQUE = 50_000

# Ingrédients exclus du bon de commande (eau réseau / approvisionnement hors MRP).
INGREDIENTS_HORS_COMMANDE = {"Eau (ml)", "Eau chaude (ml)", "Eau pétillante (ml)"}

# ──────────────────────────────────────────────────────────────────────────────
# COMMANDES CLIENTS EXCEPTIONNELLES (B2B)
# ──────────────────────────────────────────────────────────────────────────────
# Détection des pics « possible commande » dans l'historique journalier : un
# jour est marqué si vente > médiane locale × RATIO et excès ≥ EXCES_MIN unités.
# Ces jours sont neutralisés dans l'apprentissage (cf. paul_forecast/commandes.py).
COMMANDE_RATIO_SEUIL = 2.2
COMMANDE_EXCES_MIN   = 30

# Repères "terrain" communiqués par le chef, pour caler l'ordre de grandeur du
# besoin matières issu des ventes.
REFERENCES_TERRAIN = {
    "Farine": {"motif": "farine|semoule", "kg": 3000.0, "jours": 21.0,
               "commentaire": "3 tonnes de farine toutes les 3 semaines (pas de pertes)"},
}

# Calibrage PROVISOIRE de matières (motif regex, facteur) : CALAGE SUR LE TERRAIN.
# Le repère chef (3 t de farine / 3 semaines, rapporté en juin 2026) est la vérité
# de consommation. Avec le panel complété (volumes 2026 : +20 % de ventes) et la
# couverture recettes quasi complète (exact + auto + génériques), le besoin BRUT
# des recettes pour juin 2026 dépasse légèrement le terrain (~4 700 vs 4 349 kg)
# → les recettes provisoires SURESTIMENT un peu la farine : on cale ×0.87.
# (Historique : ×1.42 puis ×1.34 quand la couverture était partielle, ×0.92 avant
# l'ajout des ~170 recettes exactes du 2026-07-07 qui ont accru le besoin brut.)
# Ce facteur disparaîtra avec les vraies recettes du chef.
CALIBRAGE_MATIERES = [("farine", 0.87), ("semoule", 0.87)]


# ==============================================================================
# CHARGEMENT DES DONNÉES MÉTIER EXTERNALISÉES (JSON éditables)
# ==============================================================================
def _charger_json(nom_fichier, defaut):
    """Charge un fichier JSON du dossier data/. Retourne `defaut` si absent/illisible."""
    chemin = os.path.join(DATA_DIR, nom_fichier)
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[CONFIG] Avertissement : '{chemin}' illisible ({e}) — valeur par défaut utilisée.")
        return defaut


# Recettes à correspondance exacte (éditables sans toucher au code).
BOM = _charger_json("recettes_exactes.json", {})

# Provenance de chaque recette exacte (fiche chef, estimation logique…).
PROVENANCE_RECETTES = _charger_json("recettes_exactes_provenance.json", {})

# Sources de provenance considérées comme EXACTES (validées chef / fiche technique).
# Le reste (estimation logique, extrapolation, « à valider chef », détection par
# motif, générique famille) est marqué « estimation » dans le bon de commande.
SOURCES_RECETTE_EXACTE = {"fiche reelle", "recette chef",
                          "tableau chefs (xlsx)",              # importer_recettes_chefs
                          "fiches chef Maroc cuisine (xlsx)",  # importer_recettes_cuisine_maroc
                          "fiches chef cuisine (xlsx p2)"}     # importer_recettes_cuisine_p2

# Catégories du bon de commande segmenté (un fichier besoins_ingredients_<cat>.csv
# par catégorie ; seule la boulangerie est aujourd'hui 100 % recettes exactes).
CATEGORIES_BESOINS = ["BOULANGERIE", "CUISINE", "PATISSERIE", "VIENNOISERIE"]

# Modèle de prévision retenu PAR PRODUIT (banc d'essai walk-forward).
# Nom du modèle gagnant (ETS, Theta, ARIMA, SeasonalNaive, MoyenneMobile,
# Moyenne, Croston, Ensemble). Produits absents → défaut Holt-Winters.
# Régénérer via outils/benchmark_v2.py.
MODELE_PAR_PRODUIT = _charger_json("modele_par_produit.json", {})

# Calendrier des fêtes marocaines (dates lunaires variables chaque année).
FETES_MAROCAINES = _charger_json("fetes_maroc.json", [])

# Référentiel d'harmonisation des noms de matières premières (canonique → variantes).
# Permet de fusionner « Farine T65 » et « Farine de blé T65 (g) » en une seule ligne MRP.
ALIAS_MATIERES = _charger_json("alias_matieres.json", {})

# Sous-recettes des produits semi-finis (PSF) → matières de base, pour l'éclatement
# multi-niveaux du BOM (ex : CROISSANT GM → farine, beurre, lait...).
RECETTES_PSF = _charger_json("recettes_psf.json", {"recettes": {}, "alias": {}})

# Notes explicatives par produit (ex : hausse due à un client B2B identifié, pas
# à la demande boutique) — affichées sur le graphe de l'onglet Historique pour que
# les variations ne restent pas mystérieuses. Purement informatif, n'affecte pas
# le calcul des prévisions.
NOTES_PRODUITS = _charger_json("notes_produits.json", {"notes": []})

# Profil de consommation pendant le Ramadan/Aïd, mesuré sur données réelles 2026.
# Ramadan ≠ pic de CA (×1.03) mais le mix bascule (boulangerie ×1.55, menu ×0.37).
PROFIL_RAMADAN = _charger_json("profil_ramadan.json", {})

# Profils par fête (ramadan, aid_fitr, aid_adha, achoura, mawlid) → ratios par
# famille. Ramadan mesuré ; autres = hypothèses à calibrer. Voir profils_fetes.json.
PROFILS_FETES = _charger_json("profils_fetes.json", {})

# Événements ponctuels à fort impact (match, jour férié, concert…) saisis par
# l'utilisateur via le dashboard. Boost attendu appliqué au prorata des jours.
EVENEMENTS = _charger_json("evenements.json", {"evenements": []})

# Prix d'achat estimés des matières premières (MAD) — budget d'achat & food-cost.
PRIX_MATIERES = _charger_json("prix_matieres.json", {"prix": []})

# Types d'événements proposés dans le formulaire du dashboard (libellé affiché).
TYPES_EVENEMENTS = {
    "match":      "Match (équipe nationale)",
    "jour_ferie": "Jour férié",
    "concert":    "Concert",
    "festival":   "Festival / salon",
    "meteo":      "Météo (canicule, pluie…)",
    "promo":      "Promotion",
    "autre":      "Autre",
}

# Rafraîchir les dates de fêtes via l'API Aladhan au lancement du pipeline.
# Désactivé par défaut (pas de dépendance réseau) : fetes_maroc.json sert de cache.
# Pour mettre à jour manuellement : python -m paul_forecast.fetes_api 2026 2027 2028
RAFRAICHIR_FETES_API = False

# -*- coding: utf-8 -*-
"""
Export des ventes depuis la base SQL Server de la caisse PI Electronique vers
`donnees_ventes/ventes_journalieres.csv` (format attendu par le forecasting).

Principe : connexion **LECTURE SEULE** (SELECT + WITH (NOLOCK)) à la base du PI,
une requête qui agrège les tickets par jour × article, et écriture du CSV au même
format que `outils/convertir_ventes_journalieres.py`
(colonnes : Date;Code;Produit;Famille;Quantite;CA_TTC ; séparateur « ; » ; UTF-8).

SÛRETÉ (lecture seule = ne peut RIEN modifier/corrompre dans la caisse) :
  - la requête ne fait que des SELECT, avec WITH (NOLOCK) → aucun verrou sur la base ;
  - idéalement un login SQL dédié en lecture seule (demander à Distrilog) ;
  - à lancer à heure creuse (tôt le matin, après la RAZ de nuit) via le Planificateur.

DÉFENSIF : si la requête échoue, renvoie 0 ligne ou des totaux aberrants, le CSV
existant N'EST PAS écrasé (on garde la dernière bonne version) et le script sort en
erreur (code ≠ 0) pour déclencher l'alerte de la tâche planifiée.

Config : `data/config_pi.json` (NON versionné — cf. modèle `outils/config_pi.exemple.json`).
Dépendances : `pip install pyodbc` + « ODBC Driver 17 (ou 18) for SQL Server » installé.

Lancement : python -m outils.importer_ventes_pi   (ou : python outils/importer_ventes_pi.py)
"""

import os
import sys
import json
import shutil
import logging
from datetime import datetime

import pandas as pd

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)

CONFIG_PI = os.path.join(_RACINE, "data", "config_pi.json")
SORTIE_CSV = os.path.join(_RACINE, "donnees_ventes", "ventes_journalieres.csv")
COLONNES_CIBLE = ["Date", "Code", "Produit", "Famille", "Quantite", "CA_TTC"]

logger = logging.getLogger("import_pi")


def configurer_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def charger_config():
    """Charge data/config_pi.json ; message clair si absent (à créer sur le serveur)."""
    if not os.path.exists(CONFIG_PI):
        logger.error("Config absente : %s", CONFIG_PI)
        logger.error("Crée-la à partir de outils/config_pi.exemple.json (serveur, non versionné).")
        sys.exit(2)
    with open(CONFIG_PI, encoding="utf-8") as f:
        return json.load(f)


def chaine_connexion(cfg):
    """Construit la chaîne de connexion pyodbc depuis la config.

    Auth Windows (recommandé : aucun mot de passe stocké) si `auth` == "windows",
    sinon login SQL (`utilisateur`/`mot_de_passe`).
    """
    pilote = cfg.get("pilote_odbc", "ODBC Driver 17 for SQL Server")
    parts = [
        f"DRIVER={{{pilote}}}",
        f"SERVER={cfg['serveur']}",          # ex. "localhost\\PIELECTRONIQUE" ou ".\\SQLEXPRESS"
        f"DATABASE={cfg['base']}",           # ex. "RESTO"
    ]
    if cfg.get("auth", "windows").lower() == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={cfg['utilisateur']}")
        parts.append(f"PWD={cfg['mot_de_passe']}")
    # Chiffrement : le driver 18 chiffre par défaut ; base locale → on tolère le cert auto-signé.
    parts.append("Encrypt=optional")
    parts.append("TrustServerCertificate=yes")
    return ";".join(parts) + ";"


def lire_ventes(cfg):
    """Exécute la requête (lecture seule) et renvoie un DataFrame aux colonnes cibles."""
    import pyodbc  # importé ici pour un message clair si non installé

    requete = cfg.get("requete_sql")
    if not requete:
        logger.error("Clé 'requete_sql' manquante dans la config — impossible de continuer.")
        sys.exit(2)

    logger.info("Connexion à %s / base %s (lecture seule)…", cfg["serveur"], cfg["base"])
    with pyodbc.connect(chaine_connexion(cfg), timeout=cfg.get("timeout_s", 30), readonly=True) as cnx:
        df = pd.read_sql(requete, cnx)
    logger.info("Requête OK : %d lignes remontées.", len(df))
    return df


def valider(df):
    """Garde-fous avant d'écraser le CSV : colonnes présentes, non vide, totaux sains."""
    manquantes = [c for c in COLONNES_CIBLE if c not in df.columns]
    if manquantes:
        raise ValueError(f"Colonnes cibles manquantes dans le résultat SQL : {manquantes}. "
                         f"Vérifie les alias de la requête (attendus : {COLONNES_CIBLE}).")
    if df.empty:
        raise ValueError("La requête renvoie 0 ligne — refus d'écraser le CSV existant.")
    q_tot = pd.to_numeric(df["Quantite"], errors="coerce").fillna(0).sum()
    if q_tot <= 0:
        raise ValueError(f"Quantité totale = {q_tot} — donnée suspecte, refus d'écraser le CSV.")
    n_jours = pd.to_datetime(df["Date"], errors="coerce").dt.date.nunique()
    logger.info("Validation OK : %d lignes, %d jours, QT totale %.0f.", len(df), n_jours, q_tot)


def normaliser(df):
    """Met le DataFrame au format exact du CSV cible."""
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["Code"] = pd.to_numeric(df["Code"], errors="coerce").astype("Int64")
    df["Produit"] = df["Produit"].astype(str).str.strip()
    df["Famille"] = df["Famille"].astype(str).str.strip().replace({"": "Autres", "nan": "Autres"})
    df["Quantite"] = pd.to_numeric(df["Quantite"], errors="coerce").fillna(0.0)
    df["CA_TTC"] = pd.to_numeric(df["CA_TTC"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["Date", "Code"])
    return df[COLONNES_CIBLE].sort_values(["Date", "Code"])


def ecrire_atomique(df):
    """Sauvegarde l'ancien CSV, écrit le nouveau de façon atomique (fichier temp + rename)."""
    os.makedirs(os.path.dirname(SORTIE_CSV), exist_ok=True)
    if os.path.exists(SORTIE_CSV):
        backup = SORTIE_CSV + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(SORTIE_CSV, backup)
        logger.info("Sauvegarde de l'ancien CSV : %s", os.path.basename(backup))
    tmp = SORTIE_CSV + ".tmp"
    df.to_csv(tmp, sep=";", index=False, encoding="utf-8")
    os.replace(tmp, SORTIE_CSV)  # atomique : le CSV n'est jamais à moitié écrit
    logger.info("[OK] %d lignes écrites → %s", len(df), SORTIE_CSV)


def main():
    configurer_logging()
    logger.info("=" * 60)
    logger.info(" EXPORT VENTES PI ELECTRONIQUE → ventes_journalieres.csv")
    logger.info("=" * 60)
    cfg = charger_config()
    try:
        df = lire_ventes(cfg)
        valider(df)
        df = normaliser(df)
        valider(df)  # re-valide après normalisation
        ecrire_atomique(df)
    except Exception as e:
        logger.error("ÉCHEC de l'export : %s", e)
        logger.error("Le CSV existant est CONSERVÉ (non écrasé). Corrige puis relance.")
        sys.exit(1)
    logger.info(" TERMINÉ AVEC SUCCÈS")


if __name__ == "__main__":
    main()

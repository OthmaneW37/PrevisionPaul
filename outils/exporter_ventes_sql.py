# -*- coding: utf-8 -*-
"""
Exporte les ventes journalières par produit depuis la base SQL d'Elyx
(PI ELECTRONIQUE, instance locale SQLEXPRESS2014, base PAULCFC) vers les
CSV du projet — c'est le remplaçant automatisé des états DOS
« ProduitParJour<AAAA>.txt » de l'ancien serveur.

Principe (migration en cours : l'historique vient de l'ancien serveur,
le flux neuf arrivera ici quand les caisses seront raccordées) :
  1. lit les lignes de tickets imputées (tables IMPUTATION_<site>),
     agrégées par jour × code article — les jours ANTÉRIEURS à aujourd'hui
     seulement (le jour courant est incomplet tant que la caisse n'a pas clôturé),
  2. mémorise l'extrait cumulé dans donnees_ventes/ventes_sql.csv
     (relance = rafraîchissement complet, idempotent),
  3. fusionne dans donnees_ventes/ventes_journalieres.csv : pour chaque date
     présente dans l'extrait SQL, les lignes SQL REMPLACENT les lignes
     existantes ; l'historique (ancien serveur) reste intact pour les autres dates.

La Famille d'un code article est reprise de l'historique du projet quand le
code y est connu, sinon du référentiel articles/familles d'Elyx, sinon « Autres ».

Lancement : python outils/exporter_ventes_sql.py
            (appelé chaque nuit par outils/mise_a_jour_quotidienne.py)
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------

import os
import re
from datetime import date

import pandas as pd

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENTES = os.path.join(RACINE, "donnees_ventes")
CSV_CONSOLIDE = os.path.join(VENTES, "ventes_journalieres.csv")
CSV_SQL = os.path.join(VENTES, "ventes_sql.csv")

# Connexion à l'instance SQL Server d'Elyx (authentification Windows).
# Surcharge possible par variables d'environnement (tests, changement d'instance).
CHAINE_CONNEXION = (
    r"Driver={ODBC Driver 11 for SQL Server};"
    r"Server=" + os.environ.get("PAUL_SQL_SERVEUR", r"localhost\SQLEXPRESS2014") + ";"
    r"Database=" + os.environ.get("PAUL_SQL_BASE", "PAULCFC") + ";"
    r"Trusted_Connection=yes"
)

COLONNES = ["Date", "Code", "Produit", "Famille", "Quantite", "CA_TTC"]


def _tables_imputation(cur):
    """Tables IMPUTATION_<n> existantes (une par site déclaré dans Elyx)."""
    cur.execute(
        "SELECT name FROM sys.tables WHERE name LIKE 'IMPUTATION[_]%' "
        "AND name NOT LIKE '%programmation%'"
    )
    return [r[0] for r in cur.fetchall() if re.fullmatch(r"IMPUTATION_\d+", r[0])]


def extraire_ventes_sql():
    """DataFrame (Date, Code, Produit, Quantite, CA_TTC, FamilleSQL) des jours clos.

    Retourne un DataFrame vide (bonnes colonnes) si les caisses n'ont encore
    rien remonté — la fusion est alors sans effet, sans erreur.
    """
    import pyodbc

    cn = pyodbc.connect(CHAINE_CONNEXION)
    cur = cn.cursor()

    morceaux = []
    for table in _tables_imputation(cur):
        cur.execute(f"""
            SELECT CONVERT(date, i.date_seule)  AS Date,
                   CAST(i.Ventil AS int)        AS Code,
                   MAX(NULLIF(LTRIM(RTRIM(i.libelle_article)), '')) AS Produit,
                   SUM(i.QT)                    AS Quantite,
                   SUM(i.CA)                    AS CA_TTC
            FROM {table} i
            WHERE i.date_seule < CONVERT(date, GETDATE())
            GROUP BY CONVERT(date, i.date_seule), CAST(i.Ventil AS int)
            HAVING SUM(i.QT) <> 0 OR SUM(i.CA) <> 0
        """)
        lignes = cur.fetchall()
        if lignes:
            morceaux.append(pd.DataFrame(
                [tuple(l) for l in lignes],
                columns=["Date", "Code", "Produit", "Quantite", "CA_TTC"]))

    # Référentiel articles d'Elyx : nom + famille (utile pour les codes
    # inconnus de l'historique — nouveaux produits créés après la migration).
    cur.execute("""
        SELECT CAST(a.code_article AS int), a.libelle_article,
               NULLIF(LTRIM(RTRIM(f.libelle)), '')
        FROM articles a LEFT JOIN familles f ON f.numero = a.famille
    """)
    referentiel = {int(c): (str(n or "").strip(), fam)
                   for c, n, fam in cur.fetchall()}
    cn.close()

    if not morceaux:
        df = pd.DataFrame(columns=["Date", "Code", "Produit", "Quantite", "CA_TTC"])
    else:
        df = pd.concat(morceaux, ignore_index=True)
        # même code présent sur plusieurs sites : on somme
        df = (df.groupby(["Date", "Code"], as_index=False)
                .agg(Produit=("Produit", "first"),
                     Quantite=("Quantite", "sum"),
                     CA_TTC=("CA_TTC", "sum")))
        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        df["Quantite"] = pd.to_numeric(df["Quantite"], errors="coerce").fillna(0.0).astype(float)
        df["CA_TTC"] = pd.to_numeric(df["CA_TTC"], errors="coerce").fillna(0.0).astype(float)
    return df, referentiel


def _mapping_historique():
    """Code article -> (Produit, Famille) d'après le CSV consolidé existant."""
    if not os.path.exists(CSV_CONSOLIDE):
        return {}
    histo = pd.read_csv(CSV_CONSOLIDE, sep=";", encoding="utf-8")
    histo = histo.dropna(subset=["Code"]).sort_values("Date")
    return {int(r["Code"]): (str(r["Produit"]), str(r["Famille"]))
            for _, r in histo.iterrows()}


def completer_produit_famille(df_sql, referentiel, mapping_histo):
    """Complète Produit (libellé) et Famille pour chaque ligne SQL.

    Priorités — Produit : libellé du ticket > référentiel Elyx > nom historique
    > « ARTICLE <code> ».  Famille : historique du projet (cohérence des séries)
    > famille Elyx (si programmée) > « Autres ».
    """
    if df_sql.empty:
        df = df_sql.copy()
        df["Famille"] = pd.Series(dtype=str)
        return df[COLONNES] if all(c in df.columns for c in COLONNES) else \
            pd.DataFrame(columns=COLONNES)

    produits, familles = [], []
    for _, r in df_sql.iterrows():
        code = int(r["Code"])
        nom_ref, fam_ref = referentiel.get(code, ("", None))
        nom_histo, fam_histo = mapping_histo.get(code, ("", ""))
        produit = str(r["Produit"] or "").strip() or nom_ref or nom_histo \
            or f"ARTICLE {code}"
        if fam_histo and fam_histo.lower() != "nan":
            famille = fam_histo
        elif fam_ref and fam_ref.lower() not in ("non prog.", "nan"):
            famille = fam_ref
        else:
            famille = "Autres"
        produits.append(produit)
        familles.append(famille)

    df = df_sql.copy()
    df["Produit"] = produits
    df["Famille"] = familles
    return df[COLONNES]


def fusionner(df_consolide, df_sql):
    """Fusion : pour chaque Date présente dans df_sql, ses lignes remplacent
    celles de df_consolide ; les autres dates restent inchangées."""
    if df_sql.empty:
        return df_consolide
    dates_sql = set(df_sql["Date"].astype(str))
    garde = df_consolide[~df_consolide["Date"].astype(str).isin(dates_sql)]
    return (pd.concat([garde, df_sql], ignore_index=True)
              .sort_values(["Date", "Code"]).reset_index(drop=True))


def exporter():
    df_sql, referentiel = extraire_ventes_sql()
    mapping_histo = _mapping_historique()
    df_sql = completer_produit_famille(df_sql, referentiel, mapping_histo)

    if df_sql.empty:
        print("[Export SQL] Aucune vente dans la base Elyx pour l'instant "
              "(caisses pas encore raccordées au nouveau serveur) — rien à fusionner.")
        print(f"[Export SQL] Référentiel articles Elyx : {len(referentiel)} codes.")
        return df_sql

    # 1) mémoire cumulée des lignes issues de SQL (traçabilité / re-fusion)
    df_sql.to_csv(CSV_SQL, sep=";", index=False, encoding="utf-8")

    # 2) fusion dans le consolidé
    consolide = (pd.read_csv(CSV_CONSOLIDE, sep=";", encoding="utf-8")
                 if os.path.exists(CSV_CONSOLIDE)
                 else pd.DataFrame(columns=COLONNES))
    fusion = fusionner(consolide, df_sql)
    fusion.to_csv(CSV_CONSOLIDE, sep=";", index=False, encoding="utf-8")

    inconnues = (df_sql["Famille"] == "Autres").sum()
    print(f"[Export SQL] {len(df_sql)} lignes extraites "
          f"({df_sql['Date'].min()} -> {df_sql['Date'].max()}) ; "
          f"{df_sql['Code'].nunique()} produits ; "
          f"{inconnues} ligne(s) sans famille connue.")
    print(f"[Export SQL] Consolidé mis à jour : {len(fusion)} lignes -> {CSV_CONSOLIDE}")

    # contrôle : totaux des 7 derniers jours, à comparer aux Z de caisse
    recap = (df_sql.groupby("Date")[["Quantite", "CA_TTC"]].sum()
             .tail(7).round(0))
    print("[Export SQL] Contrôle (7 derniers jours extraits, à comparer au Z) :")
    print(recap.to_string())
    return df_sql


if __name__ == "__main__":
    exporter()

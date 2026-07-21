# -*- coding: utf-8 -*-
"""
Exploration (LECTURE SEULE) de la base SQL Server PI Electronique pour identifier
les tables/vues des ventes et leurs colonnes — utile pour vérifier/écrire la
requête d'export de `outils/exporter_ventes_sql.py` quand les caisses seront
raccordées (ou si le schéma Elyx change).

CONFIDENTIEL : ce script n'affiche QUE le SCHÉMA (noms de tables/vues, noms de
colonnes, types, nombre de lignes). Il n'affiche AUCUNE valeur de vente réelle,
donc son résultat peut être partagé sans exposer de données.

Connexion IDENTIQUE à `exporter_ventes_sql.py` (pilote 11, instance
`localhost\\SQLEXPRESS2014`, base `PAULCFC`, authentification Windows), surchargeable
par les variables d'environnement PAUL_SQL_SERVEUR / PAUL_SQL_BASE. Dépend de `pyodbc`.

Lancement : python outils/explorer_base_pi.py
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# Console Windows au Maroc = souvent cp1256 (arabe) : forcer UTF-8 pour ne pas
# planter sur les accents. errors='replace' = jamais d'exception d'encodage.
for _flux in (_sys.stdout, _sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
# ---------------------------------------------------------------------------------

import os

# Connexion à l'instance SQL Server d'Elyx (authentification Windows), même
# chaîne que outils/exporter_ventes_sql.py — surcharge possible par env.
CHAINE_CONNEXION = (
    r"Driver={ODBC Driver 11 for SQL Server};"
    r"Server=" + os.environ.get("PAUL_SQL_SERVEUR", r"localhost\SQLEXPRESS2014") + ";"
    r"Database=" + os.environ.get("PAUL_SQL_BASE", "PAULCFC") + ";"
    r"Trusted_Connection=yes"
)

# Mots-clés des objets susceptibles de contenir les ventes / le référentiel.
MOTS_CLES = ["vente", "ticket", "imputation", "stat", "jour", "detail", "ligne",
             "article", "famille", "produit", "caisse", "resto", "ca"]


def _connexion():
    import pyodbc  # importé ici pour un message clair si non installé
    return pyodbc.connect(CHAINE_CONNEXION, readonly=True)


def main():
    serveur = os.environ.get("PAUL_SQL_SERVEUR", r"localhost\SQLEXPRESS2014")
    base = os.environ.get("PAUL_SQL_BASE", "PAULCFC")
    print("=" * 70)
    print(f" EXPLORATION BASE PI — {serveur} / {base}  (lecture seule)")
    print("=" * 70)
    cnx = _connexion()
    cur = cnx.cursor()

    print("\n[Version SQL Server]")
    cur.execute("SELECT @@VERSION")
    print("  " + cur.fetchone()[0].splitlines()[0])

    # 1. Tous les objets (tables + vues) + estimation du nombre de lignes.
    cur.execute("""
        SELECT o.type_desc, s.name AS sch, o.name AS obj,
               ISNULL(p.rows, 0) AS nb
        FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        LEFT JOIN sys.partitions p ON p.object_id = o.object_id AND p.index_id IN (0,1)
        WHERE o.type IN ('U','V')
        ORDER BY nb DESC, o.name
    """)
    objets = cur.fetchall()

    def _pertinent(nom):
        n = nom.lower()
        return any(k in n for k in MOTS_CLES)

    print(f"\n[Objets pertinents] (contenant : {', '.join(MOTS_CLES)})")
    print(f"  {'TYPE':<6} {'SCHÉMA.OBJET':<45} {'~LIGNES':>12}")
    print("  " + "-" * 66)
    candidats = []
    for typ, sch, obj, nb in objets:
        if _pertinent(obj):
            candidats.append((sch, obj))
            t = "VUE" if typ == "VIEW" else "TABLE"
            print(f"  {t:<6} {sch + '.' + obj:<45} {nb:>12,}".replace(",", " "))

    # 2. Colonnes des candidats les plus volumineux (max 15 objets).
    print("\n[Colonnes des objets candidats]")
    for sch, obj in candidats[:15]:
        cur.execute("""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """, sch, obj)
        cols = cur.fetchall()
        print(f"\n  ── {sch}.{obj} ──")
        ligne = ", ".join(f"{c[0]} ({c[1]})" for c in cols)
        print("     " + ligne)

    cnx.close()
    print("\n" + "=" * 70)
    print("Copie-colle TOUT ce qui précède : utile pour vérifier la requête d'export.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Exploration (LECTURE SEULE) de la base SQL Server PI Electronique pour identifier
les tables/vues des ventes et leurs colonnes — afin d'écrire la requête d'export
(`outils/importer_ventes_pi.py`).

CONFIDENTIEL : ce script n'affiche QUE le SCHÉMA (noms de tables/vues, noms de
colonnes, types, nombre de lignes). Il n'affiche AUCUNE valeur de vente réelle,
donc son résultat peut être partagé sans exposer de données.

Réutilise `data/config_pi.json` (mêmes clés serveur/base/auth ; la clé
`requete_sql` n'est pas utilisée ici). Dépend de `pyodbc`.

Lancement : python outils\explorer_base_pi.py
"""

import os
import sys
import json

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RACINE)

CONFIG_PI = os.path.join(_RACINE, "data", "config_pi.json")

# Mots-clés des objets susceptibles de contenir les ventes / le référentiel.
MOTS_CLES = ["vente", "ticket", "imputation", "stat", "jour", "detail", "ligne",
             "article", "famille", "produit", "caisse", "resto", "ca"]


def _cfg():
    if not os.path.exists(CONFIG_PI):
        print(f"[ERREUR] Config absente : {CONFIG_PI}")
        print("Crée-la depuis outils/config_pi.exemple.json (au moins serveur/base/auth).")
        sys.exit(2)
    with open(CONFIG_PI, encoding="utf-8") as f:
        return json.load(f)


def _connexion(cfg):
    import pyodbc
    pilote = cfg.get("pilote_odbc", "ODBC Driver 17 for SQL Server")
    parts = [f"DRIVER={{{pilote}}}", f"SERVER={cfg['serveur']}", f"DATABASE={cfg['base']}"]
    if cfg.get("auth", "windows").lower() == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={cfg['utilisateur']}")
        parts.append(f"PWD={cfg['mot_de_passe']}")
    parts += ["Encrypt=optional", "TrustServerCertificate=yes"]
    return pyodbc.connect(";".join(parts) + ";", timeout=cfg.get("timeout_s", 30), readonly=True)


def main():
    cfg = _cfg()
    print("=" * 70)
    print(f" EXPLORATION BASE PI — {cfg['serveur']} / {cfg['base']}  (lecture seule)")
    print("=" * 70)
    cnx = _connexion(cfg)
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
    print("Copie-colle TOUT ce qui précède : je m'en sers pour écrire la requête d'export.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Convertit les exports « ProduitParJour<AAAA>.txt » (toutes années) en CSV propre
exploitable par le projet, en rattachant chaque produit à sa catégorie (Famille).

Le fichier brut est un état imprimable DOS : largeur fixe, filets de tableau,
encodage CP850, lignes de données séparées par « · » (octet 0xFA) et entrecoupées
de bordures / titres / sous-totaux. Pour chaque année on :
  1. extrait les lignes de données (séparateur + date), on EXCLUT les sous-totaux
     « S/T Jour Année » (code non numérique) — sinon double comptage,
  2. découpe sur « · », décode en latin-1 (accents corrects), normalise les nombres,
  3. rattache la catégorie via le code article (table globale issue des Excel annuels).

Sorties :
  - donnees_ventes/<AAAA>/ventes_journalieres_<AAAA>.csv   (par année)
  - donnees_ventes/ventes_journalieres.csv                 (toutes années, consolidé)

Lancement : python _convertir_ventes_journalieres.py
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------

import os
import re
import glob

import pandas as pd

# Racine du PROJET (ce script vit dans outils/ — un seul dirname pointerait ici).
RACINE  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENTES  = os.path.join(RACINE, "donnees_ventes")
SORTIE_GLOBALE = os.path.join(VENTES, "ventes_journalieres.csv")

SEP = b"\xfa"  # séparateur de colonne « · » dans l'export CP850
LIGNE_DATA = re.compile(rb"^\xfa?\s*\d{2}/\d{2}/\d{4}")


def _excel_annuel(dossier_annee):
    """Trouve l'Excel annuel (Janvier..Décembre) d'une année, hors fichiers temporaires."""
    motifs = ["**/Janvier*[Dd]ecembre*.xlsx", "**/janvier*decembre*.xlsx"]
    for m in motifs:
        for f in glob.glob(os.path.join(dossier_annee, m), recursive=True):
            base = os.path.basename(f)
            if base.startswith("~$") or "AutoRecover" in base:
                continue
            return f
    return None


def table_familles():
    """Table globale code article (int) -> (Nom Familles, Nom code article).

    Fusionne tous les Excel annuels ; l'année la plus récente l'emporte pour le libellé.
    """
    fam = {}
    for annee in sorted(_annees_disponibles()):
        xlsx = _excel_annuel(os.path.join(VENTES, str(annee)))
        if not xlsx:
            continue
        df = pd.read_excel(xlsx, header=1)
        if "code article" not in df.columns:
            continue
        df = df[df["code article"].notna()]
        for _, r in df.iterrows():
            code = int(r["code article"])
            nom_fam = str(r.get("Nom Familles", "")).strip()
            if not nom_fam or nom_fam.lower() == "nan":
                nom_fam = "Autres"
            fam[code] = (nom_fam, str(r.get("Nom code article", "")).strip())
    return fam


def _annees_disponibles():
    """Années ayant un fichier ProduitParJour*.txt."""
    annees = []
    for d in glob.glob(os.path.join(VENTES, "*")):
        if os.path.isdir(d) and os.path.basename(d).isdigit():
            if glob.glob(os.path.join(d, "ProduitParJour*.txt")):
                annees.append(int(os.path.basename(d)))
    return sorted(annees)


def _nombre(champ):
    """'1 234,50' (latin-1) -> 1234.5 ; vide/illisible -> 0.0."""
    s = champ.decode("latin-1").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def convertir_annee(annee, fam):
    """Convertit le fichier journalier d'une année -> DataFrame propre."""
    fichiers = glob.glob(os.path.join(VENTES, str(annee), "ProduitParJour*.txt"))
    if not fichiers:
        return None, 0
    brut = open(fichiers[0], "rb").read()

    lignes, sous_totaux = [], 0
    for l in brut.split(b"\r\n"):
        if not (l.startswith(SEP) and LIGNE_DATA.match(l)):
            continue
        ch = l.split(SEP)
        # champs : 0='' 1=Date 2=Date{Jour} 3=Code 4=Nom 5=CA_TTC 6=CA_HT 7=QT ...
        code_txt = ch[3].decode("latin-1").strip()
        if not code_txt.isdigit():   # sous-total « S/T Jour Année »
            sous_totaux += 1
            continue
        code = int(code_txt)
        nom_fam, nom_ref = fam.get(code, ("Autres", ""))
        date = pd.to_datetime(ch[1].decode("latin-1").strip(), format="%d/%m/%Y")
        lignes.append({
            "Date":     date.strftime("%Y-%m-%d"),
            "Code":     code,
            "Produit":  ch[4].decode("latin-1").strip() or nom_ref,
            "Famille":  nom_fam,
            "Quantite": _nombre(ch[7]),
            "CA_TTC":   _nombre(ch[5]),
        })
    return pd.DataFrame(lignes), sous_totaux


def convertir():
    fam = table_familles()
    print(f"Table catégories : {len(fam)} codes article rattachés à une famille.\n")

    annees = _annees_disponibles()
    morceaux = []
    for annee in annees:
        df, sous_tot = convertir_annee(annee, fam)
        if df is None or df.empty:
            print(f"  {annee} : aucun fichier exploitable.")
            continue
        sortie = os.path.join(VENTES, str(annee), f"ventes_journalieres_{annee}.csv")
        df.to_csv(sortie, sep=";", index=False, encoding="utf-8")
        inconnus = (df["Famille"] == "Autres").sum()
        print(f"  {annee} : {len(df):>6} lignes | {df['Date'].nunique():>3} jours "
              f"({df['Date'].min()} -> {df['Date'].max()}) | "
              f"{df['Produit'].nunique():>3} produits | QT {df['Quantite'].sum():>10,.0f} | "
              f"{sous_tot} sous-totaux écartés".replace(",", " "))
        morceaux.append(df)

    if not morceaux:
        print("\nAucune donnée convertie.")
        return None

    tout = pd.concat(morceaux, ignore_index=True)
    tout.to_csv(SORTIE_GLOBALE, sep=";", index=False, encoding="utf-8")
    print(f"\n[OK] Consolidé : {len(tout):,} lignes -> {SORTIE_GLOBALE}".replace(",", " "))
    print(f"  Période     : {tout['Date'].min()} -> {tout['Date'].max()}")
    print(f"  Produits    : {tout['Produit'].nunique()}")
    print(f"  Catégories  : {sorted(tout['Famille'].unique())}")
    print(f"  QT totale   : {tout['Quantite'].sum():,.0f}".replace(",", " "))
    return tout


if __name__ == "__main__":
    convertir()

# -*- coding: utf-8 -*-
"""
Enregistre les PRÉPARATIONS MAISON de la cuisine comme produits semi-finis (PSF)
dans data/recettes_psf.json, pour qu'elles soient ÉCLATÉES en matières premières
de base dans le bon de commande (au lieu d'apparaître telles quelles).

Ex. « Appareil crêpe sucrée », « Sauce béchamel », « Base couscous »… ne sont pas
des matières à commander : elles se fabriquent sur place à partir de farine, lait,
œufs, beurre… C'est ce que fait bom.exploser_psf, mais uniquement pour les PSF
déclarés dans recettes_psf.json — d'où ce script.

Deux sources :
  1. les sous-recettes déjà importées dans recettes_exactes.json (quantités en
     grammes d'un batch) -> converties en RATIOS par gramme ;
  2. quelques recettes maison standard manquantes (béchamel, appareil crêpe salée,
     béchamel aux champignons) ajoutées ici.

Les sauces ACHETÉES (nuoc-man, oyster, soja, thaï, vinaigrette, américaine…) ne
sont PAS déclarées : elles restent des matières premières (on les commande).

Alias : pour chaque PSF, toutes les variantes d'écriture présentes dans les
besoins (avec code/marque) sont mappées vers le nom du PSF.

Lancement : python outils/enregistrer_psf_cuisine.py [--dry-run]
Puis relancer python main.py.
"""

import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)

import argparse
import glob
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime

import pandas as pd

RECETTES_JSON = os.path.join(_RACINE, "data", "recettes_exactes.json")
PSF_JSON = os.path.join(_RACINE, "data", "recettes_psf.json")

# Sous-recettes importées (nom canonique dans recettes_exactes.json) à éclater.
SOUS_RECETTES = [
    "APPAREIL CREPE SUCREE", "VIANDE HACHEE MAISON", "BASE COUSCOUS",
    "SAUCE CHEDDAR", "SAUCE TOMATE", "OIGNONS FRITS", "OIGNONS CARAMELISES",
    "SAUCE HOLLANDAISE", "SAUCE GRECQUE", "SAUCE ASIA", "CHAKCHOUKA",
    "RIZ A LA CIBOULETTE", "PUREE A LA TRUFFE", "MELANGE AVOCAT",
    "CREVETTE GRISE PANEE", "SIDE FROID", "SAUCE MOUTARDE",
    "CROUTONS PAIN NORDIQUE", "TOMATES CERISES ROTIES", "APPAREIL A JAOUHARA",
    "SAUCE MIEL AGRUMES (base)",
]

# Recettes maison standard manquantes (batch en grammes). La béchamel aux
# champignons référence « Sauce béchamel » -> éclatement récursif.
STANDARD = {
    "Sauce béchamel": {
        "Lait entier (ml)": 1000, "Beurre 84% MG (g)": 80,
        "Farine de blé T55 (g)": 80, "Sel (g)": 5, "Muscade (g)": 1,
    },
    "Appareil crêpe salée": {
        "Lait entier (ml)": 1550, "Farine de blé T55 (g)": 600,
        "Œufs (g)": 600, "Huile de tournesol (ml)": 30, "Sel (g)": 10,
    },
    "Sauce béchamel aux champignons": {
        "Sauce béchamel": 800, "Champignons (g)": 180, "Beurre 84% MG (g)": 20,
    },
}


def _canon(s):
    s = str(s or "").replace("œ", "oe").replace("Œ", "OE")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def _sans_unite(nom):
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(nom)).strip()


def _base_sans_parentheses(nom):
    return re.sub(r"\([^)]*\)", " ", str(nom))


def _ratios(recette_g):
    total = sum(float(v) for v in recette_g.values())
    if total <= 0:
        return None
    return {ing: round(float(v) / total, 5) for ing, v in recette_g.items()}


def _variantes_besoins(psf_nom):
    """Toutes les écritures d'un PSF présentes dans les besoins (pour les alias)."""
    base = _canon(_base_sans_parentheses(psf_nom))
    variantes = set()
    dossiers = sorted(glob.glob(os.path.join(_RACINE, "exports", "2026-*")))
    if not dossiers:
        return variantes
    for cat in ("cuisine", "patisserie", "boulangerie"):
        f = os.path.join(dossiers[-1], f"besoins_ingredients_{cat}.csv")
        if not os.path.exists(f):
            continue
        df = pd.read_csv(f, sep=";", engine="python", on_bad_lines="skip")
        for ing in df["Ingredient"].dropna().unique():
            if _canon(_base_sans_parentheses(ing)) == base:
                variantes.add(_canon(_sans_unite(ing)))
    return variantes


def construire():
    with open(RECETTES_JSON, encoding="utf-8") as f:
        rec_exactes = json.load(f)

    recettes_psf, alias, anomalies = {}, {}, []

    def _ajouter(nom, recette_g):
        r = _ratios(recette_g)
        if r is None:
            anomalies.append(f"« {nom} » : total nul -> ignoré.")
            return
        recettes_psf[nom] = r
        alias[_canon(nom)] = nom
        for v in _variantes_besoins(nom):
            alias[v] = nom

    for canon_nom in SOUS_RECETTES:
        if canon_nom not in rec_exactes:
            anomalies.append(f"Sous-recette absente de recettes_exactes : « {canon_nom} ».")
            continue
        # nom PSF lisible = sans le « (base) » éventuel
        nom_psf = re.sub(r"\s*\(base\)\s*$", "", canon_nom).strip()
        _ajouter(nom_psf, rec_exactes[canon_nom])

    for nom, recette_g in STANDARD.items():
        _ajouter(nom, recette_g)

    return recettes_psf, alias, anomalies


def appliquer(recettes_psf, alias):
    with open(PSF_JSON, encoding="utf-8") as f:
        data = json.load(f)
    shutil.copyfile(PSF_JSON, f"{PSF_JSON}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    data.setdefault("recettes", {}).update(recettes_psf)
    data.setdefault("alias", {}).update(alias)
    with open(PSF_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    recettes_psf, alias, anomalies = construire()

    print("=" * 62)
    print(f"  ENREGISTREMENT PSF CUISINE{'   [DRY-RUN]' if args.dry_run else ''}")
    print("=" * 62)
    for nom, r in recettes_psf.items():
        apercu = ", ".join(f"{k.split(' (')[0]} {v:.0%}" for k, v in list(r.items())[:4])
        print(f"  • {nom:32} -> {apercu}…")
    print(f"\n{len(recettes_psf)} PSF, {len(alias)} alias.")
    if anomalies:
        print("Anomalies :")
        for a in anomalies:
            print(f"  ! {a}")
    if args.dry_run:
        print("\n[DRY-RUN] Rien écrit.")
        return
    appliquer(recettes_psf, alias)
    print("\n[OK] recettes_psf.json mis à jour. Relancer python main.py.")


if __name__ == "__main__":
    main()

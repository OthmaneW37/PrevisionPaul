# -*- coding: utf-8 -*-
"""Complète data/recettes_exactes.json avec des recettes recherchées (web + standard
pâtissier/boulanger français) pour les produits FABRIQUÉS à fort volume encore sans
recette. Les produits revendus tels quels (Coca, eaux, jus bouteille…) restent SANS
recette (gérés par bom.detecter_bom_produit). Provisoire — à valider par le chef.

Quantités en GRAMMES par unité finie. Noms d'ingrédients harmonisés ensuite par
bom.normaliser_bom (alias) et éclatés par bom.exploser_psf (PSF → matières de base).
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------
import json
import os
import re
import shutil

import pandas as pd

REC = os.path.join("data", "recettes_exactes.json")
VENTES = os.path.join("donnees_ventes", "ventes_journalieres.csv")

# ── Recettes recherchées (base ingrédients ; les PSF sont éclatés ensuite) ──────
NOUVELLES = {
    # ── VIENNOISERIE ──────────────────────────────────────────────────────────
    "MINI GOURMANDISE": {"Pâte à croissant": 22, "Chocolat noir bâton (g)": 3, "Sucre glace": 1},
    "GOURMANDISE": {"Pâte à croissant": 42, "Chocolat noir bâton (g)": 6, "Sucre glace": 2},
    "CHOUQUETTE UNITE": {"Pâte à choux": 12, "Sucre": 3},
    "SAC CHOUQUETTES 10PCS": {"Pâte à choux": 120, "Sucre": 30},
    "4 Chouquettes": {"Pâte à choux": 48, "Sucre": 12},
    "VIENNOISE NATURE 100GR": {"Farine T45": 60, "Lait": 25, "Beurre": 8, "Sucre": 5,
                               "Œufs": 5, "Levure fraiche": 1.5, "Sel": 1},
    "PISTOLET NATURE 50GR": {"Farine T65": 37, "Eau": 24, "Levure fraiche": 0.6, "Sel": 0.9},
    "BENOITON FROMAGE": {"Farine T65": 35, "Eau": 20, "Fromage": 12, "Beurre": 4,
                         "Levure fraiche": 0.8, "Sel": 0.6},
    "BENOITON OLIVES SESAME": {"Farine T65": 38, "Eau": 22, "Olives denoyautees": 8,
                               "Graines de sesame": 3, "Levure fraiche": 0.8, "Sel": 0.6},
    "Cramiqu Choc Suc ind": {"Brioche": 70, "Pepites de chocolat": 12, "Sucre": 4},

    # ── PÂTISSERIE ────────────────────────────────────────────────────────────
    # Mille-feuilles (pâte feuilletée + crème pâtissière)
    "MF VANILLE": {"Pâte feuilletée": 45, "Crème pâtissière": 55, "Sucre glace": 8},
    "MF fraise": {"Pâte feuilletée": 45, "Crème pâtissière": 45, "Framboises": 20, "Sucre glace": 6},
    "MF FRAMBOISE": {"Pâte feuilletée": 45, "Crème pâtissière": 45, "Framboises": 20, "Sucre glace": 6},
    "MF PRALINE": {"Pâte feuilletée": 45, "Crème pâtissière": 45, "Noix": 10, "Sucre glace": 8},
    # Opéra (recherché) : biscuit joconde + crème beurre café + ganache
    "OPERA": {"Poudre d'amande": 18, "Farine T55": 8, "Œufs": 30, "Sucre": 22, "Beurre": 25,
              "Chocolat noir bâton (g)": 20, "Crème liquide": 12, "Café": 3, "Sucre glace": 3},
    # Royal / Trianon (recherché) : dacquoise amande + praliné feuilletine + mousse chocolat
    "ROYAL IND 25 ANS": {"Poudre d'amande": 20, "Œufs": 25, "Sucre": 22,
                         "Chocolat noir bâton (g)": 30, "Crème liquide": 25, "Noix": 15, "Cacao": 3},
    "CASSE NOISETTE IND": {"Poudre d'amande": 20, "Œufs": 24, "Sucre": 20,
                           "Chocolat noir bâton (g)": 28, "Crème liquide": 24, "Noisette": 18, "Cacao": 3},
    "JIVARA": {"Chocolat noir bâton (g)": 30, "Crème liquide": 30, "Œufs": 18, "Sucre": 12,
               "Poudre d'amande": 12, "Farine T55": 5},
    "FEUILLES D AUTOMNE": {"Chocolat noir bâton (g)": 30, "Crème liquide": 30, "Œufs": 20,
                           "Sucre": 18, "Poudre d'amande": 10},
    "TIGRE": {"Poudre d'amande": 12, "Sucre glace": 14, "Blanc d'oeuf": 12,
              "Beurre noisette": 11, "Farine T55": 5, "Chocolat noir bâton (g)": 8},
    "Duchesse": {"Pâte à choux": 35, "Crème pâtissière": 45, "Sucre glace": 10},
    "ILE AUX FRUITS IND": {"Pâte sablée": 35, "Crème pâtissière": 40, "Fruits frais": 35, "Sucre glace": 3},
    "CHARLOTTE FRAMBOISE": {"Œufs": 30, "Sucre": 25, "Farine T55": 20, "Framboises": 30, "Crème liquide": 20},
    "AMANDINE PURE AMANDE": {"Pâte sablée": 40, "Crème d'amande": 45, "Amandes effilees": 5, "Sucre glace": 2},
    "AMANDINE POMMES-CANNELLE PART": {"Pâte sablée": 40, "Crème d'amande": 40, "Pommes fraiches": 30,
                                      "Amandes effilees": 4, "Sucre glace": 2},
    "B NEIGE IND 25 ANS": {"Poudre d'amande": 22, "Blanc d'oeuf": 18, "Sucre": 25, "Beurre": 10, "Noix de coco": 8},
    "MUFFIN FRUITS ROUGES": {"Farine T55": 35, "Sucre": 28, "Œufs": 18, "Beurre": 20,
                             "Lait": 15, "Framboises": 15, "Levure chimique": 1.5},
    "ROYAL FRAMBOISE IND": {"Poudre d'amande": 18, "Œufs": 24, "Sucre": 20, "Chocolat noir bâton (g)": 22,
                            "Crème liquide": 24, "Framboises": 20, "Cacao": 2},

    # ── CUISINE ───────────────────────────────────────────────────────────────
    "CREPE CHOC NOISETTE": {"Farine T55": 40, "Lait": 90, "Œufs": 25, "Sucre": 10, "Beurre": 5,
                            "Pate a tartiner choco-noisette": 25},
    "CHIA PUDDING PASS FRAMB": {"Lait": 110, "Graines de chia": 20, "Sucre": 10, "Framboises": 30},
    "Supp Huile d'olive": {"Huile d'olive": 10},
    "Sup Fromage BL Frais": {"Fromage": 30},
}


def main():
    with open(REC, encoding="utf-8") as f:
        rec = json.load(f)
    avant = len(rec)

    ajout = 0
    for nom, ing in NOUVELLES.items():
        if nom not in rec:
            rec[nom] = ing
            ajout += 1

    # Variantes suffixées (VSP/VAE/PES/SP…) : copier la recette du nom de base.
    variantes = 0
    if os.path.exists(VENTES):
        produits = set(pd.read_csv(VENTES, sep=";", usecols=["Produit"])["Produit"].astype(str).unique())
        suffixe = re.compile(r"\s*\((VSP|VAE|PES|SP|EAT ?IN|TAKE ?AWAY|A EMPORTER|SUR PLACE)\)\s*$", re.I)
        for p in produits:
            if p in rec:
                continue
            base = suffixe.sub("", p).strip()
            if base != p and base in rec:
                rec[p] = dict(rec[base])
                variantes += 1

    shutil.copy(REC, REC + ".bak")
    with open(REC, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    print(f"Recettes : {avant} → {len(rec)}  (+{ajout} recherchées, +{variantes} variantes suffixées)")


if __name__ == "__main__":
    main()

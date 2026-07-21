# -*- coding: utf-8 -*-
"""
Estime les recettes des produits « variantes » à partir d'une base déjà connue.

Beaucoup de crêpes/pancakes sont la MÊME base (pâte à crêpe sucrée ou salée) avec
une garniture qui change : « Crêpe Banane Choco » = pâte à crêpe sucrée + pâte à
tartiner + banane, comme « Crêpe Choco Noix » mais avec une banane. Plutôt que de
laisser ces produits sans recette (bon de commande matières incomplet), on les
DÉRIVE des recettes chef existantes.

IMPORTANT — ce sont des ESTIMATIONS, pas des recettes chef :
  - provenance « extrapolation » avec score 0.5 (distinct des fiches chef à 1.0) ;
  - on N'ÉCRASE JAMAIS une recette déjà présente (chef ou déjà estimée) ;
  - la base et la garniture reprennent les quantités des recettes sœurs réelles
    (Crêpe Choco Noix, Crêpe Miel Noix, Pancake Chocolat Banane, Demoiselle Tatin,
    Crêpe Crevettes Poireaux…) — cf. rationale de chaque ligne.

Les produits vraiment ambigus (Crêpe Maison, Méditerranéenne, Façon Tiramisu…)
ne sont PAS estimés : ils sont listés en fin de rapport pour saisie chef.

Lancement :
  python outils/estimer_recettes_derivees.py --dry-run   # aperçu, n'écrit rien
  python outils/estimer_recettes_derivees.py             # applique (avec sauvegarde)
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------

import argparse
import json
import os
import shutil
import unicodedata
from datetime import datetime

import pandas as pd

RECETTES_JSON = os.path.join(_RACINE, "data", "recettes_exactes.json")
PROVENANCE_JSON = os.path.join(_RACINE, "data", "recettes_exactes_provenance.json")
VENTES = os.path.join(_RACINE, "donnees_ventes", "ventes_journalieres.csv")

# Bases (mêmes libellés que les fiches chef, pour que la sous-recette éclate bien).
BASE_SUCREE = "Appareil crêpe sucrée (g)"     # ~110 g / crêpe (choco noix 113, miel noix 107)
BASE_SALEE = "Appareil crêpe salée (g)"       # ~140 g / crêpe (complète, crevettes poireaux)
BASE_PANCAKE = "Pâte à crêpes"                # convention pancakes existants (choc banane 120)

# Chaque dérivation : produit vendu (cherché tel quel dans les ventes) ->
# (recette estimée, justification). Base + garniture calées sur les recettes sœurs.
DERIVATIONS = [
    # ---- Crêpes sucrées (base pâte à crêpe sucrée + garniture) ----
    ("CREPE BANANE CHOCO",
     {BASE_SUCREE: 110, "Pâte à tartiner choco-noisette (g)": 30, "Banane (g)": 60},
     "= Crêpe Choco Noix (base) + garniture Pancake Choco Banane (pâte à tartiner + banane)"),
    ("CREPE SUCRE",
     {BASE_SUCREE: 100, "Sucre (g)": 15},
     "= pâte à crêpe sucrée nature saupoudrée de sucre"),
    ("CREPE NATURE",
     {BASE_SUCREE: 100},
     "= pâte à crêpe sucrée seule (cf. Pack 5 Crêpe nature)"),
    ("CREPE CONFITURE",
     {BASE_SUCREE: 100, "Confiture (g)": 45},
     "= base sucrée + confiture (dose type garniture)"),
    ("CREPE CARAMEL BEURRE SALE",
     {BASE_SUCREE: 110, "Caramel beurre salé (g)": 45},
     "= base sucrée + caramel beurre salé"),
    ("CREPE CHOC GLAC VANI",
     {BASE_SUCREE: 110, "Pâte à tartiner choco-noisette (g)": 35, "Glace vanille (g)": 50},
     "= Crêpe Choco (base + pâte à tartiner) + boule de glace vanille"),
    ("CREPE FRUITS ROUGES CHANTILLY",
     {BASE_SUCREE: 110, "Fruits rouges (g)": 60, "Crème chantilly sucrée (g)": 30},
     "= base sucrée + fruits rouges + chantilly (cf. Demoiselle Tatin chantilly 27 g)"),
    # ---- Crêpes salées (base pâte à crêpe salée + garniture) ----
    ("CREPE PASTRAMI POIREAUX",
     {BASE_SALEE: 140, "Poireau (g)": 40, "Sauce béchamel (g)": 40,
      "Pastrami (g)": 60, "Maasdam (g)": 20, "Beurre (g)": 10,
      "Sel (g)": 1, "Poivre blanc (g)": 1},
     "= Crêpe Crevettes Poireaux, crevette remplacée par pastrami"),
    ("CREPE LEGUMES GRILLES FETA",
     {BASE_SALEE: 140, "Légumes grillés (g)": 90, "Feta (g)": 30, "Sauce béchamel (g)": 30},
     "= base salée + légumes grillés + feta (garniture type crêpe légumes)"),
    ("CREPE 4 FROMAGE",
     {BASE_SALEE: 140, "Sauce béchamel (g)": 40, "Mélange 4 fromages (g)": 90},
     "= base salée + béchamel + mélange fromages"),
    ("CREPE FAJITAS",
     {BASE_SALEE: 140, "Escalope poulet (g)": 60, "Poivrons grillés (g)": 40,
      "Sauce fajitas (g)": 25, "Fromage râpé (g)": 25},
     "= base salée + poulet + poivrons + sauce (garniture fajitas, à confirmer)"),
    # ---- Packs (multiples d'un produit existant) ----
    ("Pack 10 crepe nature",
     {BASE_PANCAKE: 800},
     "= 2 x Pack 5 Crêpe nature (pâte à crêpes 400)"),
    # ---- Pancakes (base pâte à crêpes + garniture) ----
    ("PANCAKE FRAMB GRMND",
     {BASE_PANCAKE: 120, "Framboise (g)": 50, "Crème chantilly sucrée (g)": 30},
     "= Pancake Chocolat Banane (base) + framboise + chantilly"),
    ("PANCAKE DEMOISELLE TATIN",
     {BASE_PANCAKE: 120, "Pomme (g)": 150, "Caramel industriel (g)": 25, "Beurre (g)": 18,
      "Cannelle moulue (g)": 1, "Crème chantilly sucrée (g)": 27, "Sucre (g)": 10,
      "Nougatine (g)": 10},
     "= Crêpe Demoiselle Tatin, base crêpe remplacée par pâte à pancake"),
]

# Produits variantes trop ambigus pour être estimés (garniture inconnue) : à saisir
# par le chef. Listés dans le rapport.
A_SAISIR_CHEF = ["Crepe Maison", "Crepe Mediteranneenne", "CREPE FACON TIRAMISU",
                 "Pt Dej Crepe"]


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(s.lower().split())


def _index_ventes():
    """Nom normalisé -> nom EXACT du produit dans les ventes (clé du JSON à utiliser)."""
    df = pd.read_csv(VENTES, sep=";", encoding="utf-8")
    idx = {}
    for nom in df["Produit"].dropna().astype(str).unique():
        idx.setdefault(_norm(nom), nom)
    return idx


def construire():
    """-> (recettes {nom_exact: recette}, notes, anomalies)."""
    with open(RECETTES_JSON, encoding="utf-8") as f:
        existantes = json.load(f)
    existantes_norm = {_norm(k) for k in existantes}
    idx = _index_ventes()

    recettes, notes, anomalies = {}, [], []
    for cible, recette, rationale in DERIVATIONS:
        cn = _norm(cible)
        nom_exact = idx.get(cn)
        if nom_exact is None:
            anomalies.append(f"« {cible} » introuvable dans les ventes -> ignoré.")
            continue
        if cn in existantes_norm:
            notes.append(f"« {nom_exact} » a déjà une recette -> NON écrasée.")
            continue
        recettes[nom_exact] = recette
        notes.append(f"« {nom_exact} » estimée : {rationale}.")
    return recettes, notes, anomalies


def appliquer(recettes):
    with open(RECETTES_JSON, encoding="utf-8") as f:
        existantes = json.load(f)
    sauvegarde = f"{RECETTES_JSON}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copyfile(RECETTES_JSON, sauvegarde)
    existantes.update(recettes)
    with open(RECETTES_JSON, "w", encoding="utf-8") as f:
        json.dump(existantes, f, ensure_ascii=False, indent=2)

    try:
        with open(PROVENANCE_JSON, encoding="utf-8") as f:
            prov = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        prov = {}
    for prod in recettes:
        prov[prod] = {"source": "extrapolation (variante d'une recette connue)",
                      "score": 0.5,
                      "date": f"{datetime.now():%Y-%m-%d}"}
    with open(PROVENANCE_JSON, "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
    return sauvegarde


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    recettes, notes, anomalies = construire()

    print("=" * 66)
    print(f"  ESTIMATION RECETTES DERIVEES{'   [DRY-RUN]' if args.dry_run else ''}")
    print("=" * 66)
    for prod, ings in recettes.items():
        print(f"\n> {prod}  ({len(ings)} ingrédient(s))")
        for ing, q in ings.items():
            print(f"    - {ing:<42} {q}")
    if notes:
        print("\nNotes :")
        for n in notes:
            print(f"  - {n}")
    if anomalies:
        print("\nAnomalies :")
        for a in anomalies:
            print(f"  ! {a}")
    print("\nA saisir par le chef (variantes trop ambigües pour estimer) :")
    for p in A_SAISIR_CHEF:
        print(f"  - {p}")

    if args.dry_run:
        print("\n[DRY-RUN] Rien écrit. Relance sans --dry-run pour appliquer.")
        return
    if not recettes:
        print("\nRien de nouveau à écrire.")
        return
    sauvegarde = appliquer(recettes)
    print(f"\n[OK] {len(recettes)} recette(s) estimée(s) écrite(s) (provenance « extrapolation »).")
    print(f"     Sauvegarde : {os.path.basename(sauvegarde)}")
    print("     -> Relancer python main.py pour répercuter sur le bon de commande.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Estimations « best-effort » pour les plats CUISINE encore sans aucune recette.

Objectif : que le bon de commande matières ne soit plus VIDE pour ces produits.
Ce sont des recettes APPROXIMATIVES (standard cuisine / web), pas des fiches chef :
  - provenance « estimation approximative (a valider chef) », score 0.4 ;
  - on N'ÉCRASE JAMAIS une recette existante (chef ou autre estimation) ;
  - quantités en grammes, calées sur le poids net du plat et des proportions
    classiques ; à affiner quand le chef fournira la vraie fiche.

Volontairement EXCLUS (trop variables ou hors périmètre) : assortiments/plateaux
(PLANCHE, PLAT CANAPÉS, FOUR SALÉ, PLT FOURS), desserts/glaces/brioches sucrées,
tajines & plats RMD, suppléments, condiments. Ils restent « à saisir ».

Lancement :
  python outils/estimer_recettes_cuisine_reste.py --dry-run
  python outils/estimer_recettes_cuisine_reste.py
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

G = "(g)"  # raccourci : tous les ingrédients sont en grammes

# Produit (cherché au nom exact dans les ventes) -> recette estimée {ingrédient (g): grammes}.
# Bases réutilisées quand elles existent déjà (Viande hachee maison, béchamel, sauces…).
ESTIMATIONS = {
    # --- Burgers (pain + steak/nuggets + garniture) ---
    "Mini Burger Maison": {"Pain burger sésame "+G: 50, "Steak haché "+G: 60, "Cheddar "+G: 15,
                           "Sauce burger "+G: 15, "Oignon "+G: 10, "Cornichon "+G: 5, "Salade "+G: 10},
    "Mini burger nuggets": {"Pain burger sésame "+G: 50, "Nuggets poulet "+G: 60, "Sauce burger "+G: 15,
                            "Tomate "+G: 15, "Salade "+G: 10},
    "Mini burger vegetarien": {"Pain burger sésame "+G: 50, "Galette légumes "+G: 60, "Sauce burger "+G: 15,
                               "Tomate "+G: 15, "Salade "+G: 10},
    "HAMBOURGEOIS SAVOUREUX": {"Pain burger 110g "+G: 110, "Steak haché "+G: 150, "Cheddar "+G: 30,
                               "Sauce burger "+G: 30, "Oignon émincé cuit "+G: 40, "Tomate "+G: 30,
                               "Salade "+G: 20, "Frites cuites "+G: 130},
    "HAMBOURGEOIS LE PROVENCAL": {"Pain burger 110g "+G: 110, "Steak haché "+G: 150, "Fromage de chèvre "+G: 30,
                                  "Légumes grillés "+G: 50, "Pesto "+G: 20, "Salade "+G: 20, "Frites cuites "+G: 130},
    "Hambourgois legumes grilles": {"Pain burger 110g "+G: 110, "Galette légumes "+G: 100, "Fromage "+G: 30,
                                    "Sauce burger "+G: 20, "Salade "+G: 20, "Frites cuites "+G: 130},
    "Steack Hache Maison": {"Steak haché "+G: 150, "Sauce "+G: 30, "Frites cuites "+G: 130, "Salade "+G: 40},

    # --- Croque / panini / sandwichs chauds ---
    "Croq Mr jamb FROM VSP": {"Pain de mie "+G: 80, "Sauce béchamel "+G: 40, "Jambon de dinde "+G: 40,
                              "Emmental "+G: 40},
    "PANINI MEDICIS": {"Pain panini "+G: 100, "Escalope poulet "+G: 50, "Fromage "+G: 30, "Tomate "+G: 20,
                       "Pesto "+G: 15},
    "SW TYROLIEN": {"Pain "+G: 90, "Jambon de dinde "+G: 40, "Fromage "+G: 30, "Oignons frits "+G: 15,
                    "Sauce "+G: 20, "Salade "+G: 15},
    "SW HENRY 4": {"Pain "+G: 90, "Escalope poulet "+G: 50, "Fromage "+G: 25, "Sauce "+G: 20, "Salade "+G: 15},

    # --- Tartines ---
    "Tartine Hoummous betterave avocat": {"Pain "+G: 70, "Houmous "+G: 50, "Betterave "+G: 30,
                                          "Avocat "+G: 40, "Roquette "+G: 10},
    "Tartine Brie Noix": {"Pain "+G: 70, "Brie "+G: 50, "Cerneaux de noix "+G: 15, "Miel "+G: 10, "Roquette "+G: 10},
    "DOUBL TARTIN FORESTR": {"Pain "+G: 140, "Champignons "+G: 60, "Crème "+G: 30, "Fromage "+G: 30, "Persil "+G: 3},

    # --- Pitas (pain pita + garniture) ---
    "Pita Viand Hache": {"Pain pita "+G: 90, "Viande hachee maison "+G: 100, "Sauce blanche "+G: 20,
                         "Tomate "+G: 20, "Oignon "+G: 15, "Salade "+G: 20},
    "Pita maison": {"Pain pita "+G: 90, "Escalope poulet "+G: 80, "Sauce blanche "+G: 20, "Crudités "+G: 40},
    "Pita marine": {"Pain pita "+G: 90, "Poulet mariné "+G: 80, "Sauce blanche "+G: 20, "Crudités "+G: 40},
    "Pita crevettes": {"Pain pita "+G: 90, "Crevettes "+G: 70, "Sauce blanche "+G: 20, "Crudités "+G: 40},

    # --- Pizzas (pâte + sauce tomate + garniture) ---
    "PIZZA VH": {"Pâte à pizza "+G: 200, "Sauce tomate "+G: 60, "Viande hachee maison "+G: 80,
                 "Fromage râpé "+G: 80, "Oignon "+G: 20},
    "PIZZA JAMB FROM": {"Pâte à pizza "+G: 200, "Sauce tomate "+G: 60, "Jambon de dinde "+G: 60, "Fromage râpé "+G: 90},
    "Mini pizza maison": {"Pâte à pizza "+G: 100, "Sauce tomate "+G: 30, "Fromage râpé "+G: 40, "Garniture "+G: 30},

    # --- Salades composées ---
    "SLD HOUMOUS-FALAFEL": {"Salade mêlée "+G: 80, "Houmous "+G: 60, "Falafel "+G: 80, "Tomate "+G: 30,
                            "Concombre "+G: 30, "Sauce "+G: 20},
    "SLD Halloumi mangue": {"Salade mêlée "+G: 80, "Halloumi "+G: 60, "Mangue "+G: 50, "Vinaigrette "+G: 20,
                            "Cerneaux de noix "+G: 10},
    "SLD POKE BOWL GAMBAS WAKAME": {"Riz "+G: 150, "Gambas "+G: 60, "Wakame "+G: 20, "Edamame "+G: 30,
                                    "Avocat "+G: 30, "Sauce soja "+G: 20},
    "SLD BURRATINA": {"Salade mêlée "+G: 60, "Burrata "+G: 100, "Tomate "+G: 60, "Pesto "+G: 20, "Huile d'olive "+G: 10},
    "SLD CHEVRE CHAUD NR": {"Salade mêlée "+G: 80, "Fromage de chèvre "+G: 60, "Toast "+G: 30, "Miel "+G: 10,
                            "Cerneaux de noix "+G: 10, "Vinaigrette "+G: 20},

    # --- Soupes / veloutés ---
    "Harira 1 portion": {"Tomate "+G: 100, "Lentilles "+G: 30, "Pois chiches "+G: 30, "Viande "+G: 30,
                         "Céleri "+G: 15, "Coriandre "+G: 5, "Farine "+G: 10, "Eau "+G: 200},
    "Soupe asiatique 1 portion": {"Bouillon "+G: 250, "Nouilles "+G: 50, "Légumes "+G: 40, "Poulet "+G: 30,
                                  "Sauce soja "+G: 10},
    "SOUPE A L'OIGNON": {"Oignon "+G: 150, "Bouillon "+G: 250, "Fromage râpé "+G: 40, "Pain "+G: 30, "Beurre "+G: 15},
    "Veloute potiron graines de courge": {"Potiron "+G: 200, "Crème "+G: 30, "Bouillon "+G: 150,
                                          "Graines de courge "+G: 10},
    "VELOUTE CHAMPIGNONS": {"Champignons "+G: 150, "Crème "+G: 30, "Bouillon "+G: 150, "Oignon "+G: 20},
    "SOUPE CELERI-RAVE TRUFFE": {"Céleri rave "+G: 180, "Crème "+G: 30, "Bouillon "+G: 150, "Huile de truffe "+G: 5},
    "Soupe de Poissons": {"Poisson "+G: 120, "Tomate "+G: 60, "Fumet "+G: 200, "Ail "+G: 10, "Rouille "+G: 20},

    # --- Plats chauds ---
    "FILET BOEUF SCE BEARNAISE": {"Filet de boeuf "+G: 150, "Sauce béarnaise "+G: 40, "Frites cuites "+G: 130,
                                  "Légumes "+G: 60},
    "FILET LOUP BAR SCE CRUSTACES": {"Filet de loup "+G: 150, "Sauce crustacés "+G: 50, "Riz "+G: 60, "Légumes "+G: 60},
    "Entrecote sauce pari": {"Entrecôte "+G: 180, "Sauce "+G: 40, "Frites cuites "+G: 130, "Salade "+G: 40},
    "Boeuf strogonoff": {"Boeuf "+G: 150, "Champignons "+G: 50, "Crème "+G: 40, "Oignon "+G: 30, "Riz "+G: 120},
    "LASAGNE BOLOGNAISE": {"Pâte à lasagne "+G: 80, "Viande hachee maison "+G: 100, "Sauce tomate "+G: 80,
                           "Sauce béchamel "+G: 60, "Fromage râpé "+G: 30},
    "OEUFS A LA TOMATE": {"Oeufs "+G: 120, "Sauce tomate "+G: 100, "Oignon "+G: 20, "Poivron "+G: 20},
    "PAD THAI TERRE-MER": {"Nouilles de riz "+G: 120, "Crevettes "+G: 40, "Poulet "+G: 40, "Oeuf "+G: 50,
                           "Cacahuètes "+G: 15, "Sauce pad thaï "+G: 30},
    "OMELETTE FROM.CHAMP.": {"Oeufs "+G: 160, "Fromage "+G: 30, "Champignons "+G: 40, "Beurre "+G: 5},

    # --- Rolls / croustillants / bricks (feuille + farce) ---
    "Roll gambas": {"Galette de blé "+G: 60, "Gambas "+G: 60, "Crudités "+G: 40, "Sauce "+G: 20},
    "Croustillant Crevette-Thai": {"Pâte à brick "+G: 40, "Crevettes "+G: 60, "Vermicelle "+G: 20, "Sauce thaï "+G: 20},
    "Croustillant viande hachee": {"Pâte à brick "+G: 40, "Viande hachee maison "+G: 80, "Oignon "+G: 15},
    "Brick Maison": {"Feuille de brick "+G: 40, "Viande "+G: 60, "Oeuf "+G: 30},
    "CROUSTILLANTS FACON THAI": {"Pâte à brick "+G: 40, "Garniture thaï "+G: 70, "Sauce thaï "+G: 20},
    "Roule 3  fromages": {"Pâte feuilletée "+G: 80, "Mélange 3 fromages "+G: 90, "Sauce béchamel "+G: 30},
    "Feuillete maison": {"Pâte feuilletée "+G: 80, "Garniture "+G: 70},

    # --- Accompagnements ---
    "ACCOMPAGNEMENT RIZ": {"Riz "+G: 120, "Beurre "+G: 5, "Sel "+G: 1},
    "Riz basmati": {"Riz basmati "+G: 120, "Beurre "+G: 5, "Sel "+G: 1},
    "ACCOMPAGNEMENT FRITES STEAKHOUSE": {"Pommes de terre "+G: 150, "Huile "+G: 10, "Sel "+G: 1},
    "LEGUMES GRILLES": {"Légumes variés "+G: 200, "Huile d'olive "+G: 15, "Sel "+G: 2},
    "LEGUMES POELES": {"Légumes variés "+G: 200, "Huile d'olive "+G: 15, "Sel "+G: 2},
    "HARICOTS VERTS": {"Haricots verts "+G: 150, "Beurre "+G: 10, "Sel "+G: 1},
    "ACCOMPAGNEMENT LEGUMES AMANDES": {"Légumes variés "+G: 150, "Amande effilée "+G: 15, "Beurre "+G: 10},
    "TOMATES PROVENCALES": {"Tomate "+G: 150, "Ail "+G: 5, "Persil "+G: 5, "Chapelure "+G: 15, "Huile d'olive "+G: 10},
    "Tian legumes": {"Courgette "+G: 70, "Aubergine "+G: 60, "Tomate "+G: 60, "Huile d'olive "+G: 15, "Herbes "+G: 3},
    "ACCOMPAGNEMENT NOUILLES": {"Nouilles "+G: 120, "Sauce soja "+G: 10, "Légumes "+G: 30},

    # --- Focaccias ---
    "Focaccia Mediterraneenne": {"Pâte à focaccia "+G: 120, "Huile d'olive "+G: 15, "Tomate séchée "+G: 25,
                                 "Olives "+G: 15, "Herbes "+G: 3},
    "Focaccia provencale": {"Pâte à focaccia "+G: 120, "Huile d'olive "+G: 15, "Légumes du soleil "+G: 40, "Herbes "+G: 3},

    # --- Jus / boisson (recette approximative type web) ---
    "JUS AMANDE DATTE": {"Lait d'amande "+G: 200, "Dattes "+G: 40, "Sucre "+G: 10},
}


def _norm(s):
    s = str(s or "").lower().replace("œ", "oe").replace("æ", "ae")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum())


def _index_ventes():
    df = pd.read_csv(VENTES, sep=";", encoding="utf-8")
    idx = {}
    for nom in df["Produit"].dropna().astype(str).unique():
        idx.setdefault(_norm(nom), nom)
    return idx


def construire():
    idx = _index_ventes()
    with open(RECETTES_JSON, encoding="utf-8") as f:
        deja = {_norm(k) for k in json.load(f)}
    sorties, notes, anomalies = {}, [], []
    for cible, recette in ESTIMATIONS.items():
        cn = _norm(cible)
        nom_exact = idx.get(cn)
        if nom_exact is None:
            anomalies.append(f"« {cible} » introuvable dans les ventes -> ignoré.")
            continue
        if cn in deja:
            notes.append(f"« {nom_exact} » a déjà une recette -> NON écrasée.")
            continue
        sorties[nom_exact] = dict(recette)
    return sorties, notes, anomalies


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
        prov[prod] = {"source": "estimation approximative (a valider chef)",
                      "score": 0.4, "date": f"{datetime.now():%Y-%m-%d}"}
    with open(PROVENANCE_JSON, "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
    return sauvegarde


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    recettes, notes, anomalies = construire()

    print("=" * 60)
    print(f"  ESTIMATIONS CUISINE (reste){'   [DRY-RUN]' if args.dry_run else ''}")
    print("=" * 60)
    for prod, ings in recettes.items():
        print(f"\n> {prod}  ({len(ings)} ingr., {sum(ings.values()):.0f} g)")
        for ing, q in ings.items():
            print(f"    - {ing:<34} {q}")
    if notes:
        print("\nDéjà couvertes (non écrasées) :")
        for n in notes:
            print(f"  - {n}")
    if anomalies:
        print("\nAnomalies :")
        for a in anomalies:
            print(f"  ! {a}")
    print(f"\n{len(recettes)} estimation(s) prête(s).")

    if args.dry_run:
        print("[DRY-RUN] Rien écrit.")
        return
    if not recettes:
        print("Rien de nouveau.")
        return
    sauvegarde = appliquer(recettes)
    print(f"[OK] {len(recettes)} écrite(s) (provenance « estimation approximative », score 0.4).")
    print(f"     Sauvegarde : {os.path.basename(sauvegarde)}  ->  relancer python main.py")


if __name__ == "__main__":
    main()

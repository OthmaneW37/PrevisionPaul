# -*- coding: utf-8 -*-
"""
Nomenclatures des ingrédients (Bill of Materials) pour les produits PAUL.

Deux niveaux de détection :
  1. Correspondance exacte via le dictionnaire `config.BOM` (chargé depuis
     data/recettes_exactes.json — éditable sans toucher au code).
  2. Détection automatique par motif du nom produit : `detecter_bom_produit()`
     gère les 1 000+ autres références en déduisant la catégorie et le grammage.

Fournit aussi la normalisation des recettes (déduplication, unités) et les
ajustements saisonniers Ramadan / Aïd.
"""

import re
import unicodedata

import pandas as pd

from . import config


def _canon_matiere(nom):
    """Clé insensible casse/accents/espaces pour rapprocher les variantes."""
    # « Œ/œ » n'a pas de décomposition NFKD : translittérer avant le repli ASCII,
    # sinon « Œufs » devient « ufs » et ne rejoint jamais la variante « Oeufs ».
    s = str(nom).replace("Œ", "OE").replace("œ", "oe").replace("Æ", "AE").replace("æ", "ae")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


# Table inverse variante normalisée → nom canonique (construite une seule fois).
_ALIAS_INVERSE = {}
for _canonique, _variantes in config.ALIAS_MATIERES.items():
    for _v in _variantes:
        _ALIAS_INVERSE[_canon_matiere(_v)] = _canonique
    _ALIAS_INVERSE.setdefault(_canon_matiere(_canonique), _canonique)


# ==============================================================================
# ÉCLATEMENT DES PRODUITS SEMI-FINIS (BOM MULTI-NIVEAUX)
# ==============================================================================
_PSF_RECETTES = config.RECETTES_PSF.get("recettes", {})
_PSF_ALIAS    = {_canon_matiere(k): v for k, v in config.RECETTES_PSF.get("alias", {}).items()}


def _sans_unite(nom):
    """Retire l'unité/codes entre parenthèses en fin de nom (ex: 'Croissant GM (g)')."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(nom)).strip()


def _psf_canonique(nom):
    """Retourne le nom canonique de PSF correspondant à un ingrédient, ou None.

    On tente deux formes : le nom sans son unité finale, puis le nom débarrassé
    de TOUTE parenthèse (codes/marques internes comme « (TRA/BAS/APP/002) »), afin
    qu'une préparation reste reconnue quelle que soit sa référence interne.
    """
    recettes_canon = {_canon_matiere(k): k for k in _PSF_RECETTES}
    for candidat in (_sans_unite(nom), re.sub(r"\([^)]*\)", " ", str(nom))):
        base = _canon_matiere(candidat)
        if base in _PSF_ALIAS:
            return _PSF_ALIAS[base]
        if base in recettes_canon:
            return recettes_canon[base]
    return None


def exploser_psf(recette_dict, _profondeur=0, _vus=None):
    """
    Éclate récursivement les produits semi-finis d'une recette en matières
    premières de base. Les ingrédients qui ne sont pas des PSF connus sont
    conservés tels quels. Protégé contre les cycles et limité en profondeur.
    """
    if _profondeur > 6:
        return dict(recette_dict)
    _vus = _vus or set()
    resultat = {}
    for nom, qte in recette_dict.items():
        try:
            qte = float(qte)
        except (TypeError, ValueError):
            continue
        canon = _psf_canonique(nom)
        if canon and canon not in _vus and canon in _PSF_RECETTES:
            sous = {ing: ratio * qte for ing, ratio in _PSF_RECETTES[canon].items()}
            sous_eclate = exploser_psf(sous, _profondeur + 1, _vus | {canon})
            for ing, g in sous_eclate.items():
                resultat[ing] = resultat.get(ing, 0.0) + g
        else:
            resultat[nom] = resultat.get(nom, 0.0) + qte
    return resultat


def recette_generique_famille(nom_produit, famille):
    """
    Dernier repli du MRP : recette STANDARD par famille pour un produit fabriqué
    qu'aucune règle n'a reconnu (longue traîne). Grammage lu dans le nom si
    présent (ex. « TARTELETTE 90GR »), sinon poids type de la famille.
    Volontairement limité aux familles homogènes (pas de générique CUISINE :
    trop hétérogène, on préfère ne rien estimer que estimer faux).
    Retourne {} si la famille n'a pas de générique.
    """
    fam = str(famille or "").upper().strip()
    nom = str(nom_produit).upper()
    m = re.search(r'(\d+)\s*G(?:R)?(?:S)?(?:\b|_)', nom)
    p = int(m.group(1)) if m else None

    if fam == "BOULANGERIE":
        p = p or 100
        return {"Farine de blé T65 (g)": round(p * 0.72, 1),
                "Eau (ml)": round(p * 0.44, 1),
                "Levure boulangère (g)": round(p * 0.012, 1),
                "Sel (g)": round(p * 0.018, 1)}
    if fam == "VIENNOISERIE":
        p = p or 70
        return {"Pâte à croissant": round(p * 0.9, 1),
                "Sucre (g)": round(p * 0.05, 1)}
    if fam == "PATISSERIE":
        p = p or 90     # part individuelle type entremets
        return {"Farine de blé T55 (g)": round(p * 0.15, 1),
                "Sucre (g)": round(p * 0.20, 1),
                "Œufs (g)": round(p * 0.20, 1),
                "Beurre 84% MG (g)": round(p * 0.15, 1),
                "Crème liquide (g)": round(p * 0.15, 1),
                "Poudre amande (g)": round(p * 0.05, 1)}
    if fam in ("CONFISS/CHOCOLAT", "CONFISSERIE", "CONFISERIE"):
        p = p or 100
        return {"Chocolat noir bâton (g)": round(p * 0.7, 1),
                "Sucre (g)": round(p * 0.2, 1)}
    return {}


def detecter_bom_produit(nom_produit):
    """
    Retourne le BOM estimé pour un produit PAUL basé sur son nom.
    Quantités par unité vendue (g, ml, ou unité selon l'ingrédient).
    Retourne {} si le produit est acheté/revendu sans transformation.
    """
    nom = str(nom_produit).upper().strip()

    # ── Extraire le grammage si présent (ex: FLUTE 250GR → 250) ─────────────
    match_gr = re.search(r'(\d+)\s*GR(?:S)?(?:\b|_)', nom)
    poids_g  = int(match_gr.group(1)) if match_gr else None

    # ══════════════════════════════════════════════════════════════════════════
    # 1. BOISSONS ACHETÉES / REVENDUES — pas de BOM matière première
    # ══════════════════════════════════════════════════════════════════════════
    achete = ["COCA ", "COCA-", "COCA COLA", "SPRITE", "FANTA", "PEPSI",
              "ORANGINA", "SHWEPS", "SCHWEPPES", "7UP", "RED BULL", "REDBULL",
              "SIDI ALI", "OULMES", "EVIAN", "VITTEL", "HAWAI", "ICE TEA",
              "FRESH", "FRESH UP", "BONNE MAMAN", "POMS", "TWIX", "SNICKERS",
              "KINDER", "BOUNTY", "KIT KAT", "KITKAT",
              "NESPRESSO", "TASSIMO",
              "EAU MINÉRALE", "EAU PLATE", "VAE"]
    if any(k in nom for k in achete):
        return {}   # acheté tel quel → pas de BOM production

    # ══════════════════════════════════════════════════════════════════════════
    # 2. CAFÉS ET BOISSONS CHAUDES PRÉPARÉES
    # ══════════════════════════════════════════════════════════════════════════
    if any(k in nom for k in ["EXPRESSO", "ESPRESSO"]):
        return {"Café en grains (g)": 7.0, "Eau (ml)": 40.0,
                "Gobelet carton (unité)": 1.0}

    if any(k in nom for k in ["CAFE CREME", "CAFÉ CRÈME", "CAFE LATTE", "LATTE",
                                "CAPPUCCINO", "CAPPUCINO", "MACCHIATO"]):
        return {"Café en grains (g)": 7.0, "Lait entier (ml)": 120.0,
                "Gobelet carton (unité)": 1.0}

    if any(k in nom for k in ["CAFE AMERICAIN", "AMERICAIN", "LONG BLACK",
                                "AMERICANO"]):
        return {"Café en grains (g)": 7.0, "Eau (ml)": 120.0,
                "Gobelet carton (unité)": 1.0}

    if any(k in nom for k in ["CHOCOLAT CHAUD", "HOT CHOCO", "CACAO CHAUD"]):
        return {"Poudre cacao (g)": 25.0, "Lait entier (ml)": 200.0,
                "Sucre (g)": 10.0, "Gobelet carton (unité)": 1.0}

    if "MENU BOISSON" in nom or "BOISSON CHAUDE" in nom:
        return {"Café en grains (g)": 7.0, "Lait entier (ml)": 80.0,
                "Gobelet carton (unité)": 1.0}

    if any(k in nom for k in ["CAFE", "COFFEE", "KAWA"]) and "GLACÉ" not in nom:
        return {"Café en grains (g)": 7.0, "Eau (ml)": 60.0,
                "Gobelet carton (unité)": 1.0}

    # ── Thés ──────────────────────────────────────────────────────────────────
    if any(k in nom for k in ["THE MENTHE", "MENTHE"]):
        return {"Menthe fraîche (g)": 12.0, "Eau chaude (ml)": 250.0,
                "Sucre (g)": 15.0, "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["THE ", "THÉ ", "DARJEELING", "YUNNAN",
                                "EARL GREY", "ORGANIC", "RASPBERRY HERBAL",
                                "VERT ", "NOIR ", "BLANC ", "ROUGE "]):
        return {"Sachet thé (unité)": 1.0, "Eau chaude (ml)": 250.0,
                "Gobelet (unité)": 1.0}

    # ══════════════════════════════════════════════════════════════════════════
    # 3. BOISSONS FROIDES SIGNATURES PAUL (préparées en boutique)
    # ══════════════════════════════════════════════════════════════════════════
    if any(k in nom for k in ["CITRON GINGEMBRE", "GINGEMBRE CITRON"]):
        return {"Jus citron frais (ml)": 60.0, "Sirop gingembre (ml)": 20.0,
                "Eau pétillante (ml)": 180.0, "Glaçons (g)": 80.0,
                "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["ETE INDIEN", "ÉTÉ INDIEN"]):
        return {"Mangue (g)": 60.0, "Jus d'orange (ml)": 100.0,
                "Sirop passion (ml)": 20.0, "Glaçons (g)": 80.0,
                "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["AURORE BOREALE", "AURORE BORÉALE"]):
        return {"Sirop violette (ml)": 20.0, "Jus citron (ml)": 30.0,
                "Eau pétillante (ml)": 200.0, "Glaçons (g)": 80.0,
                "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["FRAPPE", "FRAPPÉ"]):
        return {"Lait entier (ml)": 150.0, "Sirop aromatique (ml)": 30.0,
                "Glace pilée (g)": 150.0, "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["SOUFFLE", "SOUFFLÉ"]):
        return {"Sirop aromatique (ml)": 30.0, "Eau pétillante (ml)": 200.0,
                "Glaçons (g)": 80.0, "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["SMOOTHIE", "MILK SHAKE", "MILKSHAKE"]):
        return {"Lait entier (ml)": 180.0, "Fruits frais (g)": 100.0,
                "Sucre (g)": 15.0, "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["JUS D", "JUS DE", "FRAICH", "PRESSÉ", "PRESSE"]):
        return {"Fruits frais (g)": 250.0, "Sucre (g)": 5.0,
                "Gobelet (unité)": 1.0}

    if any(k in nom for k in ["LIMONADE", "SIROP", "INFUSION FROIDE"]):
        return {"Sirop aromatique (ml)": 25.0, "Eau (ml)": 200.0,
                "Glaçons (g)": 80.0, "Gobelet (unité)": 1.0}

    # ══════════════════════════════════════════════════════════════════════════
    # 4. PAINS (quantités proportionnelles au poids final)
    # ══════════════════════════════════════════════════════════════════════════
    p = poids_g or 250   # défaut 250g si pas de grammage dans le nom

    if any(k in nom for k in ["FLUTE", "FICELLE", "BÂTARD"]):
        return {"Farine de blé T55 (g)": round(p*0.72, 1),
                "Eau (ml)": round(p*0.44, 1),
                "Levure boulangère (g)": round(p*0.012, 1),
                "Sel (g)": round(p*0.018, 1)}

    if any(k in nom for k in ["PAIN SEMOULE", "SEMOULE", "KHOBZ"]):
        return {"Semoule fine (g)": round(p*0.62, 1),
                "Farine de blé T55 (g)": round(p*0.10, 1),
                "Eau (ml)": round(p*0.40, 1),
                "Levure boulangère (g)": round(p*0.012, 1),
                "Sel (g)": round(p*0.018, 1)}

    if any(k in nom for k in ["PAIN COMPLET", "INTÉGRAL", "INTEGRAL", "MULTI"]):
        return {"Farine complète T150 (g)": round(p*0.66, 1),
                "Farine de blé T55 (g)": round(p*0.05, 1),
                "Eau (ml)": round(p*0.42, 1),
                "Levure boulangère (g)": round(p*0.012, 1),
                "Sel (g)": round(p*0.018, 1)}

    if any(k in nom for k in ["BAGUETTE", "TRADITION", "CAMPAGNE", "LEVAIN"]):
        return {"Farine de blé T65 (g)": round(p*0.72, 1),
                "Eau (ml)": round(p*0.40, 1),
                "Levure boulangère (g)": round(p*0.010, 1),
                "Sel (g)": round(p*0.018, 1)}

    # PAIN générique (exclure PAIN AU CHOC et MINI PAIN traités plus bas)
    if "PAIN" in nom and not any(k in nom for k in ["AU CHOC", "CHOCOLAT", "MINI"]):
        return {"Farine de blé T55 (g)": round(p*0.72, 1),
                "Eau (ml)": round(p*0.44, 1),
                "Levure boulangère (g)": round(p*0.012, 1),
                "Sel (g)": round(p*0.018, 1)}

    # ══════════════════════════════════════════════════════════════════════════
    # 5. VIENNOISERIES
    # ══════════════════════════════════════════════════════════════════════════
    c = poids_g or 80   # poids défaut croissant 80g

    if any(k in nom for k in ["CROISS", "CROISSANT"]):
        return {"Farine de blé T45 (g)": round(c*0.50, 1),
                "Beurre 84% MG (g)": round(c*0.25, 1),
                "Lait entier (ml)": round(c*0.12, 1),
                "Sucre (g)": round(c*0.06, 1),
                "Levure boulangère (g)": round(c*0.025, 1),
                "Œufs (g)": round(c*0.05, 1),
                "Sel (g)": round(c*0.01, 1)}

    pc = poids_g or 90  # pain au chocolat ~90g
    if any(k in nom for k in ["PAIN AU CHOC", "CHOCOLATINE", "MINI PAIN CHOC",
                                "MINI PAIN CHOCOLAT"]):
        return {"Farine de blé T45 (g)": round(pc*0.44, 1),
                "Beurre 84% MG (g)": round(pc*0.22, 1),
                "Chocolat noir bâton (g)": round(pc*0.15, 1),
                "Lait entier (ml)": round(pc*0.10, 1),
                "Sucre (g)": round(pc*0.05, 1),
                "Levure boulangère (g)": round(pc*0.02, 1),
                "Œufs (g)": round(pc*0.04, 1)}

    if any(k in nom for k in ["ESCARGOT", "SPIRAL"]):
        return {"Farine de blé T45 (g)": 50.0, "Beurre 84% MG (g)": 18.0,
                "Raisins secs (g)": 15.0, "Crème pâtissière (g)": 25.0,
                "Sucre (g)": 10.0, "Levure boulangère (g)": 2.0}

    if "RAISIN" in nom and "PAIN" not in nom:
        return {"Farine de blé T45 (g)": 55.0, "Beurre 84% MG (g)": 15.0,
                "Raisins secs (g)": 20.0, "Crème pâtissière (g)": 20.0,
                "Sucre (g)": 10.0, "Levure boulangère (g)": 2.0}

    if any(k in nom for k in ["CHAUSSON", "POMME"]):
        return {"Pâte feuilletée (g)": 80.0, "Compote pommes (g)": 60.0,
                "Sucre (g)": 10.0, "Beurre 84% MG (g)": 8.0}

    if any(k in nom for k in ["PALMIER", "SACRISTAIN"]):
        return {"Pâte feuilletée (g)": 70.0, "Sucre (g)": 20.0,
                "Beurre 84% MG (g)": 5.0}

    # ══════════════════════════════════════════════════════════════════════════
    # 6. PÂTISSERIES
    # ══════════════════════════════════════════════════════════════════════════
    if any(k in nom for k in ["ECLAIR", "ÉCLAIR"]):
        return {"Pâte à choux (g)": 35.0, "Crème pâtissière (g)": 50.0,
                "Fondant chocolat (g)": 15.0, "Beurre 84% MG (g)": 5.0}

    if any(k in nom for k in ["TARTE", "TARTELETTE"]):
        return {"Pâte sablée (g)": 55.0, "Crème pâtissière (g)": 40.0,
                "Fruits frais (g)": 80.0, "Gelée nappage (g)": 10.0}

    if "GALETTE" in nom:
        gp = poids_g or 300
        return {"Farine de blé T55 (g)": round(gp*0.35, 1),
                "Beurre 84% MG (g)": round(gp*0.30, 1),
                "Sucre (g)": round(gp*0.20, 1),
                "Œufs (g)": round(gp*0.12, 1),
                "Frangipane (g)": round(gp*0.20, 1)}

    if any(k in nom for k in ["MADELEINE"]):
        return {"Farine de blé T55 (g)": 30.0, "Beurre 84% MG (g)": 28.0,
                "Sucre (g)": 30.0, "Œufs (g)": 40.0, "Miel (g)": 5.0}

    if any(k in nom for k in ["COOKIE", "COOKIES"]):
        return {"Farine de blé T55 (g)": 40.0, "Beurre 84% MG (g)": 28.0,
                "Sucre (g)": 32.0, "Pépites chocolat (g)": 20.0,
                "Œufs (g)": 15.0}

    if "BROWNIE" in nom:
        return {"Farine de blé T55 (g)": 30.0, "Beurre 84% MG (g)": 50.0,
                "Chocolat noir 70% (g)": 60.0, "Sucre (g)": 60.0,
                "Œufs (g)": 50.0}

    if any(k in nom for k in ["CAKE", "FONDANT", "MOELLEUX"]):
        return {"Farine de blé T55 (g)": 50.0, "Beurre 84% MG (g)": 45.0,
                "Sucre (g)": 60.0, "Œufs (g)": 60.0, "Arôme (ml)": 5.0}

    if any(k in nom for k in ["MACARON", "MACARON"]):
        return {"Poudre amande (g)": 30.0, "Sucre glace (g)": 30.0,
                "Blancs d'œufs (g)": 22.0, "Sucre (g)": 30.0,
                "Ganache (g)": 20.0}

    if any(k in nom for k in ["MILLEFEUILLE", "MILLE FEUILLE"]):
        return {"Pâte feuilletée (g)": 90.0, "Crème pâtissière (g)": 80.0,
                "Fondant blanc (g)": 20.0}

    if any(k in nom for k in ["CHOUX", "RELIGIEUSE", "PARIS BREST"]):
        return {"Pâte à choux (g)": 40.0, "Crème (g)": 60.0,
                "Fondant (g)": 15.0, "Beurre 84% MG (g)": 5.0}

    if any(k in nom for k in ["FINANCIER", "FRIAND"]):
        return {"Poudre amande (g)": 25.0, "Beurre noisette (g)": 30.0,
                "Sucre glace (g)": 35.0, "Blancs d'œufs (g)": 30.0,
                "Farine de blé T55 (g)": 10.0}

    # ══════════════════════════════════════════════════════════════════════════
    # 7. SANDWICHS ET TRAITEUR
    # ══════════════════════════════════════════════════════════════════════════
    if any(k in nom for k in ["SANDWICH", "SANDW", "WRAP"]):
        return {"Demi-baguette ou pain (unité)": 1.0,
                "Garniture protéine (g)": 80.0, "Salade verte (g)": 20.0,
                "Sauce/Beurre (g)": 15.0, "Emballage (unité)": 1.0}

    if any(k in nom for k in ["SALADE", "SALAD"]) and "SAUCE" not in nom:
        return {"Salade verte (g)": 80.0, "Garniture salade (g)": 100.0,
                "Sauce vinaigrette (ml)": 20.0, "Boîte salade (unité)": 1.0}

    if any(k in nom for k in ["CROQUE", "TOAST"]):
        return {"Pain de mie (tranches)": 2.0, "Jambon (g)": 30.0,
                "Fromage (g)": 30.0, "Beurre 84% MG (g)": 10.0}

    if any(k in nom for k in ["QUICHE", "FLAMICHE"]):
        return {"Pâte brisée (g)": 80.0, "Œufs (g)": 50.0,
                "Crème fraîche (ml)": 80.0, "Garniture (g)": 60.0,
                "Fromage râpé (g)": 20.0}

    if any(k in nom for k in ["POULET", "CHICKEN"]):
        return {"Filet de poulet (g)": 120.0, "Chapelure (g)": 20.0,
                "Œufs (g)": 30.0, "Farine de blé T55 (g)": 15.0,
                "Huile friture (ml)": 20.0}

    if any(k in nom for k in ["SAUMON", "SALMON", "THON"]):
        return {"Poisson (g)": 80.0, "Crème fraîche (g)": 20.0,
                "Citron (unité)": 0.25, "Aneth/herbes (g)": 3.0}

    if any(k in nom for k in ["JAMBON", "PROSCIUTTO", "SERRANO"]):
        return {"Jambon tranché (g)": 40.0, "Pain (unité)": 0.5,
                "Beurre 84% MG (g)": 10.0}

    # ══════════════════════════════════════════════════════════════════════════
    # 8. MENUS / FORMULES / PETITS-DÉJEUNERS
    # ══════════════════════════════════════════════════════════════════════════
    if any(k in nom for k in ["PT DEJ", "PDJ", "PETIT DEJ", "BREAKFAST",
                                "BIEN ETRE", "BIEN-ÊTRE"]):
        return {"Viennoiserie (unité)": 1.0,
                "Pain tranché beurré (g)": 40.0,
                "Beurre (g)": 12.0,
                "Confiture (g)": 15.0,
                "Café en grains (g)": 7.0,
                "Lait entier (ml)": 100.0,
                "Jus de fruit (ml)": 100.0,
                "Gobelet carton (unité)": 2.0}

    if any(k in nom for k in ["GOUTER", "GOÛTER", "AFTER SCHOOL"]):
        return {"Viennoiserie (unité)": 1.0,
                "Café en grains (g)": 7.0,
                "Lait entier (ml)": 80.0,
                "Gobelet carton (unité)": 1.0}

    if any(k in nom for k in ["MENU", "FORMULE", "PLAT DU JOUR"]):
        return {"Plat (portion)": 1.0, "Boisson (unité)": 1.0,
                "Dessert (portion)": 0.5}

    # ── Suppléments / Add-ons ─────────────────────────────────────────────────
    if any(k in nom for k in ["SUPPLEMENT", "SUPPL", "EXTRA"]):
        return {"Garniture supplémentaire (portion)": 1.0}

    if any(k in nom for k in ["BOULE", "BILLE"]):
        return {"Farine de blé T55 (g)": 52.0, "Eau (ml)": 32.0,
                "Levure boulangère (g)": 0.6, "Sel (g)": 0.9}

    # ══════════════════════════════════════════════════════════════════════════
    # 9. Produit non identifié → pas de BOM
    # ══════════════════════════════════════════════════════════════════════════
    return {}


def normaliser_bom(bom_dict):
    """
    Nettoie un dictionnaire BOM avant agrégation MRP :
    1. Supprime les ingrédients hors commande (eau réseau).
    2. Normalise les noms pour éviter les doublons (ex: 'Sel' vs 'Sel (g)').
    3. Fusionne les clés identiques après normalisation.
    """
    def _normaliser_cle(cle):
        """Assure qu'une clé a son unité entre parenthèses à la fin."""
        cle = str(cle).strip()
        if not re.search(r'\([^)]+\)\s*$', cle):
            nom_maj = cle.upper()
            if any(k in nom_maj for k in [
                "FARINE", "SEMOULE", "BEURRE", "SUCRE", "SEL", "LEVURE",
                "ŒUF", "OEU", "CHOCOLAT", "POUDRE", "CRÈME", "CREME",
                "PÂTE", "COMPOTE", "RAISINS", "FRUITS", "MENTHE",
                "GLAÇONS", "GLACE", "GANACHE", "FONDANT", "GELÉE",
                "GARNITURE", "SALADE", "JAMBON", "POULET", "SAUMON",
                "POISSON", "FROMAGE", "FRANGIPANE", "AMANDE", "MIEL",
                "CHAPELURE", "PAIN DE MIE", "ANETH", "CITRON", "MANGUE",
                "AMELIORANT", "IBIS", "GRAISSE", "HUILE", "MARGARINE"
            ]):
                cle = f"{cle} (g)"
            elif any(k in nom_maj for k in [
                "LAIT", "JUS", "SIROP", "ARÔME", "AROME", "SAUCE"
            ]):
                cle = f"{cle} (ml)"
        return cle

    resultat = {}
    for cle, val in bom_dict.items():
        # 1. Harmonisation via le référentiel d'alias (canonique si connu).
        cle = _ALIAS_INVERSE.get(_canon_matiere(cle), cle)
        if cle in config.INGREDIENTS_HORS_COMMANDE:
            continue   # eau réseau → hors commande
        # 2. Ajout de l'unité si absente.
        cle_norm = _normaliser_cle(cle)
        resultat[cle_norm] = resultat.get(cle_norm, 0.0) + float(val)
    return resultat


def ajuster_bom_ramadan(bom_dict, date_mois):
    """
    Applique des coefficients d'ajustement saisonniers (Ramadan / Aïd)
    sur les quantités d'ingrédients.

    Ramadan → moins de viennoiseries le matin, plus de sucreries / pâtisseries.
    Aïd     → hausse des pâtisseries festives et des ingrédients orientaux.
    """
    date = pd.Timestamp(date_mois)
    fete_active = None
    for fete in config.FETES_MAROCAINES:
        debut = pd.Timestamp(fete["debut"])
        fin   = pd.Timestamp(fete["fin"])
        if debut <= date.replace(day=28) and fin >= date.replace(day=1):
            fete_active = fete["nom"].upper()
            break

    if fete_active is None:
        return bom_dict

    def _coef(cle):
        n = cle.upper()
        if "RAMADAN" in fete_active:
            if any(k in n for k in ["BEURRE", "LAIT"]):
                return 0.75
            if any(k in n for k in ["SUCRE", "CHOCOLAT", "POUDRE AMANDE", "FRANGIPANE",
                                     "CRÈME", "GANACHE"]):
                return 1.30
            return 0.90
        if "AID" in fete_active or "AÏD" in fete_active:
            if any(k in n for k in ["SUCRE", "AMANDE", "FRANGIPANE", "GANACHE",
                                     "MIEL", "BEURRE"]):
                return 1.40
            return 1.10
        return 1.0

    return {cle: round(val * _coef(cle), 2) for cle, val in bom_dict.items()}


def obtenir_multiplicateur_fete(dt, categorie):
    """Retourne le multiplicateur de demande pour une date et une catégorie donnée."""
    for fete in config.FETES_MAROCAINES:
        if pd.Timestamp(fete["debut"]) <= dt <= pd.Timestamp(fete["fin"]):
            return fete.get(categorie, 1.0)
    return 1.0

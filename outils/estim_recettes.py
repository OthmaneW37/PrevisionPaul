# -*- coding: utf-8 -*-
"""
Estimation ENRICHIE des recettes, pour PRÉ-REMPLIR le tableau des chefs.

But : donner à chaque produit fabriqué une recette-type plausible (ingrédients +
quantités pour 1 portion), issue de la composition classique de ces produits,
pour que les chefs n'aient qu'à CORRIGER au lieu de tout saisir. Ce ne sont que
des ESTIMATIONS à valider — jamais des vérités.

`estimer(nom, famille)` -> {"Ingrédient (g|ml|unité)": quantité} ; {} si aucune
règle ne s'applique (le générateur retombe alors sur bom.detecter / générique).
Le pain « nu » (flûtes, pistolets…) et la viennoiserie nue sont laissés à bom
(déjà correct) : on renvoie {} pour la famille BOULANGERIE.
"""

import re


def _m(*parts):
    """Fusionne des dicts d'ingrédients (additionne les quantités identiques)."""
    out = {}
    for p in parts:
        for k, v in p.items():
            out[k] = out.get(k, 0) + v
    return out


# ── Bases réutilisables (pour 1 portion) ──────────────────────────────────────
def _base_crepe():
    return {"Farine de blé T45 (g)": 60, "Œufs (g)": 30, "Lait entier (ml)": 120,
            "Beurre 84% MG (g)": 10, "Sucre (g)": 8}


def _base_pancake():
    return {"Farine de blé T45 (g)": 55, "Œufs (g)": 25, "Lait entier (ml)": 80,
            "Sucre (g)": 12, "Levure chimique (g)": 3, "Beurre 84% MG (g)": 8}


def _base_quiche():
    return {"Pâte brisée (g)": 80, "Œufs (g)": 50, "Crème liquide (g)": 60,
            "Lait entier (ml)": 30}


def _base_salade():
    return {"Salade verte (g)": 80, "Tomate (g)": 40, "Vinaigrette (ml)": 20}


def _base_omelette():
    return {"Œufs (g)": 120, "Crème liquide (g)": 20, "Beurre 84% MG (g)": 8}


def _base_cake():
    return {"Farine de blé T55 (g)": 40, "Sucre (g)": 35, "Œufs (g)": 40,
            "Beurre 84% MG (g)": 35, "Levure chimique (g)": 3}


def _base_tarte():
    return {"Pâte sablée (g)": 60, "Crème pâtissière (g)": 40}


def _base_entremet():
    return {"Génoise (g)": 45, "Crème mousseline (g)": 70, "Sucre (g)": 15,
            "Nappage (g)": 10}


def _cafe(lait=0, sirop=0, glacons=0, gobelet=True):
    r = {"Café en grains (g)": 9}
    if lait:
        r["Lait entier (ml)"] = lait
    if sirop:
        r["Sirop aromatique (ml)"] = sirop
    if glacons:
        r["Glaçons (g)"] = glacons
    if gobelet:
        r["Gobelet carton (unité)"] = 1
    return r


def _jus(fruit_g, fruit="Fruits frais", sucre=0):
    r = {f"{fruit} (g)": fruit_g, "Gobelet (unité)": 1}
    if sucre:
        r["Sucre (g)"] = sucre
    return r


def _garni_crepe_pancake(n):
    """Garniture commune crêpes/pancakes selon le nom."""
    if "BANANE" in n and ("CHOCO" in n or "CHOC" in n):
        return {"Banane (g)": 60, "Pâte à tartiner chocolat (g)": 30}
    if "POULET" in n and ("CHAMP" in n):
        return {"Poulet cuit (g)": 50, "Champignons (g)": 30, "Crème liquide (g)": 20,
                "Emmental râpé (g)": 20}
    if "CREVETTE" in n and "POIREAU" in n:
        return {"Crevettes (g)": 50, "Poireaux (g)": 40, "Crème liquide (g)": 20}
    if "SAUMON" in n:
        return {"Saumon fumé (g)": 45, "Crème fraîche (g)": 25, "Aneth (g)": 2}
    if "CARAMEL" in n and ("BEURRE" in n or "SALE" in n or "SALÉ" in n):
        return {"Caramel beurre salé (g)": 35}
    if "MIEL" in n and "NOIX" in n:
        return {"Miel (g)": 25, "Noix (g)": 15}
    if "CHOCO" in n and "NOIX" in n:
        return {"Pâte à tartiner chocolat (g)": 30, "Noix (g)": 15}
    if "TATIN" in n or "POMME" in n:
        return {"Pommes caramélisées (g)": 60, "Caramel (g)": 15}
    if "FRAMB" in n or "FRUITS ROUGES" in n:
        return {"Framboises (g)": 50, "Chantilly (g)": 25}
    if "COMPLETE" in n or "COMPLÈTE" in n or ("JAM" in n and "FROM" in n):
        return {"Jambon (g)": 40, "Œufs (g)": 50, "Emmental râpé (g)": 30}
    if "CONFITURE" in n:
        return {"Confiture (g)": 30}
    if "SUCRE" in n:
        return {"Sucre (g)": 15}
    if "MIEL" in n:
        return {"Miel (g)": 25}
    if "GLAC" in n and "VANI" in n:      # crêpe glace vanille
        return {"Glace vanille (g)": 60}
    return {"Sucre (g)": 10}             # défaut « nature »


def _garni_sandwich(n):
    if "SAUMON" in n:
        return {"Saumon fumé (g)": 50, "Fromage frais (g)": 30, "Crudités (g)": 30}
    if "POULET" in n and ("PANE" in n or "PANÉ" in n or "CROUST" in n):
        return {"Poulet pané (g)": 70, "Salade (g)": 20, "Tomate (g)": 25, "Sauce (g)": 25}
    if "POULET" in n:
        return {"Poulet grillé (g)": 70, "Crudités (g)": 40, "Sauce (g)": 20}
    if "THON" in n:
        return {"Thon (g)": 60, "Mayonnaise (g)": 20, "Crudités (g)": 30}
    if "DIEPPOIS" in n or "CREVETTE" in n:
        return {"Crevettes (g)": 60, "Sauce cocktail (g)": 25, "Salade (g)": 20}
    if "HOT DOG" in n:
        return {"Saucisse (g)": 70, "Moutarde (g)": 10, "Ketchup (g)": 15,
                "Oignons frits (g)": 15}
    if "FRAICHEUR" in n or "VSP" in n:
        return {"Crudités (g)": 60, "Fromage (g)": 30, "Sauce (g)": 20}
    if "MONTAGNARD" in n:
        return {"Charcuterie (g)": 60, "Fromage (g)": 40, "Crudités (g)": 20}
    return {"Garniture (g)": 70, "Crudités (g)": 30, "Sauce (g)": 20}


def _garni_hamburger(n):
    r = {"Pain hamburger (g)": 80, "Cheddar (g)": 20, "Salade (g)": 15,
         "Tomate (g)": 25, "Sauce burger (g)": 25, "Oignons (g)": 15}
    if "POULET" in n or "CROUSTILLANT" in n:
        r["Poulet croustillant (g)"] = 90
    else:
        r["Steak haché (g)"] = 90
    if "GUACAMOLE" in n:
        r["Guacamole (g)"] = 25
    return r


def _garni_omelette(n):
    if "FORESTIERE" in n or "FORESTIÈRE" in n or "CHAMPIGNON" in n:
        return {"Champignons (g)": 50, "Persil (g)": 3}
    if "PROVENCALE" in n or "PROVENÇALE" in n:
        return {"Tomate (g)": 40, "Poivron (g)": 30, "Herbes de Provence (g)": 2}
    if "BURRATA" in n:
        return {"Burrata (g)": 60, "Basilic (g)": 5}
    if "TRUFFE" in n:
        return {"Truffe (g)": 5, "Parmesan (g)": 20}
    return {"Fromage (g)": 40}


def _garni_tarte(n):
    if "PASSION" in n and "FRAMB" in n:
        return _m(_base_tarte(), {"Crème passion (g)": 50, "Framboises (g)": 40})
    if "CHOCOLAT" in n or "CHOCO" in n:
        return {"Pâte sablée (g)": 60, "Ganache chocolat (g)": 80}
    if "FRAISE" in n:
        return _m(_base_tarte(), {"Fraises (g)": 90, "Nappage (g)": 10})
    if "FRAMB" in n:
        return _m(_base_tarte(), {"Framboises (g)": 90, "Nappage (g)": 10})
    if "CITRON" in n:
        return {"Pâte sablée (g)": 60, "Crème citron (g)": 80, "Meringue (g)": 20}
    return _base_tarte()


# ── Point d'entrée ────────────────────────────────────────────────────────────
def estimer(nom, famille):
    """Recette-type estimée d'un produit, ou {} si aucune règle ne s'applique."""
    n = str(nom).upper()
    fam = str(famille or "").upper()

    # Le pain « nu » est déjà bien géré ailleurs (bom) : ne pas interférer.
    if fam == "BOULANGERIE":
        return {}

    # ── BOISSONS ────────────────────────────────────────────────────────────
    if fam == "BEVERAGE" or any(k in n for k in (
            "CAFE", "CAFÉ", "EXPRESSO", "ESPRESSO", "CAPPUC", "LATTE", "MACCHIATO",
            "MACHIATTO", "MOCACC", "FLAT WHITE", "AMERICANO", "RISTRETTO",
            "THE ", "THÉ", "MATCHA", "JUS", "CITRONNADE", "MOJITO", "VIRGIN",
            "DETOX", "MOCKTAIL", "COCKT", "VERVEINE")):
        # Cafés
        if any(k in n for k in ("CAFE", "CAFÉ", "EXPRESSO", "ESPRESSO", "CAPPUC",
                                "MACCHIATO", "MACHIATTO", "MOCACC", "FLAT WHITE",
                                "AMERICANO", "RISTRETTO", "ALLONGE", "MOITIE",
                                "CASSE", "SEPARE", "DOUBLE", "LATTE", "SPANISH")):
            lait = 120 if any(k in n for k in ("CREME", "CRÈME", "CAPPUC", "LATTE",
                                               "MACCHIATO", "MACHIATTO", "MOCACC",
                                               "FLAT WHITE", "SPANISH", "NOISET",
                                               "VANI", "CARAMEL", "CHOC")) else 0
            sirop = 15 if any(k in n for k in ("VANI", "CARAMEL", "CHOC", "NOISET",
                                               "SPANISH", "GOURMAND")) else 0
            glacons = 80 if ("GLAC" in n) else 0
            r = _cafe(lait=lait, sirop=sirop, glacons=glacons)
            if "MOCACC" in n or ("CHOC" in n and "LATTE" in n):
                r["Poudre cacao (g)"] = 10
            return r
        # Matcha
        if "MATCHA" in n:
            r = {"Poudre matcha (g)": 3, "Lait entier (ml)": 200, "Gobelet carton (unité)": 1}
            if "COCO" in n:
                r["Lait de coco (ml)"] = 60
            if "GLAC" in n:
                r["Glaçons (g)"] = 80
            return r
        # Thés / infusions
        if "THE " in n or "THÉ" in n or "EARL GREY" in n or "VERVEINE" in n:
            r = {"Eau chaude (ml)": 250, "Gobelet (unité)": 1}
            if "MENTHE" in n:
                r = _m(r, {"Menthe fraîche (g)": 12, "Thé vert (g)": 3, "Sucre (g)": 15})
            elif "VERVEINE" in n:
                r = _m(r, {"Verveine (g)": 2})
            else:
                r = _m(r, {"Sachet thé (unité)": 1})
            if "LAIT" in n:
                r["Lait entier (ml)"] = 60
            if "GLAC" in n or "PECHE" in n or "PÊCHE" in n:
                r = _m(r, {"Glaçons (g)": 80, "Sirop (ml)": 20})
            return r
        # Citronnades
        if "CITRONNADE" in n or ("CITRON" in n and "GINGEMBRE" in n):
            r = {"Jus de citron (ml)": 60, "Eau (ml)": 180, "Sucre (g)": 20,
                 "Glaçons (g)": 80, "Gobelet (unité)": 1}
            if "GINGEMBRE" in n:
                r["Sirop gingembre (ml)"] = 15
            if "MENTHE" in n:
                r["Menthe fraîche (g)"] = 8
            return r
        # Mojitos / virgin / mocktails / cocktails / detox
        if any(k in n for k in ("MOJITO", "VIRGIN", "MOCKTAIL", "COCKT", "COLADA",
                                "BALINAIS", "ROUGE PASSION", "AURORE", "ETE INDIEN",
                                "ÉTÉ INDIEN", "CREPUSCULE", "DETOX", "GOLDEN", "REED",
                                "GREEN", "VITAMINE", "ILES", "PINA")):
            return {"Jus de fruits (ml)": 180, "Fruits frais (g)": 80,
                    "Sirop (ml)": 20, "Glaçons (g)": 80, "Gobelet (unité)": 1}
        # Jus pressés / bouteilles
        if "JUS" in n or "BTL" in n or "ORANGE" in n or "CAROTTE" in n or "MANGUE" in n or "CITRON" in n:
            if "CAROTTE" in n:
                return _jus(300, "Carottes")
            if "CITRON" in n:
                return _jus(80, "Citrons", sucre=25)
            if "MANGUE" in n and "ORANGE" in n:
                return _m(_jus(150, "Oranges"), {"Mangue (g)": 120})
            if "MANGUE" in n:
                return _jus(250, "Mangue")
            if "ORANGE" in n:
                return _jus(300, "Oranges")
            return _jus(280, "Fruits frais")
        return {}   # boisson non identifiée -> générique

    # ── CRÊPES / PANCAKES / GAUFRES ───────────────────────────────────────────
    if "CREPE" in n or "CRÊPE" in n:
        return _m(_base_crepe(), _garni_crepe_pancake(n))
    if "PANCAKE" in n:
        return _m(_base_pancake(), _garni_crepe_pancake(n))

    # ── QUICHES / GALETTES / OMELETTES ────────────────────────────────────────
    if "QUICHE" in n:
        r = _base_quiche()
        if "SAUMON" in n:
            r = _m(r, {"Saumon fumé (g)": 45, "Épinards (g)": 40})
        elif "LORRAINE" in n:
            r = _m(r, {"Lardons (g)": 40, "Emmental râpé (g)": 30})
        else:
            r = _m(r, {"Garniture (g)": 50, "Emmental râpé (g)": 25})
        return r
    if "GALETTE" in n:                      # galette de sarrasin garnie
        r = {"Farine de sarrasin (g)": 70, "Œufs (g)": 20, "Eau (ml)": 100}
        r = _m(r, _garni_crepe_pancake(n) if "SUCR" in n else
               {"Poulet cuit (g)": 50, "Champignons (g)": 30, "Emmental râpé (g)": 25}
               if "POULET" in n else
               {"Saumon fumé (g)": 45, "Crème fraîche (g)": 25} if "SAUM" in n else
               {"Pommes de terre (g)": 60, "Poireaux (g)": 40, "Crème fraîche (g)": 20})
        return r
    if "OMELETTE" in n:
        return _m(_base_omelette(), _garni_omelette(n))

    # ── SANDWICHS / TARTINES / CLUBS / PANINIS / HAMBURGERS ────────────────────
    if "HAMBOUR" in n or "HAMB" in n or "CHEESE-HAMB" in n or "BURGER" in n:
        return _garni_hamburger(n)
    if "PANINI" in n:
        r = {"Pain panini (g)": 100, "Fromage (g)": 30}
        if "VIANDE" in n:
            r["Viande hachée (g)"] = 70
        elif "POULET" in n:
            r = _m(r, {"Poulet grillé (g)": 60, "Pesto (g)": 15})
        elif "JMB" in n or "JAMBON" in n:
            r = _m(r, {"Jambon (g)": 50, "Oignons caramélisés (g)": 25})
        else:
            r["Garniture (g)"] = 60
        return r
    if n.startswith("SW ") or "SANDWICH" in n or "CLUB SW" in n or "CLUB" in n:
        base = "Pain de mie (g)" if "CLUB" in n else "Baguette (g)"
        return _m({base: 110 if "CLUB" in n else 120}, _garni_sandwich(n))
    if "TARTINE" in n or "DOUBL" in n and "TARTIN" in n:
        r = {"Pain de campagne (g)": 70}
        if "POULET" in n and "AVOCAT" in n:
            r = _m(r, {"Poulet grillé (g)": 50, "Avocat (g)": 40})
        elif "FORESTR" in n or "FORESTIERE" in n:
            r = _m(r, {"Champignons (g)": 50, "Crème fraîche (g)": 20})
        elif "PASTRAMI" in n:
            r = _m(r, {"Pastrami (g)": 50, "Cornichons (g)": 15, "Moutarde (g)": 10})
        else:
            r = _m(r, {"Garniture (g)": 50})
        return r

    # ── CROISSANTS / BRIOCHES GARNIS ──────────────────────────────────────────
    if "CROISS" in n and any(k in n for k in ("JAMBON", "FROM", "SAUMON", "POULET",
                                              "SALE", "SALÉ", "PARISIEN", "AVOCAT",
                                              "HAMB", "PASTRAMI", "GRAINE")):
        r = {"Croissant (g)": 70}
        if "JAMBON" in n or ("FROM" in n and "SAUMON" not in n):
            r = _m(r, {"Jambon (g)": 35, "Emmental râpé (g)": 30})
        elif "SAUMON" in n:
            r = _m(r, {"Saumon fumé (g)": 40, "Fromage frais (g)": 25})
        elif "POULET" in n:
            r = _m(r, {"Poulet grillé (g)": 45, "Avocat (g)": 30})
        else:
            r = _m(r, {"Garniture (g)": 40})
        return r
    if "BRIOCHE" in n and any(k in n for k in ("SAUMON", "POULET", "AVOCAT", "FRAMB",
                                              "CHOCOLAT", "BANANE", "PARISIEN",
                                              "MEDITERRAN", "TATIN", "PER ", "PERDU",
                                              "SUCREE", "SUCRÉE", "GRMD", "GRMND", "BENEDICTE")):
        r = {"Brioche (g)": 80}
        if "SAUMON" in n and "BENEDICTE" in n:
            r = _m(r, {"Saumon fumé (g)": 40, "Œuf poché (g)": 50, "Sauce hollandaise (g)": 25})
        elif "POULET" in n or "AVOCAT" in n:
            r = _m(r, {"Poulet grillé (g)": 45, "Avocat (g)": 30})
        elif "CHOCOLAT" in n and "BANANE" in n:
            r = _m(r, {"Pâte à tartiner chocolat (g)": 30, "Banane (g)": 40})
        elif "FRAMB" in n:
            r = _m(r, {"Framboises (g)": 40, "Chantilly (g)": 20})
        elif "PER" in n or "PERDU" in n:
            r = _m(r, {"Œufs (g)": 40, "Lait entier (ml)": 60, "Sucre (g)": 15})
        else:
            r = _m(r, {"Garniture (g)": 40})
        return r

    # ── PAIN PERDU ────────────────────────────────────────────────────────────
    if "PAIN PERDU" in n or "PAIN PER" in n or "BROICH PAIN PER" in n:
        r = {"Brioche (g)": 90, "Œufs (g)": 50, "Lait entier (ml)": 90, "Sucre (g)": 20,
             "Beurre 84% MG (g)": 10}
        if "CARAMEL" in n:
            r["Caramel (g)"] = 25
        if "CHOCOLAT" in n:
            r["Sauce chocolat (g)"] = 25
        if "NOIS" in n:
            r["Noisettes (g)"] = 15
        return r

    # ── SALADES ───────────────────────────────────────────────────────────────
    if "SALAD" in n or n.startswith("SLD") or "SALADE" in n:
        r = _base_salade()
        if "NICOISE" in n or "NIÇOISE" in n:
            return _m(r, {"Thon (g)": 50, "Œufs (g)": 50, "Olives (g)": 15,
                          "Haricots verts (g)": 40, "Pommes de terre (g)": 50})
        if "CEASAR" in n or "CESAR" in n or "CAESAR" in n:
            r = _m(r, {"Poulet grillé (g)": 60, "Parmesan (g)": 15, "Croûtons (g)": 20,
                       "Sauce César (g)": 30})
            if "GMB" in n or "GAMBAS" in n:
                r["Gambas (g)"] = 50
            return r
        if "GAMBAS" in n or "GMB" in n:
            return _m(r, {"Gambas (g)": 60, "Agrumes (g)": 40, "Avocat (g)": 30})
        if "EXOTIQUE" in n and "SAUMON" in n:
            return _m(r, {"Saumon (g)": 50, "Mangue (g)": 40, "Avocat (g)": 40})
        if "LEGUM" in n or "LÉGUM" in n:
            return _m(r, {"Courgette (g)": 50, "Poivron (g)": 50, "Aubergine (g)": 50,
                          "Huile d'olive (ml)": 10})
        return r

    # ── PLATS / DIVERS CUISINE ────────────────────────────────────────────────
    if "COUSCOUS" in n:
        r = {"Semoule (g)": 120, "Légumes (g)": 150, "Pois chiches (g)": 40,
             "Bouillon (ml)": 100}
        r["Bœuf (g)" if "BOEUF" in n or "BŒUF" in n else "Poulet (g)"] = 120
        return r
    if "COUSCOUS" not in n and ("FILET BOEUF" in n or "EMINCE BOEUF" in n or "PAVE SAUMON" in n
                                or "PAVÉ SAUMON" in n or "TIGRE QUI PLEURE" in n):
        prot = ("Filet de bœuf (g)" if "FILET BOEUF" in n else
                "Émincé de bœuf (g)" if "EMINCE" in n else
                "Bœuf (g)" if "TIGRE" in n else "Pavé de saumon (g)")
        r = {prot: 150, "Sauce (g)": 40, "Légumes (g)": 80}
        if "GUACAMOLE" in n:
            r["Guacamole (g)"] = 30
        return r
    if "CHAKSHUKA" in n or "CHAKCHOUKA" in n:
        return {"Tomate (g)": 120, "Poivron (g)": 60, "Œufs (g)": 100, "Oignons (g)": 40,
                "Huile d'olive (ml)": 15, "Épices (g)": 3}
    if "JAWHARA" in n or "JAOUHARA" in n:
        return {"Pâte filo (g)": 40, "Amandes (g)": 40, "Sucre glace (g)": 20,
                "Beurre 84% MG (g)": 15, "Pistache (g)": 10}
    if "CROQ" in n and ("MONSIEUR" in n or "MR" in n):
        return {"Pain de mie (g)": 80, "Jambon (g)": 50, "Emmental râpé (g)": 40,
                "Béchamel (g)": 40}
    if "FROM BL" in n or "FROMAGE BLANC" in n or "FROM BLANC" in n:
        r = {"Fromage blanc (g)": 150}
        if "GRANOLA" in n:
            r["Granola (g)"] = 40
        if "FRUIT" in n or "FRAMBOISE" in n or "PASSION" in n or "ROUGE" in n:
            r["Fruits (g)"] = 60
        if "MIEL" in n:
            r["Miel (g)"] = 15
        return r
    if "CHIA" in n:
        return {"Graines de chia (g)": 30, "Lait (ml)": 150, "Framboises (g)": 50,
                "Sirop d'agave (ml)": 15}
    if "OEUFS BROUILL" in n or "ŒUFS BROUILL" in n:
        r = {"Œufs (g)": 120, "Crème liquide (g)": 20, "Beurre 84% MG (g)": 10}
        if "TRUFFE" in n:
            r["Truffe (g)"] = 5
        return r

    # ── PÂTISSERIE ────────────────────────────────────────────────────────────
    if "MOELLEUX" in n and ("CHOC" in n or "CHOCO" in n):
        return {"Chocolat noir (g)": 50, "Beurre 84% MG (g)": 40, "Œufs (g)": 50,
                "Sucre (g)": 40, "Farine de blé T55 (g)": 20}
    if "TARTE" in n or "TARTELETTE" in n:
        return _garni_tarte(n)
    if "CAKE" in n:
        r = _base_cake()
        if "CITRON" in n:
            r = _m(r, {"Citron (g)": 20, "Graines de pavot (g)": 5})
        if "ROCHER" in n and ("BLANC" in n):
            r = _m(r, {"Chocolat blanc (g)": 30, "Amandes (g)": 15})
        elif "ROCHER" in n or "CHOCOLAT" in n:
            r = _m(r, {"Chocolat noir (g)": 30, "Noisettes (g)": 15})
        return r
    if "FRAMBOISIER" in n:
        return _m(_base_entremet(), {"Framboises (g)": 60})
    if "FRAISIER" in n:
        return _m(_base_entremet(), {"Fraises (g)": 60, "Pâte de pistache (g)": 10})
    if "FORET NOIRE" in n or "FORÊT NOIRE" in n:
        return {"Génoise cacao (g)": 50, "Chantilly (g)": 70, "Griottes (g)": 40,
                "Copeaux chocolat (g)": 15}
    if any(k in n for k in ("JIVARA", "DUCHESSE", "ROYAL", "B NEIGE", "F AUTOMNE",
                            "MF VANILLE", "PASSIONNANT", "AMANDINE", "DANISH",
                            "ESCARGOT", "BEIGNET", "VELOURS")):
        return _base_entremet()

    return {}

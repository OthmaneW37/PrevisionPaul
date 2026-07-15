# -*- coding: utf-8 -*-
"""
Chiffrage des besoins matières premières (budget d'achat + food-cost).

À partir des prix estimés (data/prix_matieres.json) et des besoins matières
(quantités du MRP), calcule le coût de chaque ligne, le budget d'achat total et,
rapporté au chiffre d'affaires prévu, le food-cost (% du CA dépensé en matières).

Prix par KG (solides, ingrédient en « (g) »), par LITRE (liquides, « (ml) ») ou
à l'UNITÉ (« (unité) »). Estimations à affiner avec les factures réelles.
"""
import json
import os
import re
import unicodedata

from . import config

_PRIX = config.PRIX_MATIERES.get("prix", []) if isinstance(config.PRIX_MATIERES, dict) else []
DEVISE = config.PRIX_MATIERES.get("_devise", "MAD") if isinstance(config.PRIX_MATIERES, dict) else "MAD"


def _canon(s):
    # « Œ/œ » n'a pas de décomposition NFKD : translittérer avant le repli ASCII,
    # sinon « Œufs » devient « ufs » et le mot-clé « oeuf » ne matche jamais.
    s = str(s).replace("Œ", "OE").replace("œ", "oe").replace("Æ", "AE").replace("æ", "ae")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


# Liste (mot-clé canonique, prix) figée une fois, dans l'ordre du fichier
# (du plus spécifique au plus général).
_LISTE = [(_canon(kw), float(p)) for kw, p in _PRIX]


def prix_unitaire(nom):
    """Prix (MAD par kg/L/unité) pour un ingrédient, ou None si aucun mot-clé ne matche."""
    base = _canon(re.sub(r"\([^)]*\)", "", str(nom)))   # sans l'unité entre parenthèses
    for kw, p in _LISTE:
        if kw and kw in base:
            return p
    return None


def cout_ligne(nom, quantite):
    """Coût (MAD) d'une quantité d'un ingrédient. None si prix inconnu.

    L'unité de la quantité est lue dans le nom : (g)→kg, (ml)→L, (unité)→pièce.
    """
    p = prix_unitaire(nom)
    if p is None:
        return None
    n = str(nom).lower()
    try:
        q = float(quantite)
    except (TypeError, ValueError):
        return None
    if "(ml)" in n or "(l)" in n:
        return q / 1000.0 * p
    if "unit" in n or "piece" in n or "pièce" in n:
        return q * p
    return q / 1000.0 * p          # (g) ou défaut solide


def chiffrer_besoins(df_besoins, col_ing="Ingredient", col_qte="Quantite_Requise"):
    """
    Ajoute une colonne 'Cout_MAD' à un DataFrame de besoins et renvoie
    (df_enrichi, total_cout, taux_couverture) où taux_couverture = part des
    besoins (en quantité pondérée) dont le prix est connu.
    """
    df = df_besoins.copy()
    df["Cout_MAD"] = [cout_ligne(n, q) for n, q in zip(df[col_ing], df[col_qte])]
    total = float(df["Cout_MAD"].dropna().sum())
    n_connus = df["Cout_MAD"].notna().sum()
    couverture = (n_connus / len(df)) if len(df) else 0.0
    return df, total, couverture

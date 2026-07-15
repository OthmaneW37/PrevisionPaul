# -*- coding: utf-8 -*-
"""Fiabilisation des recettes -> BOM exploitable par le pipeline.

Produit :
  - data/referentiel_nettoyage.json : regles de nettoyage (bruit, PSF, alias).
  - data/recettes_reelles_clean.json : recettes extraites, nettoyees.
  - data/recettes_exactes.json       : BOM final keye par PRODUIT VENDU
                                       (recettes reelles validees + proposees),
                                       directement utilise par le pipeline MRP.
    (l'ancien fichier de test est sauvegarde en .backup_test.json)

Sans validation chef : les recettes proposees sont PROVISOIRES (clairement
identifiables) ; tout est reversible via la sauvegarde.
"""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------
import json, os, re, shutil, unicodedata
from difflib import SequenceMatcher

RECETTES_REELLES = "data/recettes_reelles.json"
PROPOSEES_TXT    = "docs/recettes_proposees_produits_importants.txt"
RANK_CSV         = "/tmp/produits_classes.csv"
OUT_REF          = "data/referentiel_nettoyage.json"
OUT_CLEAN        = "data/recettes_reelles_clean.json"
OUT_BOM          = "data/recettes_exactes.json"
OUT_PROV         = "data/recettes_exactes_provenance.json"

FICHES_REJETEES = {
    "CUISSON CROISSANT ROLL", "SAUCE A LA MOUTARDE", "ŒUFS BROUILLES A LA TRUFFE",
    "JAOUHARA MONTAGE", "OMLETTE CHAKCHOUKA", "CHAKCHOUKA", "APPAREIL JAOUHARA",
}

# --- Bruit : lignes qui ne sont pas des matieres premieres ---
NOISE_PATTERNS = [
    r"°\s*c\b", r"\b\d+\s*°", r"\b\d+\s*c\b", r"\bt\.?a\b", r"voir\s*ft",
    r"\(.*ft.*\)", r"pique en bois", r"\bpqt\b", r"au choix", r"\bvar\b",
    r"^\s*[\-/.,*]+\s*$", r"decoupage", r"assemblage\b.*\bunit",
]
# --- PSF : produits semi-finis (a eclater plus tard en MP de base) ---
PSF_KEYWORDS = [
    "PSF", "CUIT", "CUITE", "ASSEMBLAGE", "MONTAGE", "PREPARE", "PREPAREE",
    "SAUCE", "CREME", "CRÈME", "PATE A", "PÂTE A", "COMPOTE", "PUREE", "PURÉE",
    "GUACAMOLE", "SIDE", "ACCOMPAGNEMENT", "ACOMPAGNEMENT", "CROISSANT GM",
    "CROISSANT MINI", "BRIOCHE 300", "PAIN NORDIQUE", "PAIN SW", "PAIN BRIOCHE",
    "PAIN DE MIE", "PAIN TRESSE", "CROUTON", "FRITES", "POCHE", "POCHÉ",
    "OEUF DUR", "ŒUF DUR", "VINAIGRETTE", "NOUGATINE", "CARAMEL", "MAYONNAISE",
    "CHANTILLY", "FOUETTEE", "RAVIGOTE", "HOLLANDAISE", "SAVORA", "BERNAISE",
]

def strip_accents(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()

def canon(s):
    """Cle canonique (insensible casse/accents/espaces) pour fusionner les doublons."""
    return re.sub(r"\s+", " ", strip_accents(s).lower()).strip()

def is_noise(nom):
    low = canon(nom)
    if len(low) <= 1:
        return True
    return any(re.search(p, low) for p in NOISE_PATTERNS)

def is_psf(nom):
    up = strip_accents(nom).upper()
    return any(k in up for k in (strip_accents(x).upper() for x in PSF_KEYWORDS))

def nettoyer_recette(ing_dict):
    """Retire le bruit, fusionne les doublons de casse. Retourne (clean, psf_set)."""
    fusion, repr_nom, psf = {}, {}, set()
    for nom, g in ing_dict.items():
        if is_noise(nom):
            continue
        c = canon(nom)
        repr_nom.setdefault(c, nom.strip())
        fusion[c] = fusion.get(c, 0.0) + float(g)
        if is_psf(nom):
            psf.add(repr_nom[c])
    clean = {repr_nom[c]: round(v, 1) for c, v in fusion.items()}
    return clean, psf

# ---------------- matching fiche -> produit vendu ----------------
def norm_match(s):
    s = strip_accents(s).upper()
    s = re.sub(r"\b(VAE|VSP|PSF|MAG|FT|FACON|CORDON|BLEU|A LA|AU|AUX|DE|DU|LA|LE|"
               r"GR|GM|UNITE|UNITES|PFU|NM)\b", " ", s)
    s = s.replace("SW", "SANDWICH").replace("CEASAR", "CESAR")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def score(a, b):
    ta, tb = set(norm_match(a).split()), set(norm_match(b).split())
    if not ta or not tb:
        return 0.0
    jacc = len(ta & tb) / len(ta | tb)
    return 0.6 * jacc + 0.4 * SequenceMatcher(None, norm_match(a), norm_match(b)).ratio()

def charger_produits():
    import csv
    prods = []
    with open(RANK_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            prods.append(row["Nom code article"])
    return prods

# ---------------- parsing des recettes proposees (txt) ----------------
def parser_proposees():
    recettes, courant = {}, None
    if not os.path.exists(PROPOSEES_TXT):
        return recettes
    with open(PROPOSEES_TXT, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^---\s+(.+?)\s+\(.*\)\s+---\s*$", line)
            if m:
                courant = m.group(1).strip()
                recettes[courant] = {}
                continue
            if courant is None:
                continue
            mi = re.match(r"^\s+(.+?)\s*\.{2,}\s*([\d.,]+)\s*g\s*$", line)
            if mi:
                nom = mi.group(1).strip()
                g = float(mi.group(2).replace(",", "."))
                recettes[courant][nom] = g
    return {k: v for k, v in recettes.items() if v}

def main():
    with open(RECETTES_REELLES, encoding="utf-8") as f:
        reelles = json.load(f)
    fiches = {k: v for k, v in reelles.items() if k not in FICHES_REJETEES}
    produits = charger_produits()

    # 1) nettoyage des recettes reelles
    clean_reelles, psf_global = {}, set()
    for nom_fiche, data in fiches.items():
        clean, psf = nettoyer_recette(data.get("ingredients_g", {}))
        clean_reelles[nom_fiche] = {"fichier": data.get("fichier"), "ingredients_g": clean}
        psf_global |= psf

    # 2) mapping fiche -> produit vendu (>=0.40, dedoublonne par meilleur score)
    cand = []
    for fiche in fiches:
        best, best_s = None, 0.0
        for prod in produits:
            sc = score(fiche, prod)
            if sc > best_s:
                best_s, best = sc, prod
        if best_s >= 0.40:
            cand.append((best_s, fiche, best))
    cand.sort(reverse=True)
    bom_reel, pris, provenance = {}, set(), {}
    for sc, fiche, prod in cand:
        if prod in pris:
            continue
        ing = clean_reelles[fiche]["ingredients_g"]
        if ing:
            bom_reel[prod] = dict(ing)
            provenance[prod] = {"source": "fiche reelle", "fiche": fiche, "score": round(sc, 2)}
            pris.add(prod)

    # 3) recettes proposees (nettoyees), keyees par produit vendu
    proposees = parser_proposees()
    bom_propose = {}
    for prod, ing in proposees.items():
        if prod in bom_reel:        # une vraie fiche prime sur une proposition
            continue
        clean, psf = nettoyer_recette(ing)
        psf_global |= psf
        if clean:
            bom_propose[prod] = dict(clean)
            provenance[prod] = {"source": "recette proposee (a valider chef)"}

    # 4) BOM final = reel + propose
    bom_final = {}
    bom_final.update(bom_reel)
    bom_final.update(bom_propose)

    # 5) referentiel de nettoyage (reutilisable / documentation)
    referentiel = {
        "description": "Regles de fiabilisation des recettes avant calcul MRP.",
        "bruit_patterns_regex": NOISE_PATTERNS,
        "psf_mots_cles": PSF_KEYWORDS,
        "psf_detectes": sorted(psf_global),
        "note": "Les PSF (produits semi-finis) restent dans les recettes mais "
                "devront etre eclates en matieres premieres de base (BOM multi-niveaux).",
    }

    os.makedirs("data", exist_ok=True)
    if os.path.exists(OUT_BOM):
        shutil.copy(OUT_BOM, OUT_BOM.replace(".json", ".backup_test.json"))
    with open(OUT_REF, "w", encoding="utf-8") as f:
        json.dump(referentiel, f, ensure_ascii=False, indent=2)
    with open(OUT_CLEAN, "w", encoding="utf-8") as f:
        json.dump(clean_reelles, f, ensure_ascii=False, indent=2)
    with open(OUT_BOM, "w", encoding="utf-8") as f:
        json.dump(bom_final, f, ensure_ascii=False, indent=2)
    with open(OUT_PROV, "w", encoding="utf-8") as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2)

    print(f"Recettes reelles nettoyees : {len(clean_reelles)}")
    print(f"  -> mappees a un produit vendu : {len(bom_reel)}")
    print(f"Recettes proposees integrees : {len(bom_propose)}")
    print(f"BOM final (produits couverts) : {len(bom_final)}")
    print(f"PSF detectes (a eclater plus tard) : {len(psf_global)}")
    print(f"\nEcrits : {OUT_BOM} (+ backup), {OUT_CLEAN}, {OUT_REF}")
    print("\nExemples PSF :", ", ".join(sorted(psf_global)[:10]))

if __name__ == "__main__":
    main()

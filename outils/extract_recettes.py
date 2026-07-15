# -*- coding: utf-8 -*-
"""Extraction des fiches techniques (Recette + MP) -> recettes structurées.
Sortie : data/recettes_reelles.json + impression lisible pour vérification.
Script utilitaire one-shot (peut être supprimé ensuite)."""

# --- Script utilitaire : exécutable depuis n'importe où (se cale sur la racine) ---
import os as _os, sys as _sys
_RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _RACINE)
_os.chdir(_RACINE)
# ---------------------------------------------------------------------------------
import os, re, json, zipfile, glob
import xml.etree.ElementTree as ET
import openpyxl
import pdfplumber

DOSSIER = "Recette + MP"
SORTIE  = os.path.join("data", "recettes_reelles.json")

NUM = re.compile(r"[-+]?\d[\d\s.,]*")

def to_float(x):
    if x is None: return None
    s = str(x).replace("\xa0", " ").strip()
    m = NUM.search(s)
    if not m: return None
    v = m.group(0).replace(" ", "").replace(",", ".")
    # garder seulement le premier nombre propre
    v = re.sub(r"\.(?=.*\.)", "", v)
    try: return float(v)
    except ValueError: return None

def est_entete_ou_total(nom):
    n = (nom or "").lower()
    cles = ["matière première", "matiere premiere", "ingrédient", "ingredient",
            "code psf", "poids total", "poids net", "conservation", "allergène",
            "allergene", "matériel", "materiel", "préparation", "preparation",
            "nom du produit", "catégorie", "categorie", "équipements", "equipements",
            "garantie", "validation", "signature", "dlv", "quantité", "quantite",
            "unité", "unite", "pays", "information", "composition"]
    if not n.strip(): return True
    if n.strip() in ("/", "nm", "t.a", "ta"): return True
    return any(c in n for c in cles)

# Mots d'équipement / process qui polluent parfois la colonne ingrédient
_BRUIT = ["four", "réfrigérateur", "refrigerateur", "chambre froide", "décongélation",
          "decongelation", "frais +", "ventilé", "ventile", "sole", "corbeil",
          "plaque", "poêle", "poele", "spatule", "casserole", "planche", "couteau",
          "cuillère", "cuillere", "fourchette", "à la demande", "a la demande"]

def est_bruit(nom):
    n = (nom or "").lower().strip()
    return any(b in n for b in _BRUIT)

# Nettoyage du nom d'ingrédient : retire codes fournisseurs / parenthèses techniques
_CODE = re.compile(r"\(?\b(TRA|PFU|NM|PFU\d+|NM\d+|PFU000\d+|TRA/[A-Z]+/?\d*)[A-Z0-9/ ]*\)?", re.I)
def nettoyer_ingredient(nom):
    s = str(nom).replace("\xa0", " ").strip()
    s = re.sub(r"\(KG\)", "", s, flags=re.I)
    s = re.sub(r"\(PFU[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\(TRA[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\bTRA/[A-Z]+/\d+\b", "", s)
    s = re.sub(r"\bPFU\d*\b", "", s)
    s = re.sub(r"\bNM\d+\b", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–·:")
    return s

# ---------- XLSX (layout FT MAG : col A=code '/', col B=Ingrédients, col C=Quantité g) ----------
def extraire_xlsx(chemin):
    wb = openpyxl.load_workbook(chemin, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    nom = None
    ing_col = qty_col = None
    entete_ligne = None
    for i, r in enumerate(rows):
        vals = [str(c).strip() if c is not None else "" for c in r]
        low = [v.lower() for v in vals]
        if nom is None:
            for j, v in enumerate(low):
                if "nom du produit" in v and j + 1 < len(vals) and vals[j + 1]:
                    nom = vals[j + 1]
        for j, v in enumerate(low):
            if "ingrédient" in v or "ingredient" in v:
                ing_col = j
            if "quantité" in v or "quantite" in v:
                qty_col = j
        if ing_col is not None and qty_col is not None:
            entete_ligne = i
            break

    ingredients = {}
    if entete_ligne is None:
        return nom, ingredients
    for r in rows[entete_ligne + 1:]:
        if ing_col >= len(r) or qty_col >= len(r):
            continue
        ing = r[ing_col]
        grammes = to_float(r[qty_col])
        ing = "" if ing is None else str(ing).strip()
        if not ing or est_entete_ou_total(ing) or est_bruit(ing):
            continue
        if grammes is None or grammes <= 0:
            continue
        ing = nettoyer_ingredient(ing)
        if ing:
            ingredients[ing] = ingredients.get(ing, 0) + grammes
    return nom, ingredients

# ---------- PDF ----------
def extraire_pdf(chemin):
    nom = os.path.splitext(os.path.basename(chemin))[0]
    ingredients = {}
    with pdfplumber.open(chemin) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for tbl in tables:
                for row in tbl:
                    cells = [(_c or "").replace("\n", " ").strip() for _c in row]
                    if not any(cells): continue
                    ing = cells[0]
                    if est_entete_ou_total(ing) or est_bruit(ing): continue
                    nombres = [to_float(c) for c in cells[1:] if to_float(c) is not None]
                    if not nombres: continue
                    grammes = nombres[-1]
                    ing = nettoyer_ingredient(ing)
                    if ing and grammes and grammes > 0:
                        ingredients[ing] = ingredients.get(ing, 0) + grammes
    return nom, ingredients

# ---------- DOCX ----------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
def _cell_text(tc):
    return "".join(t.text or "" for t in tc.iter(W+"t")).strip()

def extraire_docx(chemin):
    nom = os.path.splitext(os.path.basename(chemin))[0].replace("FT ", "")
    with zipfile.ZipFile(chemin) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    ingredients = {}
    for tbl in root.iter(W+"tbl"):
        for tr in tbl.iter(W+"tr"):
            cells = [_cell_text(tc) for tc in tr.iter(W+"tc")]
            if not any(cells): continue
            ing = cells[0]
            if est_entete_ou_total(ing) or est_bruit(ing): continue
            nombres = [to_float(c) for c in cells[1:] if to_float(c) is not None]
            if not nombres: continue
            grammes = nombres[-1]
            ing = nettoyer_ingredient(ing)
            if ing and grammes and grammes > 0:
                ingredients[ing] = ingredients.get(ing, 0) + grammes
    return nom, ingredients

def main():
    fichiers = sorted(glob.glob(os.path.join(DOSSIER, "*")))
    recettes = {}
    for f in fichiers:
        ext = os.path.splitext(f)[1].lower()
        try:
            if ext == ".xlsx":
                nom, ing = extraire_xlsx(f)
            elif ext == ".pdf":
                nom, ing = extraire_pdf(f)
            elif ext == ".docx":
                nom, ing = extraire_docx(f)
            else:
                continue
        except Exception as e:
            print(f"[ERREUR] {os.path.basename(f)} : {e}")
            continue
        nom = nom or os.path.splitext(os.path.basename(f))[0]
        # Heuristique : certaines fiches expriment les poids en kg (toutes < 5).
        if ing and max(ing.values()) < 5:
            ing = {k: v * 1000 for k, v in ing.items()}
        recettes[nom] = {
            "fichier": os.path.basename(f),
            "ingredients_g": {k: round(v, 1) for k, v in ing.items()},
        }
        print(f"\n### {nom}   ({os.path.basename(f)})  [{len(ing)} lignes]")
        for k, v in ing.items():
            print(f"    - {k}: {v} g")

    os.makedirs("data", exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as fp:
        json.dump(recettes, fp, ensure_ascii=False, indent=2)
    print(f"\n==> {len(recettes)} recettes écrites dans {SORTIE}")

if __name__ == "__main__":
    main()

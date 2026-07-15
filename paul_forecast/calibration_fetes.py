# -*- coding: utf-8 -*-
"""
Calibration des profils de fêtes À PARTIR DES VENTES JOURNALIÈRES réelles.

profils_fetes.json contenait des ratios par catégorie en grande partie SUPPOSÉS
(« hypothèses à calibrer dès qu'on aura des données »). On a désormais ~5 ans de
ventes journalières (donnees_ventes/ventes_journalieres.csv) et les dates de fêtes
(fetes_maroc.json). Ce module MESURE l'uplift réel par catégorie pendant chaque
fête, moyenné sur toutes les occurrences, et écrit data/profils_fetes_mesures.json.

Uplift = (vente moyenne/jour pendant la fête) / (vente moyenne/jour des ~28 jours
autour, hors jours de fête). Le forecasting journalier préfère ces ratios mesurés.

Lancement : python -m paul_forecast.calibration_fetes
"""

import os
import json
from collections import defaultdict

import numpy as np
import pandas as pd

from . import config
from .logging_setup import get_logger

logger = get_logger()

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(RACINE, "donnees_ventes", "ventes_journalieres.csv")
SORTIE = os.path.join(config.DATA_DIR, "profils_fetes_mesures.json")

MARGE_BASELINE = 28      # jours de part et d'autre pour le niveau « normal »
MIN_OCCURRENCES = 2      # nb minimal d'occurrences pour retenir un ratio mesuré
MIN_BASE_JOUR = 5        # vente/jour minimale en baseline (ignore les familles rares)


def _type_fete(fete):
    if fete.get("type"):
        return fete["type"]
    nom = str(fete.get("nom", "")).lower()
    return ("ramadan" if "ramadan" in nom else
            "aid_fitr" if "fitr" in nom else
            "aid_adha" if "adha" in nom else
            "achoura" if "achoura" in nom else
            "mawlid" if ("mawlid" in nom or "mouloud" in nom) else None)


def _occurrences():
    out = []
    for f in config.FETES_MAROCAINES:
        typ = _type_fete(f)
        if not typ:
            continue
        try:
            out.append((typ, pd.Timestamp(f["debut"]), pd.Timestamp(f["fin"])))
        except (KeyError, ValueError):
            continue
    return out


def mesurer():
    if not os.path.exists(SOURCE):
        logger.warning("[Calibration] Source introuvable : %s", SOURCE)
        return None
    df = pd.read_csv(SOURCE, sep=";", parse_dates=["Date"])
    df["Quantite"] = df["Quantite"].clip(lower=0)
    dmin, dmax = df["Date"].min(), df["Date"].max()

    # série quotidienne par catégorie (0-comblée)
    cal = pd.date_range(dmin, dmax)
    series = {fam: g.groupby("Date")["Quantite"].sum().reindex(cal, fill_value=0.0)
              for fam, g in df.groupby("Famille")}

    occ = _occurrences()
    jours_fete = set()
    for _, deb, fin in occ:
        jours_fete.update(pd.date_range(deb, fin))

    ratios = defaultdict(lambda: defaultdict(list))
    n_occ = defaultdict(int)
    for typ, deb, fin in occ:
        if deb < dmin or fin > dmax:
            continue
        n_occ[typ] += 1
        fenetre_fete = pd.date_range(deb, fin)
        base = [d for d in pd.date_range(deb - pd.Timedelta(days=MARGE_BASELINE),
                                         fin + pd.Timedelta(days=MARGE_BASELINE))
                if d not in jours_fete]
        for fam, s in series.items():
            base_m = s.reindex(base, fill_value=0).mean()
            if base_m < MIN_BASE_JOUR:
                continue
            fete_m = s.reindex(fenetre_fete, fill_value=0).mean()
            ratios[typ][fam].append(fete_m / base_m)

    profils = {"_description": "Profils de fêtes MESURÉS sur les ventes journalières réelles "
                               "(uplift = vente/jour pendant la fête / vente/jour normale autour). "
                               "Généré par paul_forecast.calibration_fetes.",
               "_periode": f"{dmin.date()} -> {dmax.date()}"}
    for typ, fams in ratios.items():
        r = {fam: round(float(np.clip(np.mean(vals), 0.2, 3.0)), 2)
             for fam, vals in fams.items() if len(vals) >= MIN_OCCURRENCES}
        if r:
            profils[typ] = {"_n_occurrences": n_occ[typ], "ratios": r}
    return profils


def mesurer_uplift(debut, fin, marge=MARGE_BASELINE):
    """Uplift par catégorie d'une période passée quelconque (ex. Coupe du Monde 2022).

    Renvoie {famille: ratio} = vente/jour pendant [debut, fin] / vente/jour autour.
    Outil d'aide à l'estimation d'un événement à partir d'un précédent comparable.
    """
    if not os.path.exists(SOURCE):
        return {}
    df = pd.read_csv(SOURCE, sep=";", parse_dates=["Date"])
    df["Quantite"] = df["Quantite"].clip(lower=0)
    deb, fin = pd.Timestamp(debut), pd.Timestamp(fin)
    cal = pd.date_range(df["Date"].min(), df["Date"].max())
    out = {}
    for fam, g in df.groupby("Famille"):
        s = g.groupby("Date")["Quantite"].sum().reindex(cal, fill_value=0.0)
        base_days = list(pd.date_range(deb - pd.Timedelta(days=marge), deb - pd.Timedelta(days=1))) \
                    + list(pd.date_range(fin + pd.Timedelta(days=1), fin + pd.Timedelta(days=marge)))
        base_m = s.reindex(base_days, fill_value=0).mean()
        if base_m < MIN_BASE_JOUR:
            continue
        out[str(fam)] = round(float(s.reindex(pd.date_range(deb, fin), fill_value=0).mean() / base_m), 2)
    return out


def mesurer_uplift_jours(dates, marge=21):
    """Uplift par catégorie sur une LISTE de jours non contigus (ex. jours de match).

    Compare la vente/jour de ces jours à celle des `marge` jours autour (hors ces jours).
    """
    if not os.path.exists(SOURCE):
        return {}
    df = pd.read_csv(SOURCE, sep=";", parse_dates=["Date"])
    df["Quantite"] = df["Quantite"].clip(lower=0)
    cal = pd.date_range(df["Date"].min(), df["Date"].max())
    plage = set(cal)
    # ne garder que les jours réellement couverts par l'historique (ignore les dates futures)
    jours = [d for d in (pd.Timestamp(x) for x in dates) if d in plage]
    if not jours:
        return {}
    autour = set()
    for d in jours:
        autour.update(pd.date_range(d - pd.Timedelta(days=marge), d + pd.Timedelta(days=marge)))
    base_days = [d for d in autour if d not in set(jours) and d in plage]
    out = {}
    for fam, g in df.groupby("Famille"):
        s = g.groupby("Date")["Quantite"].sum().reindex(cal, fill_value=0.0)
        base_m = s.reindex(base_days, fill_value=0).mean()
        if base_m < MIN_BASE_JOUR:
            continue
        out[str(fam)] = round(float(s.reindex(jours, fill_value=0).mean() / base_m), 2)
    return out


def calibrer(ecrire=True, rapport=True):
    profils = mesurer()
    if not profils:
        return None
    if ecrire:
        with open(SORTIE, "w", encoding="utf-8") as f:
            json.dump(profils, f, ensure_ascii=False, indent=2)
        logger.info("[Calibration] Profils mesurés écrits → %s", SORTIE)
    if not rapport:
        return profils

    # Rapport comparatif vs hypothèses
    hyp = config.PROFILS_FETES or {}
    print(f"Profils de fêtes mesurés ({profils.get('_periode')}) :\n")
    for typ in ("ramadan", "aid_fitr", "aid_adha", "achoura", "mawlid"):
        if typ not in profils:
            continue
        r = profils[typ]["ratios"]
        print(f"--- {typ}  (n={profils[typ]['_n_occurrences']} occurrences) ---")
        rh = hyp.get(typ, {}).get("ratios", {})
        for fam in sorted(r):
            anc = rh.get(fam)
            comp = f"  (hypothèse : x{anc:.2f})" if anc else ""
            print(f"   {fam:18s}: x{r[fam]:.2f}{comp}")
        print()
    return profils


# Profils mesurés chargés à l'import (utilisés en priorité par le forecasting).
def profils_mesures():
    try:
        with open(SORTIE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    calibrer()

# -*- coding: utf-8 -*-
"""
Récupération dynamique des dates des fêtes marocaines via l'API Aladhan
(conversion calendrier Hégirien ↔ Grégorien, gratuite, sans clé).

Pourquoi une API : les fêtes islamiques (Aïd, Ramadan…) suivent le calendrier
lunaire et leurs dates officielles ne sont confirmées qu'au dernier moment
(observation de la lune). On les recalcule donc à chaque mise à jour, avec
`data/fetes_maroc.json` comme cache/repli hors-ligne et override manuel
(si la date officielle marocaine diffère de ±1 jour du calcul).

Note : l'API ne renvoie pas les *profils de ventes* — seulement les dates.
Les profils (combien de + ou - par famille) sont dans data/profils_fetes.json.
"""

import json
import os
import ssl
import urllib.request
from datetime import datetime, timedelta

from . import config
from .logging_setup import get_logger

logger = get_logger()

_API = "https://api.aladhan.com/v1/hToG/{d:02d}-{m:02d}-{hy}"

# (nom, type, jour_hijri, mois_hijri, jours_effet). Ramadan = cas spécial (mois).
_FETES = [
    ("Ramadan",      "ramadan",   1, 9,  None),   # fin = veille de l'Aïd el-Fitr
    ("Aid el-Fitr",  "aid_fitr",  1, 10, 3),
    ("Aid el-Adha",  "aid_adha", 10, 12, 4),
    ("Achoura",      "achoura",  10, 1,  2),
    ("Mawlid",       "mawlid",   12, 3,  2),
]

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _hijri_vers_greg(d, m, hy):
    url = _API.format(d=d, m=m, hy=hy)
    with urllib.request.urlopen(url, timeout=15, context=_CTX) as r:
        g = json.loads(r.read().decode())["data"]["gregorian"]["date"]
    return datetime.strptime(g, "%d-%m-%Y").date()


def _trouver_date(d, m, annee_greg):
    """Trouve l'occurrence (date grégorienne) d'une date hégirienne dans l'année G."""
    for hy in (annee_greg - 580, annee_greg - 579, annee_greg - 578, annee_greg - 577):
        try:
            g = _hijri_vers_greg(d, m, hy)
            if g.year == annee_greg:
                return g
        except Exception:
            continue
    return None


def construire_fetes(annees):
    """Construit la liste des fêtes (nom, type, debut, fin) pour les années données."""
    fetes = []
    for G in annees:
        for nom, typ, d, m, jours in _FETES:
            deb = _trouver_date(d, m, G)
            if deb is None:
                logger.warning("[Fêtes API] %s %d introuvable.", nom, G)
                continue
            if typ == "ramadan":
                aid = _trouver_date(1, 10, G)            # 1er Shawwal
                fin = (aid - timedelta(days=1)) if aid and aid > deb else deb + timedelta(days=29)
            else:
                fin = deb + timedelta(days=jours - 1)
            fetes.append({"nom": f"{nom} {G}", "type": typ,
                          "debut": deb.isoformat(), "fin": fin.isoformat()})
    return fetes


def mettre_a_jour_fetes(annees, chemin=None):
    """
    Met à jour data/fetes_maroc.json via l'API. En cas d'échec réseau, conserve
    le fichier existant (repli). Sauvegarde l'ancien fichier en .backup.
    """
    chemin = chemin or os.path.join(config.DATA_DIR, "fetes_maroc.json")
    fetes = construire_fetes(annees)
    if not fetes:
        logger.warning("[Fêtes API] Aucune date récupérée — fichier existant conservé.")
        return None
    if os.path.exists(chemin):
        try:
            import shutil
            shutil.copy(chemin, chemin + ".backup")
        except OSError:
            pass
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(fetes, f, ensure_ascii=False, indent=2)
    logger.info("[Fêtes API] %d dates écrites dans %s (années %s).",
                len(fetes), chemin, list(annees))
    return fetes


if __name__ == "__main__":
    import sys
    annee_courante = datetime.now().year
    annees = [int(a) for a in sys.argv[1:]] or list(range(annee_courante, annee_courante + 3))
    res = mettre_a_jour_fetes(annees)
    if res:
        for fe in res:
            print(f"  {fe['type']:<10} {fe['nom']:<16} {fe['debut']} -> {fe['fin']}")

# -*- coding: utf-8 -*-
"""
Assistant LOCAL de questions/réponses PAUL — 100 % hors ligne, confidentiel.

Aucune API, aucun appel réseau : rien ne quitte la machine. L'assistant répond
à des questions GUIDÉES (choisies dans des listes du dashboard) en lisant les
vraies données du projet (prévisions, historique, matières, commandes, suivi) et
en les mettant en forme. Comme tout est déterministe (pas de modèle de langage),
il n'y a AUCUN risque d'hallucination : chaque chiffre affiché vient d'un fichier.

- `interroger(type_q, ...)` : point d'entrée unique du dashboard → {'texte', 'table'}.
- `outil_*` : accès aux données (réutilisables ailleurs).

(Historique : une version reliée à l'API Claude a existé ; retirée pour garantir
la confidentialité totale des données de ventes.)
"""

import os
import re

import pandas as pd

from . import forecast_journalier as fj
from . import commandes as mod_commandes
from . import evenements as mod_evenements
from . import matchs as mod_matchs

RACINE = fj.RACINE
EXPORTS = os.path.join(RACINE, "exports")


# ══════════════════════════════════════════════════════════════════════════════
# ACCÈS AUX DONNÉES (lecture des vraies sources locales)
# ══════════════════════════════════════════════════════════════════════════════
def _dossier_export_recent():
    """Sous-dossier daté (AAAA-MM-JJ) le plus récent d'exports/, sinon la racine."""
    if not os.path.isdir(EXPORTS):
        return EXPORTS
    dates = [d for d in os.listdir(EXPORTS)
             if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
             and os.path.isdir(os.path.join(EXPORTS, d))]
    return os.path.join(EXPORTS, max(dates)) if dates else EXPORTS


def _charger_prev():
    """Prévisions journalières (exports/previsions_journalieres.csv) ou None."""
    p = os.path.join(EXPORTS, "previsions_journalieres.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, sep=";", parse_dates=["Date"])
    except Exception:
        return None
    for c in ("Qty_Prev", "Qty_Recommandee", "Qty_Commande"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def _charger_mrp():
    """Besoins matières premières planifiés (dernier export daté) ou None."""
    for base in (_dossier_export_recent(), EXPORTS):
        p = os.path.join(base, "besoins_ingredients_planifies.csv")
        if os.path.exists(p):
            try:
                df = pd.read_csv(p, sep=";", parse_dates=["Date"])
                df["Quantite_Requise"] = pd.to_numeric(df["Quantite_Requise"], errors="coerce")
                return df
            except Exception:
                continue
    return None


def _charger_suivi():
    """Suivi prévu vs réel (exports/suivi_prevu_reel.csv) ou None."""
    p = os.path.join(EXPORTS, "suivi_prevu_reel.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, sep=";", parse_dates=["Date"])
        for c in ("Prev", "Reel"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception:
        return None


def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _produits_connus():
    """Noms de produits connus (ventes réelles)."""
    df = fj.charger_ventes()
    if df is None or df.empty:
        return []
    return sorted(df["Produit"].astype(str).unique().tolist())


def _resoudre_produit(terme):
    """(nom_exact, suggestions) : nom canonique si trouvé, sinon liste de candidats."""
    noms = _produits_connus()
    t = _norm(terme)
    for n in noms:
        if _norm(n) == t:
            return n, []
    contient = [n for n in noms if t in _norm(n)]
    if len(contient) == 1:
        return contient[0], []
    return None, contient[:12]


def _premier_jour_utile(jours):
    """Premier jour de prévision ≥ aujourd'hui (sinon le 1er disponible).

    Sans cela, « prochain jour » répondrait pour la 1re date du fichier de
    prévisions, qui peut être déjà passée si le calcul n'a pas été relancé.
    """
    auj = pd.Timestamp.now().normalize()
    futurs = [j for j in jours if pd.Timestamp(j) >= auj]
    return pd.Timestamp(futurs[0] if futurs else jours[0])


# ══════════════════════════════════════════════════════════════════════════════
# OUTILS — chacun renvoie un dict de VRAIES données (aucun chiffre inventé)
# ══════════════════════════════════════════════════════════════════════════════
def outil_prevision_produit(produit, date=None):
    """Prévision de production d'un produit (jour précis, ou 7 prochains jours)."""
    prev = _charger_prev()
    if prev is None or prev.empty:
        return {"erreur": "Prévisions journalières non calculées. Clique « Relancer le calcul »."}
    nom, suggestions = _resoudre_produit(produit)
    if nom is None:
        return {"erreur": f"Produit « {produit} » introuvable.", "suggestions": suggestions}
    sub = prev[prev["Produit"].astype(str) == nom].sort_values("Date")
    if date:
        try:
            d = pd.Timestamp(date).normalize()
        except (ValueError, TypeError):
            return {"erreur": f"Date « {date} » invalide."}
        sub = sub[sub["Date"] == d]
        if sub.empty:
            return {"erreur": f"Aucune prévision pour « {nom} » le {date} "
                              f"(horizon {prev['Date'].min().date()} → {prev['Date'].max().date()})."}
    else:
        d0 = _premier_jour_utile(sorted(sub["Date"].unique())) if not sub.empty else None
        if d0 is not None:
            sub = sub[sub["Date"] >= d0]
        sub = sub.head(7)
    jours = [{"date": r["Date"].strftime("%Y-%m-%d"),
              "a_produire": int(round(r["Qty_Recommandee"])),
              "prevision": int(round(r["Qty_Prev"])),
              "dont_commande_client": int(round(r.get("Qty_Commande", 0) or 0)),
              "fiabilite": str(r.get("Fiabilite", ""))}
             for _, r in sub.iterrows()]
    return {"produit": nom, "jours": jours}


def outil_production_jour(date=None, categorie=None, top=10):
    """Total à produire un jour + top produits (toutes catégories ou une famille)."""
    prev = _charger_prev()
    if prev is None or prev.empty:
        return {"erreur": "Prévisions journalières non calculées."}
    jours = sorted(prev["Date"].unique())
    if date:
        try:
            d = pd.Timestamp(date).normalize()
        except (ValueError, TypeError):
            return {"erreur": f"Date « {date} » invalide."}
    else:
        d = _premier_jour_utile(jours)
    j = prev[prev["Date"] == d]
    if j.empty:
        return {"erreur": f"Aucune prévision le {d.date()} "
                          f"(horizon {pd.Timestamp(jours[0]).date()} → {pd.Timestamp(jours[-1]).date()})."}
    if categorie and categorie not in ("__all__", "toutes") and "Famille" in j.columns:
        j = j[j["Famille"].astype(str).str.lower() == str(categorie).lower()]
        if j.empty:
            return {"erreur": f"Aucun produit pour la catégorie « {categorie} » le {d.date()}."}
        cat = categorie
    else:
        cat = "toutes"
    total = int(round(j["Qty_Recommandee"].sum()))
    n = int((j["Qty_Recommandee"] > 0).sum())
    top_df = j.sort_values("Qty_Recommandee", ascending=False).head(int(top or 10))
    top_list = [{"produit": str(r["Produit"]), "categorie": str(r.get("Famille", "")),
                 "a_produire": int(round(r["Qty_Recommandee"]))}
                for _, r in top_df.iterrows() if r["Qty_Recommandee"] > 0]
    return {"date": d.strftime("%Y-%m-%d"), "categorie": cat,
            "total_a_produire": total, "nb_produits": n, "top": top_list}


def outil_historique_produit(produit, debut=None, fin=None, granularite="mois"):
    """Ventes RÉELLES passées d'un produit, agrégées par jour / semaine / mois."""
    df = fj.charger_ventes()
    if df is None or df.empty:
        return {"erreur": "Historique des ventes indisponible."}
    nom, suggestions = _resoudre_produit(produit)
    if nom is None:
        return {"erreur": f"Produit « {produit} » introuvable.", "suggestions": suggestions}
    s = df[df["Produit"].astype(str) == nom].groupby("Date")["Quantite"].sum()
    if s.empty:
        return {"erreur": f"Aucune vente enregistrée pour « {nom} »."}
    s = s.reindex(pd.date_range(s.index.min(), s.index.max()), fill_value=0.0)
    for borne, comp in ((debut, "ge"), (fin, "le")):
        if borne:
            try:
                b = pd.Timestamp(borne)
            except (ValueError, TypeError):
                return {"erreur": f"Date « {borne} » invalide."}
            s = s[s.index >= b] if comp == "ge" else s[s.index <= b]
    if s.empty:
        return {"erreur": "Aucune vente sur la période demandée."}
    gran = str(granularite or "mois").lower()
    if gran.startswith("jour"):
        g, fmt, gran = s, "%Y-%m-%d", "jour"
    elif gran.startswith("sem"):
        g = s.groupby(s.index - pd.to_timedelta(s.index.dayofweek, unit="D")).sum()
        fmt, gran = "%Y-%m-%d", "semaine"
    else:
        g = s.groupby(s.index.to_period("M").to_timestamp()).sum()
        fmt, gran = "%Y-%m", "mois"
    g = g.tail(24)
    periodes = [{"periode": pd.Timestamp(idx).strftime(fmt), "quantite": int(round(v))}
                for idx, v in g.items()]
    return {"produit": nom, "granularite": gran, "total_periode": int(round(g.sum())),
            "moyenne_periode": int(round(g.mean())), "periodes": periodes}


def outil_matieres_premieres(mois=None, top=15):
    """Besoins en matières premières (bon de commande) pour un mois."""
    mrp = _charger_mrp()
    if mrp is None or mrp.empty:
        return {"erreur": "Besoins matières non calculés (relancer le calcul mensuel)."}
    mois_dispo = sorted(mrp["Date"].dt.strftime("%Y-%m").unique())
    # Par défaut : le mois courant s'il est couvert, sinon le 1er mois futur,
    # sinon le 1er disponible (fichier ancien).
    mois_auj = pd.Timestamp.now().strftime("%Y-%m")
    futurs = [x for x in mois_dispo if x >= mois_auj]
    m = mois or (futurs[0] if futurs else mois_dispo[0])
    d = mrp[mrp["Date"].dt.strftime("%Y-%m") == m]
    if d.empty:
        return {"erreur": f"Aucune donnée matières pour {m}.", "mois_disponibles": mois_dispo}
    agg = d.groupby("Ingredient")["Quantite_Requise"].sum().sort_values(ascending=False).head(int(top or 15))

    def conv(ing, v):
        if "(g)" in str(ing):
            return round(v / 1000, 1), "kg"
        if "(ml)" in str(ing):
            return round(v / 1000, 1), "L"
        return round(v, 1), "unité"
    ings = []
    for ing, v in agg.items():
        q, u = conv(ing, v)
        ings.append({"ingredient": re.sub(r"\s*\([^)]*\)\s*$", "", str(ing)), "quantite": q, "unite": u})
    return {"mois": m, "mois_disponibles": mois_dispo, "ingredients": ings}


def outil_evenements_commandes():
    """Événements et commandes clients à venir (déjà pris en compte dans les prévisions)."""
    auj = pd.Timestamp.today().normalize()
    horizon = auj + pd.Timedelta(days=60)
    evs = [{"nom": e.get("nom", "") or e.get("type", ""), "type": e.get("type", ""),
            "debut": e["debut"].strftime("%Y-%m-%d"), "fin": e["fin"].strftime("%Y-%m-%d"),
            "impact_global_pct": int(round((e["global"] - 1) * 100))}
           for e in mod_evenements.evenements_normalises()
           if e["fin"] >= auj and e["debut"] <= horizon]
    matchs = [{"date": pd.Timestamp(m["date"]).strftime("%Y-%m-%d"),
               "adversaire": m.get("adversaire", "")}
              for m in mod_matchs.charger_matchs()
              if m.get("date") and auj <= pd.Timestamp(m["date"]) <= horizon]
    cmds = [{"date": c["date"].strftime("%Y-%m-%d"), "produit": c["produit"],
             "quantite": int(c["quantite"]), "client": c["client"]}
            for c in mod_commandes.commandes_normalisees() if auj <= c["date"] <= horizon]
    return {"evenements": evs, "matchs": matchs, "commandes_clients": cmds}


def outil_fiabilite(produit=None):
    """Qualité des prévisions récentes (prévu vs réel) : écart global et biais par produit."""
    from . import suivi as mod_suivi
    comp = _charger_suivi()
    if comp is None or comp.empty:
        return {"erreur": "Suivi prévu/réel non calculé (relancer le calcul)."}
    m = mod_suivi.metriques_globales(comp)
    out = {"jours_suivis": m["n_jours"],
           "ecart_moyen_pct": None if m["wmape"] is None else int(round(m["wmape"] * 100)),
           "biais_global_pct": None if m["biais_pct"] is None else int(round(m["biais_pct"]))}
    biais = mod_suivi.biais_par_produit(comp)
    if produit:
        nom, suggestions = _resoudre_produit(produit)
        if nom is None:
            return {"erreur": f"Produit « {produit} » introuvable.", "suggestions": suggestions}
        r = biais[biais["Produit"] == nom]
        out["produit"] = nom
        out["detail"] = ({} if r.empty else
                         {"reel_par_jour": int(r.iloc[0]["Réel/j"]),
                          "prevu_par_jour": int(r.iloc[0]["Prév/j"]),
                          "biais": str(r.iloc[0]["Biais"]), "sens": str(r.iloc[0]["Sens"])})
    else:
        out["produits_a_biais"] = [
            {"produit": str(r["Produit"]), "sens": str(r["Sens"]), "biais": str(r["Biais"]),
             "reel_par_jour": int(r["Réel/j"]), "prevu_par_jour": int(r["Prév/j"])}
            for _, r in biais[biais["Sens"] != "OK"].head(10).iterrows()]
    return out


# ══════════════════════════════════════════════════════════════════════════════
# COUCHE DE RÉPONSE (déterministe) — utilisée par l'onglet Assistant du dashboard
# ══════════════════════════════════════════════════════════════════════════════
TYPES_QUESTION = {
    "production":  "Que produire un jour donné",
    "prevision":   "Prévision d'un produit",
    "historique":  "Historique des ventes d'un produit",
    "matieres":    "Matières premières à commander (par mois)",
    "evenements":  "Événements et commandes à venir",
    "fiabilite":   "Fiabilité des prévisions (prévu vs réel)",
}


def _espaces(n):
    return f"{int(n):,}".replace(",", " ")


def _pct_signe(x):
    return "—" if x is None else (f"+{x}%" if x >= 0 else f"{x}%")


def _suggestion(r):
    return (f" Essaie plutôt : {', '.join(r['suggestions'])}." if r.get("suggestions") else "")


def interroger(type_q, produit=None, date=None, categorie=None, mois=None,
               granularite=None, debut=None, fin=None):
    """Répond LOCALEMENT à une question guidée. Retourne {'texte': str, 'table': list|None}.

    Aucun appel réseau : la réponse est calculée à partir des fichiers locaux.
    """
    if type_q == "production":
        r = outil_production_jour(date=date, categorie=categorie, top=15)
        if "erreur" in r:
            return {"texte": r["erreur"], "table": None}
        cat = "" if r["categorie"] == "toutes" else f" en {r['categorie']}"
        texte = (f"Le {r['date']}{cat} : {_espaces(r['total_a_produire'])} unités à produire, "
                 f"{r['nb_produits']} références.")
        table = [{"Produit": p["produit"], "Catégorie": p["categorie"], "À produire": p["a_produire"]}
                 for p in r["top"]]
        return {"texte": texte, "table": table or None}

    if type_q == "prevision":
        if not produit:
            return {"texte": "Choisis d'abord un produit.", "table": None}
        r = outil_prevision_produit(produit, date=date)
        if "erreur" in r:
            return {"texte": r["erreur"] + _suggestion(r), "table": None}
        j = r["jours"]
        if len(j) == 1:
            x = j[0]
            comm = f" (dont {x['dont_commande_client']} de commande client)" if x["dont_commande_client"] else ""
            texte = (f"Le {x['date']}, produire {_espaces(x['a_produire'])} « {r['produit']} »{comm} — "
                     f"prévision {_espaces(x['prevision'])}, fiabilité {x['fiabilite']}.")
        else:
            tot = sum(x["a_produire"] for x in j)
            texte = (f"« {r['produit']} » — 7 prochains jours : {_espaces(tot)} à produire au total "
                     f"(~{_espaces(round(tot / len(j)))}/jour).")
        table = [{"Date": x["date"], "À produire": x["a_produire"], "Prévision": x["prevision"],
                  "Dont commande": x["dont_commande_client"], "Fiabilité": x["fiabilite"]} for x in j]
        return {"texte": texte, "table": table}

    if type_q == "historique":
        if not produit:
            return {"texte": "Choisis d'abord un produit.", "table": None}
        r = outil_historique_produit(produit, debut=debut, fin=fin, granularite=granularite or "mois")
        if "erreur" in r:
            return {"texte": r["erreur"] + _suggestion(r), "table": None}
        texte = (f"« {r['produit']} » — par {r['granularite']} : {_espaces(r['total_periode'])} vendus au total, "
                 f"~{_espaces(r['moyenne_periode'])}/{r['granularite']} en moyenne.")
        table = [{"Période": p["periode"], "Quantité": p["quantite"]} for p in r["periodes"]]
        return {"texte": texte, "table": table}

    if type_q == "matieres":
        r = outil_matieres_premieres(mois=mois, top=20)
        if "erreur" in r:
            return {"texte": r["erreur"], "table": None}
        texte = f"Matières premières à commander — {r['mois']} (top {len(r['ingredients'])})."
        table = [{"Ingrédient": i["ingredient"], "Quantité": i["quantite"], "Unité": i["unite"]}
                 for i in r["ingredients"]]
        return {"texte": texte, "table": table or None}

    if type_q == "evenements":
        r = outil_evenements_commandes()
        parts = []
        if r["evenements"]:
            parts.append(f"{len(r['evenements'])} événement(s)")
        if r["matchs"]:
            parts.append(f"{len(r['matchs'])} match(s)")
        if r["commandes_clients"]:
            parts.append(f"{len(r['commandes_clients'])} commande(s) client")
        texte = "À venir (60 prochains jours) : " + (", ".join(parts) if parts else "rien de prévu") + "."
        table = []
        for e in r["evenements"]:
            periode = e["debut"] + (f" → {e['fin']}" if e["fin"] != e["debut"] else "")
            table.append({"Type": e["type"] or "événement", "Détail": e["nom"] or "—",
                          "Date": periode, "Info": _pct_signe(e["impact_global_pct"])})
        for m in r["matchs"]:
            table.append({"Type": "match", "Détail": m["adversaire"] or "match",
                          "Date": m["date"], "Info": "impact appris"})
        for c in r["commandes_clients"]:
            det = f"{c['quantite']} × {c['produit']}" + (f" ({c['client']})" if c["client"] else "")
            table.append({"Type": "commande", "Détail": det, "Date": c["date"],
                          "Info": "ajoutée aux prévisions"})
        return {"texte": texte, "table": table or None}

    if type_q == "fiabilite":
        r = outil_fiabilite(produit=produit or None)
        if "erreur" in r:
            return {"texte": r["erreur"] + _suggestion(r), "table": None}
        ecart = "—" if r["ecart_moyen_pct"] is None else f"{r['ecart_moyen_pct']}%"
        texte = (f"Prévisions récentes ({r['jours_suivis']} jours) : écart moyen {ecart}, "
                 f"biais global {_pct_signe(r['biais_global_pct'])}.")
        if "detail" in r:
            d = r["detail"]
            if d:
                texte += (f" « {r['produit']} » : {d['sens'].lower()} ({d['biais']}), "
                          f"réel ~{d['reel_par_jour']}/j vs prévu ~{d['prevu_par_jour']}/j.")
            else:
                texte += f" « {r['produit']} » : pas de biais marqué."
            return {"texte": texte, "table": None}
        table = [{"Produit": p["produit"], "Sens": p["sens"], "Biais": p["biais"],
                  "Réel/j": p["reel_par_jour"], "Prévu/j": p["prevu_par_jour"]}
                 for p in r.get("produits_a_biais", [])]
        return {"texte": texte, "table": table or None}

    return {"texte": "Type de question inconnu.", "table": None}

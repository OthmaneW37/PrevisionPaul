# -*- coding: utf-8 -*-
"""
Tableau de bord PAUL — Statistiques & Prévisions (Dash / Plotly)
Lancement : python dashboard.py  →  http://127.0.0.1:8050

Onglets :
  1. Vue d'ensemble     — KPIs globaux + courbe CA historique + prévisions
  2. Statistiques / an  — détail année par année (2021-2025)
  3. Prévisions 2026    — prévisions mensuelles + top produits interactif
  4. Matières premières — bon de commande interactif par mois
  5. Backtest           — validation du modèle
"""

import os
import sys
import json
import uuid
import subprocess
from functools import lru_cache

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from dash import Dash, dcc, html, dash_table, Input, Output, State, ctx, ALL, no_update

from paul_forecast import (explications, config as pf_config, matchs as pf_matchs,
                           forecast_journalier as pf_fj, couts as pf_couts,
                           commandes as pf_commandes, couverture as pf_couv,
                           assistant as pf_assistant)

# ── Chemins ───────────────────────────────────────────────────────────────────
RACINE  = os.path.dirname(os.path.abspath(__file__))
EXPORTS = os.path.join(RACINE, "exports")
MODELE  = "Holt_Winters"

# ── Palette PAUL ──────────────────────────────────────────────────────────────
C = {
    "noir":     "#1c1714",
    "noir2":    "#2a221c",
    "creme":    "#f3ecdd",
    "carte":    "#fbf9f3",
    "or":       "#b8904a",
    "or_clair": "#cda85f",
    "brun":     "#6f4e37",
    "bordure":  "#e6dcc7",
    "txt":      "#2b2320",
    "txt_doux": "#8a7d6b",
    "vert":     "#2e7d32",
    "rouge":    "#a8432f",
    "bleu":     "#1565c0",
    "blanc":    "#ffffff",
}
COLORWAY = ["#b8904a","#6f4e37","#3d7a3d","#a8432f","#8a7d6b",
            "#cda85f","#9c6b3f","#4f6f52","#7a5c45","#c0392b"]
SERIF = "'Playfair Display', Georgia, serif"
SANS  = "'Inter', system-ui, sans-serif"


# ═══════════════════════════════════════════════════════════════════════════════
# CHARGEMENT DES DONNÉES
# ═══════════════════════════════════════════════════════════════════════════════
import re

def _dossier_export_recent():
    """Dossier daté (YYYY-MM-DD) le plus récent sous exports/, sinon la racine."""
    if not os.path.isdir(EXPORTS):
        return EXPORTS
    dates = [d for d in os.listdir(EXPORTS)
             if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
             and os.path.isdir(os.path.join(EXPORTS, d))]
    if dates:
        return os.path.join(EXPORTS, max(dates))
    return EXPORTS


def lire(nom, **kw):
    # On cherche d'abord dans le dossier daté le plus récent, puis à la racine,
    # pour toujours refléter le dernier calcul même si la racine est périmée.
    for base in (_dossier_export_recent(), EXPORTS):
        p = os.path.join(base, nom)
        if os.path.exists(p):
            try:
                return pd.read_csv(p, sep=";", **kw)
            except Exception:
                continue
    return None


_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def mois_label(aaaa_mm):
    """'2026-01' -> 'janvier 2026'."""
    try:
        a, m = str(aaaa_mm).split("-")[:2]
        return f"{_MOIS_FR[int(m)]} {a}"
    except (ValueError, IndexError):
        return str(aaaa_mm)


def charger_plan():
    """Plan de production sécurisé du mois prochain (quantités à produire)."""
    plan = lire("plan_production_securise.csv")
    if plan is not None:
        for c in ("Prevision", "Fourchette_basse", "Quantite_recommandee", "Erreur_rel_%"):
            if c in plan.columns:
                plan[c] = pd.to_numeric(plan[c], errors="coerce")
    return plan


def charger_mrp_detail():
    """Besoins ingrédients détaillés par département/produit (traçabilité MRP).

    Colonnes : Date, Famille, Produit, Ingredient, Quantite_Requise. Permet de
    répondre à « pour produire JIVARA ce mois-ci, le rayon PÂTISSERIE a besoin de
    X g de farine, X g de sucre… ». None si le fichier n'existe pas encore
    (calcul à relancer). Écrit par pipeline.run() (besoins_ingredients_detail.csv).
    """
    det = lire("besoins_ingredients_detail.csv")
    if det is not None and "Date" in det.columns:
        det["Date"] = pd.to_datetime(det["Date"])
        det["Quantite_Requise"] = pd.to_numeric(det["Quantite_Requise"], errors="coerce")
    return det


def charger_journalier():
    """Prévisions journalières par produit (exports/previsions_journalieres.csv)."""
    p = os.path.join(EXPORTS, "previsions_journalieres.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, sep=";", parse_dates=["Date"])
    except Exception:
        return None
    for c in ("Qty_Prev", "Qty_Recommandee"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def charger_suivi():
    """Comparaison prévu vs réel des jours récents (exports/suivi_prevu_reel.csv)."""
    p = os.path.join(EXPORTS, "suivi_prevu_reel.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, sep=";", parse_dates=["Date"])
    except Exception:
        return None
    for c in ("Prev", "Reel"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def charger_tout():
    hist = lire("historique_agrege_global.csv")
    prev = lire("previsions_global.csv")
    det  = lire("previsions_detaillees_par_produit.csv")
    mrp  = lire("besoins_ingredients_planifies.csv")

    if hist is not None:
        hist["Date"] = pd.to_datetime(hist["Date"])
        hist = hist.sort_values("Date")

    if prev is not None:
        prev["Date"] = pd.to_datetime(prev["Date"])

    if det is not None:
        det["Date"] = pd.to_datetime(det["Date"])

    if mrp is not None:
        mrp["Date"] = pd.to_datetime(mrp["Date"])

    return hist, prev, det, mrp


# ── Événements (data/evenements.json, édité depuis le dashboard) ───────────────
EVT_PATH = os.path.join(RACINE, "data", "evenements.json")


def lire_evenements():
    """Liste des événements planifiés (depuis data/evenements.json)."""
    try:
        with open(EVT_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return doc.get("evenements", []) if isinstance(doc, dict) else (doc or [])


def ecrire_evenements(evs):
    """Réécrit la liste d'événements en préservant la description du fichier."""
    doc = {}
    try:
        with open(EVT_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["evenements"] = evs
    with open(EVT_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


# ── Commandes clients planifiées (data/commandes_clients.json) ─────────────────
CMD_PATH = os.path.join(RACINE, "data", "commandes_clients.json")


def lire_commandes():
    """Liste des commandes clients planifiées (depuis data/commandes_clients.json)."""
    try:
        with open(CMD_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return doc.get("commandes", []) if isinstance(doc, dict) else (doc or [])


def ecrire_commandes(cmds):
    """Réécrit la liste des commandes en préservant la description du fichier."""
    doc = {}
    try:
        with open(CMD_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["commandes"] = cmds
    with open(CMD_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


# ── Corrections manuelles par produit (data/ajustements_produits.json) ─────────
OVR_PATH = os.path.join(RACINE, "data", "ajustements_produits.json")


def lire_overrides():
    try:
        with open(OVR_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return doc.get("ajustements", []) if isinstance(doc, dict) else (doc or [])


def ecrire_overrides(ovrs):
    doc = {}
    try:
        with open(OVR_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["ajustements"] = ovrs
    with open(OVR_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def categories_connues():
    """Liste des catégories (familles) issues des données, pour les overrides."""
    _, _, det, _ = charger_tout()
    if det is not None and "Famille" in det.columns:
        cats = sorted(det["Famille"].dropna().astype(str).unique().tolist())
        if cats:
            return cats
    return ["BEVERAGE", "BOULANGERIE", "VIENNOISERIE", "PATISSERIE", "CUISINE",
            "MENU", "CONFISS/CHOCOLAT", "PAUL EXPRESS", "Prestation compl", "Autres"]


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS UI
# ═══════════════════════════════════════════════════════════════════════════════
def fig_base(titre="", height=420):
    return go.Figure().update_layout(
        template="plotly_white",
        title=dict(text=titre, font=dict(family=SERIF, size=17, color=C["txt"])),
        paper_bgcolor=C["carte"], plot_bgcolor=C["carte"],
        font=dict(family=SANS, color=C["txt"], size=12),
        colorway=COLORWAY,
        margin=dict(l=50, r=30, t=54, b=44),
        height=height,
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        xaxis=dict(gridcolor=C["bordure"], linecolor=C["bordure"]),
        yaxis=dict(gridcolor=C["bordure"], linecolor=C["bordure"]),
        hoverlabel=dict(bgcolor=C["blanc"], font=dict(family=SANS, color=C["txt"]),
                        bordercolor=C["or"]),
    )


def panneau(*children, style=None):
    s = {"background": C["carte"], "borderRadius": "10px", "padding": "24px",
         "marginBottom": "20px", "boxShadow": "0 2px 12px rgba(28,23,20,.07)",
         "border": f"1px solid {C['bordure']}"}
    if style:
        s.update(style)
    return html.Div(list(children), style=s)


def kpi(label, valeur, sous="", couleur=None):
    return html.Div(className="kpi-card", style={
        "background": C["carte"], "borderRadius": "10px", "padding": "20px 24px",
        "boxShadow": "0 2px 10px rgba(28,23,20,.08)", "border": f"1px solid {C['bordure']}",
        "minWidth": "160px", "flex": "1",
    }, children=[
        html.Div(label, style={"color": C["txt_doux"], "fontSize": "11px",
                               "fontWeight": "600", "letterSpacing": "1.2px",
                               "textTransform": "uppercase"}),
        html.Div(valeur, style={"color": couleur or C["or"], "fontFamily": SERIF,
                                "fontSize": "30px", "fontWeight": "700",
                                "margin": "6px 0 2px", "lineHeight": "1.15"}),
        html.Div(sous, style={"color": C["txt_doux"], "fontSize": "12px"}),
    ])


def rangee_kpis(*cards):
    return html.Div(list(cards), style={"display": "flex", "gap": "16px",
                                         "flexWrap": "wrap", "marginBottom": "20px"})


def titre_section(texte):
    return html.H3(texte, style={"fontFamily": SERIF, "color": C["txt"],
                                  "marginBottom": "16px", "fontSize": "20px"})


def legende(texte):
    return html.Div(texte, style={"color": C["txt_doux"], "fontSize": "12px",
                                   "fontStyle": "italic", "marginTop": "6px"})


def table_style(df, page=12):
    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": c, "id": c} for c in df.columns],
        page_size=page, sort_action="native", filter_action="native",
        style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"],
                      "fontWeight": "700", "fontFamily": SANS, "padding": "10px",
                      "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"],
                    "textAlign": "left", "padding": "8px 10px",
                    "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[{"if": {"row_index": "odd"},
                                  "backgroundColor": C["carte"]}],
    )


# Couleurs de pastille par niveau de fiabilité du plan de production.
_FIAB_COULEUR = {
    "Fiable":      C["vert"],
    "Moyen":       C["or"],
    "Incertain":   C["rouge"],
    "Hist. court": C["txt_doux"],
    "Peu vendu":   C["txt_doux"],   # quasi aucune vente récente : enjeu de production nul
}


def table_plan_production(df, page=20):
    """Tableau « À produire » : produit, quantité recommandée, fourchette, fiabilité."""
    cols = [
        {"name": "Produit",        "id": "Produit"},
        {"name": "Catégorie",      "id": "Catégorie"},
        {"name": "À produire",     "id": "À produire",     "type": "numeric"},
        {"name": "Prévision",      "id": "Prévision",      "type": "numeric"},
        {"name": "Mini",           "id": "Mini",           "type": "numeric"},
        {"name": "Fiabilité",      "id": "Fiabilité"},
    ]
    fiab_styles = [
        {"if": {"filter_query": f'{{Fiabilité}} = "{niv}"', "column_id": "Fiabilité"},
         "color": coul, "fontWeight": "700"}
        for niv, coul in _FIAB_COULEUR.items()
    ]
    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=cols,
        page_size=page, sort_action="native", filter_action="native",
        sort_by=[{"column_id": "À produire", "direction": "desc"}],
        style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"],
                      "fontWeight": "700", "fontFamily": SANS, "padding": "10px",
                      "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"],
                    "textAlign": "left", "padding": "8px 10px",
                    "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_cell_conditional=[
            {"if": {"column_id": c}, "textAlign": "right", "fontVariantNumeric": "tabular-nums"}
            for c in ("À produire", "Prévision", "Mini")
        ] + [{"if": {"column_id": "À produire"}, "fontWeight": "700", "color": C["brun"]}],
        style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": C["carte"]}]
                               + fiab_styles,
    )


_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def jour_label(ts):
    """Timestamp -> 'dimanche 29 juin 2026'."""
    ts = pd.Timestamp(ts)
    return f"{_JOURS_FR[ts.dayofweek]} {ts.day} {_MOIS_FR[ts.month]} {ts.year}"


def table_jour(df):
    """Tableau « À produire ce jour » : produit, catégorie, à produire, prévision, fiabilité."""
    cols = [
        {"name": "Produit",     "id": "Produit"},
        {"name": "Catégorie",   "id": "Catégorie"},
        {"name": "À produire",  "id": "À produire",  "type": "numeric"},
        {"name": "Prévision",   "id": "Prévision",   "type": "numeric"},
        {"name": "Fiabilité",   "id": "Fiabilité"},
    ]
    if "Dont commande" in df.columns:
        cols.insert(3, {"name": "Dont commande", "id": "Dont commande", "type": "numeric"})
    if "Habituel (boutique)" in df.columns:
        # décomposition d'un produit en nouveau régime (ex. client B2B récurrent) :
        # « Habituel » = demande boutique seule ; « Dont client récent » = l'écart
        i = next(i for i, c in enumerate(cols) if c["id"] == "Prévision") + 1
        cols[i:i] = [{"name": "Habituel (boutique)", "id": "Habituel (boutique)", "type": "numeric"},
                     {"name": "Dont client récent", "id": "Dont client récent", "type": "numeric"}]
    fiab_styles = [
        {"if": {"filter_query": f'{{Fiabilité}} = "{niv}"', "column_id": "Fiabilité"},
         "color": coul, "fontWeight": "700"}
        for niv, coul in _FIAB_COULEUR.items()
    ]
    return dash_table.DataTable(
        data=df.to_dict("records"), columns=cols,
        page_size=20, sort_action="native", filter_action="native",
        sort_by=[{"column_id": "À produire", "direction": "desc"}],
        style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"],
                      "fontWeight": "700", "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_cell_conditional=[
            {"if": {"column_id": c}, "textAlign": "right", "fontVariantNumeric": "tabular-nums"}
            for c in ("À produire", "Prévision", "Dont commande",
                      "Habituel (boutique)", "Dont client récent")
        ] + [{"if": {"column_id": "À produire"}, "fontWeight": "700", "color": C["brun"]},
             {"if": {"column_id": "Dont commande"}, "color": C["bleu"], "fontWeight": "600"},
             {"if": {"column_id": "Dont client récent"}, "color": C["bleu"], "fontWeight": "600"}],
        style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": C["carte"]}]
                               + fiab_styles,
    )


def jour_pour_table(dfj, jour, categorie=None):
    """Sous-ensemble du jour choisi, renommé pour l'affichage."""
    d = dfj[dfj["Date"] == pd.Timestamp(jour)]
    if categorie and categorie != "__all__" and "Famille" in d.columns:
        d = d[d["Famille"].astype(str) == str(categorie)]
    out = pd.DataFrame({
        "Produit":    d["Produit"].astype(str),
        "Catégorie":  d["Famille"].astype(str) if "Famille" in d.columns else "—",
        "À produire": d["Qty_Recommandee"].round().astype("Int64"),
        "Prévision":  d["Qty_Prev"].round().astype("Int64"),
        "Fiabilité":  d["Fiabilite"].astype(str) if "Fiabilite" in d.columns else "—",
    })
    if "Qty_Commande" in d.columns and (pd.to_numeric(d["Qty_Commande"],
                                                      errors="coerce").fillna(0) > 0).any():
        # visible uniquement quand une commande client tombe ce jour-là (0 → vide)
        cmd = pd.to_numeric(d["Qty_Commande"], errors="coerce").fillna(0).round().astype("Int64")
        out["Dont commande"] = cmd.where(cmd > 0).values
    if "Qty_Base" in d.columns:
        # produit en nouveau régime (ex. fast-food qui commande tous les jours) :
        # « Habituel » = demande boutique hors ce client, « Dont client récent » = l'écart.
        cmd0 = (pd.to_numeric(d["Qty_Commande"], errors="coerce").fillna(0)
                if "Qty_Commande" in d.columns else 0)
        prev_hors_cmd = pd.to_numeric(d["Qty_Prev"], errors="coerce").fillna(0) - cmd0
        base = pd.to_numeric(d["Qty_Base"], errors="coerce").fillna(0)
        ecart = (prev_hors_cmd - base).round().astype("Int64")
        if (ecart.fillna(0) > 0).any():
            out["Habituel (boutique)"] = base.round().astype("Int64").where(ecart > 0).values
            out["Dont client récent"] = ecart.where(ecart > 0).values
    return out[out["À produire"] > 0].sort_values("À produire", ascending=False).reset_index(drop=True)


def semaine_pour_table(dfj, debut, categorie=None, jours=7):
    """Cumul de production par produit sur `jours` jours à partir de `debut`."""
    fin = pd.Timestamp(debut) + pd.Timedelta(days=jours - 1)
    d = dfj[(dfj["Date"] >= pd.Timestamp(debut)) & (dfj["Date"] <= fin)]
    if categorie and categorie != "__all__" and "Famille" in d.columns:
        d = d[d["Famille"].astype(str) == str(categorie)]
    g = (d.groupby(["Produit", "Famille"], as_index=False)
           .agg(total=("Qty_Recommandee", "sum"), fiab=("Fiabilite", "last")))
    out = pd.DataFrame({
        "Produit":     g["Produit"].astype(str),
        "Catégorie":   g["Famille"].astype(str),
        "Semaine":     g["total"].round().astype("Int64"),
        "Moy./jour":   (g["total"] / jours).round().astype("Int64"),
        "Fiabilité":   g["fiab"].astype(str),
    })
    return out[out["Semaine"] > 0].sort_values("Semaine", ascending=False).reset_index(drop=True)


def table_semaine(df):
    cols = [{"name": "Produit", "id": "Produit"}, {"name": "Catégorie", "id": "Catégorie"},
            {"name": "Total semaine", "id": "Semaine", "type": "numeric"},
            {"name": "Moy./jour", "id": "Moy./jour", "type": "numeric"},
            {"name": "Fiabilité", "id": "Fiabilité"}]
    fiab_styles = [{"if": {"filter_query": f'{{Fiabilité}} = "{n}"', "column_id": "Fiabilité"},
                    "color": c, "fontWeight": "700"} for n, c in _FIAB_COULEUR.items()]
    return dash_table.DataTable(
        data=df.to_dict("records"), columns=cols,
        page_size=15, sort_action="native", filter_action="native",
        sort_by=[{"column_id": "Semaine", "direction": "desc"}], style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"], "fontWeight": "700",
                      "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "right",
                                 "fontVariantNumeric": "tabular-nums"} for c in ("Semaine", "Moy./jour")]
                               + [{"if": {"column_id": "Semaine"}, "fontWeight": "700", "color": C["brun"]}],
        style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": C["carte"]}] + fiab_styles,
    )


def table_alertes(df):
    if df is None or df.empty:
        return html.Div("Aucun produit en changement de niveau marqué.",
                        style={"color": C["txt_doux"], "padding": "8px"})
    cols = [{"name": c, "id": c} for c in df.columns]
    return dash_table.DataTable(
        data=df.to_dict("records"), columns=cols, page_size=10, sort_action="native",
        style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"], "fontWeight": "700",
                      "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": C["carte"]},
            {"if": {"filter_query": '{Variation} contains "+"', "column_id": "Variation"},
             "color": C["vert"], "fontWeight": "700"},
            {"if": {"filter_query": '{Variation} contains "-"', "column_id": "Variation"},
             "color": C["rouge"], "fontWeight": "700"},
        ],
    )


def table_overrides(ovrs):
    lignes = []
    for o in ovrs:
        mode = o.get("mode", "facteur")
        val = o.get("valeur")
        effet = (f"×{val}" if mode == "facteur" else f"= {val}/jour")
        lignes.append({"id": o.get("id", o.get("produit", "")),
                       "Produit": o.get("produit", ""),
                       "Correction": effet})
    return dash_table.DataTable(
        id="table-overrides", data=lignes,
        columns=[{"name": "Produit", "id": "Produit"}, {"name": "Correction", "id": "Correction"}],
        page_size=8, style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"], "fontWeight": "700",
                      "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": C["carte"]}],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET 1 — VUE D'ENSEMBLE
# ═══════════════════════════════════════════════════════════════════════════════
def onglet_apercu():
    hist, prev, det, _ = charger_tout()

    # ── KPIs ──────────────────────────────────────────────────────────────────
    cards = []
    if hist is not None and not hist.empty:
        ca_total  = hist["Chiffre_Affaires_Total"].sum()
        ca_moy    = hist["Chiffre_Affaires_Total"].mean()
        meilleur  = hist.loc[hist["Chiffre_Affaires_Total"].idxmax()]
        n_annees  = hist["Date"].dt.year.nunique()
        cards += [
            kpi("CA total historique", f"{ca_total/1e6:.2f} M MAD", f"{n_annees} années de données (HT)"),
            kpi("CA mensuel moyen",    f"{ca_moy:,.0f} MAD".replace(",", " "), "2021 – 2025 · HT"),
            kpi("Meilleur mois", mois_label(meilleur["Date"].strftime("%Y-%m")).capitalize(),
                f"{meilleur['Chiffre_Affaires_Total']:,.0f} MAD HT".replace(",", " ")),
        ]
    if prev is not None and not prev.empty:
        # Même source que l'Accueil et la synthèse : la colonne Selection
        # (meilleur modèle par produit, réconciliée), repli Holt-Winters.
        col_rev = "Rev_Prev_Selection" if "Rev_Prev_Selection" in prev.columns \
                  else f"Rev_Prev_{MODELE}"
        if col_rev in prev.columns:
            ca_an_prev = prev[col_rev].sum()
            m0 = prev.iloc[0]
            sous_modele = ("sélection par produit · 12 mois · HT"
                           if col_rev == "Rev_Prev_Selection" else "Holt-Winters · 12 mois · HT")
            cards.append(kpi("CA prévu 2026", f"{ca_an_prev/1e6:.2f} M MAD",
                              sous_modele, couleur=C["brun"]))
            ca_m0 = float(m0[col_rev])
            cards.append(kpi("Prochain mois",
                              mois_label(pd.Timestamp(m0["Date"]).strftime("%Y-%m")).capitalize(),
                              (f"{ca_m0:,.0f} MAD HT ≈ {ca_m0 * _ratio_ttc_ht():,.0f} TTC"
                               .replace(",", " ")), couleur=C["brun"]))

    # ── Graphe CA historique + prévisions + IC ─────────────────────────────
    fig = fig_base("Chiffre d'affaires mensuel (HT) — Historique & Prévisions 2026", height=460)
    if hist is not None:
        fig.add_trace(go.Scatter(
            x=hist["Date"], y=hist["Chiffre_Affaires_Total"],
            name="Historique réel", fill="tozeroy",
            fillcolor="rgba(111,78,55,.10)", line=dict(color=C["brun"], width=2.4),
            hovertemplate="%{x|%b %Y}<br>%{y:,.0f} MAD<extra></extra>",
        ))
    if prev is not None:
        col_rev = f"Rev_Prev_{MODELE}"
        col_dec = "Rev_Prev_Decompo_Saisonniere"
        if col_rev in prev.columns:
            vals = prev[col_rev].values
            mape = 0.143
            n    = len(vals)
            spread = np.linspace(1.0, 1.5, n)
            ci_hi  = vals * (1 + mape * spread)
            ci_lo  = vals * (1 - mape * spread)
            # IC
            fig.add_trace(go.Scatter(
                x=pd.concat([prev["Date"], prev["Date"][::-1]]),
                y=np.concatenate([ci_hi, ci_lo[::-1]]),
                fill="toself", fillcolor="rgba(184,144,74,.15)",
                line=dict(width=0), showlegend=True,
                name="IC ±14% (MAPE backtest)",
                hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=prev["Date"], y=prev[col_rev],
                name="Prévision Holt-Winters",
                line=dict(color=C["or"], width=2.6, dash="dash"),
                hovertemplate="%{x|%b %Y}<br>%{y:,.0f} MAD<extra></extra>",
            ))
        if col_dec in prev.columns:
            fig.add_trace(go.Scatter(
                x=prev["Date"], y=prev[col_dec],
                name="Décompo Saisonnière",
                line=dict(color="#8e44ad", width=1.5, dash="dot"), opacity=0.7,
                hovertemplate="%{x|%b %Y}<br>%{y:,.0f} MAD<extra></extra>",
            ))

    # Ligne de séparation historique / prévision
    if hist is not None and prev is not None:
        fig.add_vline(x=hist["Date"].iloc[-1], line_dash="dot",
                      line_color=C["txt_doux"], opacity=0.5)
        fig.add_annotation(x=hist["Date"].iloc[-1], y=1, yref="paper",
                           text="→ Prévisions", showarrow=False,
                           font=dict(color=C["txt_doux"], size=11), xshift=6)

    fig.update_yaxes(tickformat=",.0f", ticksuffix=" MAD")

    # ── Ombrage des périodes de fête + annotations des pics/creux ──────────────
    dates_deco, vals_deco = [], []
    if hist is not None and not hist.empty:
        dates_deco += list(hist["Date"])
        vals_deco  += list(hist["Chiffre_Affaires_Total"])
    if prev is not None and not prev.empty:
        col_rev = f"Rev_Prev_{MODELE}"
        if col_rev in prev.columns:
            dates_deco += list(prev["Date"])
            vals_deco  += list(prev[col_rev])
    if dates_deco:
        explications.decorer_figure(fig, dates_deco, vals_deco)

    return html.Div([
        rangee_kpis(*cards),
        panneau(dcc.Graph(figure=fig, config={"displayModeBar": False}),
                legende("Bande orange = intervalle de confiance ±14% (MAPE mesuré en backtest 2025)"
                        " · Zones ombrées = Ramadan / Aïd")),
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET 2 — STATISTIQUES PAR ANNÉE
# ═══════════════════════════════════════════════════════════════════════════════
def onglet_stats_annuelles():
    hist, _, det, _ = charger_tout()

    if hist is None or hist.empty:
        return html.Div("Historique indisponible.", style={"color": C["txt_doux"], "padding": "40px"})

    hist["Annee"] = hist["Date"].dt.year
    stats = hist.groupby("Annee").agg(
        CA_Total=("Chiffre_Affaires_Total", "sum"),
        CA_Moyen=("Chiffre_Affaires_Total", "mean"),
        CA_Max=("Chiffre_Affaires_Total", "max"),
        CA_Min=("Chiffre_Affaires_Total", "min"),
        QT_Total=("Quantite_Total", "sum"),
    ).reset_index()
    stats["Croissance_%"] = stats["CA_Total"].pct_change().mul(100).round(1)

    # ── Graphe 1 : CA annuel en barres ────────────────────────────────────────
    fig_bar = fig_base("CA annuel total (MAD)", height=380)
    couleurs_bar = [C["or"] if a == stats["Annee"].max() else C["brun"] for a in stats["Annee"]]
    fig_bar.add_trace(go.Bar(
        x=stats["Annee"].astype(str), y=stats["CA_Total"],
        marker_color=couleurs_bar,
        text=[f"{v/1e6:.2f} M" for v in stats["CA_Total"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>CA : %{y:,.0f} MAD<extra></extra>",
    ))
    # Flèches de croissance
    for i, row in stats.iterrows():
        if not pd.isna(row["Croissance_%"]):
            couleur_c = C["vert"] if row["Croissance_%"] >= 0 else C["rouge"]
            symbole   = "▲" if row["Croissance_%"] >= 0 else "▼"
            fig_bar.add_annotation(
                x=str(int(row["Annee"])), y=row["CA_Total"],
                text=f"{symbole} {abs(row['Croissance_%']):.1f}%",
                showarrow=False, yshift=32,
                font=dict(color=couleur_c, size=12, family=SANS),
            )
    fig_bar.update_yaxes(tickformat=",.0f", ticksuffix=" MAD")

    # ── Graphe 2 : Saisonnalité mensuelle superposée par année ────────────────
    fig_sais = fig_base("Profil saisonnier mensuel — comparaison par année", height=420)
    mois_labels = ["Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]
    hist["Mois_num"] = hist["Date"].dt.month
    for i, annee in enumerate(sorted(hist["Annee"].unique())):
        df_a = hist[hist["Annee"] == annee].sort_values("Mois_num")
        epaisseur = 2.8 if annee == hist["Annee"].max() else 1.8
        dash_style = "solid" if annee == hist["Annee"].max() else "dot"
        fig_sais.add_trace(go.Scatter(
            x=df_a["Mois_num"], y=df_a["Chiffre_Affaires_Total"],
            name=str(annee), mode="lines+markers",
            line=dict(color=COLORWAY[i % len(COLORWAY)], width=epaisseur, dash=dash_style),
            marker=dict(size=5),
            hovertemplate=f"<b>{annee}</b> · %{{text}}<br>%{{y:,.0f}} MAD<extra></extra>",
            text=[mois_labels[m-1] for m in df_a["Mois_num"]],
        ))
    fig_sais.update_xaxes(tickvals=list(range(1,13)), ticktext=mois_labels)
    fig_sais.update_yaxes(tickformat=",.0f", ticksuffix=" MAD")

    # ── Graphe 3 : CA mensuel min/moyen/max par mois ──────────────────────────
    hist_mois = hist.groupby("Mois_num")["Chiffre_Affaires_Total"].agg(["min","mean","max"]).reset_index()
    fig_range  = fig_base("Fourchette mensuelle (min / moyenne / max sur 2021-2025)", height=380)
    fig_range.add_trace(go.Scatter(
        x=hist_mois["Mois_num"], y=hist_mois["max"],
        name="Maximum", line=dict(color=C["or_clair"], width=0), showlegend=True,
        fill=None, hoverinfo="skip",
    ))
    fig_range.add_trace(go.Scatter(
        x=hist_mois["Mois_num"], y=hist_mois["min"],
        name="Fourchette min–max", fill="tonexty",
        fillcolor="rgba(184,144,74,.15)", line=dict(width=0), hoverinfo="skip",
    ))
    fig_range.add_trace(go.Scatter(
        x=hist_mois["Mois_num"], y=hist_mois["mean"],
        name="Moyenne", line=dict(color=C["brun"], width=2.5),
        hovertemplate="%{text}<br>Moy : %{y:,.0f} MAD<extra></extra>",
        text=mois_labels,
    ))
    fig_range.update_xaxes(tickvals=list(range(1,13)), ticktext=mois_labels)
    fig_range.update_yaxes(tickformat=",.0f", ticksuffix=" MAD")

    # ── Tableau récap annuel ──────────────────────────────────────────────────
    df_table = stats.copy()
    df_table["CA Total (MAD)"]   = df_table["CA_Total"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    df_table["CA Moyen/mois"]    = df_table["CA_Moyen"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    df_table["Meilleur mois"]    = df_table["CA_Max"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    df_table["Plus bas mois"]    = df_table["CA_Min"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
    df_table["Quantité totale"]  = df_table["QT_Total"].apply(lambda x: f"{x:,.0f}".replace(",", " ") if pd.notna(x) else "—")
    df_table["Croissance %"]     = df_table["Croissance_%"].apply(
        lambda x: f"▲ +{x:.1f}%" if (pd.notna(x) and x >= 0) else (f"▼ {x:.1f}%" if pd.notna(x) else "—"))
    df_table = df_table[["Annee","CA Total (MAD)","CA Moyen/mois","Meilleur mois",
                          "Plus bas mois","Quantité totale","Croissance %"]]
    df_table.columns = ["Année","CA Total (MAD)","Moy/mois (MAD)","Pic mensuel",
                        "Creux mensuel","Qté totale","Croissance"]

    # ── Top produits par année (si dispo) ─────────────────────────────────────
    section_top = []
    if det is not None and not det.empty:
        col_rev = f"Rev_Prev_{MODELE}"
        if col_rev in det.columns:
            det["Annee"] = det["Date"].dt.year
            top_par_an = (det.groupby(["Annee","Produit"])[col_rev]
                          .sum().reset_index()
                          .sort_values([" Annee", col_rev], ascending=[True, False])
                          if " Annee" in det.columns
                          else det.groupby(["Annee","Produit"])[col_rev]
                          .sum().reset_index()
                          .sort_values(["Annee", col_rev], ascending=[True, False]))

            annees_dispo = sorted(top_par_an["Annee"].unique(), reverse=True)
            # On affiche sous forme de tableau côte à côte pour les 2 dernières années
            cols_top = []
            for annee in annees_dispo[:3]:
                df_top5 = top_par_an[top_par_an["Annee"] == annee].head(5)[["Produit", col_rev]]
                df_top5.columns = ["Produit", "CA (MAD)"]
                df_top5["CA (MAD)"] = df_top5["CA (MAD)"].apply(lambda x: f"{x:,.0f}".replace(",", " "))
                cols_top.append(html.Div([
                    html.H4(f"Top 5 — {annee}", style={"fontFamily": SERIF, "color": C["txt"],
                                                        "marginBottom": "10px", "fontSize": "16px"}),
                    table_style(df_top5, page=5),
                ], style={"flex": "1", "minWidth": "240px"}))

            if cols_top:
                section_top = [panneau(
                    titre_section("Top 5 produits par CA — par année"),
                    html.Div(cols_top, style={"display": "flex", "gap": "20px", "flexWrap": "wrap"}),
                )]

    return html.Div([
        panneau(
            titre_section("Résumé annuel"),
            table_style(df_table, page=10),
        ),
        html.Div([
            panneau(dcc.Graph(figure=fig_bar, config={"displayModeBar": False}),
                    style={"flex": "1", "minWidth": "300px"}),
            panneau(dcc.Graph(figure=fig_range, config={"displayModeBar": False}),
                    style={"flex": "1", "minWidth": "300px"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}),
        panneau(dcc.Graph(figure=fig_sais, config={"displayModeBar": False}),
                legende("Le trait plein = année la plus récente.")),
        *section_top,
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET — CHIFFRE D'AFFAIRES (tout le CA regroupé : historique + prévisions + stats)
# ═══════════════════════════════════════════════════════════════════════════════
def panneau_marges():
    """Marge matière par produit : prix de vente constaté − coût matières estimé."""
    from paul_forecast import marges as pf_marges
    t = pf_marges.table_marges()
    if t is None or t.empty:
        return html.Div()
    ok = t[t["Alerte"] == ""]
    fc_med = ok["FoodCost_pct"].median()
    top = ok.nlargest(15, "Marge_totale_MAD")

    fig = fig_base("Top 15 contributeurs à la marge matière (fenêtre 120 j)", height=400)
    fig.add_trace(go.Bar(
        x=top["Marge_totale_MAD"], y=top["Produit"], orientation="h",
        marker_color=C["or"],
        hovertemplate="%{y}<br>Marge totale : %{x:,.0f} MAD<extra></extra>"))
    fig.update_layout(yaxis=dict(autorange="reversed"))
    fig.update_xaxes(title="Marge matière cumulée (MAD)")

    aff = t.rename(columns={
        "Famille": "Catégorie", "Volume_recent": "Volume 120 j",
        "Prix_vente_MAD": "Prix vente", "Cout_matiere_MAD": "Coût matière",
        "Marge_MAD": "Marge/u", "FoodCost_pct": "Food-cost %",
        "Source_recette": "Recette",
    })[["Produit", "Catégorie", "Volume 120 j", "Prix vente", "Coût matière",
        "Marge/u", "Food-cost %", "Recette", "Alerte"]]

    table = dash_table.DataTable(
        data=aff.to_dict("records"),
        columns=[{"name": c, "id": c} for c in aff.columns],
        page_size=14, sort_action="native", filter_action="native", style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"], "fontWeight": "700",
                      "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS,
                    "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": C["carte"]},
            {"if": {"column_id": "Marge/u"}, "fontWeight": "700", "color": C["vert"]},
            {"if": {"filter_query": '{Alerte} != ""', "column_id": "Alerte"},
             "color": C["rouge"], "fontWeight": "600"},
        ])

    return panneau(
        titre_section("Marges par produit (matière)"),
        rangee_kpis(
            kpi("Produits chiffrés", str(len(t)), "recette + prix connus"),
            kpi("Food-cost médian", f"{fc_med:.0f} %", "coût matières / prix de vente"),
            kpi("Meilleur contributeur", str(top.iloc[0]["Produit"])[:22],
                f"{top.iloc[0]['Marge_totale_MAD']:,.0f} MAD de marge".replace(",", " "),
                couleur=C["brun"]),
        ),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
        html.Div(table, style={"marginTop": "14px"}),
        legende("Marge MATIÈRE indicative : prix de vente TTC constaté (120 derniers jours) − "
                "coût matières estimé (recettes provisoires, prix estimés). Main-d'œuvre, "
                "énergie et emballages non déduits. Les lignes en alerte (prix quasi nul, "
                "food-cost anormal) sont à vérifier, pas à prendre au pied de la lettre."),
    )


def onglet_ca():
    """Regroupe en un seul onglet tout l'affichage orienté chiffre d'affaires."""
    def _enfants(div):
        c = getattr(div, "children", [])
        return c if isinstance(c, list) else [c]

    return html.Div([
        html.P("Tout le suivi du chiffre d'affaires (historique et prévisions) est regroupé ici. "
               "Le cœur du tableau de bord — ce qu'il faut produire et commander — est dans les "
               "onglets « Production journalière », « Production mensuelle » et « Matières premières ».",
               style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "20px"}),
        *_enfants(onglet_apercu()),
        panneau_marges(),
        *_enfants(onglet_stats_annuelles()),
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET 3 — PRODUCTION À PRÉVOIR (quantités par produit)
# ═══════════════════════════════════════════════════════════════════════════════
def plan_pour_table(plan, categorie=None):
    """Sous-ensemble du plan, renommé pour l'affichage « À produire »."""
    df = plan
    if categorie and categorie != "__all__" and "Famille" in df.columns:
        df = df[df["Famille"].astype(str) == str(categorie)]
    out = pd.DataFrame({
        "Produit":   df["Produit"].astype(str),
        "Catégorie": df["Famille"].astype(str) if "Famille" in df.columns else "—",
        "À produire": df["Quantite_recommandee"].round().astype("Int64"),
        "Prévision":  df["Prevision"].round().astype("Int64"),
        "Mini":       df["Fourchette_basse"].round().astype("Int64"),
        "Fiabilité":  df["Fiabilite"].astype(str) if "Fiabilite" in df.columns else "—",
    })
    return out.sort_values("À produire", ascending=False).reset_index(drop=True)


def onglet_previsions():
    _, _, det, _ = charger_tout()
    plan = charger_plan()

    if plan is None or plan.empty:
        return html.Div(
            "Plan de production indisponible — relancez le calcul (plan_production_securise.csv).",
            style={"padding": "40px", "color": C["txt_doux"]})

    mois = mois_label(plan["Mois"].iloc[0]) if "Mois" in plan.columns else "le mois prochain"

    # ── KPIs orientés production ───────────────────────────────────────────────
    total_a_produire = int(plan["Quantite_recommandee"].fillna(0).round().sum())
    n_produits       = plan["Produit"].nunique()
    top              = plan.loc[plan["Quantite_recommandee"].idxmax()]
    if "Fiabilite" in plan.columns and len(plan):
        pct_fiable = 100 * (plan["Fiabilite"] == "Fiable").mean()
        sous_fiab  = f"{(plan['Fiabilite'] == 'Fiable').sum()} produits fiables / {len(plan)}"
    else:
        pct_fiable, sous_fiab = 0, ""

    cards = [
        kpi("À produire", f"{total_a_produire:,.0f}".replace(",", " "),
            f"unités · {mois}"),
        kpi("Produits à préparer", f"{n_produits:,.0f}".replace(",", " "),
            "références distinctes", couleur=C["brun"]),
        kpi("Produit n°1", str(top["Produit"])[:22],
            f"{int(round(top['Quantite_recommandee'])):,.0f} unités".replace(",", " ")),
        kpi("Prévisions fiables", f"{pct_fiable:.0f} %", sous_fiab, couleur=C["brun"]),
    ]

    # ── Catégories pour le filtre du tableau ──────────────────────────────────
    cats_plan = (sorted(plan["Famille"].dropna().astype(str).unique().tolist())
                 if "Famille" in plan.columns else [])
    options_cat = [{"label": "Toutes les catégories", "value": "__all__"}] \
                  + [{"label": c, "value": c} for c in cats_plan]

    # ── Sélecteur catégorie → produit pour le détail 12 mois ──────────────────
    a_famille = det is not None and "Famille" in det.columns
    if a_famille:
        categories = sorted(det["Famille"].dropna().astype(str).unique().tolist())
        cat0 = categories[0] if categories else None
        produits0 = (sorted(det.loc[det["Famille"].astype(str) == cat0, "Produit"]
                            .astype(str).unique().tolist()) if cat0 else [])
    else:
        categories, cat0 = [], None
        produits0 = (sorted(det["Produit"].astype(str).unique().tolist())
                     if det is not None and "Produit" in det.columns else [])

    largeur_demi = {"flex": "1", "minWidth": "240px"}
    selecteurs = []
    if a_famille:
        selecteurs.append(html.Div([
            html.Label("1. Choisir une catégorie :",
                       style={"fontWeight":"600","marginBottom":"8px","display":"block"}),
            dcc.Dropdown(id="dd-categorie-prev", clearable=False,
                         options=[{"label": c, "value": c} for c in categories],
                         value=cat0, style={"fontFamily": SANS}),
        ], style=largeur_demi))
    selecteurs.append(html.Div([
        html.Label(("2. Choisir un produit :" if a_famille else "Choisir un produit :"),
                   style={"fontWeight":"600","marginBottom":"8px","display":"block"}),
        dcc.Dropdown(id="dd-produit-prev", clearable=False,
                     options=[{"label": p, "value": p} for p in produits0],
                     value=produits0[0] if produits0 else None,
                     style={"fontFamily": SANS}),
    ], style=largeur_demi))

    return html.Div([
        rangee_kpis(*cards),
        panneau(
            titre_section(f"À produire — {mois}"),
            html.Div([
                html.Label("Filtrer par catégorie :",
                           style={"fontWeight":"600","marginBottom":"8px","display":"block"}),
                dcc.Dropdown(id="dd-cat-plan", clearable=False, options=options_cat,
                             value="__all__",
                             style={"fontFamily": SANS, "maxWidth": "320px"}),
            ], style={"marginBottom": "16px"}),
            html.Div(table_plan_production(plan_pour_table(plan)), id="zone-table-plan"),
            legende("« À produire » = quantité recommandée (prévision + stock de sécurité). "
                    "« Mini » = plancher prudent. Tape dans l'en-tête d'une colonne pour filtrer, "
                    "clique pour trier. Fiabilité : vert = fiable, orange = moyen, "
                    "rouge = incertain, gris = historique court."),
        ),
        panneau(
            titre_section("Détail d'un produit — quantités sur 12 mois"),
            html.Div(selecteurs, style={"display": "flex", "gap": "16px",
                                        "flexWrap": "wrap", "marginBottom": "16px"}),
            dcc.Graph(id="g-detail-produit"),
        ),
    ])


# ── Explication des pics des courbes journalières ─────────────────────────────
def cause_speciale_jour(d):
    """Label si le jour `d` est spécial (match / événement / fête), sinon None."""
    d = pd.Timestamp(d)
    for m in pf_matchs.charger_matchs():
        try:
            if pd.Timestamp(m["date"]) == d:
                adv = (m.get("adversaire") or "").strip()
                return "⚽ Match" + (f" {adv}" if adv else "")
        except (KeyError, ValueError, TypeError):
            continue
    for e in lire_evenements():
        try:
            deb = pd.Timestamp(e["date"]); fin = pd.Timestamp(e.get("date_fin") or e["date"])
            if deb <= d <= fin:
                return "★ " + (e.get("nom") or "Événement")
        except (KeyError, ValueError, TypeError):
            continue
    for f in pf_config.FETES_MAROCAINES:
        try:
            if pd.Timestamp(f["debut"]) <= d <= pd.Timestamp(f["fin"]):
                return "🌙 " + (f.get("nom") or f.get("type") or "Fête")
        except (KeyError, ValueError, TypeError):
            continue
    return None


def expliquer_courbe_jour(fig, dates, valeurs):
    """Annote les pics d'une courbe journalière (jours spéciaux + jour fort récurrent).

    Retourne une phrase résumant le rythme hebdomadaire (jour fort / faible).
    """
    s = pd.Series([float(v) for v in valeurs], index=pd.to_datetime(list(dates))).sort_index()
    if len(s) < 7:
        return ""
    causes = {d: cause_speciale_jour(d) for d in s.index}
    moy = s.mean()

    # 1) jours spéciaux (match/événement/fête) : chaque cause une seule fois, à son pic
    pics_cause = {}
    for d, v in s.items():
        lab = causes[d]
        if lab and v >= moy and (lab not in pics_cause or v > pics_cause[lab][1]):
            pics_cause[lab] = (d, v)
    for lab, (d, v) in list(pics_cause.items())[:6]:
        fig.add_annotation(x=d, y=v, text=lab, showarrow=True, arrowhead=2,
                           arrowcolor=C["or"], ax=0, ay=-32,
                           font=dict(size=10, color=C["rouge"], family=SANS),
                           bgcolor="rgba(251,248,241,.96)", bordercolor=C["or"], borderwidth=1)

    # 2) pic récurrent : nom du jour fort, seulement si le rythme hebdo est marqué (≥ +15 %)
    ordinaires = s[[causes[d] is None for d in s.index]]
    wk = s.groupby(s.index.dayofweek).mean()
    jf, jb = int(wk.idxmax()), int(wk.idxmin())
    ratio = wk.max() / moy if moy > 0 else 1.0
    if len(ordinaires) and ratio >= 1.15:
        d_max = ordinaires.idxmax()
        fig.add_annotation(
            x=d_max, y=ordinaires.max(),
            text=f"{_JOURS_FR[d_max.dayofweek]} — jour fort",
            showarrow=True, arrowhead=2, arrowcolor=C["vert"], ax=0, ay=-34,
            font=dict(size=10, color=C["vert"], family=SANS),
            bgcolor="rgba(251,248,241,.96)", bordercolor=C["vert"], borderwidth=1)

    return (f"Rythme hebdo : pic le {_JOURS_FR[jf]} (~{wk.max():.0f}/j, ×{ratio:.1f} la moyenne), "
            f"creux le {_JOURS_FR[jb]} (~{wk.min():.0f}/j). Les jours de match / fête / événement "
            f"sont annotés sur la courbe.")


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET — PRODUCTION JOURNALIÈRE (combien produire chaque jour, par produit)
# ═══════════════════════════════════════════════════════════════════════════════
def onglet_journalier():
    dfj = charger_journalier()

    if dfj is None or dfj.empty:
        return html.Div([
            html.P("Les prévisions journalières n'ont pas encore été calculées.",
                   style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "16px"}),
            html.Button("⚙ Générer les prévisions journalières", id="btn-gen-jour",
                        n_clicks=0, style={
                            "background": C["or"], "border": "none", "color": C["noir"],
                            "borderRadius": "6px", "padding": "10px 22px", "cursor": "pointer",
                            "fontFamily": SANS, "fontWeight": "700", "fontSize": "14px"}),
            dcc.Loading(html.Div(id="zone-statut-jour", style={"marginTop": "12px"}),
                        type="circle", color=C["or"]),
        ], style={"padding": "20px"})

    jours = sorted(dfj["Date"].unique())
    # Jour par défaut = aujourd'hui (ou le 1er jour futur disponible) ; si les
    # prévisions datent (fichier non recalculé), on retombe sur leur 1er jour.
    aujourdhui = pd.Timestamp.now().normalize()
    futurs = [j for j in jours if pd.Timestamp(j) >= aujourdhui]
    jour0 = futurs[0] if futurs else jours[0]
    cats = sorted(dfj["Famille"].dropna().astype(str).unique().tolist()) if "Famille" in dfj.columns else []
    opt_cat = [{"label": "Toutes les catégories", "value": "__all__"}] \
              + [{"label": c, "value": c} for c in cats]
    produits = sorted(dfj["Produit"].astype(str).unique().tolist())

    # KPIs du jour par défaut (aujourd'hui, demain, ou 1er jour prévu)
    d0 = dfj[dfj["Date"] == jour0]
    total0 = int(d0["Qty_Recommandee"].fillna(0).round().sum())
    nprod0 = int((d0["Qty_Recommandee"].fillna(0) > 0).sum())
    if pd.Timestamp(jour0) == aujourdhui:
        libelle0 = "À produire aujourd'hui"
    elif pd.Timestamp(jour0) == aujourdhui + pd.Timedelta(days=1):
        libelle0 = "À produire demain"
    else:
        libelle0 = "À produire (1er jour prévu)"

    # Total à produire par jour sur l'horizon (week-ends teintés)
    par_jour = dfj.groupby("Date")["Qty_Recommandee"].sum().reset_index()
    fig_total = fig_base("", height=320)
    couleurs = [C["or_clair"] if pd.Timestamp(d).dayofweek >= 5 else C["or"]
                for d in par_jour["Date"]]
    fig_total.add_trace(go.Bar(
        x=par_jour["Date"], y=par_jour["Qty_Recommandee"], marker_color=couleurs,
        hovertemplate="%{x|%a %d/%m}<br>%{y:,.0f} unités<extra></extra>"))
    fig_total.update_yaxes(title="Unités/jour", rangemode="tozero")
    fig_total.update_layout(showlegend=False)
    texte_total = expliquer_courbe_jour(fig_total, par_jour["Date"], par_jour["Qty_Recommandee"])
    cards = [
        kpi(libelle0, f"{total0:,.0f}".replace(",", " "), jour_label(jour0)),
        kpi("Références à préparer", f"{nprod0:,.0f}".replace(",", " "),
            "produits avec quantité", couleur=C["brun"]),
        kpi("Horizon", f"{len(jours)} jours",
            f"jusqu'au {pd.Timestamp(jours[-1]).strftime('%d/%m/%Y')}", couleur=C["brun"]),
    ]

    btn_sec = {"background": "transparent", "border": f"1px solid {C['or']}",
               "color": C["or"], "borderRadius": "6px", "padding": "8px 16px",
               "cursor": "pointer", "fontFamily": SANS, "fontWeight": "700", "fontSize": "13px"}
    btn_or = {"background": C["or"], "border": "none", "color": C["noir"],
              "borderRadius": "6px", "padding": "9px 20px", "cursor": "pointer",
              "fontFamily": SANS, "fontWeight": "700", "fontSize": "14px"}

    return html.Div([
        html.P("Combien produire chaque jour, par produit. Choisis un jour pour le plan du jour, "
               "exporte la fiche, surveille les produits qui changent de niveau, et corrige à la main "
               "si besoin.",
               style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "20px"}),
        rangee_kpis(*cards),
        dcc.Download(id="dl-jour"), dcc.Download(id="dl-semaine"),
        dcc.Store(id="store-overrides", data=lire_overrides()),
        panneau(
            titre_section("À produire — jour choisi"),
            html.Div([
                html.Div([
                    html.Label("Jour :", style={"fontWeight": "600", "fontSize": "12px",
                                                "marginBottom": "6px", "display": "block",
                                                "color": C["txt_doux"]}),
                    dcc.DatePickerSingle(
                        id="dd-jour", display_format="DD/MM/YYYY",
                        min_date_allowed=jours[0], max_date_allowed=jours[-1],
                        date=pd.Timestamp(jour0).strftime("%Y-%m-%d")),
                ], style={"minWidth": "180px"}),
                html.Div([
                    html.Label("Catégorie :", style={"fontWeight": "600", "fontSize": "12px",
                                                     "marginBottom": "6px", "display": "block",
                                                     "color": C["txt_doux"]}),
                    dcc.Dropdown(id="dd-cat-jour", clearable=False, options=opt_cat,
                                 value="__all__", style={"fontFamily": SANS}),
                ], style={"flex": "1", "minWidth": "240px"}),
                html.Div([
                    html.Label(" ", style={"fontSize": "12px", "marginBottom": "6px",
                                                "display": "block"}),
                    html.Button("Exporter ce jour (Excel)", id="btn-export-jour",
                                n_clicks=0, style=btn_sec),
                ], style={"alignSelf": "start"}),
            ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap",
                      "alignItems": "start", "marginBottom": "16px"}),
            html.H4(id="titre-jour", style={"fontFamily": SERIF, "color": C["txt"],
                                            "fontSize": "16px", "marginBottom": "12px"}),
            html.Div(table_jour(jour_pour_table(dfj, jour0)), id="zone-table-jour"),
            legende("« À produire » = prévision + marge de sécurité. Fiabilité (mesurée par "
                    "backtest glissant des 28 derniers jours, modèle réajusté chaque semaine "
                    "comme en production) : vert = fiable, orange = moyen, rouge = incertain, "
                    "gris = « Peu vendu » (quasi aucune vente récente, enjeu nul) ou "
                    "« Hist. court » (pas assez d'historique pour mesurer). "
                    "Quand un produit a un gros client récurrent récent (ex. FLUTE 250GR), "
                    "deux colonnes s'ajoutent : « Habituel (boutique) » = ce qu'il faudrait "
                    "produire SANS ce client, « Dont client récent » = la part qui vient de lui."),
        ),
        panneau(
            titre_section("Production de la semaine (7 jours)"),
            html.Div([
                html.Div([
                    html.Label("Catégorie :", style={"fontWeight": "600", "fontSize": "12px",
                                                     "marginBottom": "6px", "display": "block",
                                                     "color": C["txt_doux"]}),
                    dcc.Dropdown(id="dd-cat-semaine", clearable=False, options=opt_cat,
                                 value="__all__", style={"fontFamily": SANS}),
                ], style={"flex": "1", "minWidth": "240px"}),
                html.Div([
                    html.Label(" ", style={"fontSize": "12px", "marginBottom": "6px",
                                                "display": "block"}),
                    html.Button("Exporter la semaine (Excel)", id="btn-export-semaine",
                                n_clicks=0, style=btn_sec),
                ], style={"alignSelf": "start"}),
            ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap",
                      "alignItems": "start", "marginBottom": "16px"}),
            html.Div(table_semaine(semaine_pour_table(dfj, jour0)), id="zone-table-semaine"),
            legende(f"Cumul du {pd.Timestamp(jour0).strftime('%d/%m')} au "
                    f"{(pd.Timestamp(jour0) + pd.Timedelta(days=6)).strftime('%d/%m')} par produit."),
        ),
        panneau(
            titre_section("Produits à surveiller — changement de niveau récent"),
            html.P("Ces produits vendent récemment bien plus (ou bien moins) qu'il y a un an : "
                   "leur prévision est plus délicate. Vérifie-les, et corrige-les à la main si besoin "
                   "(ci-dessous).",
                   style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "12px"}),
            table_alertes(pf_fj.alertes_niveau()),
        ),
        panneau(
            titre_section("Corriger un produit manuellement"),
            html.P("Force une quantité fixe ou applique un facteur sur un produit "
                   "(ex. baguette ×0.7 si la prévision te paraît trop haute). "
                   "Pris en compte à la prochaine génération.",
                   style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "14px"}),
            html.Div([
                _champ("Produit", dcc.Dropdown(id="ovr-produit",
                       options=[{"label": p, "value": p} for p in produits],
                       placeholder="choisir un produit…", style={"fontFamily": SANS}),
                       largeur="280px"),
                _champ("Mode", dcc.Dropdown(id="ovr-mode", clearable=False,
                       options=[{"label": "Facteur (×)", "value": "facteur"},
                                {"label": "Quantité fixe / jour", "value": "fixe"}],
                       value="facteur", style={"fontFamily": SANS}), largeur="200px"),
                _champ("Valeur", dcc.Input(id="ovr-valeur", type="number", value=1,
                       style=_INPUT_STYLE), largeur="120px"),
            ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "14px"}),
            html.Button("Appliquer la correction", id="btn-add-ovr", n_clicks=0, style=btn_or),
            html.Span(id="ovr-status", style={"marginLeft": "16px", "fontFamily": SANS,
                                              "fontSize": "13px"}),
            html.Div([
                _champ("Retirer une correction",
                       dcc.Dropdown(id="dd-suppr-ovr", placeholder="choisir…",
                                    style={"fontFamily": SANS}), largeur="280px"),
                html.Button("Retirer", id="btn-suppr-ovr", n_clicks=0, style={
                    "background": C["blanc"], "border": f"1px solid {C['rouge']}",
                    "color": C["rouge"], "borderRadius": "6px", "padding": "9px 18px",
                    "cursor": "pointer", "fontFamily": SANS, "fontWeight": "700",
                    "fontSize": "13px", "alignSelf": "end", "height": "38px"}),
            ], style={"display": "flex", "gap": "16px", "alignItems": "end",
                      "marginTop": "16px", "marginBottom": "16px"}),
            html.Div(table_overrides(lire_overrides()), id="zone-table-ovr"),
            html.Hr(style={"border": "none", "borderTop": f"1px solid {C['bordure']}",
                           "margin": "18px 0"}),
            html.Button("Régénérer les prévisions journalières", id="btn-gen-jour", n_clicks=0,
                        style=btn_or),
            dcc.Loading(html.Div(id="zone-statut-jour", style={"marginTop": "12px"}),
                        type="circle", color=C["or"]),
            legende("À lancer après une correction, un nouvel événement ou un nouveau match "
                    "pour mettre à jour les prévisions."),
        ),
        panneau(
            titre_section("Courbe d'un produit sur l'horizon"),
            html.Div([
                html.Div([
                    html.Label("1. Catégorie :", style={"fontWeight": "600", "fontSize": "12px",
                                                        "marginBottom": "6px", "display": "block",
                                                        "color": C["txt_doux"]}),
                    dcc.Dropdown(id="dd-cat-produit-jour", clearable=False,
                                 options=[{"label": c, "value": c} for c in cats],
                                 value=(cats[0] if cats else None), style={"fontFamily": SANS}),
                ], style={"flex": "1", "minWidth": "240px"}),
                html.Div([
                    html.Label("2. Produit :", style={"fontWeight": "600", "fontSize": "12px",
                                                      "marginBottom": "6px", "display": "block",
                                                      "color": C["txt_doux"]}),
                    dcc.Dropdown(id="dd-produit-jour", clearable=False,
                                 style={"fontFamily": SANS}),
                ], style={"flex": "1", "minWidth": "240px"}),
            ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "12px"}),
            dcc.Graph(id="g-produit-jour"),
            html.Div(id="legende-produit-jour"),
        ),
        panneau(
            titre_section("Total à produire par jour (toutes catégories)"),
            dcc.Graph(id="g-total-jour", figure=fig_total, config={"displayModeBar": False}),
            legende("Barres claires = week-ends. " + texte_total),
        ),
    ])


@lru_cache(maxsize=1)
def _ratio_ttc_ht():
    """Ratio médian CA TTC (caisse, ventes journalières) / CA HT (panel pipeline)
    sur les derniers mois communs.

    Les prévisions de CA du pipeline sont en HT (le panel xlsx l'est) alors que
    le gérant raisonne en TTC (total caisse) : sans conversion, la prévision
    paraît ~10 % trop basse (ex. juin 2026 : 1 089 222 HT lu contre 1 300 000 TTC
    réel → écart perçu de 16 % alors que l'erreur modèle réelle était ~3 %).
    Repli 1.107 (ratio médian historique) si les fichiers manquent.
    """
    try:
        vj = _ventes_brutes()
        hist = lire("historique_agrege_global.csv")
        if vj is None or hist is None or hist.empty:
            return 1.107
        ttc = vj.groupby(vj["Date"].dt.to_period("M"))["CA_TTC"].sum()
        hist = hist.copy()
        hist["Date"] = pd.to_datetime(hist["Date"])
        ht = hist.set_index(hist["Date"].dt.to_period("M"))["Chiffre_Affaires_Total"]
        cmp = pd.concat([ttc.rename("ttc"), ht.rename("ht")], axis=1).dropna()
        cmp = cmp[(cmp["ht"] > 0) & (cmp["ttc"] > 0)].tail(6)
        return float((cmp["ttc"] / cmp["ht"]).median()) if len(cmp) else 1.107
    except Exception:
        return 1.107


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET — HISTORIQUE (explorer les ventes passées : jour / semaine / mois)
# ═══════════════════════════════════════════════════════════════════════════════
@lru_cache(maxsize=1)
def _ventes_brutes():
    """Ventes journalières réelles (donnees_ventes/ventes_journalieres.csv), en cache."""
    df = pf_fj.charger_ventes()
    if df is None or df.empty:
        return None
    df = df.copy()
    df["Produit"] = df["Produit"].astype(str)
    if "Famille" in df.columns:
        df["Famille"] = df["Famille"].astype(str)
    df["CA_TTC"] = pd.to_numeric(df.get("CA_TTC", 0), errors="coerce").fillna(0.0)
    return df


@lru_cache(maxsize=1)
def _pics_commandes():
    """Jours « possible commande » détectés dans l'historique (en cache)."""
    try:
        return pf_commandes.detecter_pics(_ventes_brutes())
    except Exception:
        return pd.DataFrame(columns=["Date", "Produit", "Famille",
                                     "Quantite", "Attendu", "Exces"])


def _cle_periode(idx, gran):
    """Clé d'agrégation : jour, lundi de la semaine, ou 1er du mois."""
    if gran == "Semaine":
        return idx - pd.to_timedelta(idx.dayofweek, unit="D")
    if gran == "Mois":
        return idx.to_period("M").to_timestamp()
    return idx  # Jour


def _hist_produit(prod, gran):
    """Historique agrégé (Quantité, CA) d'un produit par période (0 comblés)."""
    df = _ventes_brutes()
    if df is None or df.empty or not prod:
        return pd.DataFrame(columns=["Date", "Quantite", "CA"])
    sub = df[df["Produit"] == str(prod)]
    if sub.empty:
        return pd.DataFrame(columns=["Date", "Quantite", "CA"])
    full = pd.date_range(df["Date"].min(), df["Date"].max())
    q = sub.groupby("Date")["Quantite"].sum().reindex(full, fill_value=0.0)
    c = sub.groupby("Date")["CA_TTC"].sum().reindex(full, fill_value=0.0)
    cle = _cle_periode(q.index, gran)
    g = pd.DataFrame({"Date": q.groupby(cle).sum().index,
                      "Quantite": q.groupby(cle).sum().values,
                      "CA": c.groupby(cle).sum().values})
    return g


def _prev_produit(prod, gran):
    """Prévision journalière agrégée par période (pour superposer à l'historique)."""
    dfj = charger_journalier()
    if dfj is None or dfj.empty or not prod or "Qty_Prev" not in dfj.columns:
        return pd.DataFrame(columns=["Date", "Quantite"])
    sub = dfj[dfj["Produit"].astype(str) == str(prod)]
    if sub.empty:
        return pd.DataFrame(columns=["Date", "Quantite"])
    full = pd.date_range(sub["Date"].min(), sub["Date"].max())
    q = sub.groupby("Date")["Qty_Prev"].sum().reindex(full, fill_value=0.0)
    cle = _cle_periode(q.index, gran)
    return pd.DataFrame({"Date": q.groupby(cle).sum().index,
                         "Quantite": q.groupby(cle).sum().values})


def _label_periode(ts, gran):
    """Étiquette triable (préfixe ISO) + lisible pour une période."""
    ts = pd.Timestamp(ts)
    if gran == "Mois":
        return f"{ts.year}-{ts.month:02d} · {_MOIS_FR[ts.month]} {ts.year}"
    if gran == "Semaine":
        fin = ts + pd.Timedelta(days=6)
        return f"{ts:%Y-%m-%d} · sem. {ts:%d/%m}–{fin:%d/%m}"
    return f"{ts:%Y-%m-%d} · {_JOURS_FR[ts.dayofweek]}"


def onglet_historique():
    df = _ventes_brutes()
    cats = (sorted(df["Famille"].dropna().astype(str).unique().tolist())
            if (df is not None and "Famille" in df.columns) else [])
    dmin = df["Date"].min() if df is not None else None
    dfj = charger_journalier()
    dmax_prev = dfj["Date"].max() if (dfj is not None and not dfj.empty) else None
    dmax = df["Date"].max() if df is not None else None
    fin = max([d for d in (dmax, dmax_prev) if d is not None], default=None)
    return html.Div([
        titre_section("Explorer l'historique des ventes"),
        legende("Combien un produit s'est vendu tel jour, telle semaine ou tel mois — "
                "et comparaison avec la prévision (en vert) pour vérifier qu'elle est cohérente."),
        panneau(
            html.Div([
                _champ("Catégorie", dcc.Dropdown(
                    id="dd-cat-hist", clearable=False,
                    options=[{"label": c, "value": c} for c in cats],
                    value=(cats[0] if cats else None), style={"fontFamily": SANS})),
                _champ("Produit", dcc.Dropdown(
                    id="dd-produit-hist", clearable=False, style={"fontFamily": SANS})),
                _champ("Granularité", dcc.RadioItems(
                    id="radio-gran-hist", value="Mois", inline=True,
                    options=[{"label": " " + g, "value": g} for g in ("Jour", "Semaine", "Mois")],
                    labelStyle={"marginRight": "14px", "fontFamily": SANS, "fontSize": "13px"},
                    style={"marginTop": "8px"}), largeur="230px"),
                _champ("Période", dcc.DatePickerRange(
                    id="date-range-hist", display_format="DD/MM/YYYY",
                    min_date_allowed=(dmin.date() if dmin is not None else None),
                    max_date_allowed=(fin.date() if fin is not None else None),
                    start_date=(dmin.date() if dmin is not None else None),
                    end_date=(fin.date() if fin is not None else None)), largeur="290px"),
            ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "alignItems": "end"}),
        ),
        html.Div(id="hist-kpis"),
        panneau(dcc.Graph(id="g-historique")),
        panneau(
            titre_section("Détail par période"),
            legende("Astuce : tape une date (ex. « 2026-06 » ou « juin ») dans le filtre "
                    "de la colonne Période pour retrouver une période précise."),
            html.Div(id="hist-table", style={"marginTop": "10px"})),
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET — ÉVÉNEMENTS (match, jour férié, concert… → boost des prévisions)
# ═══════════════════════════════════════════════════════════════════════════════
_INPUT_STYLE = {"fontFamily": SANS, "padding": "8px 10px", "width": "100%",
                "borderRadius": "6px", "border": f"1px solid {C['bordure']}",
                "background": C["blanc"], "color": C["txt"], "boxSizing": "border-box"}


def _champ(label, composant, largeur="220px"):
    return html.Div([
        html.Label(label, style={"fontWeight": "600", "fontSize": "12px",
                                 "marginBottom": "6px", "display": "block",
                                 "color": C["txt_doux"]}),
        composant,
    ], style={"flex": "1", "minWidth": largeur})


# Niveaux d'importance d'un match → coefficient appliqué à l'uplift mesuré.
IMPORTANCE_MATCH = {"Poule": 1.0, "Élimination directe": 1.4, "Demi / Finale": 1.8}


def _horizon_debut():
    """Premier jour de l'horizon journalier (= un événement à cette date ou après est « à venir »).

    Jamais avant aujourd'hui : si le fichier de prévisions date de plusieurs
    jours, un événement déjà passé ne doit plus apparaître comme « à venir ».
    """
    auj = pd.Timestamp.today().normalize()
    dfj = charger_journalier()
    if dfj is not None and not dfj.empty:
        return max(pd.Timestamp(dfj["Date"].min()), auj)
    return auj


def evenements_en_lignes(evs):
    """Lignes lisibles pour le tableau unique (matchs + événements manuels)."""
    debut_h = _horizon_debut()
    libelle_imp = {v: k for k, v in IMPORTANCE_MATCH.items()}
    lignes = []
    for e in evs:
        typ = e.get("type", "")
        try:
            d0 = pd.Timestamp(e["date"]); d1 = pd.Timestamp(e.get("date_fin") or e["date"])
        except (KeyError, ValueError, TypeError):
            continue
        periode = d0.strftime("%d/%m/%Y")
        if d1 != d0:
            periode += " → " + d1.strftime("%d/%m/%Y")
        if typ == "match":
            adv = (e.get("adversaire") or "").strip()
            nom = "Match" + (f" — {adv}" if adv else "")
            imp = libelle_imp.get(float(e.get("importance", 1.0) or 1.0), "Poule")
            impact = f"Auto · {imp}"
            statut = "à venir (boosté)" if d0 >= debut_h else "passé (mesuré)"
        else:
            nom = (e.get("nom") or "").strip() or pf_config.TYPES_EVENEMENTS.get(typ, typ)
            ovr = e.get("familles_pct") or {}
            extra = (" · " + ", ".join(f"{k} +{int(v)}%" for k, v in ovr.items())) if ovr else ""
            impact = f"+{int(e.get('impact_pct', 0) or 0)}%{extra}"
            statut = "à venir" if d1 >= debut_h else "passé"
        lignes.append({"id": e.get("id", ""), "Nom": nom,
                       "Type": pf_config.TYPES_EVENEMENTS.get(typ, typ),
                       "Période": periode, "Impact": impact, "Statut": statut})
    return lignes


def table_evenements(evs):
    cols = [{"name": c, "id": c} for c in ["Nom", "Type", "Période", "Impact", "Statut"]]
    return dash_table.DataTable(
        id="table-evenements", data=evenements_en_lignes(evs), columns=cols,
        page_size=14, sort_action="native", style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"],
                      "fontWeight": "700", "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": C["carte"]},
            {"if": {"filter_query": '{Statut} contains "venir"', "column_id": "Statut"},
             "color": C["vert"], "fontWeight": "700"},
        ],
    )


def profil_match_texte():
    """Phrase résumant l'uplift moyen appris sur les jours de match passés."""
    prof = pf_matchs.profil_match()
    if not prof:
        return ("Aucun match passé mesurable pour l'instant — ajoute des dates de matchs "
                "déjà joués (présents dans l'historique) pour que le système apprenne l'impact.")
    items = sorted(prof.items(), key=lambda kv: -kv[1])
    bouts = ", ".join(f"{fam} {'+' if r >= 1 else ''}{(r-1)*100:.0f}%" for fam, r in items[:6])
    return f"Impact moyen appris sur les jours de match passés : {bouts}."


def onglet_evenements():
    evs = lire_evenements()
    cats = categories_connues()
    opt_type = [{"label": v, "value": k} for k, v in pf_config.TYPES_EVENEMENTS.items()]

    overrides = html.Div([
        html.Div([
            html.Label(c, style={"fontSize": "11px", "color": C["txt_doux"],
                                 "marginBottom": "4px", "display": "block"}),
            dcc.Input(id={"type": "ev-ovr", "cat": c}, type="number", placeholder="%",
                      style={**_INPUT_STYLE, "padding": "6px 8px"}),
        ], style={"width": "120px"}) for c in cats
    ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap"})

    # Champs spécifiques aux MATCHS (impact appris) — affichés si type = match.
    grp_match = html.Div([
        _champ("Adversaire (option)",
               dcc.Input(id="ev-adversaire", type="text", placeholder="ex. France",
                         style=_INPUT_STYLE)),
        _champ("Importance",
               dcc.Dropdown(id="ev-importance", clearable=False,
                            options=[{"label": k, "value": k} for k in IMPORTANCE_MATCH],
                            value="Poule", style={"fontFamily": SANS}), largeur="200px"),
    ], id="grp-match", style={"display": "flex", "gap": "16px", "flexWrap": "wrap",
                              "marginBottom": "14px"})

    # Champs spécifiques aux événements MANUELS — affichés si type ≠ match.
    grp_manuel = html.Div([
        html.Div([
            _champ("Nom", dcc.Input(id="ev-nom", type="text",
                                    placeholder="ex. Jour de l'an", style=_INPUT_STYLE)),
            _champ("Portée", dcc.Dropdown(id="ev-portee", clearable=False,
                                          options=[{"label": "National", "value": "national"},
                                                   {"label": "Local", "value": "local"}],
                                          value="national", style={"fontFamily": SANS}),
                   largeur="140px"),
            _champ("Boost global (%)",
                   dcc.Input(id="ev-impact", type="number", placeholder="ex. 50",
                             value=30, style=_INPUT_STYLE), largeur="140px"),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "14px"}),
        html.Details([
            html.Summary("Affiner par catégorie (optionnel)",
                         style={"cursor": "pointer", "fontWeight": "600",
                                "color": C["brun"], "marginBottom": "12px"}),
            html.Div("Laisse vide une catégorie pour qu'elle suive le boost global. "
                     "Renseigne un % seulement là où l'effet diffère.",
                     style={"fontSize": "12px", "color": C["txt_doux"], "marginBottom": "12px"}),
            overrides,
        ]),
    ], id="grp-manuel", style={"marginBottom": "14px"})

    formulaire = panneau(
        titre_section("Ajouter un événement"),
        html.P("Un seul endroit pour tout ajouter. Choisis le TYPE : un « Match » a son "
               "impact appris automatiquement (donne juste la date + l'importance) ; les "
               "autres types prennent un boost que tu saisis.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "12px"}),
        html.Div([
            _champ("Type", dcc.Dropdown(id="ev-type", options=opt_type, value="match",
                                        clearable=False, style={"fontFamily": SANS})),
            _champ("Date", dcc.DatePickerSingle(id="ev-date", display_format="DD/MM/YYYY",
                                                placeholder="jour J"), largeur="160px"),
            _champ("Date de fin (option)",
                   dcc.DatePickerSingle(id="ev-date-fin", display_format="DD/MM/YYYY",
                                        placeholder="si plusieurs jours"), largeur="160px"),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "14px"}),
        grp_match,
        grp_manuel,
        html.Button("Ajouter", id="btn-add-evt", n_clicks=0, style={
            "background": C["or"], "border": "none", "color": C["noir"],
            "borderRadius": "6px", "padding": "10px 22px", "cursor": "pointer",
            "fontFamily": SANS, "fontWeight": "700", "fontSize": "14px"}),
        html.Span(id="ev-status", style={"marginLeft": "16px", "fontFamily": SANS,
                                         "fontSize": "13px"}),
        html.Div(profil_match_texte(), id="match-profil",
                 style={"color": C["brun"], "fontSize": "13px", "fontStyle": "italic",
                        "marginTop": "14px"}),
    )

    gestion = panneau(
        titre_section("Événements planifiés"),
        html.Div([
            _champ("Supprimer un événement",
                   dcc.Dropdown(id="dd-suppr-evt", placeholder="choisir…",
                                style={"fontFamily": SANS}), largeur="280px"),
            html.Button("Supprimer", id="btn-suppr-evt", n_clicks=0, style={
                "background": C["blanc"], "border": f"1px solid {C['rouge']}",
                "color": C["rouge"], "borderRadius": "6px", "padding": "9px 18px",
                "cursor": "pointer", "fontFamily": SANS, "fontWeight": "700",
                "fontSize": "13px", "alignSelf": "end", "height": "38px"}),
        ], style={"display": "flex", "gap": "16px", "alignItems": "end",
                  "marginBottom": "16px"}),
        html.Div(table_evenements(evs), id="zone-table-evt"),
        legende("Matchs : « passé (mesuré) » sert à apprendre l'impact, « à venir (boosté) » "
                "le reçoit. Les boosts ne s'empilent pas (on garde le plus fort). "
                "Régénère les prévisions journalières pour répercuter."),
    )

    apercu = panneau(
        titre_section("Boost attendu par événement"),
        dcc.Graph(id="g-evt-preview", config={"displayModeBar": False}),
    )

    return html.Div([
        html.P("Anticipe les pics de fréquentation liés à un événement (match, jour férié, "
               "concert…). Tout s'ajoute ici, d'un seul formulaire.",
               style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "20px"}),
        dcc.Store(id="store-evenements", data=evs),
        formulaire,
        gestion,
        apercu,
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET — COMMANDES CLIENTS (grosses commandes B2B : fast-food, traiteur…)
# ═══════════════════════════════════════════════════════════════════════════════
def commandes_en_lignes(cmds):
    """Lignes lisibles pour le tableau des commandes planifiées."""
    debut_h = _horizon_debut()
    lignes = []
    for c in cmds:
        try:
            d = pd.Timestamp(c["date"])
            q = float(c["quantite"])
        except (KeyError, ValueError, TypeError):
            continue
        lignes.append({"id": c.get("id", ""),
                       "Date": d.strftime("%d/%m/%Y"),
                       "Produit": str(c.get("produit", "")),
                       "Quantité": int(round(q)),
                       "Client": str(c.get("client", "") or "").strip() or "—",
                       "Statut": "à venir (ajoutée aux prévisions)" if d >= debut_h else "passée"})
    return lignes


def table_commandes(cmds):
    cols = [{"name": c, "id": c} for c in ["Date", "Produit", "Quantité", "Client", "Statut"]]
    return dash_table.DataTable(
        id="table-commandes", data=commandes_en_lignes(cmds), columns=cols,
        page_size=12, sort_action="native", style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"],
                      "fontWeight": "700", "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": C["carte"]},
            {"if": {"filter_query": '{Statut} contains "venir"', "column_id": "Statut"},
             "color": C["vert"], "fontWeight": "700"},
        ],
    )


def table_pics_commandes(pics, page=12):
    """Tableau des jours « possible commande » détectés dans l'historique."""
    if pics is None or pics.empty:
        return html.Div("Aucun pic « type commande » détecté dans l'historique récent.",
                        style={"color": C["txt_doux"], "padding": "8px"})
    t = pd.DataFrame({
        "Date": pd.to_datetime(pics["Date"]).dt.strftime("%d/%m/%Y"),
        "Produit": pics["Produit"].astype(str),
        "Catégorie": pics["Famille"].astype(str),
        "Vendu": pics["Quantite"].round().astype(int),
        "Niveau habituel": pics["Attendu"].round().astype(int),
        "Excès": pics["Exces"].round().astype(int),
    })
    return table_style(t, page=page)


def onglet_commandes():
    cmds = lire_commandes()
    ventes = _ventes_brutes()
    produits = (sorted(ventes["Produit"].astype(str).unique().tolist())
                if ventes is not None and not ventes.empty else [])
    btn_or = {"background": C["or"], "border": "none", "color": C["noir"],
              "borderRadius": "6px", "padding": "10px 22px", "cursor": "pointer",
              "fontFamily": SANS, "fontWeight": "700", "fontSize": "14px"}

    formulaire = panneau(
        titre_section("Déclarer une commande à venir"),
        html.P("Une entreprise (fast-food, traiteur, bureau…) a commandé pour une date précise ? "
               "Saisis la date, le produit et la quantité : elle sera ajoutée TELLE QUELLE aux "
               "prévisions de production (jour + mois) et aux besoins matières premières.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "12px"}),
        html.Div([
            _champ("Date de livraison",
                   dcc.DatePickerSingle(id="cmd-date", display_format="DD/MM/YYYY",
                                        placeholder="jour J"), largeur="160px"),
            _champ("Produit", dcc.Dropdown(id="cmd-produit",
                   options=[{"label": p, "value": p} for p in produits],
                   placeholder="choisir un produit…", style={"fontFamily": SANS}),
                   largeur="300px"),
            _champ("Quantité", dcc.Input(id="cmd-qte", type="number", min=1,
                   placeholder="ex. 500", style=_INPUT_STYLE), largeur="130px"),
            _champ("Client (option)", dcc.Input(id="cmd-client", type="text",
                   placeholder="ex. fast-food du coin", style=_INPUT_STYLE), largeur="220px"),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap",
                  "alignItems": "end", "marginBottom": "14px"}),
        html.Button("Ajouter la commande", id="btn-add-cmd", n_clicks=0, style=btn_or),
        html.Span(id="cmd-status", style={"marginLeft": "16px", "fontFamily": SANS,
                                          "fontSize": "13px"}),
    )

    gestion = panneau(
        titre_section("Commandes planifiées"),
        html.Div([
            _champ("Supprimer une commande",
                   dcc.Dropdown(id="dd-suppr-cmd", placeholder="choisir…",
                                style={"fontFamily": SANS}), largeur="320px"),
            html.Button("Supprimer", id="btn-suppr-cmd", n_clicks=0, style={
                "background": C["blanc"], "border": f"1px solid {C['rouge']}",
                "color": C["rouge"], "borderRadius": "6px", "padding": "9px 18px",
                "cursor": "pointer", "fontFamily": SANS, "fontWeight": "700",
                "fontSize": "13px", "alignSelf": "end", "height": "38px"}),
        ], style={"display": "flex", "gap": "16px", "alignItems": "end",
                  "marginBottom": "16px"}),
        html.Div(table_commandes(cmds), id="zone-table-cmd"),
        legende("Après un ajout ou une suppression, clique « Relancer le calcul » (en haut à "
                "droite) pour répercuter sur la production et les matières premières."),
    )

    pics = _pics_commandes()
    detection = panneau(
        titre_section("Pics détectés dans l'historique — « possible commande »"),
        html.P("Journées où un produit s'est vendu bien au-dessus de son niveau habituel "
               "(hors fêtes, événements et matchs) : probablement une commande ponctuelle d'une "
               "entreprise. Ces jours sont marqués ◆ dans l'onglet Historique et NEUTRALISÉS "
               "automatiquement dans l'apprentissage, pour ne pas gonfler les prévisions des "
               "jours normaux. Un client régulier (qui commande tous les jours) n'est pas "
               "neutralisé : le modèle suit ce nouveau niveau.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "12px"}),
        table_pics_commandes(pics),
    )

    return html.Div([
        html.P("Gère les grosses commandes ponctuelles des entreprises : celles déjà passées "
               "(détectées automatiquement) et celles à venir (à déclarer ici).",
               style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "20px"}),
        dcc.Store(id="store-commandes", data=cmds),
        formulaire,
        gestion,
        detection,
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET — ASSISTANT (questions guidées, 100 % LOCAL : aucune donnée ne sort du PC)
# ═══════════════════════════════════════════════════════════════════════════════
def _champs_assistant():
    """Groupes de champs (produit, date, catégorie, mois, période) montrés selon la question."""
    dfj = charger_journalier()
    produits = (sorted(dfj["Produit"].astype(str).unique().tolist())
                if dfj is not None and not dfj.empty else [])
    familles = (sorted(dfj["Famille"].dropna().astype(str).unique().tolist())
                if dfj is not None and "Famille" in getattr(dfj, "columns", []) else [])
    jmin = pd.Timestamp(dfj["Date"].min()).date() if dfj is not None and not dfj.empty else None
    jmax = pd.Timestamp(dfj["Date"].max()).date() if dfj is not None and not dfj.empty else None
    _, _, _, mrp = charger_tout()
    mois = (sorted(mrp["Date"].dt.strftime("%Y-%m").unique().tolist())
            if mrp is not None and not mrp.empty else [])

    cache = {"display": "none"}
    grp = lambda **kw: {"minWidth": "220px", "flex": "1"}
    return html.Div([
        html.Div(_champ("Produit", dcc.Dropdown(
            id="q-produit", options=[{"label": p, "value": p} for p in produits],
            placeholder="choisir un produit…", style={"fontFamily": SANS})),
            id="grp-q-produit", style=cache),
        html.Div(_champ("Jour (optionnel)", dcc.DatePickerSingle(
            id="q-date", display_format="DD/MM/YYYY",
            min_date_allowed=jmin, max_date_allowed=jmax,
            placeholder="par défaut : prochain jour"), largeur="180px"),
            id="grp-q-date", style=cache),
        html.Div(_champ("Catégorie (optionnel)", dcc.Dropdown(
            id="q-categorie", options=[{"label": "Toutes", "value": "__all__"}]
            + [{"label": f, "value": f} for f in familles],
            value="__all__", clearable=False, style={"fontFamily": SANS})),
            id="grp-q-categorie", style=cache),
        html.Div(_champ("Mois", dcc.Dropdown(
            id="q-mois", options=[{"label": m, "value": m} for m in mois],
            value=(mois[0] if mois else None), clearable=False, style={"fontFamily": SANS}),
            largeur="160px"), id="grp-q-mois", style=cache),
        html.Div(_champ("Regrouper par", dcc.Dropdown(
            id="q-gran", options=[{"label": g.capitalize(), "value": g} for g in ("jour", "semaine", "mois")],
            value="mois", clearable=False, style={"fontFamily": SANS}), largeur="150px"),
            id="grp-q-gran", style=cache),
    ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "alignItems": "end",
              "marginBottom": "16px"})


# Quels groupes de champs afficher pour chaque type de question.
_CHAMPS_PAR_QUESTION = {
    "production": {"grp-q-date", "grp-q-categorie"},
    "prevision":  {"grp-q-produit", "grp-q-date"},
    "historique": {"grp-q-produit", "grp-q-gran"},
    "matieres":   {"grp-q-mois"},
    "evenements": set(),
    "fiabilite":  {"grp-q-produit"},
}
_GRP_IDS = ["grp-q-produit", "grp-q-date", "grp-q-categorie", "grp-q-mois", "grp-q-gran"]


def onglet_assistant():
    opts = [{"label": v, "value": k} for k, v in pf_assistant.TYPES_QUESTION.items()]
    return html.Div([
        html.P("Pose une question sur tes données : choisis le type, complète les champs, et l'assistant "
               "affiche la réponse tirée de tes prévisions, ton historique, tes matières, tes commandes "
               "et le suivi de fiabilité. 100 % sur ton ordinateur — aucune donnée ne sort, aucun risque "
               "d'invention (chaque chiffre vient d'un fichier).",
               style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "16px"}),
        panneau(
            html.Div([
                _champ("Ma question", dcc.Dropdown(id="q-type", options=opts, value="production",
                                                   clearable=False, style={"fontFamily": SANS}),
                       largeur="320px"),
            ], style={"display": "flex", "marginBottom": "14px"}),
            _champs_assistant(),
            html.Button("Répondre", id="btn-q-run", n_clicks=0, style={
                "background": C["or"], "border": "none", "color": C["noir"],
                "borderRadius": "6px", "padding": "10px 24px", "cursor": "pointer",
                "fontFamily": SANS, "fontWeight": "700", "fontSize": "14px"}),
            dcc.Loading(html.Div(id="zone-q-reponse", style={"marginTop": "18px"}),
                        type="circle", color=C["or"]),
            legende("Astuce : « Que produire un jour donné » sans date = le prochain jour. "
                    "Les réponses reflètent le dernier calcul (relance-le si les ventes ont changé)."),
        ),
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET 4 — MATIÈRES PREMIÈRES
# ═══════════════════════════════════════════════════════════════════════════════
def _panneau_couverture_recettes():
    """Panneau « recettes à saisir en priorité » : classe les produits sans recette
    exacte par leur poids sur les achats, pour cibler la saisie du chef."""
    dfj = charger_journalier()
    vol = pf_couv.volumes_depuis_journalier(dfj)
    if vol is None or vol.empty:
        return html.Div()
    syn = pf_couv.synthese(vol)
    rap = pf_couv.rapport_couverture(vol)
    prio = rap[rap["Prioritaire"]].head(20)

    src = syn["par_source"]
    cards = rangee_kpis(
        kpi("Recettes exactes", f"{src.get('exacte', 0)}",
            f"sur {syn['n_produits']} produits", couleur=C["vert"]),
        kpi("Poids matières fiable", f"{syn['couverture_poids']*100:.0f} %",
            "part des kg issue de recettes exactes", couleur=C["brun"]),
        kpi("À estimer", f"{src.get('auto', 0) + src.get('générique', 0)}",
            "produits sur recette approchée", couleur=C["or"]),
    )

    t = pd.DataFrame({
        "Produit": prio["Produit"],
        "Catégorie": prio["Famille"],
        "Volume prévu": prio["Volume"].astype(int),
        "Recette": prio["Source"].map({"auto": "estimée (nom)", "générique": "générique famille",
                                        "aucune": "aucune"}).fillna(prio["Source"]),
        "Poids matières (kg)": prio["Poids_matiere_kg"],
    })
    return panneau(
        titre_section("Recettes à saisir en priorité"),
        html.P("Le bon de commande n'est exact que là où la recette l'est. Voici les produits "
               "SANS recette exacte qui pèsent le plus sur les achats : saisir leur vraie recette "
               "(data/recettes_exactes.json) fiabilise le plus vite le budget matières et le food-cost.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "14px"}),
        cards,
        table_style(t, page=10) if not t.empty
        else html.Div("Toutes les recettes à fort volume sont déjà exactes.",
                      style={"color": C["txt_doux"], "padding": "8px"}),
        legende("« Poids matières » = volume prévu × poids de la recette (estimée). "
                "Trie par cette colonne pour cibler l'effort de saisie."),
    )


def _fmt_qte_ing(ingredient, valeur):
    """(nombre, unité, libellé) pour un besoin ingrédient : (g)→kg, (ml)→L, sinon unité."""
    ing = str(ingredient)
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        return 0.0, "", "—"
    if "(g)" in ing:
        return round(v / 1000, 2), "kg", f"{v/1000:,.2f} kg".replace(",", " ")
    if "(ml)" in ing:
        return round(v / 1000, 2), "L", f"{v/1000:,.2f} L".replace(",", " ")
    return round(v, 1), "unité", f"{v:,.1f}".replace(",", " ")


def _nom_ingredient(ingredient):
    """Nom d'ingrédient sans l'unité entre parenthèses finale."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(ingredient))


def _panneau_par_departement():
    """Traçabilité MRP : département → produits du mois → ingrédients de chacun.

    Répond à « pour produire JIVARA ce mois-ci, le rayon PÂTISSERIE a besoin de
    X g de farine, X g de sucre… ». Alimenté par besoins_ingredients_detail.csv
    (écrit par le pipeline). Absent tant que le calcul n'a pas été relancé.
    """
    det = charger_mrp_detail()
    if det is None or det.empty:
        return panneau(
            titre_section("Besoins par département (production)"),
            html.Div("Détail par département pas encore disponible — cliquez « Relancer le "
                     "calcul » (en haut à droite) pour le générer.",
                     style={"color": C["txt_doux"], "padding": "8px"}))

    mois_dispo = sorted(det["Date"].dt.strftime("%Y-%m").unique())
    depts = sorted(det["Famille"].dropna().astype(str).unique())
    return panneau(
        titre_section("Besoins par département (production)"),
        html.P("Choisissez un département puis un produit pour voir, mois par mois, "
               "les matières premières nécessaires à sa production. « Tout le département » "
               "additionne les besoins de tous ses produits.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "14px"}),
        html.Div([
            _champ("Mois", dcc.Dropdown(
                id="dd-mrp-dept-mois", clearable=False,
                options=[{"label": mois_label(m), "value": m} for m in mois_dispo],
                value=(mois_dispo[0] if mois_dispo else None),
                style={"fontFamily": SANS}), largeur="200px"),
            _champ("Département", dcc.Dropdown(
                id="dd-mrp-dept", clearable=False,
                options=[{"label": d, "value": d} for d in depts],
                value=(depts[0] if depts else None),
                style={"fontFamily": SANS}), largeur="230px"),
            _champ("Produit", dcc.Dropdown(
                id="dd-mrp-dept-produit", clearable=False,
                style={"fontFamily": SANS}), largeur="300px"),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "alignItems": "end"}),
        html.Div(id="mrp-dept-resume", style={"marginTop": "16px"}),
        html.Div(id="mrp-dept-detail", style={"marginTop": "14px"}),
    )


# Valeur sentinelle du filtre catégorie de l'onglet MRP (toutes catégories).
_TOUTES_CAT = "__TOUTES__"


def onglet_mrp():
    _, _, _, mrp = charger_tout()

    if mrp is None or mrp.empty:
        return html.Div("Données matières premières indisponibles — relancez le calcul.",
                        style={"padding": "40px", "color": C["txt_doux"]})

    mois_dispo = sorted(mrp["Date"].dt.strftime("%Y-%m").unique())
    mois_prochain = mois_dispo[0] if mois_dispo else None

    # KPIs mois prochain
    cards = []
    if mois_prochain:
        df_m0 = mrp[mrp["Date"].dt.strftime("%Y-%m") == mois_prochain]
        n_ing  = df_m0["Ingredient"].nunique()
        total_g = df_m0[df_m0["Ingredient"].str.contains(r"\(g\)", na=False)]["Quantite_Requise"].sum()
        total_l = df_m0[df_m0["Ingredient"].str.contains(r"\(ml\)", na=False)]["Quantite_Requise"].sum()
        cards = [
            kpi("Ingrédients distincts",  str(n_ing), mois_prochain),
            kpi("Farine + solides",       f"{total_g/1000:,.1f} kg".replace(",", " "), "total estimé"),
            kpi("Liquides (hors eau)",    f"{total_l/1000:,.1f} L".replace(",", " "), "lait, sirops…"),
        ]

    return html.Div([
        rangee_kpis(*cards) if cards else html.Div(),
        panneau(
            html.Div([
                html.Label("Mois :", style={"fontWeight":"600","marginRight":"12px"}),
                dcc.Dropdown(id="dd-mois-mrp",
                             options=[{"label": m, "value": m} for m in mois_dispo],
                             value=mois_prochain, clearable=False,
                             style={"width":"200px","fontFamily": SANS, "display":"inline-block"}),
                html.Label("Catégorie :", style={"fontWeight":"600",
                           "marginLeft":"24px","marginRight":"12px"}),
                dcc.Dropdown(id="dd-mrp-categorie",
                             options=([{"label": "Toutes catégories", "value": _TOUTES_CAT}]
                                      + [{"label": c.capitalize(), "value": c}
                                         for c in pf_config.CATEGORIES_BESOINS]),
                             value=_TOUTES_CAT, clearable=False,
                             style={"width":"240px","fontFamily": SANS, "display":"inline-block"}),
            ], style={"marginBottom":"18px"}),
            html.Div(id="mrp-fiabilite-cat", style={"marginBottom": "14px"}),
            html.Div(id="mrp-budget", style={"marginBottom": "18px"}),
            dcc.Graph(id="g-mrp-bar"),
            html.Div(id="t-mrp-table", style={"marginTop": "14px"}),
            legende("Budget d'achat = quantités × prix estimés (data/prix_matieres.json, "
                    "à affiner avec les factures réelles). Le food-cost est un PLANCHER : "
                    "de nombreux produits n'ont pas encore de recette exacte (couverture indiquée)."),
        ),
        _panneau_par_departement(),
        _panneau_couverture_recettes(),
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET 5 — BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════
def _metriques_validation(v):
    """MAE/RMSE/MAPE RÉELS par modèle depuis un df de validation walk-forward."""
    if v is None or v.empty or "Reel" not in v.columns:
        return None
    reel = pd.to_numeric(v["Reel"], errors="coerce")
    lignes = []
    for col in v.columns:
        if not col.startswith("Prev_"):
            continue
        pred = pd.to_numeric(v[col], errors="coerce")
        diff = (reel - pred).abs()
        m = reel > 0
        mape = float((diff[m] / reel[m]).mean() * 100) if m.any() else float("nan")
        lignes.append({"Modèle": col.replace("Prev_", "").replace("_", " "),
                       "MAE": float(diff.mean()),
                       "RMSE": float(((reel - pred) ** 2).mean() ** 0.5),
                       "MAPE": mape})
    if not lignes:
        return None
    return pd.DataFrame(lignes).sort_values("MAPE").reset_index(drop=True)


def _fig_reel_vs_prevu(v, titre, unite=""):
    """Courbe RÉELLE réel vs modèle le mieux classé, sur les mois rejoués."""
    d = _metriques_validation(v)
    fig = fig_base(titre, height=380)
    if d is None or v is None or v.empty:
        return fig, None
    best = d.iloc[0]["Modèle"]
    col = "Prev_" + best.replace(" ", "_")
    v = v.copy(); v["Date"] = pd.to_datetime(v["Date"])
    fig.add_trace(go.Scatter(x=v["Date"], y=pd.to_numeric(v["Reel"], errors="coerce"),
                             name="Réel", line=dict(color=C["brun"], width=2.6),
                             hovertemplate="%{x|%b %Y}<br>%{y:,.0f}"+f" {unite}<extra></extra>"))
    if col in v.columns:
        fig.add_trace(go.Scatter(x=v["Date"], y=pd.to_numeric(v[col], errors="coerce"),
                                 name=f"Prévu ({best})", line=dict(color=C["or"], width=2.2, dash="dash"),
                                 hovertemplate="%{x|%b %Y}<br>%{y:,.0f}"+f" {unite}<extra></extra>"))
    if unite:
        fig.update_yaxes(ticksuffix=f" {unite}")
    return fig, best


def _table_metriques(d, unite):
    """DataFrame formaté (MAE/RMSE/MAPE + rang) pour l'affichage."""
    rangs = ["🥇 1er", "🥈 2e", "🥉 3e"] + [f"{i}e" for i in range(4, 20)]
    out = pd.DataFrame({
        "Modèle": d["Modèle"],
        f"MAE ({unite})": d["MAE"].map(lambda x: f"{x:,.0f}".replace(",", " ")),
        f"RMSE ({unite})": d["RMSE"].map(lambda x: f"{x:,.0f}".replace(",", " ")),
        "MAPE": d["MAPE"].map(lambda x: f"{x:.1f} %"),
        "Rang": rangs[:len(d)],
    })
    return out


def _table_biais(biais):
    """Tableau des produits à biais systématique (sur/sous-production)."""
    if biais is None or biais.empty:
        return html.Div("Pas de biais marqué sur la période.",
                        style={"color": C["txt_doux"], "padding": "8px"})
    cols = [{"name": c, "id": c} for c in biais.columns]
    return dash_table.DataTable(
        data=biais.to_dict("records"), columns=cols, page_size=12, sort_action="native",
        filter_action="native", style_as_list_view=True,
        style_header={"backgroundColor": C["noir"], "color": C["creme"], "fontWeight": "700",
                      "fontFamily": SANS, "padding": "10px", "border": "none"},
        style_cell={"backgroundColor": C["blanc"], "color": C["txt"], "textAlign": "left",
                    "padding": "8px 10px", "fontFamily": SANS, "border": f"1px solid {C['bordure']}"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": C["carte"]},
            {"if": {"filter_query": '{Sens} = "Sur-production"', "column_id": "Sens"},
             "color": C["rouge"], "fontWeight": "700"},
            {"if": {"filter_query": '{Sens} = "Sous-production"', "column_id": "Sens"},
             "color": C["bleu"], "fontWeight": "700"},
            {"if": {"filter_query": '{Sens} = "OK"', "column_id": "Sens"},
             "color": C["vert"], "fontWeight": "700"},
        ],
    )


def _section_suivi():
    """Section « prévu vs réel » (suivi quotidien) en tête de l'onglet Fiabilité."""
    from paul_forecast import suivi as pf_suivi
    comp = charger_suivi()
    if comp is None or comp.empty:
        return panneau(
            titre_section("Suivi prévu vs réel (jours récents)"),
            html.Div("Pas encore calculé — clique « Relancer le calcul » (en haut à droite) "
                     "pour générer le suivi des jours récents.",
                     style={"color": C["txt_doux"], "padding": "8px"}))
    m = pf_suivi.metriques_globales(comp)
    res = pf_suivi.resume_journalier(comp)
    biais = pf_suivi.biais_par_produit(comp)

    def _pct(x):
        return "—" if x is None else f"{x:.0f} %"
    signe = "sur-production" if (m["biais_pct"] or 0) > 0 else "sous-production"
    cards = rangee_kpis(
        kpi("Écart moyen (wMAPE)", _pct((m["wmape"] or 0) * 100),
            f"sur {m['n_jours']} jours × {m['n_produits']} produits"),
        kpi("Biais global", (f"+{m['biais_pct']:.0f} %" if (m['biais_pct'] or 0) >= 0
                             else f"{m['biais_pct']:.0f} %"),
            f"tendance à la {signe}",
            couleur=(C["rouge"] if (m['biais_pct'] or 0) > 5 else
                     C["bleu"] if (m['biais_pct'] or 0) < -5 else C["vert"])),
        kpi("Jours suivis", f"{m['n_jours']}", "reconstitués hors-échantillon", couleur=C["brun"]),
    )

    fig = fig_base("Total prévu vs réellement vendu — par jour", height=340)
    fig.add_trace(go.Scatter(x=res["Date"], y=res["Reel"], name="Réel",
                             line=dict(color=C["brun"], width=2.6),
                             hovertemplate="%{x|%a %d/%m}<br>%{y:,.0f} u<extra></extra>"))
    fig.add_trace(go.Scatter(x=res["Date"], y=res["Prev"], name="Prévu (reconstitué)",
                             line=dict(color=C["or"], width=2.2, dash="dash"),
                             hovertemplate="%{x|%a %d/%m}<br>%{y:,.0f} u (prév.)<extra></extra>"))
    fig.update_yaxes(title="Unités/jour", rangemode="tozero")
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))

    return panneau(
        titre_section("Suivi prévu vs réel (jours récents)"),
        html.P("À quel point la prévision colle-t-elle à la réalité ? Comme les prévisions passées "
               "ne sont pas archivées, on RECONSTITUE ce que le modèle aurait prédit sur les jours "
               "récents en ne l'entraînant que sur l'avant (origine glissante, ré-estimée chaque "
               "semaine) — une mesure honnête et sans fuite.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "14px"}),
        cards,
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
        titre_section("Produits à biais systématique"),
        html.P("Produits toujours sur- ou sous-produits sur la période : le signal le plus utile à "
               "corriger (réglage, recette, ou correction manuelle). Une forte sous-production peut "
               "aussi venir d'une hausse récente que le modèle rattrape en 1-2 semaines, ou d'une "
               "commande ponctuelle non anticipée.",
               style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "12px"}),
        _table_biais(biais),
        legende("« Biais » = (prévu − réel) / réel sur la fenêtre ; « wMAPE » = ampleur de l'erreur. "
                "Rouge = on produit trop, bleu = pas assez."),
    )


def onglet_backtest():
    suivi_section = _section_suivi()
    vca = lire("validation_mensuelle_ca.csv")
    vqt = lire("validation_mensuelle_quantité.csv")
    if vca is None and vqt is None:
        return html.Div([suivi_section, html.Div(
            "Validation mensuelle indisponible — relancez le calcul "
            "(fichiers validation_mensuelle_*.csv).",
            style={"padding": "40px", "color": C["txt_doux"]})])

    intro = html.P(
        "Validation « walk-forward » (glissante) : on rejoue les derniers mois un par un — "
        "pour chacun, le modèle est entraîné uniquement sur le passé (aucune fuite de données) "
        "puis on compare sa prévision au chiffre réellement réalisé. C'est la mesure honnête de "
        "l'erreur « à 1 mois ». Le modèle le mieux classé sert de référence pour l'agrégat.",
        style={"color": C["txt_doux"], "fontSize": "14px", "marginBottom": "20px", "lineHeight": "1.6"})

    blocs = [suivi_section, intro]
    for v, unite, label in [(vca, "MAD", "Chiffre d'affaires"), (vqt, "u", "Quantité")]:
        d = _metriques_validation(v)
        if d is None:
            continue
        fig, best = _fig_reel_vs_prevu(v, f"Réel vs prévu — {label}", unite)
        best_mape = d.iloc[0]["MAPE"]
        blocs.append(panneau(
            titre_section(f"{label} — validation mensuelle"),
            html.Div([
                panneau(table_style(_table_metriques(d, unite), page=8),
                        style={"flex": "1", "minWidth": "320px", "marginBottom": "0"}),
                panneau(dcc.Graph(figure=fig, config={"displayModeBar": False}),
                        style={"flex": "1.3", "minWidth": "360px", "marginBottom": "0"}),
            ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}),
            legende(f"Meilleur modèle : {best} (erreur moyenne ≈ {best_mape:.1f} % à 1 mois, "
                    f"mesurée sur {len(v)} mois rejoués)."),
        ))
    return html.Div(blocs)


# ═══════════════════════════════════════════════════════════════════════════════
# ONGLET ACCUEIL — tout comprendre d'un coup d'œil (nouvel utilisateur)
# ═══════════════════════════════════════════════════════════════════════════════
def _prochaine_fete():
    """(nom, jours restants) de la prochaine fête, ou None."""
    auj = pd.Timestamp.today().normalize()
    prochaines = []
    for f in pf_config.FETES_MAROCAINES:
        try:
            deb = pd.Timestamp(f["debut"])
        except (KeyError, ValueError):
            continue
        if deb >= auj:
            prochaines.append((deb, f.get("nom", f.get("type", "Fête"))))
    if not prochaines:
        return None
    deb, nom = min(prochaines)
    return nom, int((deb - auj).days)


def _fraicheur():
    """Phrase d'état des données : ventes à jour au…, prévisions calculées le…"""
    morceaux = []
    df = _ventes_brutes()
    if df is not None and not df.empty:
        morceaux.append(f"Ventes réelles à jour au {pd.Timestamp(df['Date'].max()):%d/%m/%Y}")
    p = os.path.join(EXPORTS, "previsions_journalieres.csv")
    if os.path.exists(p):
        morceaux.append(f"prévisions calculées le "
                        f"{pd.Timestamp(os.path.getmtime(p), unit='s'):%d/%m/%Y}")
    pf = _prochaine_fete()
    if pf:
        morceaux.append(f"prochaine fête : {pf[0]} dans {pf[1]} j")
    return " · ".join(morceaux) if morceaux else "Données chargées depuis exports/"


def _carte_nav(titre, texte, tab):
    """Carte cliquable de l'accueil qui ouvre l'onglet correspondant."""
    return html.Div(className="nav-card", id={"type": "nav-card", "tab": tab},
                    n_clicks=0, children=[
        html.Div(titre, style={"fontWeight": "700", "fontSize": "15px",
                                             "color": C["noir"], "marginBottom": "6px",
                                             "fontFamily": SERIF}),
        html.Div(texte, style={"color": C["txt_doux"], "fontSize": "12.5px",
                               "lineHeight": "1.5"}),
        html.Div("Ouvrir →", style={"color": C["or"], "fontSize": "12px",
                                    "fontWeight": "700", "marginTop": "10px"}),
    ])


def onglet_accueil():
    dfj = charger_journalier()
    plan = charger_plan()
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")

    # ── Jour de production affiché (aujourd'hui si couvert, sinon le 1er dispo)
    cards = []
    jour, dfj_jour = None, None
    ventes = _ventes_brutes()
    if dfj is not None and not dfj.empty:
        auj = pd.Timestamp.today().normalize()
        jours = sorted(dfj["Date"].unique())
        futurs = [j for j in jours if pd.Timestamp(j) >= auj]
        jour = futurs[0] if futurs else jours[-1]
        dfj_jour = dfj[dfj["Date"] == jour]
        total_jour = dfj_jour["Qty_Recommandee"].fillna(0).sum()
        # tendance vs la réalité des 7 derniers jours connus
        sous = f"unités · {jour_label(jour)}"
        if ventes is not None and not ventes.empty:
            fin_v = ventes["Date"].max()
            reel7 = (ventes[ventes["Date"] > fin_v - pd.Timedelta(days=7)]
                     .groupby("Date")["Quantite"].sum().mean())
            if reel7 and reel7 > 0:
                d = (total_jour / reel7 - 1) * 100
                fleche = "▲" if d >= 0 else "▼"
                sous = f"{jour_label(jour)} · {fleche} {d:+.0f}% vs moyenne réelle 7 j"
        cards.append(kpi("À produire " + ("aujourd'hui" if jour == auj else "au prochain jour"),
                         fmt(total_jour), sous))
    gp = lire("previsions_global.csv")
    hist_g = lire("historique_agrege_global.csv")
    if gp is not None and not gp.empty:
        l0 = gp.iloc[0]
        ca = float(l0.get("Rev_Prev_Selection", l0.get(f"Rev_Prev_{MODELE}", 0)))
        # le pipeline prévoit en HT (panel xlsx) ; le gérant compare au total
        # caisse TTC → afficher le TTC en principal, le HT en rappel.
        ca_ttc = ca * _ratio_ttc_ht()
        sous_ca = (f"pour {mois_label(pd.Timestamp(l0['Date']).strftime('%Y-%m'))} "
                   f"· {fmt(ca)} MAD HT")
        if hist_g is not None and not hist_g.empty:
            dernier_reel = float(hist_g.iloc[-1]["Chiffre_Affaires_Total"])
            if dernier_reel > 0:
                d = (ca / dernier_reel - 1) * 100
                fleche = "▲" if d >= 0 else "▼"
                sous_ca += f" · {fleche} {d:+.0f}% vs dernier mois réel"
        cards.append(kpi("Chiffre d'affaires prévu (TTC)", fmt(ca_ttc) + " MAD",
                         sous_ca, couleur=C["brun"]))
    pf = _prochaine_fete()
    if pf:
        cards.append(kpi("Prochaine fête", pf[0].replace(" 2026", "").replace(" 2027", ""),
                         f"dans {pf[1]} jours — les prévisions en tiennent déjà compte"))
    if plan is not None and "Fiabilite" in plan.columns and len(plan):
        pct = 100 * (plan["Fiabilite"] == "Fiable").mean()
        cards.append(kpi("Prévisions fiables", f"{pct:.0f} %",
                         "part des produits bien prévus", couleur=C["vert"]))
    comp_suivi = charger_suivi()
    if comp_suivi is not None and not comp_suivi.empty:
        from paul_forecast import suivi as pf_suivi
        ms = pf_suivi.metriques_globales(comp_suivi)
        if ms["wmape"] is not None:
            b = ms["biais_pct"] or 0
            sous = ("colle au réel" if abs(b) <= 5 else
                    f"{'sur' if b > 0 else 'sous'}-production ~{abs(b):.0f}%")
            cards.append(kpi("Prévu vs réel", f"{ms['wmape']*100:.0f} %",
                             f"écart moyen · {sous}",
                             couleur=(C["vert"] if ms["wmape"] < 0.25 else
                                      C["or"] if ms["wmape"] < 0.45 else C["rouge"])))

    # ── Top 10 à produire ce jour ─────────────────────────────────────────────
    bloc_top = html.Div()
    if dfj_jour is not None and not dfj_jour.empty:
        top = (dfj_jour[["Produit", "Famille", "Qty_Recommandee"]]
               .sort_values("Qty_Recommandee", ascending=False).head(10))
        top = pd.DataFrame({"Produit": top["Produit"], "Catégorie": top["Famille"],
                            "À produire": top["Qty_Recommandee"].round().astype(int)})
        bloc_top = panneau(
            titre_section(f"Top 10 à produire — {jour_label(jour)}"),
            table_style(top, page=10),
            legende("Liste complète (toutes catégories, tous produits, export Excel) "
                    "dans Production → « Du jour »."),
        )

    # ── Alertes : produits en flambée / chute + événements à venir ───────────
    lignes_alertes = []
    # Fraîcheur des données : au-delà de quelques jours de retard, les prévisions
    # « du jour » reposent sur un passé qui date — le signaler en premier.
    if ventes is not None and not ventes.empty:
        retard = (pd.Timestamp.today().normalize() - ventes["Date"].max()).days
        if retard > 3:
            lignes_alertes.append(html.Div(className="alerte-ligne", children=[
                html.Span(f"Ventes réelles arrêtées au {ventes['Date'].max():%d/%m/%Y} "
                          f"({retard} jours de retard)", style={"fontWeight": "600"}),
                html.Span("mettre à jour donnees_ventes/ puis « Relancer le calcul »",
                          style={"color": C["rouge"]}),
            ]))
    try:
        al = pf_fj.alertes_niveau()
        for _, r in (al.head(5).iterrows() if al is not None and not al.empty else []):
            lignes_alertes.append(html.Div(className="alerte-ligne", children=[
                html.Span(f"{r['Produit']} ({r['Famille']})", style={"fontWeight": "600"}),
                html.Span(f"{r['Variation']} vs l'an dernier — vérifier la prévision",
                          style={"color": C["rouge"]}),
            ]))
    except Exception:
        pass
    auj = pd.Timestamp.today().normalize()
    for e in lire_evenements():
        try:
            d0 = pd.Timestamp(e["date"])
        except (KeyError, ValueError):
            continue
        if auj <= d0 <= auj + pd.Timedelta(days=30):
            nom = (e.get("nom") or "").strip() or \
                  ("Match " + (e.get("adversaire") or "")).strip()
            lignes_alertes.append(html.Div(className="alerte-ligne", children=[
                html.Span(f"{nom}", style={"fontWeight": "600"}),
                html.Span(f"le {d0:%d/%m} — boost déjà appliqué aux prévisions",
                          style={"color": C["vert"]}),
            ]))
    for c in pf_commandes.commandes_normalisees():
        if auj <= c["date"] <= auj + pd.Timedelta(days=30):
            qui = f" ({c['client']})" if c["client"] else ""
            lignes_alertes.append(html.Div(className="alerte-ligne", children=[
                html.Span(f"Commande client{qui} : {c['quantite']:.0f} × {c['produit']}",
                          style={"fontWeight": "600"}),
                html.Span(f"le {c['date']:%d/%m} — ajoutée à la production et aux matières",
                          style={"color": C["bleu"]}),
            ]))
    bloc_alertes = panneau(
        titre_section("À surveiller"),
        html.Div(lignes_alertes) if lignes_alertes
        else html.Div("Rien à signaler : pas de produit en rupture de tendance ni "
                      "d'événement dans les 30 prochains jours.",
                      style={"color": C["txt_doux"], "fontSize": "13px"}),
    )

    # ── Comment ça marche (flux) + où cliquer pour quoi ──────────────────────
    nb_ventes = f"{len(ventes):,}".replace(",", " ") if ventes is not None else "—"
    annees = ""
    if ventes is not None and not ventes.empty:
        annees = f"{ventes['Date'].min():%Y} → {ventes['Date'].max():%d/%m/%Y}"
    etapes = [
        ("", "1 · Ventes réelles", "Chaque ticket de vente sert de base d'apprentissage.",
         f"{nb_ventes} lignes · {annees}"),
        ("🧠", "2 · Modèles", "9 modèles testés ; le meilleur est retenu produit par produit.",
         "erreur mesurée : ≈ 8 % à 1 mois"),
        ("📈", "3 · Prévisions", "Quantités par jour et par mois, fêtes et matchs intégrés.",
         "Ramadan, Aïds, matchs : automatique"),
        ("", "4 · Production & achats", "Quoi produire, quoi commander, avec marge de sécurité.",
         "couvre 95 % des cas (anti-rupture)"),
    ]
    flux = []
    for i, (ic, t, s, chiffre) in enumerate(etapes):
        flux.append(html.Div(className="flux-etape", children=[
            html.Div(ic, style={"fontSize": "22px"}),
            html.Div(t, style={"fontWeight": "700", "margin": "4px 0", "fontSize": "13.5px"}),
            html.Div(s, style={"color": C["txt_doux"], "fontSize": "12px", "lineHeight": "1.45"}),
            html.Div(chiffre, className="flux-chiffre"),
        ]))
        if i < len(etapes) - 1:
            flux.append(html.Div("→", className="flux-fleche"))

    nav = [
        _carte_nav("Production du jour",
                   "Ce qu'il faut produire chaque jour, produit par produit, avec export Excel pour l'équipe.",
                   "journalier"),
        _carte_nav("Production du mois",
                   "Le plan du mois prochain : quantité recommandée par produit (avec stock de sécurité).",
                   "previsions"),
        _carte_nav("Matières premières",
                   "Le bon de commande : farine, beurre, sucre… calculés depuis les recettes.",
                   "mrp"),
        _carte_nav("Événements & matchs",
                   "Un match ou un événement approche ? Ajoute sa date ici, l'impact est appliqué automatiquement.",
                   "evenements"),
        _carte_nav("Commandes clients",
                   "Une entreprise commande en grande quantité ? Déclare date, produit et quantité : "
                   "tout est ajouté aux prévisions et aux matières premières.",
                   "commandes"),
        _carte_nav("Vérifier une prévision",
                   "Compare une prévision aux ventes passées du produit (jour, semaine, mois).",
                   "historique"),
        _carte_nav("Chiffre d'affaires & marges",
                   "CA prévu, marges par produit, meilleurs contributeurs.",
                   "ca"),
    ]

    return html.Div([
        html.Div([
            html.Div(f"{jour_label(pd.Timestamp.today()).capitalize()}",
                     style={"color": C["txt_doux"], "fontSize": "13px",
                            "letterSpacing": "1px", "textTransform": "uppercase"}),
            html.H3("Bonjour — voici la situation en un coup d'œil",
                    style={"fontFamily": SERIF, "fontSize": "24px", "color": C["noir"],
                           "margin": "6px 0 18px"}),
        ]),
        rangee_kpis(*cards) if cards else html.Div(),
        html.Div([bloc_top, bloc_alertes],
                 style={"display": "grid", "gridTemplateColumns": "1.2fr 1fr",
                        "gap": "18px", "alignItems": "start"}),
        panneau(
            titre_section("Comment ça marche"),
            html.Div(flux, style={"display": "flex", "gap": "10px", "flexWrap": "wrap",
                                  "marginBottom": "6px"}),
        ),
        titre_section("Que voulez-vous faire ?"),
        html.Div(nav, style={"display": "flex", "gap": "14px", "flexWrap": "wrap"}),
    ])


# ── Description + aide contextuelle de chaque onglet (pour un nouvel utilisateur)
INFOS_ONGLETS = {
    "accueil": ("L'essentiel du jour, les alertes, et par où commencer.", []),
    "journalier": (
        "Combien produire CHAQUE JOUR, produit par produit.",
        ["Choisis une date et une catégorie → le tableau donne les quantités à produire.",
         "« À produire » inclut une marge de sécurité au-dessus de la prévision brute.",
         "Boutons d'export Excel pour donner la liste à l'équipe de production.",
         "En bas : corrections manuelles (forcer une quantité) et alertes de tendance."]),
    "previsions": (
        "Le plan de production du MOIS prochain, par produit.",
        ["« À produire » = prévision + stock de sécurité (95 % de chances de couvrir la demande).",
         "La colonne Fiabilité te dit quels chiffres suivre les yeux fermés (vert) ou vérifier (rouge).",
         "En bas : la courbe sur 12 mois de n'importe quel produit."]),
    "historique": (
        "Explorer les ventes passées pour VÉRIFIER une prévision.",
        ["Choisis un produit, une granularité (jour/semaine/mois) et une période.",
         "Les barres vertes = prévision à venir, à comparer aux barres dorées (réel passé).",
         "Sers-t'en pour vérifier qu'une prévision est logique avant de t'y fier."]),
    "mrp": (
        "Le bon de commande MATIÈRES PREMIÈRES (farine, beurre, sucre…).",
        ["Les besoins sont calculés depuis les prévisions × les recettes de chaque produit.",
         "Choisis le mois → top 20 en graphique + tableau complet filtrable.",
         "La farine est calée sur la consommation réelle rapportée par le chef (3 t / 3 semaines)."]),
    "evenements": (
        "Déclarer un MATCH ou un ÉVÉNEMENT pour booster les prévisions.",
        ["Pour un match : donne juste la date (+ importance) — l'impact est appris des matchs passés.",
         "Pour le reste (férié, concert, promo…) : indique un % d'impact estimé.",
         "Après un ajout, clique « Relancer le calcul » (en haut à droite) pour mettre à jour."]),
    "assistant": (
        "Un ASSISTANT qui répond à tes questions à partir de tes vraies données — 100 % en local.",
        ["Choisis le TYPE de question, complète les champs, clique « Répondre ».",
         "Chaque chiffre vient d'un fichier : rien n'est inventé, et AUCUNE donnée ne quitte l'ordinateur.",
         "Les réponses reflètent le dernier calcul — relance-le si les ventes ont changé."]),
    "commandes": (
        "Les GROSSES COMMANDES d'entreprises (fast-food, traiteur…) : passées et à venir.",
        ["Une commande connue à l'avance ? Saisis date + produit + quantité : elle est ajoutée "
         "telle quelle à la production ET aux matières premières.",
         "Le tableau du bas liste les pics de vente passés qui ressemblent à des commandes "
         "(« possible commande », marqués ◆ dans l'Historique).",
         "Ces pics passés sont neutralisés automatiquement : ils ne faussent pas les prévisions "
         "des jours normaux.",
         "Après un ajout, clique « Relancer le calcul » (en haut à droite) pour mettre à jour."]),
    "ca": (
        "Le CHIFFRE D'AFFAIRES : historique, prévisions et marges par produit.",
        ["Courbe historique + prévision 12 mois, avec l'explication des pics (fêtes, saisons).",
         "Marges par produit : prix de vente réel − coût matières estimé.",
         "Stats année par année en bas."]),
    "backtest": (
        "La FIABILITÉ des prévisions + le suivi quotidien prévu vs réel.",
        ["En haut : suivi « prévu vs réel » des jours récents et produits à biais systématique.",
         "En bas : validation mensuelle — le modèle prédit sans connaître le résultat, puis on compare.",
         "≈ 8 % d'erreur moyenne à 1 mois sur le chiffre d'affaires global.",
         "Un produit toujours sur-produit → corrige-le (onglet Production du jour) ou vérifie sa recette."]),
}


def bandeau_onglet(tab):
    """Sous-titre + aide repliable affichés au-dessus du contenu de l'onglet."""
    desc, points = INFOS_ONGLETS.get(tab, ("", []))
    if not desc:
        return html.Div()
    enfants = [html.Div(desc, style={"color": C["brun"], "fontSize": "14px",
                                     "fontWeight": "600", "marginBottom": "10px"})]
    if points:
        enfants.append(html.Details(className="aide", children=[
            html.Summary("Comment utiliser cet onglet ?"),
            html.Ul([html.Li(p) for p in points], style={"margin": "4px 0 4px 18px",
                                                         "paddingLeft": "0"}),
        ]))
    return html.Div(enfants)


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION DASH
# ═══════════════════════════════════════════════════════════════════════════════
app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "PAUL — Prévisions & Approvisionnement"

TAB_STYLE = {
    "backgroundColor": "transparent", "border": "none",
    "borderBottom": "3px solid transparent", "color": C["txt_doux"],
    "padding": "12px 22px", "fontFamily": SANS, "fontWeight": "500", "fontSize": "14px",
}
TAB_SEL = {**TAB_STYLE, "borderBottom": f"3px solid {C['or']}",
           "color": C["noir"], "fontWeight": "700"}


def _tab(label, value):
    return dcc.Tab(label=label, value=value, style=TAB_STYLE, selected_style=TAB_SEL)


# Sous-onglets (secondaires, à l'intérieur d'un groupe) — style plus discret.
SOUS_TAB_STYLE = {
    "backgroundColor": "transparent", "border": "none",
    "borderBottom": "2px solid transparent", "color": C["txt_doux"],
    "padding": "8px 16px", "fontFamily": SANS, "fontWeight": "500", "fontSize": "13px",
}
SOUS_TAB_SEL = {**SOUS_TAB_STYLE, "borderBottom": f"2px solid {C['brun']}",
                "color": C["noir"], "fontWeight": "700"}


def _sous_tab(label, value):
    return dcc.Tab(label=label, value=value, style=SOUS_TAB_STYLE, selected_style=SOUS_TAB_SEL)


def _layout():
    return html.Div(style={"background": C["creme"], "minHeight": "100vh",
                           "fontFamily": SANS}, children=[
        # Thème complémentaire (survols, aide, cartes) — lien explicite,
        # l'auto-injection des assets n'est pas fiable avec cette version de Dash.
        html.Link(rel="stylesheet", href=app.get_asset_url("paul_extra.css")),

        # ── Barre supérieure ──────────────────────────────────────────────────
        html.Div(style={
            "background": C["noir"], "padding": "0 40px",
            "display": "flex", "alignItems": "center", "justifyContent": "space-between",
            "height": "64px", "boxShadow": "0 2px 10px rgba(0,0,0,.3)",
        }, children=[
            html.Div([
                html.Span("PAUL", style={"fontFamily": SERIF, "fontSize": "28px",
                                         "fontWeight": "700", "color": C["or"],
                                         "letterSpacing": "4px"}),
                html.Span("  Maison fondée en 1889 · Prévisions & Approvisionnement",
                          style={"color": C["txt_doux"], "fontSize": "13px",
                                 "marginLeft": "16px"}),
            ]),
            html.Div([
                html.Button("Recharger", id="btn-reload", n_clicks=0,
                            title="Recharge l'affichage depuis les derniers fichiers "
                                  "calculés (rapide).",
                            style={
                    "background": "transparent", "border": f"1px solid {C['or']}",
                    "color": C["or"], "borderRadius": "6px", "padding": "7px 18px",
                    "cursor": "pointer", "fontFamily": SANS, "fontSize": "13px",
                    "marginRight": "10px",
                }),
                html.Button("Relancer le calcul", id="btn-run", n_clicks=0,
                            title="Recalcule toutes les prévisions (plusieurs minutes). "
                                  "À faire après un nouvel événement/match ou de nouvelles "
                                  "données de ventes.",
                            style={
                    "background": C["or"], "border": "none", "color": C["noir"],
                    "borderRadius": "6px", "padding": "7px 18px",
                    "cursor": "pointer", "fontFamily": SANS, "fontWeight": "700",
                    "fontSize": "13px",
                }),
            ]),
        ]),

        # ── Corps ─────────────────────────────────────────────────────────────
        html.Div(style={"maxWidth": "1400px", "margin": "0 auto",
                        "padding": "28px 32px"}, children=[

            html.H2("Tableau de bord — Production & Prévisions",
                    style={"fontFamily": SERIF, "color": C["txt"], "marginBottom": "4px",
                           "fontSize": "26px"}),
            html.Div(id="lbl-statut", children=_fraicheur(),
                     style={"color": C["txt_doux"], "fontSize": "13px",
                            "marginBottom": "20px"}),

            dcc.Loading(html.Div(id="zone-statut"), type="circle", color=C["or"]),

            dcc.Store(id="vue-cible"),
            dcc.Tabs(id="tabs", value="accueil", style={
                "borderBottom": f"1px solid {C['bordure']}",
                "marginBottom": "16px", "background": "transparent",
            }, children=[_tab(lbl, gid) for gid, lbl, _ in GROUPES]),

            dcc.Loading(html.Div(id="contenu-onglet"), type="circle", color=C["or"]),
        ]),
    ])


app.layout = _layout


# ── Regroupement des onglets par thème (onglets principaux + sous-onglets) ─────
# Chaque groupe : (id, libellé) → liste de vues (id, libellé court, fonction).
# Les vues qui « se ressemblent » sont réunies sous un même onglet principal.
GROUPES = [
    ("accueil",    "Accueil",
     [("accueil", "Accueil", onglet_accueil)]),
    ("production", "Production",
     [("journalier", "Du jour",           onglet_journalier),
      ("previsions", "Du mois",           onglet_previsions),
      ("mrp",        "Matières premières", onglet_mrp)]),
    ("planning",   "Événements & commandes",
     [("evenements", "Événements & matchs", onglet_evenements),
      ("commandes",  "Commandes clients",   onglet_commandes)]),
    ("assistant",  "Assistant",
     [("assistant", "Assistant", onglet_assistant)]),
    ("analyse",    "Analyse",
     [("ca",         "CA & marges", onglet_ca),
      ("historique", "Historique",  onglet_historique),
      ("backtest",   "Fiabilité",   onglet_backtest)]),
]
GROUPE_VUES = {gid: vues for gid, _, vues in GROUPES}
VUE_FN      = {vid: fn for _, _, vues in GROUPES for vid, _, fn in vues}
VUE_GROUPE  = {vid: gid for gid, _, vues in GROUPES for vid, _, _ in vues}


@app.callback(Output("contenu-onglet", "children"),
              Input("tabs", "value"), State("vue-cible", "data"))
def afficher_groupe(groupe, vue_cible):
    """Affiche un groupe : soit sa vue unique, soit une barre de sous-onglets."""
    vues = GROUPE_VUES.get(groupe) or GROUPE_VUES["accueil"]
    if len(vues) == 1:
        vid = vues[0][0]
        return html.Div([bandeau_onglet(vid), VUE_FN[vid]()])
    ids = [v[0] for v in vues]
    val = vue_cible if vue_cible in ids else ids[0]
    sous = dcc.Tabs(id="sous-tabs", value=val, style={
        "borderBottom": f"1px solid {C['bordure']}", "marginBottom": "14px",
        "background": "transparent"},
        children=[_sous_tab(lbl, vid) for vid, lbl, _ in vues])
    return html.Div([sous, dcc.Loading(html.Div(id="contenu-vue"),
                                       type="circle", color=C["or"])])


@app.callback(Output("contenu-vue", "children"), Input("sous-tabs", "value"))
def afficher_vue(vue):
    fn = VUE_FN.get(vue)
    if fn is None:
        return no_update
    return html.Div([bandeau_onglet(vue), fn()])


@app.callback(Output("tabs", "value"), Output("vue-cible", "data"),
              Input({"type": "nav-card", "tab": ALL}, "n_clicks"),
              prevent_initial_call=True)
def naviguer_depuis_accueil(clics):
    """Les cartes « Que voulez-vous faire ? » de l'accueil ouvrent la vue visée
    (bon groupe + bon sous-onglet)."""
    if not isinstance(clics, (list, tuple)):
        clics = [clics]
    if not any(c for c in clics if c):
        return no_update, no_update
    t = ctx.triggered_id
    vid = t.get("tab") if isinstance(t, dict) else None
    if vid in VUE_GROUPE:
        return VUE_GROUPE[vid], vid
    return no_update, no_update


@app.callback(Output("dd-produit-prev", "options"), Output("dd-produit-prev", "value"),
              Input("dd-categorie-prev", "value"))
def maj_produits_par_categorie(categorie):
    _, _, det, _ = charger_tout()
    if det is None or "Produit" not in det.columns:
        return [], None
    sous = det
    if categorie is not None and "Famille" in det.columns:
        sous = det[det["Famille"].astype(str) == str(categorie)]
    produits = sorted(sous["Produit"].astype(str).unique().tolist())
    options = [{"label": p, "value": p} for p in produits]
    return options, (produits[0] if produits else None)


@app.callback(Output("zone-table-plan", "children"), Input("dd-cat-plan", "value"))
def maj_table_plan(categorie):
    plan = charger_plan()
    if plan is None or plan.empty:
        return html.Div("Plan indisponible.", style={"color": C["txt_doux"]})
    return table_plan_production(plan_pour_table(plan, categorie))


# ── Production journalière ─────────────────────────────────────────────────────
@app.callback(Output("zone-table-jour", "children"), Output("titre-jour", "children"),
              Input("dd-jour", "date"), Input("dd-cat-jour", "value"))
def maj_table_jour(jour, categorie):
    dfj = charger_journalier()
    if dfj is None or dfj.empty or not jour:
        return html.Div("Prévisions journalières indisponibles.",
                        style={"color": C["txt_doux"]}), ""
    j = pd.Timestamp(jour)
    df = jour_pour_table(dfj, j, categorie)
    total = int(df["À produire"].fillna(0).sum())
    titre = f"{jour_label(j).capitalize()} — {total:,} unités à produire".replace(",", " ")
    contenu = [table_jour(df)]
    # notes produit (cause CONFIRMÉE d'un pic, ex. client B2B identifié) : rappel
    # sous le tableau pour les produits affichés avec une part « client récent ».
    if "Dont client récent" in df.columns:
        for prod in df.loc[df["Dont client récent"].notna(), "Produit"]:
            note = _note_produit(prod)
            if note:
                contenu.append(html.Div(f"ℹ️ {prod} : {note['texte']}", style={
                    "background": C["bleu"] + "1a", "border": f"1px solid {C['bleu']}",
                    "borderRadius": "8px", "padding": "10px 14px", "marginTop": "10px",
                    "color": C["txt"], "fontFamily": SANS, "fontSize": "13px"}))
    return html.Div(contenu), titre


@app.callback(Output("dd-produit-jour", "options"), Output("dd-produit-jour", "value"),
              Input("dd-cat-produit-jour", "value"))
def maj_produits_jour_par_cat(categorie):
    dfj = charger_journalier()
    if dfj is None or dfj.empty or "Produit" not in dfj.columns:
        return [], None
    sous = dfj
    if categorie is not None and "Famille" in dfj.columns:
        sous = dfj[dfj["Famille"].astype(str) == str(categorie)]
    produits = sorted(sous["Produit"].astype(str).unique().tolist())
    return [{"label": p, "value": p} for p in produits], (produits[0] if produits else None)


@app.callback(Output("g-produit-jour", "figure"), Output("legende-produit-jour", "children"),
              Input("dd-produit-jour", "value"))
def maj_produit_jour(prod):
    dfj = charger_journalier()
    fig = fig_base(f"Quantité à produire par jour — {prod}", height=380)
    if dfj is None or dfj.empty or not prod:
        return fig, ""
    s = dfj[dfj["Produit"].astype(str) == str(prod)].sort_values("Date")
    couleurs = [C["or_clair"] if pd.Timestamp(d).dayofweek >= 5 else C["or"] for d in s["Date"]]
    cmd = (pd.to_numeric(s["Qty_Commande"], errors="coerce").fillna(0.0)
           if "Qty_Commande" in s.columns else pd.Series(0.0, index=s.index))
    fig.add_trace(go.Bar(
        x=s["Date"], y=s["Qty_Recommandee"] - cmd, marker_color=couleurs,
        name="Demande prévue",
        hovertemplate="%{x|%a %d/%m}<br>%{y:,.0f} unités<extra></extra>"))
    if (cmd > 0).any():
        # part « commande client » empilée au-dessus de la demande prévue
        fig.add_trace(go.Bar(
            x=s["Date"], y=cmd, marker_color=C["bleu"], name="Commande client",
            hovertemplate="%{x|%a %d/%m}<br>+%{y:,.0f} u — commande client<extra></extra>"))
        fig.update_layout(barmode="stack", showlegend=True,
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    else:
        fig.update_layout(showlegend=False)
    fig.update_yaxes(title="Unités/jour", rangemode="tozero")
    texte = expliquer_courbe_jour(fig, s["Date"], s["Qty_Recommandee"])
    if (cmd > 0).any():
        jours_cmd = ", ".join(pd.Timestamp(d).strftime("%d/%m")
                              for d in s.loc[cmd > 0, "Date"].head(5))
        texte += (f" Les barres bleues sont des commandes clients planifiées ({jours_cmd}), "
                  "ajoutées telles quelles à la production.")
    return fig, legende(texte)


@app.callback(Output("zone-statut-jour", "children"),
              Input("btn-gen-jour", "n_clicks"), prevent_initial_call=True)
def generer_journalier(n):
    try:
        subprocess.run([sys.executable, "-m", "paul_forecast.forecast_journalier"],
                       cwd=RACINE, check=True, timeout=1200,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return html.Div("✓ Prévisions journalières régénérées — rechargez l'onglet.",
                        style={"color": C["vert"], "fontWeight": "600"})
    except Exception as e:
        return html.Div(f"✗ Échec : {e}", style={"color": C["rouge"]})


def _note_produit(prod):
    """Note explicative connue pour ce produit (data/notes_produits.json), ou None."""
    prod = str(prod or "").strip().upper()
    for n in pf_config.NOTES_PRODUITS.get("notes", []):
        if str(n.get("produit", "")).strip().upper() == prod:
            return n
    return None


# ── Historique (exploration des ventes passées) ────────────────────────────────
@app.callback(Output("dd-produit-hist", "options"), Output("dd-produit-hist", "value"),
              Input("dd-cat-hist", "value"))
def maj_produits_hist(cat):
    df = _ventes_brutes()
    if df is None or df.empty:
        return [], None
    sous = df if not cat else df[df["Famille"].astype(str) == str(cat)]
    prods = sorted(sous["Produit"].astype(str).unique().tolist())
    return [{"label": p, "value": p} for p in prods], (prods[0] if prods else None)


@app.callback(Output("g-historique", "figure"), Output("hist-kpis", "children"),
              Output("hist-table", "children"),
              Input("dd-produit-hist", "value"), Input("radio-gran-hist", "value"),
              Input("date-range-hist", "start_date"), Input("date-range-hist", "end_date"))
def maj_historique(prod, gran, d0, d1):
    gran = gran or "Mois"
    unite = {"Jour": "jour", "Semaine": "semaine", "Mois": "mois"}[gran]
    hist = _hist_produit(prod, gran)
    prev = _prev_produit(prod, gran)
    # On ne superpose que les périodes ENTIÈREMENT futures : la période courante
    # (ex. juin) a déjà un historique quasi complet, sa prévision ne couvre que
    # les jours restants → la retirer évite une barre trompeusement petite.
    if not hist.empty and not prev.empty:
        prev = prev[prev["Date"] > hist["Date"].max()]
    if d0:
        d0 = pd.Timestamp(d0); hist = hist[hist["Date"] >= d0]; prev = prev[prev["Date"] >= d0]
    if d1:
        d1 = pd.Timestamp(d1); hist = hist[hist["Date"] <= d1]; prev = prev[prev["Date"] <= d1]

    fig = fig_base(f"Ventes de « {prod} » par {unite}", height=420)
    if hist.empty:
        vide = html.Div("Aucune vente pour ce produit sur la période choisie.",
                        style={"color": C["txt_doux"], "fontStyle": "italic", "padding": "20px"})
        return fig, "", vide

    # Pics « possible commande » du produit (bornés à la période choisie —
    # pas aux dates d'agrégat : en granularité Mois, hist ne contient que des
    # 1ers du mois et exclurait à tort un pic en cours de mois).
    pics = _pics_commandes()
    pics = pics[pics["Produit"] == str(prod)]
    if not pics.empty:
        if d0:
            pics = pics[pics["Date"] >= d0]
        if d1:
            pics = pics[pics["Date"] <= d1]

    note = _note_produit(prod)
    if gran == "Jour":
        if note:
            deb = pd.Timestamp(note["depuis"])
            fin_note = pd.Timestamp(note["jusqu_a"]) if note.get("jusqu_a") else hist["Date"].max()
            libelle = (f"Client « {note['client']} »" if note.get("client")
                       else "Commande B2B connue")
            fig.add_vrect(x0=max(deb, hist["Date"].min()), x1=min(fin_note, hist["Date"].max()),
                          fillcolor=C["bleu"], opacity=0.08, line_width=0,
                          annotation_text=libelle, annotation_position="top left",
                          annotation=dict(font_size=11, font_color=C["bleu"]))
        fig.add_trace(go.Scatter(x=hist["Date"], y=hist["Quantite"], name="Historique",
                                 mode="lines", line=dict(color=C["or"], width=1.6),
                                 hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f} u<extra></extra>"))
        if not prev.empty:
            fig.add_trace(go.Scatter(x=prev["Date"], y=prev["Quantite"], name="Prévision",
                                     mode="lines", line=dict(color=C["vert"], width=2, dash="dot"),
                                     hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f} u (prév.)<extra></extra>"))
        if not pics.empty:
            fig.add_trace(go.Scatter(
                x=pics["Date"], y=pics["Quantite"], mode="markers",
                name="Possible commande", customdata=pics["Attendu"].round(),
                marker=dict(symbol="diamond", size=9, color=C["bleu"],
                            line=dict(color=C["blanc"], width=1)),
                hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f} u — possible commande"
                              "<br>niveau habituel ≈ %{customdata:,.0f} u<extra></extra>"))
        # commandes clients planifiées sur ce produit (déjà incluses dans la prévision)
        cmds_prod = [c for c in pf_commandes.commandes_normalisees()
                     if c["produit"] == str(prod) and not prev.empty
                     and prev["Date"].min() <= c["date"] <= prev["Date"].max()]
        if cmds_prod:
            pl = prev.set_index("Date")["Quantite"]
            fig.add_trace(go.Scatter(
                x=[c["date"] for c in cmds_prod],
                y=[pl.get(c["date"], 0) for c in cmds_prod],
                mode="markers", name="Commande client prévue",
                customdata=[c["quantite"] for c in cmds_prod],
                marker=dict(symbol="star", size=12, color=C["bleu"]),
                hovertemplate="%{x|%d/%m/%Y}<br>commande client : +%{customdata:,.0f} u"
                              "<br>(incluse dans la prévision)<extra></extra>"))
        fig.update_xaxes(rangeslider=dict(visible=True), rangeslider_thickness=0.06)
    else:
        fig.add_trace(go.Bar(x=hist["Date"], y=hist["Quantite"], name="Historique",
                             marker_color=C["or"],
                             hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f} u<extra></extra>"))
        if not prev.empty:
            fig.add_trace(go.Bar(x=prev["Date"], y=prev["Quantite"], name="Prévision",
                                 marker_color=C["vert"],
                                 hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f} u (prév.)<extra></extra>"))
        if not pics.empty:
            # marquer les périodes (semaine/mois) contenant au moins un pic
            cle = _cle_periode(pd.DatetimeIndex(pics["Date"]), gran)
            exces = pics.groupby(cle)["Exces"].sum()
            hh = hist.set_index("Date")["Quantite"]
            periodes = [d for d in exces.index if d in hh.index]
            if periodes:
                fig.add_trace(go.Scatter(
                    x=periodes, y=[float(hh.get(d, 0)) * 1.04 for d in periodes],
                    mode="markers", name="Possible commande",
                    customdata=[round(float(exces[d])) for d in periodes],
                    marker=dict(symbol="diamond", size=10, color=C["bleu"],
                                line=dict(color=C["blanc"], width=1)),
                    hovertemplate="%{x|%d/%m/%Y}<br>possible commande : ≈ +%{customdata:,.0f} u"
                                  " au-dessus du niveau habituel<extra></extra>"))
        fig.update_layout(barmode="group")
    fig.update_yaxes(title=f"Unités / {unite}", rangemode="tozero")
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))

    total = float(hist["Quantite"].sum()); moy = float(hist["Quantite"].mean())
    best = hist.loc[hist["Quantite"].idxmax()]
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    sous_record = _label_periode(best["Date"], gran).split(" · ", 1)[-1]
    if gran == "Jour" and not pics.empty and pd.Timestamp(best["Date"]) in set(pics["Date"]):
        sous_record += " · possible commande"
    pluriel = unite if unite.endswith("s") else unite + "s"
    cards = [
        kpi(f"Total ({pluriel} affichés)", fmt(total), "unités vendues"),
        kpi(f"Moyenne / {unite}", fmt(moy), "sur la période affichée"),
        kpi("Record", fmt(float(best["Quantite"])), sous_record),
    ]
    if not prev.empty:
        cards.append(kpi(f"Prévision moy. / {unite}", fmt(float(prev["Quantite"].mean())),
                         "à comparer à l'historique", couleur=C["vert"]))

    t = hist.copy()
    t["Période"] = t["Date"].apply(lambda d: _label_periode(d, gran))
    t["Quantité"] = t["Quantite"].round().astype(int)
    cols = ["Période", "Quantité"]
    if "CA" in t.columns:
        t["CA (MAD)"] = t["CA"].round().astype(int)
        cols.append("CA (MAD)")
    tt = t[cols].sort_values("Période", ascending=False)
    kpis = rangee_kpis(*cards)
    if note:
        banniere = html.Div(f"ℹ️ {note['texte']}", style={
            "background": C["bleu"] + "1a", "border": f"1px solid {C['bleu']}",
            "borderRadius": "8px", "padding": "10px 14px", "marginBottom": "12px",
            "color": C["txt"], "fontFamily": SANS, "fontSize": "13px"})
        kpis = html.Div([banniere, kpis])
    return fig, kpis, table_style(tt, page=15)


# ── Vue semaine + exports ──────────────────────────────────────────────────────
@app.callback(Output("zone-table-semaine", "children"), Input("dd-cat-semaine", "value"))
def maj_table_semaine(categorie):
    dfj = charger_journalier()
    if dfj is None or dfj.empty:
        return html.Div("Prévisions indisponibles.", style={"color": C["txt_doux"]})
    # Semaine à partir d'aujourd'hui (ou du 1er jour prévu si le fichier date).
    auj = pd.Timestamp.now().normalize()
    futurs = dfj.loc[dfj["Date"] >= auj, "Date"]
    jour0 = futurs.min() if not futurs.empty else dfj["Date"].min()
    return table_semaine(semaine_pour_table(dfj, jour0, categorie))


@app.callback(Output("dl-jour", "data"), Input("btn-export-jour", "n_clicks"),
              State("dd-jour", "date"), State("dd-cat-jour", "value"),
              prevent_initial_call=True)
def export_jour(n, jour, categorie):
    dfj = charger_journalier()
    if dfj is None or dfj.empty or not jour:
        return None
    df = jour_pour_table(dfj, pd.Timestamp(jour), categorie)
    nom = f"production_{pd.Timestamp(jour).strftime('%Y-%m-%d')}.xlsx"
    return dcc.send_data_frame(df.to_excel, nom, sheet_name="À produire", index=False)


@app.callback(Output("dl-semaine", "data"), Input("btn-export-semaine", "n_clicks"),
              State("dd-cat-semaine", "value"), prevent_initial_call=True)
def export_semaine(n, categorie):
    dfj = charger_journalier()
    if dfj is None or dfj.empty:
        return None
    auj = pd.Timestamp.now().normalize()
    futurs = dfj.loc[dfj["Date"] >= auj, "Date"]
    jour0 = futurs.min() if not futurs.empty else dfj["Date"].min()
    df = semaine_pour_table(dfj, jour0, categorie)
    nom = f"production_semaine_{pd.Timestamp(jour0).strftime('%Y-%m-%d')}.xlsx"
    return dcc.send_data_frame(df.to_excel, nom, sheet_name="Semaine", index=False)


# ── Corrections manuelles par produit ─────────────────────────────────────────
@app.callback(
    Output("store-overrides", "data"), Output("ovr-status", "children"),
    Output("ovr-status", "style"),
    Input("btn-add-ovr", "n_clicks"), Input("btn-suppr-ovr", "n_clicks"),
    State("ovr-produit", "value"), State("ovr-mode", "value"), State("ovr-valeur", "value"),
    State("dd-suppr-ovr", "value"), State("store-overrides", "data"),
    prevent_initial_call=True,
)
def muter_overrides(n_add, n_sup, produit, mode, valeur, suppr_id, ovrs):
    ovrs = list(ovrs or [])
    base = {"marginLeft": "16px", "fontFamily": SANS, "fontSize": "13px"}
    erreur = {**base, "color": C["rouge"], "fontWeight": "600"}
    succes = {**base, "color": C["vert"], "fontWeight": "600"}

    if ctx.triggered_id == "btn-add-ovr":
        if not produit or valeur in (None, ""):
            return ovrs, "Choisis un produit et une valeur.", erreur
        try:
            val = float(valeur)
        except (TypeError, ValueError):
            return ovrs, "Valeur invalide.", erreur
        ovrs = [o for o in ovrs if o.get("produit") != produit]  # remplace si déjà présent
        ovrs.append({"id": uuid.uuid4().hex[:8], "produit": produit,
                     "mode": mode or "facteur", "valeur": val})
        ecrire_overrides(ovrs)
        effet = f"×{val}" if (mode or "facteur") == "facteur" else f"= {val:.0f}/jour"
        return ovrs, f"✓ « {produit} » {effet} — régénère pour appliquer.", succes

    if ctx.triggered_id == "btn-suppr-ovr":
        if not suppr_id:
            return ovrs, "Choisis une correction à retirer.", erreur
        ovrs = [o for o in ovrs if o.get("id") != suppr_id]
        ecrire_overrides(ovrs)
        return ovrs, "✓ Correction retirée — régénère pour appliquer.", succes

    return ovrs, "", base


@app.callback(Output("zone-table-ovr", "children"), Output("dd-suppr-ovr", "options"),
              Input("store-overrides", "data"))
def rendre_overrides(ovrs):
    ovrs = list(ovrs or [])
    options = [{"label": o.get("produit", "?"), "value": o.get("id", "")} for o in ovrs]
    return table_overrides(ovrs), options


@app.callback(Output("g-detail-produit", "figure"), Input("dd-produit-prev", "value"))
def maj_detail_produit(prod):
    _, _, det, _ = charger_tout()
    fig = fig_base(f"Quantité à produire par mois — {prod}", height=380)
    if det is None or not prod:
        return fig
    s = det[det["Produit"].astype(str) == str(prod)].sort_values("Date")
    col_qty = f"Qty_Prev_{MODELE}"
    if col_qty in s.columns:
        fig.add_trace(go.Bar(
            x=s["Date"], y=s[col_qty], name="Quantité prévue",
            marker_color=C["or"], opacity=0.9,
            text=[f"{v:,.0f}".replace(",", " ") for v in s[col_qty]],
            textposition="outside",
            hovertemplate="%{x|%b %Y}<br>%{y:,.0f} unités<extra></extra>",
        ))
    fig.update_yaxes(title="Unités à produire", rangemode="tozero")
    return fig


# ── Événements (formulaire unique : matchs + manuels) ─────────────────────────
@app.callback(Output("grp-match", "style"), Output("grp-manuel", "style"),
              Input("ev-type", "value"))
def toggle_champs_evt(typ):
    flex = {"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "14px"}
    cache = {"display": "none"}
    if typ == "match":
        return flex, cache
    return cache, {"marginBottom": "14px"}


@app.callback(
    Output("store-evenements", "data"),
    Output("ev-status", "children"),
    Output("ev-status", "style"),
    Input("btn-add-evt", "n_clicks"),
    Input("btn-suppr-evt", "n_clicks"),
    State("ev-type", "value"), State("ev-date", "date"), State("ev-date-fin", "date"),
    State("ev-adversaire", "value"), State("ev-importance", "value"),
    State("ev-nom", "value"), State("ev-portee", "value"), State("ev-impact", "value"),
    State({"type": "ev-ovr", "cat": ALL}, "value"),
    State({"type": "ev-ovr", "cat": ALL}, "id"),
    State("dd-suppr-evt", "value"),
    State("store-evenements", "data"),
    prevent_initial_call=True,
)
def muter_evenements(n_add, n_sup, typ, date, date_fin, adv, imp,
                     nom, portee, impact, ovr_vals, ovr_ids, suppr_id, evs):
    evs = list(evs or [])
    base = {"marginLeft": "16px", "fontFamily": SANS, "fontSize": "13px"}
    erreur = {**base, "color": C["rouge"], "fontWeight": "600"}
    succes = {**base, "color": C["vert"], "fontWeight": "600"}

    if ctx.triggered_id == "btn-add-evt":
        if not date:
            return evs, "La date est obligatoire.", erreur
        if typ == "match":
            evt = {
                "id": uuid.uuid4().hex[:8], "type": "match",
                "date": date, "date_fin": date_fin or date,
                "adversaire": (adv or "").strip(),
                "importance": IMPORTANCE_MATCH.get(imp, 1.0), "portee": "national",
            }
            libelle = "Match" + (f" — {evt['adversaire']}" if evt["adversaire"] else "")
        else:
            familles_pct = {}
            for v, i in zip(ovr_vals or [], ovr_ids or []):
                if v not in (None, ""):
                    try:
                        familles_pct[i["cat"]] = float(v)
                    except (TypeError, ValueError):
                        pass
            evt = {
                "id": uuid.uuid4().hex[:8], "type": typ or "autre",
                "nom": (nom or "").strip(), "date": date, "date_fin": date_fin or date,
                "portee": portee or "national",
                "impact_pct": float(impact) if impact not in (None, "") else 0.0,
                "familles_pct": familles_pct,
            }
            libelle = evt["nom"] or pf_config.TYPES_EVENEMENTS.get(evt["type"], evt["type"])
        evs.append(evt)
        ecrire_evenements(evs)
        return evs, f"✓ « {libelle} » ajouté.", succes

    if ctx.triggered_id == "btn-suppr-evt":
        if not suppr_id:
            return evs, "Choisis un événement à supprimer.", erreur
        evs = [e for e in evs if e.get("id") != suppr_id]
        ecrire_evenements(evs)
        return evs, "✓ Événement supprimé.", succes

    return evs, "", base


def _libelle_evt(e):
    if e.get("type") == "match":
        adv = (e.get("adversaire") or "").strip()
        return "Match" + (f" {adv}" if adv else "")
    return (e.get("nom") or "").strip() or pf_config.TYPES_EVENEMENTS.get(e.get("type", ""), "Événement")


def _boost_pct_evt(e, profil, imp_moyen):
    """Boost représentatif (%) d'un événement, pour l'aperçu."""
    if e.get("type") == "match":
        return (imp_moyen - 1.0) * float(e.get("importance", 1.0) or 1.0) * 100
    return float(e.get("impact_pct", 0) or 0)


@app.callback(
    Output("zone-table-evt", "children"),
    Output("dd-suppr-evt", "options"),
    Output("g-evt-preview", "figure"),
    Output("match-profil", "children"),
    Input("store-evenements", "data"),
)
def rendre_evenements(evs):
    evs = list(evs or [])
    options = [{"label": f"{_libelle_evt(e)} ({e.get('date', '')})", "value": e.get("id", "")}
               for e in evs]

    profil = pf_matchs.profil_match()
    imp_moyen = (sum(profil.values()) / len(profil)) if profil else 1.0

    fig = fig_base("Boost attendu par événement", height=360)
    visibles = [e for e in evs if _boost_pct_evt(e, profil, imp_moyen) != 0]
    if visibles:
        noms = [_libelle_evt(e) for e in visibles]
        boosts = [_boost_pct_evt(e, profil, imp_moyen) for e in visibles]
        fig.add_trace(go.Bar(
            x=boosts[::-1], y=noms[::-1], orientation="h",
            marker_color=[C["or"] if b >= 0 else C["rouge"] for b in boosts[::-1]],
            text=[f"+{b:.0f}%" if b >= 0 else f"{b:.0f}%" for b in boosts[::-1]],
            textposition="outside",
            hovertemplate="%{y}<br>boost attendu %{x:.0f}%<extra></extra>",
        ))
        fig.update_xaxes(ticksuffix=" %")
    else:
        fig.add_annotation(text="Aucun événement à venir avec impact.",
                           showarrow=False, font=dict(color=C["txt_doux"], size=14))
    return table_evenements(evs), options, fig, profil_match_texte()


# ── Commandes clients (déclaration + suppression) ─────────────────────────────
@app.callback(
    Output("store-commandes", "data"),
    Output("cmd-status", "children"),
    Output("cmd-status", "style"),
    Input("btn-add-cmd", "n_clicks"),
    Input("btn-suppr-cmd", "n_clicks"),
    State("cmd-date", "date"), State("cmd-produit", "value"),
    State("cmd-qte", "value"), State("cmd-client", "value"),
    State("dd-suppr-cmd", "value"), State("store-commandes", "data"),
    prevent_initial_call=True,
)
def muter_commandes(n_add, n_sup, date, produit, qte, client, suppr_id, cmds):
    cmds = list(cmds or [])
    base = {"marginLeft": "16px", "fontFamily": SANS, "fontSize": "13px"}
    erreur = {**base, "color": C["rouge"], "fontWeight": "600"}
    succes = {**base, "color": C["vert"], "fontWeight": "600"}

    if ctx.triggered_id == "btn-add-cmd":
        if not date or not produit or qte in (None, ""):
            return cmds, "Date, produit et quantité sont obligatoires.", erreur
        try:
            q = float(qte)
        except (TypeError, ValueError):
            return cmds, "Quantité invalide.", erreur
        if q <= 0:
            return cmds, "La quantité doit être positive.", erreur
        cmds.append({"id": uuid.uuid4().hex[:8], "date": date, "produit": str(produit),
                     "quantite": q, "client": (client or "").strip()})
        ecrire_commandes(cmds)
        return (cmds, f"✓ {q:.0f} × « {produit} » le "
                      f"{pd.Timestamp(date).strftime('%d/%m/%Y')} — relance le calcul "
                      "pour l'ajouter aux prévisions.", succes)

    if ctx.triggered_id == "btn-suppr-cmd":
        if not suppr_id:
            return cmds, "Choisis une commande à supprimer.", erreur
        cmds = [c for c in cmds if c.get("id") != suppr_id]
        ecrire_commandes(cmds)
        return cmds, "✓ Commande supprimée — relance le calcul pour appliquer.", succes

    return cmds, "", base


@app.callback(Output("zone-table-cmd", "children"), Output("dd-suppr-cmd", "options"),
              Input("store-commandes", "data"))
def rendre_commandes(cmds):
    cmds = list(cmds or [])
    options = [{"label": f"{c.get('quantite', 0):.0f} × {c.get('produit', '?')} "
                         f"({c.get('date', '')})", "value": c.get("id", "")}
               for c in cmds]
    return table_commandes(cmds), options


# ── Assistant guidé (100 % local : interroge les données, aucune sortie réseau) ─
@app.callback([Output(g, "style") for g in _GRP_IDS], Input("q-type", "value"))
def maj_champs_assistant(type_q):
    visibles = _CHAMPS_PAR_QUESTION.get(type_q, set())
    visible = {"minWidth": "220px", "flex": "1"}
    return [visible if g in visibles else {"display": "none"} for g in _GRP_IDS]


@app.callback(
    Output("zone-q-reponse", "children"),
    Input("btn-q-run", "n_clicks"),
    State("q-type", "value"), State("q-produit", "value"), State("q-date", "date"),
    State("q-categorie", "value"), State("q-mois", "value"), State("q-gran", "value"),
    prevent_initial_call=True,
)
def repondre_assistant(n, type_q, produit, date, categorie, mois, gran):
    res = pf_assistant.interroger(type_q, produit=produit, date=date, categorie=categorie,
                                  mois=mois, granularite=gran)
    contenu = [html.Div(res["texte"], style={
        "background": C["carte"], "border": f"1px solid {C['bordure']}", "borderRadius": "8px",
        "padding": "14px 16px", "color": C["txt"], "fontFamily": SANS, "fontSize": "14.5px",
        "fontWeight": "600", "lineHeight": "1.5"})]
    if res.get("table"):
        contenu.append(html.Div(table_style(pd.DataFrame(res["table"]), page=15),
                                style={"marginTop": "14px"}))
    return contenu


def _bandeau_budget(mois, mrp):
    """Cartes budget d'achat + food-cost pour le mois, à partir des prix estimés."""
    mois_df = mrp[mrp["Date"].dt.strftime("%Y-%m") == mois]
    if mois_df.empty:
        return None, {}
    coste = (mois_df.groupby("Ingredient", as_index=False)["Quantite_Requise"].sum())
    _, total, couv = pf_couts.chiffrer_besoins(coste)
    # CA prévu du même mois (modèle Selection sinon Holt-Winters)
    gp = lire("previsions_global.csv")
    ca = None
    if gp is not None:
        gp = gp.copy(); gp["Date"] = pd.to_datetime(gp["Date"])
        row = gp[gp["Date"].dt.strftime("%Y-%m") == mois]
        if not row.empty:
            for c in ("Rev_Prev_Selection", "Rev_Prev_Holt_Winters"):
                if c in row.columns:
                    ca = float(row.iloc[0][c]); break
    cards = [kpi("Budget d'achat estimé", f"{total:,.0f} {pf_couts.DEVISE}".replace(",", " "),
                 f"matières · {mois}")]
    if ca:
        cards.append(kpi("Food-cost (plancher)", f"{100*total/ca:.1f} %",
                         "part du CA en matières", couleur=C["brun"]))
    cards.append(kpi("Couverture prix", f"{couv*100:.0f} %",
                     "ingrédients avec un prix connu", couleur=C["txt_doux"]))
    return rangee_kpis(*cards), {"total": total}


def _mrp_filtre_categorie(categorie):
    """Besoins matières restreints à une catégorie (depuis le détail MRP).

    Retourne (df type « planifiés » Date/Ingredient/Quantite_Requise, % du besoin
    issu de recettes exactes) ou (None, None) si le détail est indisponible.
    """
    det = charger_mrp_detail()
    if det is None or det.empty or "Famille" not in det.columns:
        return None, None
    d = det[det["Famille"].astype(str).str.strip().str.upper() == categorie]
    if d.empty:
        return None, None
    pct_exact = None
    if "Source_Couverture" in d.columns:
        tot = d["Quantite_Requise"].sum()
        if tot > 0:
            pct_exact = d.loc[d["Source_Couverture"] == "recette_exacte",
                              "Quantite_Requise"].sum() / tot * 100
    agg = (d.groupby(["Date", "Ingredient"], as_index=False)["Quantite_Requise"].sum())
    return agg, pct_exact


@app.callback(Output("g-mrp-bar", "figure"), Output("t-mrp-table", "children"),
              Output("mrp-budget", "children"), Output("mrp-fiabilite-cat", "children"),
              Input("dd-mois-mrp", "value"), Input("dd-mrp-categorie", "value"))
def maj_mrp(mois, categorie):
    _, _, _, mrp = charger_tout()
    titre_cat = "" if not categorie or categorie == _TOUTES_CAT \
                else f" · {categorie.capitalize()}"
    fig = fig_base(f"Top 20 matières premières — {mois}{titre_cat}", height=440)
    if mrp is None or not mois:
        return fig, None, None, None

    note_fiab = None
    if categorie and categorie != _TOUTES_CAT:
        mrp_cat, pct_exact = _mrp_filtre_categorie(categorie)
        if mrp_cat is None:
            return fig, html.Div(
                "Détail par catégorie indisponible — relancez le calcul "
                "(besoins_ingredients_detail.csv manquant ou sans colonne Famille).",
                style={"color": C["txt_doux"]}), None, None
        mrp = mrp_cat
        if pct_exact is not None:
            fiable = pct_exact >= 95
            note_fiab = html.Div(
                ("✔ " if fiable else "⚠ ")
                + f"{pct_exact:.0f} % du besoin {categorie.capitalize()} vient de "
                + ("recettes exactes (fiches chef) — chiffres utilisables tels quels."
                   if fiable else
                   "recettes exactes ; le reste est ESTIMÉ en attendant les recettes chef "
                   "— chiffres indicatifs."),
                style={"padding": "10px 14px", "borderRadius": "8px",
                       "background": "#e8f4e8" if fiable else "#fdf3e0",
                       "color": "#2e5e2e" if fiable else "#7a5218",
                       "fontFamily": SANS, "fontSize": "14px"})

    bandeau, _ = _bandeau_budget(mois, mrp)

    s = (mrp[mrp["Date"].dt.strftime("%Y-%m") == mois]
         .groupby("Ingredient")["Quantite_Requise"].sum()
         .sort_values(ascending=False).head(20).reset_index())

    # Convertir en kg/L si unité g ou ml
    def convertir(row):
        ing = str(row["Ingredient"])
        val = row["Quantite_Requise"]
        if "(g)" in ing:
            return round(val / 1000, 2), "kg"
        if "(ml)" in ing:
            return round(val / 1000, 2), "L"
        return round(val, 1), "unité"

    s[["Qte_conv","Unite"]] = s.apply(convertir, axis=1, result_type="expand")
    import re as _re
    s["Nom"] = s["Ingredient"].apply(lambda x: _re.sub(r'\s*\([^)]*\)\s*$', '', str(x)))
    s_inv = s.iloc[::-1]

    fig.add_trace(go.Bar(
        x=s_inv["Qte_conv"], y=s_inv["Nom"], orientation="h",
        marker_color=C["or"],
        text=[f"{v:.1f} {u}" for v, u in zip(s_inv["Qte_conv"], s_inv["Unite"])],
        textposition="outside",
        hovertemplate="%{y}<br>%{x:.1f}<extra></extra>",
    ))
    fig.update_xaxes(title="Quantité (kg ou L)")

    df_table = s[["Nom","Qte_conv","Unite"]].copy()
    df_table.columns = ["Ingrédient","Quantité","Unité"]
    couts = [pf_couts.cout_ligne(ing, q) for ing, q in zip(s["Ingredient"], s["Quantite_Requise"])]
    df_table["Coût (MAD)"] = [f"{c:,.0f}".replace(",", " ") if c is not None else "—" for c in couts]
    return fig, table_style(df_table, page=20), bandeau, note_fiab


# ── Besoins matières par département / produit (traçabilité) ───────────────────
_TOUT_DEPT = "__TOUT__"


@app.callback(Output("dd-mrp-dept-produit", "options"),
              Output("dd-mrp-dept-produit", "value"),
              Input("dd-mrp-dept-mois", "value"), Input("dd-mrp-dept", "value"))
def maj_produits_dept(mois, dept):
    det = charger_mrp_detail()
    base = [{"label": "— Tout le département —", "value": _TOUT_DEPT}]
    if det is None or det.empty or not mois or not dept:
        return base, _TOUT_DEPT
    d = det[(det["Date"].dt.strftime("%Y-%m") == mois) & (det["Famille"].astype(str) == str(dept))]
    # Produits triés par poids matières décroissant (les plus « lourds » d'abord).
    ordre = (d.groupby("Produit")["Quantite_Requise"].sum()
             .sort_values(ascending=False).index.tolist())
    return base + [{"label": p, "value": p} for p in ordre], _TOUT_DEPT


@app.callback(Output("mrp-dept-resume", "children"), Output("mrp-dept-detail", "children"),
              Input("dd-mrp-dept-mois", "value"), Input("dd-mrp-dept", "value"),
              Input("dd-mrp-dept-produit", "value"))
def maj_detail_dept(mois, dept, produit):
    det = charger_mrp_detail()
    if det is None or det.empty or not mois or not dept:
        return html.Div(), html.Div()
    d = det[(det["Date"].dt.strftime("%Y-%m") == mois) & (det["Famille"].astype(str) == str(dept))]
    if d.empty:
        return (html.Div(f"Aucun besoin pour {dept} en {mois_label(mois)}.",
                         style={"color": C["txt_doux"], "padding": "8px"}), html.Div())

    # ── Résumé : combien de produits, budget matières du département ──────────
    par_prod = d.groupby("Produit")["Quantite_Requise"]
    n_prod = par_prod.ngroups
    couts_prod = {p: sum(c for c in (pf_couts.cout_ligne(i, q)
                  for i, q in zip(g["Ingredient"], g["Quantite_Requise"])) if c is not None)
                  for p, g in d.groupby("Produit")}
    budget_dept = sum(couts_prod.values())
    cards = rangee_kpis(
        kpi("Produits à fabriquer", str(n_prod), f"{dept} · {mois_label(mois)}"),
        kpi("Budget matières", f"{budget_dept:,.0f} {pf_couts.DEVISE}".replace(",", " "),
            "coût estimé des recettes", couleur=C["brun"]),
    )

    # ── Détail selon la sélection ────────────────────────────────────────────
    if produit and produit != _TOUT_DEPT:
        g = d[d["Produit"] == produit]
        titre = f"Matières premières pour « {produit} » — {mois_label(mois)}"
        # Quantité produite (base prévision) pour contextualiser.
        qte_txt = _qte_produite_txt(produit, mois)
        sous = (f"{qte_txt} · " if qte_txt else "") + \
               "quantités de matières nécessaires à cette production."
    else:
        g = d
        titre = f"Toutes les matières de {dept} — {mois_label(mois)}"
        sous = f"Besoins cumulés des {n_prod} produits du département."

    agg = (g.groupby("Ingredient")["Quantite_Requise"].sum()
           .sort_values(ascending=False).reset_index())
    lignes = []
    for _, r in agg.iterrows():
        num, unite, libelle = _fmt_qte_ing(r["Ingredient"], r["Quantite_Requise"])
        cout = pf_couts.cout_ligne(r["Ingredient"], r["Quantite_Requise"])
        lignes.append({"Ingrédient": _nom_ingredient(r["Ingredient"]),
                       "Quantité": libelle,
                       "Coût (MAD)": f"{cout:,.0f}".replace(",", " ") if cout is not None else "—"})
    table = pd.DataFrame(lignes, columns=["Ingrédient", "Quantité", "Coût (MAD)"])

    detail = html.Div([
        titre_section(titre),
        html.P(sous, style={"color": C["txt_doux"], "fontSize": "13px", "marginBottom": "10px"}),
        table_style(table, page=25),
    ])
    return cards, detail


def _qte_produite_txt(produit, mois):
    """« ~1 130 unités prévues » pour un produit/mois, depuis les prévisions détaillées."""
    _, _, det, _ = charger_tout()
    if det is None or det.empty or "Produit" not in det.columns:
        return ""
    col = next((c for c in ("Qty_Prev_Selection", "Qty_Prev_Holt_Winters") if c in det.columns), None)
    if col is None:
        return ""
    m = det[(det["Produit"].astype(str) == str(produit)) &
            (det["Date"].dt.strftime("%Y-%m") == mois)]
    if m.empty:
        return ""
    q = pd.to_numeric(m[col], errors="coerce").fillna(0).sum()
    return f"≈ {q:,.0f} unités prévues".replace(",", " ") if q > 0 else ""


@app.callback(Output("zone-statut", "children"), Output("lbl-statut", "children"),
              Input("btn-reload", "n_clicks"), Input("btn-run", "n_clicks"),
              prevent_initial_call=True)
def actions(n_reload, n_run):
    from dash import ctx as _ctx
    if _ctx.triggered_id == "btn-run":
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            # 1) Prévisions journalières (rapide) — cœur des onglets Production.
            subprocess.run([sys.executable, "-m", "paul_forecast.forecast_journalier"],
                           cwd=RACINE, check=True, timeout=1800, env=env)
            # 2) Pipeline mensuel (matières premières, validation, plan sécurisé).
            subprocess.run([sys.executable, os.path.join(RACINE, "main.py")],
                           cwd=RACINE, check=True, timeout=1800, env=env)
            _ventes_brutes.cache_clear()   # rafraîchit le cache historique
            _ratio_ttc_ht.cache_clear()
            _pics_commandes.cache_clear()
            return (html.Div("✓ Calcul terminé (journalier + mensuel) — changez d'onglet pour voir.",
                             style={"color": C["vert"], "fontWeight": "600", "padding": "8px"}),
                    "Dernier calcul : terminé avec succès")
        except Exception as e:
            return (html.Div(f"✗ Échec du calcul : {e}", style={"color": C["rouge"], "padding": "8px"}),
                    "Dernier calcul : échec — voir le terminal")
    _ventes_brutes.cache_clear()
    _ratio_ttc_ht.cache_clear()
    _pics_commandes.cache_clear()
    return (html.Div("Données rechargées.", style={"color": C["brun"], "padding": "8px"}),
            "Données actualisées depuis exports/")


if __name__ == "__main__":
    print("\n" + "═"*52)
    print("  PAUL — Tableau de bord interactif")
    print("  → http://127.0.0.1:8050")
    print("  (Ctrl+C pour arrêter)")
    print("═"*52 + "\n")
    # debug=False : évite le rechargeur Werkzeug qui lance un 2e process
    # (source des instances multiples sur le port 8050).
    # Port configurable via la variable d'environnement PORT (défaut 8050).
    app.run(debug=False, port=int(os.environ.get("PORT", "8050")))

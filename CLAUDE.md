# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Sales forecasting and production planning for a **PAUL bakery in Morocco**: how much to produce (per day and per month), what raw materials to order, with Moroccan holidays (Ramadan, Aïds), football matches, one-off events, and B2B client orders factored in. Single-user internal tool.

**The codebase is in French** — comments, UI text, variable/function names, JSON keys, and CSV columns. Keep new code, tests, and UI strings in French to match.

Not a git repository. Windows host (paths use backslashes; commands below work in PowerShell or Git Bash).

## Commands

```bash
pip install -r requirements.txt

python dashboard.py                            # web dashboard → http://127.0.0.1:8050
python main.py                                 # full MONTHLY pipeline (forecasts, MRP, plan, validation)
python -m paul_forecast.forecast_journalier    # DAILY forecast → exports/previsions_journalieres.csv (+ suivi)
python -m pytest tests/ -q                     # run all tests
python -m pytest tests/test_commandes.py::test_ajout_commande_journaliere -q   # single test
python outils/convertir_ventes_journalieres.py # rebuild the daily sales CSV from the raw DOS export
python outils/exporter_ventes_sql.py           # pull fresh sales from the Elyx SQL DB into the daily CSV
python outils/mise_a_jour_quotidienne.py       # full nightly chain: SQL export → daily forecast → main.py
```

There is no build step, linter config, or CI. Tests are plain `pytest` (no `pytest.ini`/`pyproject.toml`); `tests/conftest.py` just puts the repo root on `sys.path`.

The dashboard's **"Relancer le calcul"** button runs the daily forecast **then** `main.py` in subprocesses — the canonical "recompute everything" action after data or config changes.

## Architecture — the big picture

### Two forecasting systems live side by side (this is the key mental model)

1. **Monthly** — `main.py` → `paul_forecast/pipeline.py::run()`. Per-product: 9 models are benchmarked and the winner is stored in `data/modele_par_produit.json` (regenerate via `outils/benchmark_v2.py`); `forecasting.py` computes them. The pipeline then does **top-down reconciliation** (sum of product forecasts calibrated onto the aggregate Holt-Winters), **walk-forward validation** (honest "next month" error), and explodes forecasts into raw-material needs via the **BOM** (`bom.py`). Outputs: `plan_production_securise.csv`, `besoins_ingredients_planifies.csv`, `previsions_*.csv`, validation files.

2. **Daily** — `paul_forecast/forecast_journalier.py` (this is the product's **home/default** tab, "Production du jour"). A single decomposition model (~7–8% MAPE): `niveau = β·récent + (1−β)·ancre_annuelle`, `× poids_jour_semaine × boosts`. It reads `donnees_ventes/ventes_journalieres.csv` and writes `exports/previsions_journalieres.csv`.

The daily system is **not** a rewrite of the monthly one — they coexist. Changes usually touch one, not both.

### Adjustment layers stack on the base forecast in a fixed order

Both systems apply the same conceptual layers, computed by dedicated modules and combined so boosts **don't stack** (strongest up × strongest down):

- `saisonnalite_fetes.py` + `calibration_fetes.py` — Moroccan holidays; profiles are **measured** from history (`data/profils_fetes_mesures.json`), falling back to hypotheses in `data/profils_fetes.json`.
- `evenements.py` + `matchs.py` — one-off events and national-team matches (match impact is *learned* from past match days, applied to upcoming ones).
- `commandes.py` — big B2B orders: **detects past order-like spikes and neutralizes them in training** (so they don't inflate normal-day forecasts), and **adds known future orders** as-is to daily forecasts, monthly forecasts, and therefore the MRP. See `data/commandes_clients.json`.
- `incertitude.py` — safety-stock / service-level intervals (monthly `Qty_Recommandee`).

Cross-cutting diagnostics: `suivi.py` (out-of-sample "prévu vs réel" reconstruction with a rolling origin → `exports/suivi_prevu_reel.csv`), `couverture.py` (which products still lack an exact recipe, ranked by material weight).

### Data flow and conventions

- **Business data is externalized to `data/*.json`** (recipes `recettes_exactes.json`, prices `prix_matieres.json`, holidays `fetes_maroc.json`, events/matches `evenements.json`, client orders `commandes_clients.json`, per-product manual overrides `ajustements_produits.json`). Edit these instead of hardcoding. `config.py` loads them all.
- **`config.py` is the central knobs file** — model selection, thresholds, service level, and **provisional calibrations** (e.g. `CALIBRAGE_MATIERES` scales flour ~×0.92 to match the chef's real consumption; this disappears once real recipes land).
- **`ventes_journalieres.csv` is the source of truth for the daily side.** It's rebuilt from a fixed-width CP850 DOS "print-to-file" export by `outils/convertir_ventes_journalieres.py` (which also drops sub-total lines to avoid double counting). The monthly panel is auto-completed from this daily file for recent months missing from the xlsx.
- **Automated sales export (server migration).** This machine (`ELYX-SERVER`) runs the PI ELECTRONIQUE back office (Elyx Resto 11.15.5) with SQL Server Express 2014, database `PAULCFC`. History up to the migration came from the OLD server (the `ProduitParJour*.txt` DOS reports); fresh sales will land in the `IMPUTATION_<site>` ticket-line tables once the tills are reconnected. `outils/exporter_ventes_sql.py` aggregates them per day × product (closed days only — today is excluded) and merges them into `ventes_journalieres.csv` (SQL rows replace same-date rows; the converter re-merges `donnees_ventes/ventes_sql.csv` on rebuild so nothing is lost). The Windows scheduled task **"PrevisionPaul - MAJ quotidienne"** (05:30 daily, S4U as Administrateur) runs `outils/mise_a_jour_quotidienne.py`: SQL export → daily forecast → `main.py`, logged to `logs/auto_AAAAMMJJ.log`. While the tills are not yet connected the export is a harmless no-op.
- **Exports have a dual location — a known gotcha.** `pipeline.run()` writes into a dated subfolder `exports/AAAA-MM-JJ/`; the dashboard's `lire()` reads the **most recent dated** folder. But the daily forecast, `suivi`, and `couverture` write/read files at the **`exports/` root** (`previsions_journalieres.csv`, `suivi_prevu_reel.csv`). When adding a new export, be deliberate about which location the reader expects.

### Dashboard

`dashboard.py` is one large Dash app (~3000 lines). Tabs are **grouped by theme** in the `GROUPES` list: 5 top-level tabs (Accueil, Production, Événements & commandes, Assistant, Analyse), each holding one or more **views** (the `onglet_*()` builders). `afficher_groupe` renders a group (single view directly, or a `dcc.Tabs id="sous-tabs"` sub-bar); `afficher_vue` renders the selected view. `VUE_FN`/`VUE_GROUPE` map view id → builder / parent group. Accueil nav cards target a **view id** and set both the group tab and a `vue-cible` store so navigation lands on the exact sub-view. Per-view help lives in `INFOS_ONGLETS` (keyed by view id, used by `bandeau_onglet`). Callbacks use `@app.callback` with `ctx.triggered_id`; `suppress_callback_exceptions=True` is required (sub-tab components exist only when their group is open). Palette/fonts: `C`/`SERIF`/`SANS`. `_ventes_brutes()` and `_pics_commandes()` are `lru_cache`d — cleared in the reload/run callbacks. The server port is `PORT` env var (default 8050).

### Assistant (guided Q&A, 100% local — confidentiality requirement)

`paul_forecast/assistant.py` + the "💬 Assistant" tab. **No LLM, no API, no network — deliberately.** Sales data must stay on the machine, so the assistant is a deterministic guided-query panel: the user picks a question TYPE (`TYPES_QUESTION`), fills fields, and `interroger(type_q, ...)` calls the matching `outil_*` function (which reads the real files) and returns `{texte, table}`. Deterministic ⇒ zero hallucination (every number comes from a file); zero external dependency. Do **not** reintroduce any API/network call here without explicit approval (a test, `test_pas_de_dependance_reseau`, guards against it). A new question = a new `outil_*` + a branch in `interroger` + an entry in the dashboard's `_CHAMPS_PAR_QUESTION` field-visibility map. (A local LLM via Ollama could be added later as a separate backend if free-text chat is wanted, keeping the same tool grounding.)

## Working notes

- After changing forecasts, holidays, events, orders, recipes, or `config.py`, **regenerate** the relevant CSVs (daily via `python -m paul_forecast.forecast_journalier`; monthly via `python main.py`) so the dashboard reflects the change.
- **Chef recipe workflow:** `python outils/generer_tableau_recettes.py` builds `docs/recettes_produits_a_completer.xlsx` — one sheet per category (BOULANGERIE, CUISINE, PATISSERIE…) listing every product except resold brands and mono-stock items (those get their own read-only sheets). Each product is pre-filled with its **exact recipe** from `data/recettes_exactes.json` when it exists (origin column shows the provenance: recette chef, fiche technique, extrapolation, estimation), otherwise with the system's estimate. The xlsx is a **generated snapshot**: rerun the script after recipe changes. Chefs correct/complete it and mark products OUI on the product's title row; `python outils/importer_recettes_chefs.py` then imports validated recipes into `data/recettes_exactes.json` (+ provenance, timestamped backup). After importing (and updating `data/prix_matieres.json`), remove the provisional `CALIBRAGE_MATIERES` in `config.py` and rerun `python main.py`.
- Prophet is optional and off by default (`config.ACTIVER_PROPHET`); statsmodels drives Holt-Winters (falls back to plain decomposition if absent).
- **Do not attribute sales variations to invented causes** (e.g. "the World Cup"). Irregular products (FLUTE 250GR, PAIN PUR SEMOULE) simply have volatile demand — that's what the "Incertain" reliability flag signals, without explaining the cause.

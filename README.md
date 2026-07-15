# PAUL — Prévisions & Approvisionnement

Système de prévision des ventes et de planification de production pour la
boulangerie PAUL : combien produire (par jour et par mois), quoi commander en
matières premières, avec fêtes (Ramadan, Aïds…), matchs et événements intégrés.

## Démarrage rapide

```bash
pip install -r requirements.txt

python dashboard.py        # tableau de bord → http://127.0.0.1:8050
python main.py             # recalcul complet des prévisions mensuelles + MRP
python -m paul_forecast.forecast_journalier   # prévisions jour par jour
python -m pytest tests/ -q # tests
```

Sous Windows, deux raccourcis double-clic :
- **`lancer_dashboard.bat`** — ouvre le tableau de bord dans le navigateur ;
- **`lancer.bat`** — lance le *watcher* : il surveille `donnees_ventes\` et
  relance automatiquement tous les calculs dès qu'un fichier de ventes arrive
  (conversion des exports bruts incluse si un `ProduitParJour*.txt` change).

Le tableau de bord s'ouvre sur l'onglet **Accueil** qui explique tout :
quoi produire aujourd'hui, alertes, et navigation guidée.
Le port se change via la variable d'environnement `PORT` (défaut 8050).

## Mise à jour des données de ventes

1. Déposer le nouvel export DOS `ProduitParJour<AAAA>.txt` dans
   `donnees_ventes/<AAAA>/` (et les xlsx mensuels dans
   `donnees_ventes/<AAAA>/12_mois/` quand ils existent).
2. `python outils/convertir_ventes_journalieres.py` — reconstruit
   `donnees_ventes/ventes_journalieres.csv` (sous-totaux écartés, familles
   rattachées). *(Automatique si le watcher tourne.)*
3. Cliquer **« Relancer le calcul »** dans le dashboard (ou `python main.py`).
   Le pipeline complète automatiquement le panel mensuel avec les mois complets
   du fichier journalier et prévoit le mois suivant.

> ⚠️ Si les ventes datent de plus de quelques jours, l'Accueil du dashboard
> l'affiche en tête de « À surveiller », et le pipeline avertit si la
> « prévision du mois prochain » cible un mois déjà écoulé.

## Structure du dossier

| Dossier / fichier | Rôle |
|---|---|
| `main.py` | Pipeline mensuel (prévisions, MRP, plan de production, validation) |
| `dashboard.py` | Tableau de bord web (Dash) |
| `watcher.py` + `lancer.bat` | Relance auto (conversion + calculs) quand un fichier de ventes arrive |
| `lancer_dashboard.bat` | Double-clic : démarre le tableau de bord et ouvre le navigateur |
| `paul_forecast/` | Le code (modèles, BOM, fêtes, coûts, marges…) |
| `data/` | Données métier éditables (recettes, prix, fêtes, événements…) |
| `donnees_ventes/` | Ventes réelles (xlsx mensuels + CSV journalier) |
| `exports/` | Résultats générés (un sous-dossier daté par calcul) |
| `outils/` | Scripts occasionnels (benchmark des modèles, extraction recettes…) |
| `docs/` | Documents pour le chef (recettes à valider, analyses) |
| `tests/` | Tests automatiques |
| `Recette + MP/` | Fiches techniques fournies par le chef |
| `benchmark_modeles.csv` | Erreurs mesurées par produit (utilisé par la fiabilité) |

## Points d'entrée « occasionnels » (dossier `outils/`)

| Script | Quand l'utiliser |
|---|---|
| `benchmark_v2.py` | Re-choisir le meilleur modèle par produit (tous les 3-6 mois) |
| `convertir_ventes_journalieres.py` | Régénérer le CSV journalier depuis les xlsx |
| `extract_recettes.py` | Ré-extraire les fiches techniques de `Recette + MP/` |
| `construire_referentiel_bom.py` | Reconstruire les recettes après validation du chef |
| `recettes_recherche.py` | Ré-appliquer les recettes recherchées (web) |

## À faire quand le chef valide

1. Corriger `data/recettes_exactes.json` (quantités réelles) et
   `data/prix_matieres.json` (prix des factures).
2. Supprimer le calibrage provisoire farine dans `paul_forecast/config.py`
   (`CALIBRAGE_MATIERES`) si les recettes deviennent exactes.
3. Relancer `python main.py`.

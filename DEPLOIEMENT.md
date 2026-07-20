# Déploiement — serveur PAUL (caisse PI Electronique)

Procédure de mise en place et de mise à jour de PrevisionPaul sur le serveur de
production (Windows Server, accès AnyDesk).

## Principe

- **Le code** vit sur GitHub → récupéré par `git clone` / `git pull`.
- **Les données** (ventes, recettes, prix…) ne quittent jamais GitHub (cf. `.gitignore`) :
  elles restent sur le serveur, alimentées par la base SQL de la caisse.

## Première installation

```cmd
cd C:\Users\Administrateur\Desktop
git clone https://github.com/OthmaneW37/PrevisionPaul.git
cd PrevisionPaul
pip install -r requirements.txt
```

Puis transférer une fois `data\` et `donnees_ventes\` via l'onglet fichiers d'AnyDesk,
et tester :

```cmd
python main.py
```

## Mise à jour (au quotidien)

```cmd
cd C:\Users\Administrateur\Desktop\PrevisionPaul
git pull
```

Le code se met à jour ; les données locales ne bougent pas.

## Export des ventes depuis la caisse PI (SQL Server)

1. `pip install pyodbc` + installer « ODBC Driver 18 for SQL Server ».
2. Copier `outils\config_pi.exemple.json` → `data\config_pi.json` (non versionné),
   renseigner `serveur` / `base` / `auth`.
3. Découvrir le schéma : `python outils\explorer_base_pi.py`
4. Renseigner la vraie `requete_sql` (colonnes : `Date;Code;Produit;Famille;Quantite;CA_TTC`).
5. Tester : `python outils\importer_ventes_pi.py` (lecture seule, défensif — n'écrase
   jamais le CSV en cas d'échec).

## Chaînage quotidien (à planifier via le Planificateur de tâches Windows)

```
1. python outils\importer_ventes_pi.py        (ventes depuis la base)
2. python -m paul_forecast.forecast_journalier (prévision du jour)
3. python main.py                              (prévisions mensuelles + MRP)
```

Lancer tôt le matin (après la RAZ de nuit de la caisse).

## Dashboard

```cmd
python dashboard.py
```

→ `http://127.0.0.1:8050`

## Lanceur « double-clic » (utilisateur non technique)

Pour ouvrir l'appli sans passer par la ligne de commande :

1. Double-cliquer **une fois** sur `Creer_raccourci_bureau.bat` → crée l'icône
   **« PAUL - Prévisions »** (logo PAUL) sur le Bureau.
2. Ensuite, double-clic sur cette icône : une fenêtre démarre le serveur et le
   navigateur s'ouvre tout seul sur le tableau de bord. Fermer la fenêtre = quitter.

Détails pour l'utilisateur final : voir `OUVRIR_L_APPLICATION.txt`.
Fichiers concernés : `Lancer_PAUL.bat` (serveur + ouverture navigateur),
`outils/_ouvrir_navigateur.bat` (attend le port 8050), `assets/Logo_Paul.ico`.

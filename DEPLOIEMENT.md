# Déploiement — serveur PAUL (caisse PI Electronique)

Procédure de mise en place et de mise à jour de PrevisionPaul sur le serveur de
production (`ELYX-SERVER`, Windows Server, accès AnyDesk).

## Principe

- **Le code** vit sur GitHub → récupéré par `git clone` / `git pull`.
- **Les données** (ventes, recettes, prix…) ne quittent jamais GitHub (cf. `.gitignore`) :
  elles restent sur le serveur, alimentées par la base SQL de la caisse.
- **Emplacement de production : `C:\PrevisionPaul`** (chemin stable, hors profil
  utilisateur — ne pas remettre dans `Downloads`/`Desktop`, fragiles). La tâche
  planifiée et les scripts pointent tous vers ce dossier.

## Première installation

```cmd
cd C:\
git clone https://github.com/OthmaneW37/PrevisionPaul.git
cd PrevisionPaul
pip install -r requirements.txt
```

Puis transférer une fois `data\` et `donnees_ventes\` via l'onglet fichiers d'AnyDesk
(ils sont hors git), et tester :

```cmd
python main.py
```

## Mise à jour du code

```cmd
cd C:\PrevisionPaul
git pull
```

Le code se met à jour ; les données locales ne bougent pas.

## Export des ventes depuis la caisse PI (SQL Server)

L'export est **déjà câblé et fonctionnel** via `outils/exporter_ventes_sql.py` :

- instance locale `localhost\SQLEXPRESS2014`, base `PAULCFC`, **authentification
  Windows** (aucun mot de passe stocké), pilote **« ODBC Driver 11 for SQL Server »**
  (déjà installé sur le serveur) ;
- lit les lignes de tickets des tables `IMPUTATION_<site>` (jours clos uniquement),
  agrège par jour × article, et **fusionne par date** dans
  `donnees_ventes\ventes_journalieres.csv` (l'historique des autres dates reste intact) ;
- **défensif** : si la base ne renvoie rien (caisses pas encore raccordées) ou des
  totaux aberrants, le CSV existant **n'est pas écrasé**. Tant que les tills ne
  postent pas dans `IMPUTATION_*`, l'export est un no-op sans effet ni erreur.

Rien à configurer. Pour changer d'instance/base ponctuellement, surcharger par
variables d'environnement `PAUL_SQL_SERVEUR` / `PAUL_SQL_BASE`.

Diagnostic du schéma (si le schéma Elyx change ou pour vérifier les tables/colonnes,
lecture seule, n'affiche aucune donnée de vente) :

```cmd
python outils\explorer_base_pi.py
```

## Chaînage quotidien — tâche planifiée Windows

Tâche **« PrevisionPaul - MAJ quotidienne »** (Planificateur de tâches Windows,
chaque nuit **05:30**, S4U en tant qu'Administrateur) qui lance :

```cmd
python C:\PrevisionPaul\outils\mise_a_jour_quotidienne.py
```

Ce script enchaîne dans l'ordre, journalisé dans `logs\auto_AAAAMMJJ.log` :

```
1. outils\exporter_ventes_sql.py            (ventes fraîches depuis la base Elyx)
2. python -m paul_forecast.forecast_journalier  (prévision du jour)
3. python main.py                           (prévisions mensuelles + MRP + plan)
```

C'est l'équivalent du bouton « Relancer le calcul » du dashboard, précédé de la
récupération des ventes. Lancé tôt le matin, après la RAZ de nuit de la caisse.

Vérifier / relancer manuellement la tâche :

```powershell
Get-ScheduledTaskInfo -TaskName "PrevisionPaul - MAJ quotidienne"   # dernier résultat, prochaine exécution
Start-ScheduledTask     -TaskName "PrevisionPaul - MAJ quotidienne"  # forcer un run
```

> Après un déplacement du dossier, mettre à jour l'action de la tâche
> (`Set-ScheduledTask`) : chemin du script **et** `WorkingDirectory`.

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

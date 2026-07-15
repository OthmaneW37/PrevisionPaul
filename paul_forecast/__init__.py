# -*- coding: utf-8 -*-
"""
Package paul_forecast — pipeline de prévision des ventes et planification MRP
pour la boulangerie PAUL.

Modules :
  - config         : constantes et chargement des données métier (JSON)
  - logging_setup  : configuration du logger (console + fichier horodaté)
  - data_loader    : chargement, validation, nettoyage, agrégation des ventes
  - bom            : nomenclatures (détection, normalisation, ajustement fêtes)
  - forecasting    : modèles de prévision (base + Prophet)
  - backtest       : backtesting inter-annuel
  - reporting      : dashboards, rapport d'approvisionnement, export Excel
  - pipeline       : orchestration complète (point d'entrée `run`)
"""

from . import config

__all__ = ["config"]
__version__ = "1.0.0"

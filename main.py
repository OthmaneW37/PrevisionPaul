# -*- coding: utf-8 -*-
"""
Point d'entrée du projet de prévision PAUL.

Lancement :
    python main.py

La configuration se fait dans paul_forecast/config.py ; les recettes et le
calendrier des fêtes sont éditables dans data/*.json.
"""

from paul_forecast import pipeline


if __name__ == "__main__":
    pipeline.run()

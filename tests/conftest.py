# -*- coding: utf-8 -*-
"""Configuration pytest : rend le paquet paul_forecast importable depuis la racine."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

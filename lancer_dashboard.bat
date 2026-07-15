@echo off
title PAUL Previsions - Tableau de bord
cd /d "%~dp0"
echo Demarrage du tableau de bord... (http://127.0.0.1:8050)
start "" http://127.0.0.1:8050
python dashboard.py
pause

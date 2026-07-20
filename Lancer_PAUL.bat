@echo off
chcp 65001 >nul
title PAUL - Previsions (NE PAS FERMER cette fenetre)
cd /d "%~dp0"

echo.
echo   ============================================================
echo      PAUL - Tableau de bord des previsions
echo   ------------------------------------------------------------
echo      Demarrage en cours... patientez quelques secondes.
echo      Le navigateur va s'ouvrir tout seul.
echo.
echo      NE FERMEZ PAS cette fenetre tant que vous utilisez
echo      l'application. Pour quitter : fermez cette fenetre.
echo   ============================================================
echo.

REM --- Choix de l'interpreteur Python (py -3 en priorite, sinon python) ---
set "PY=python"
where py >nul 2>nul && set "PY=py -3"

REM --- Ouvre le navigateur des que le serveur repond (en tache de fond) ---
start "" /min "%~dp0outils\_ouvrir_navigateur.bat"

REM --- Lance le serveur (reste ouvert : c'est l'application) ---
%PY% dashboard.py

REM --- Si on arrive ici, le serveur s'est arrete ---
echo.
echo   L'application est arretee.
pause

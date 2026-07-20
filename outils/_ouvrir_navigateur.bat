@echo off
REM Attend que le dashboard reponde sur le port 8050, puis ouvre le navigateur.
REM Appele en tache de fond par Lancer_PAUL.bat — ne pas lancer directement.
set "URL=http://127.0.0.1:8050"
for /l %%i in (1,1,90) do (
    curl -s -o nul "%URL%" && goto :ouvrir
    timeout /t 1 >nul
)
:ouvrir
start "" "%URL%"
exit

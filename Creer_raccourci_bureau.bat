@echo off
chcp 65001 >nul
title Installation du raccourci PAUL
cd /d "%~dp0"

echo.
echo   Creation du raccourci "PAUL - Previsions" sur le Bureau...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$W = New-Object -ComObject WScript.Shell;" ^
  "$b = $W.SpecialFolders('Desktop');" ^
  "$l = $W.CreateShortcut((Join-Path $b 'PAUL - Previsions.lnk'));" ^
  "$l.TargetPath = (Join-Path '%~dp0' 'Lancer_PAUL.bat');" ^
  "$l.WorkingDirectory = '%~dp0'.TrimEnd('\');" ^
  "$l.IconLocation = (Join-Path '%~dp0' 'assets\Logo_Paul.ico');" ^
  "$l.Description = 'Ouvre le tableau de bord des previsions PAUL';" ^
  "$l.WindowStyle = 7;" ^
  "$l.Save();"

if errorlevel 1 (
  echo   [ERREUR] Le raccourci n'a pas pu etre cree.
) else (
  echo   [OK] Raccourci cree sur le Bureau : "PAUL - Previsions"
  echo.
  echo   Double-cliquez dessus pour ouvrir l'application.
)
echo.
pause

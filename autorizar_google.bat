@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===================================================
echo   RENOVANDO ACESSO AO GOOGLE DRIVE / GMAIL / AGENDA
echo ===================================================
echo.
echo Uma aba do navegador vai abrir para voce confirmar sua conta Google.
echo.
.\.venv\Scripts\python.exe backend\configurar_google.py
echo.
pause


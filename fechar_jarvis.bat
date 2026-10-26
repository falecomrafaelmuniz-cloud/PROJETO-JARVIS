@echo off
chcp 65001 >nul
echo ===================================================
echo   ENCERRANDO PROCESSOS DO JARVIS
echo ===================================================
echo.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'agent\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'next' -or $_.CommandLine -match 'pnpm' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo.
echo Jarvis encerrado com sucesso!
timeout /t 2 /nobreak >nul

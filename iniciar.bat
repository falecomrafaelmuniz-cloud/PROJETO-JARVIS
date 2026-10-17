@echo off
cd /d "%~dp0"
echo Iniciando o Jarvis...
set "PYTHON_CMD=.\.venv\Scripts\python.exe"

if not exist "%PYTHON_CMD%" (
    echo [ERRO] O interpretador python nao foi encontrado em:
    echo %PYTHON_CMD%
    echo.
    echo Verifique se a virtual environment foi criada corretamente ou execute:
    echo    python -m venv .venv
    echo.
    pause
    exit /b
)

echo [INFO] Ativando o ambiente virtual...
call .venv\Scripts\activate

echo [INFO] Executando o Jarvis com Python...
echo [INFO] Pressione Ctrl+C para parar a execucao

%PYTHON_CMD% backend\agent.py dev

pause
@echo off
chcp 65001 >nul
title INSTALADOR AUTOMÁTICO DO JARVIS
color 0B

echo =====================================================================
echo                JARVIS SYSTEM - INSTALADOR AUTOMÁTICO
echo =====================================================================
echo.
echo Olá! Estamos preparando todos os sistemas do seu Jarvis.
echo Aguarde alguns instantes enquanto configuramos as inteligências...
echo.

cd /d "%~dp0"

:: 1. Verifica Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERRO] O Python não foi encontrado no seu computador!
    echo Por favor, instale o Python em https://www.python.org/downloads/
    echo ATENÇÃO: Marque a opção "Add Python to PATH" durante a instalação.
    echo.
    pause
    exit /b 1
)

:: 2. Verifica Node.js
node -v >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERRO] O Node.js não foi encontrado no seu computador!
    echo Por favor, instale o Node.js LTS em https://nodejs.org/
    echo.
    pause
    exit /b 1
)

:: 3. Garante pnpm
call pnpm -v >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Instalando gerenciador de pacotes rápido (pnpm)...
    call npm install -g pnpm >nul 2>&1
)

:: 4. Canal de Atualizações Automáticas (Git)
git --version >nul 2>&1
if %errorlevel% equ 0 (
    if not exist ".git" (
        echo [*] Configurando canal de atualizações automáticas...
        git init >nul 2>&1
        git remote add origin https://github.com/falecomrafaelmuniz-cloud/PROJETO-JARVIS.git >nul 2>&1
        git fetch origin main --depth=1 >nul 2>&1
        git reset --soft origin/main >nul 2>&1
    )
)

:: 5. Ambiente Virtual Python (.venv)
if not exist ".venv" (
    echo [*] Criando ambiente neural seguro (.venv)...
    python -m venv .venv
)

echo [*] Instalando dependências e inteligências do Python...
call .\.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
call .\.venv\Scripts\pip.exe install -r backend\requirements.txt

:: 5. Dependências do Frontend
echo [*] Instalando interface gráfica do Jarvis...
cd frontend
call pnpm install
cd ..

:: 6. Verificação do arquivo de Chaves (.env)
if not exist ".env" (
    echo.
    echo =====================================================================
    echo                 CONFIGURAÇÃO DA CHAVE DE ACESSO
    echo =====================================================================
    echo Criando seu arquivo de configuração (.env)...
    (
        echo # Servidor de Voz Local Jarvis
        echo LIVEKIT_URL=ws://127.0.0.1:7880
        echo LIVEKIT_API_KEY=APIJarvisLocal
        echo LIVEKIT_API_SECRET=JarvisLocalSecretKey32BytesMinimumLength2026
        echo.
        echo # Chave da Inteligência Artificial (Google Gemini)
        echo GOOGLE_API_KEY=SUA_CHAVE_AQUI
    ) > .env

    echo.
    echo ATENÇÃO: Abra o arquivo .env e cole sua chave do Google Gemini onde diz SUA_CHAVE_AQUI.
    echo.
)

color 0A
echo.
echo =====================================================================
echo           TUDO PRONTO! SEU JARVIS ESTÁ 100%% CONFIGURADO!
echo =====================================================================
echo.
echo Para ligar o Jarvis no dia a dia, basta dar dois cliques em:
echo   --> iniciar.bat
echo.
pause

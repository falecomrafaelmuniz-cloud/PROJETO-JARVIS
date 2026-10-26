$ErrorActionPreference = 'Stop'

$agentDirectory = $PSScriptRoot
$backendDirectory = Join-Path $agentDirectory 'backend'
$agentFile = Join-Path $backendDirectory 'agent.py'
$pythonExe = Join-Path $agentDirectory '.venv\Scripts\python.exe'
$webDirectory = Join-Path $agentDirectory 'frontend'
$webEnvFile = Join-Path $webDirectory '.env.local'
$webUrl = 'http://localhost:3000'

function Get-JarvisWorkers {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'agent\.py' -and
            $_.CommandLine -match '\bdev\b'
        }
}

function Test-JarvisWeb {
    try {
        $req = [System.Net.HttpWebRequest]::Create("http://localhost:3000")
        $req.Timeout = 2000
        $req.Method = "GET"
        $resp = $req.GetResponse()
        $status = [int]$resp.StatusCode
        $resp.Close()
        return ($status -ge 200 -and $status -lt 500)
    } catch {
        return $false
    }
}

try {
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host "         INICIANDO SISTEMAS DO JARVIS    " -ForegroundColor Cyan
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw "Ambiente Python (.venv) não encontrado em: $pythonExe"
    }
    if (-not (Test-Path -LiteralPath $agentFile -PathType Leaf)) {
        throw "Arquivo principal do agente não encontrado em: $agentFile"
    }
    if (-not (Test-Path -LiteralPath $webDirectory -PathType Container)) {
        throw "Pasta do frontend não encontrada em: $webDirectory"
    }

    # Limpa duplicatas do Python se houver mais de um worker rodando
    $workers = @(Get-JarvisWorkers)
    if ($workers.Count -gt 1) {
        Write-Host "Detectados múltiplos processos do Python. Limpando duplicatas..." -ForegroundColor Yellow
        $workers | Select-Object -Skip 1 | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 500
        $workers = @(Get-JarvisWorkers)
    }

    # 1. Inicia o Worker do LiveKit (Cérebro Python) em janela CMD visível
    if ($workers.Count -eq 1) {
        Write-Host "[1/3] Cérebro do Jarvis (Python) já está em execução (PID: $($workers[0].ProcessId))." -ForegroundColor Green
    }
    else {
        Write-Host "[1/3] Ligando o Cérebro do Jarvis em Python..." -ForegroundColor Yellow
        $backendArgs = "/k title [JARVIS 1/2] BACKEND (Python) && cd /d `"$agentDirectory`" && `"$pythonExe`" backend\agent.py dev"
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList $backendArgs `
            -WorkingDirectory $agentDirectory `
            -WindowStyle Normal | Out-Null
        Start-Sleep -Seconds 3

        if (-not (Get-JarvisWorkers)) {
            Write-Warning "O worker do Jarvis está iniciando..."
        } else {
            Write-Host "  -> Cérebro conectado com sucesso!" -ForegroundColor Green
        }
    }

    # 2. Inicia o Servidor Web (Frontend Next.js) em janela CMD visível
    if (Test-JarvisWeb) {
        Write-Host "[2/3] Interface web do Jarvis já está ativa e respondendo." -ForegroundColor Green
    }
    else {
        Write-Host "[2/3] Ligando a Interface Gráfica Next.js..." -ForegroundColor Yellow
        $pkgCommand = if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { 'pnpm dev' } else { 'npm run dev' }
        $frontendArgs = "/k title [JARVIS 2/2] FRONTEND (Next.js) && cd /d `"$webDirectory`" && $pkgCommand"
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList $frontendArgs `
            -WorkingDirectory $webDirectory `
            -WindowStyle Normal | Out-Null

        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 800
            if (Test-JarvisWeb) {
                break
            }
        }
        Write-Host "  -> Interface gráfica ativa!" -ForegroundColor Green
    }

    # 3. Abre o Navegador
    Write-Host "[3/3] Abrindo o Jarvis no navegador..." -ForegroundColor Cyan
    Start-Sleep -Seconds 1
    Start-Process $webUrl
    Write-Host "Jarvis pronto para uso!" -ForegroundColor Green
    Start-Sleep -Seconds 2
}
catch {
    Write-Host ""
    Write-Host "[ERRO AO INICIAR]" -ForegroundColor Red
    Write-Error $_
    Start-Sleep -Seconds 5
}

$ErrorActionPreference = 'Stop'
$env:ELECTRON_RUN_AS_NODE = $null

$agentDirectory = $PSScriptRoot
$backendDirectory = Join-Path $agentDirectory 'backend'
$agentFile = Join-Path $backendDirectory 'agent.py'
$pythonExe = Join-Path $agentDirectory '.venv\Scripts\python.exe'
$livekitExe = Join-Path $agentDirectory 'bin\livekit-server.exe'
$webDirectory = Join-Path $agentDirectory 'frontend'
$webUrl = 'http://localhost:3000'

function Get-JarvisWorkers {
    $allPy = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'agent\.py' -and
            $_.CommandLine -match '\b(dev|start)\b'
        })
    $pyIds = $allPy | ForEach-Object { $_.ProcessId }
    # Retorna apenas os processos-raiz (ignora o subprocesso filho criado pelo lançador do .venv no Windows)
    return @($allPy | Where-Object { $pyIds -notcontains $_.ParentProcessId })
}

function Get-LiveKitProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'livekit-server.exe'" -ErrorAction SilentlyContinue
}

function Test-LiveKitServer {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $async = $tcp.BeginConnect('127.0.0.1', 7880, $null, $null)
        $wait = $async.AsyncWaitHandle.WaitOne(800, $false)
        if ($wait -and $tcp.Connected) {
            $tcp.Close()
            return $true
        }
        $tcp.Close()
        return $false
    } catch {
        return $false
    }
}

function Test-JarvisWeb {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:3000/api/connection-details" -Method Post -ContentType "application/json" -Body "{}" -TimeoutSec 2 -ErrorAction Stop
        return ($null -ne $resp.serverUrl)
    } catch {
        return $false
    }
}

function Test-JarvisWebConfig {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:3000/api/connection-details" -Method Post -ContentType "application/json" -Body "{}" -TimeoutSec 3 -ErrorAction Stop
        return ($resp.serverUrl -eq "ws://127.0.0.1:7880")
    } catch {
        return $false
    }
}

try {
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host "         INICIANDO SISTEMAS DO JARVIS    " -ForegroundColor Cyan
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host ""

    # 0. Verificação e Auto-Update Silencioso via Servidor Central (GitHub)
    if (Test-Path -LiteralPath (Join-Path $agentDirectory '.git')) {
        try {
            Write-Host "[Update] Verificando atualizacoes no servidor central..." -ForegroundColor DarkCyan
            & git -C $agentDirectory fetch origin main --quiet 2>$null
            $localSha = (& git -C $agentDirectory rev-parse HEAD 2>$null)
            $remoteSha = (& git -C $agentDirectory rev-parse origin/main 2>$null)
            if ($localSha -and $remoteSha -and ($localSha.Trim() -ne $remoteSha.Trim())) {
                Write-Host "[Update] Nova versao detectada! Atualizando o Jarvis automaticamente..." -ForegroundColor Green
                & git -C $agentDirectory pull --autostash origin main --quiet 2>$null
                Write-Host "[Update] Jarvis atualizado com sucesso para a versao mais recente!" -ForegroundColor Green
            }
        } catch {
            # Continua sem interromper se estiver sem internet ou offline
        }
    }

    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw "Ambiente Python (.venv) não encontrado em: $pythonExe"
    }
    if (-not (Test-Path -LiteralPath $agentFile -PathType Leaf)) {
        throw "Arquivo principal do agente não encontrado em: $agentFile"
    }
    if (-not (Test-Path -LiteralPath $webDirectory -PathType Container)) {
        throw "Pasta do frontend não encontrada em: $webDirectory"
    }

    # Garante que qualquer livekit-server antigo sem livekit.yaml seja reiniciado
    $lkProcs = @(Get-LiveKitProcesses)
    foreach ($proc in $lkProcs) {
        if ($proc.CommandLine -notmatch 'livekit\.yaml') {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        }
    }

    # 0. Inicia o Servidor LiveKit Local (em segundo plano, sem limite de minutos)
    if (Test-LiveKitServer) {
        Write-Host "[0/3] Servidor de Voz Local (LiveKit) já está ativo na porta 7880." -ForegroundColor Green
    }
    elseif (Test-Path -LiteralPath $livekitExe -PathType Leaf) {
        Write-Host "[0/3] Iniciando Servidor de Voz Local (LiveKit 127.0.0.1:7880)..." -ForegroundColor Yellow
        Start-Process -FilePath $livekitExe `
            -ArgumentList '--config "bin\livekit.yaml"' `
            -WorkingDirectory $agentDirectory `
            -WindowStyle Hidden | Out-Null

        $lkDeadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $lkDeadline) {
            Start-Sleep -Milliseconds 300
            if (Test-LiveKitServer) {
                break
            }
        }
        Write-Host "  -> Servidor de Voz Local ativo!" -ForegroundColor Green
    }

    # Reinicia o Worker do LiveKit (Cérebro Python) para sempre carregar o código mais recente
    $workers = @(Get-JarvisWorkers)
    if ($workers.Count -gt 0) {
        Write-Host "Reiniciando Cérebro do Jarvis (Python) para carregar as últimas atualizações..." -ForegroundColor Yellow
        $workers | ForEach-Object {
            & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null
        }
        Start-Sleep -Milliseconds 800
    }

    Write-Host "[1/3] Ligando o Cérebro do Jarvis em Python (Modo Silencioso)..." -ForegroundColor Yellow
    Start-Process -FilePath $pythonExe `
        -ArgumentList 'backend\agent.py dev' `
        -WorkingDirectory $agentDirectory `
        -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 3

    if (-not (Get-JarvisWorkers)) {
        Write-Warning "O worker do Jarvis está iniciando..."
    } else {
        Write-Host "  -> Cérebro conectado com sucesso!" -ForegroundColor Green
    }

    # Se o frontend estiver rodando com configuração antiga (LiveKit Cloud), reinicia o node
    if ((Test-JarvisWeb) -and (-not (Test-JarvisWebConfig))) {
        Write-Host "Atualizando configuração do servidor web para o LiveKit Local..." -ForegroundColor Yellow
        Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'next' -or $_.CommandLine -match 'pnpm' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }

    # Garante que a porta 3000 esteja livre se ocupada por processo residual
    $conn3000 = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    if ($conn3000 -and (-not (Test-JarvisWeb))) {
        Write-Host "Liberando porta 3000 ocupada por processo residual..." -ForegroundColor Yellow
        foreach ($c in $conn3000) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }

    # 2. Inicia o Servidor Web (Frontend Next.js) em janela CMD visível
    if (Test-JarvisWeb) {
        Write-Host "[2/3] Interface web do Jarvis já está ativa e respondendo." -ForegroundColor Green
    }
    else {
        Write-Host "[2/3] Ligando a Interface Gráfica Next.js (Modo Silencioso)..." -ForegroundColor Yellow
        $pkgCommand = if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { 'pnpm dev' } else { 'npm run dev' }
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList @('/c', "title [JARVIS 2/2] FRONTEND (Next.js) && $pkgCommand") `
            -WorkingDirectory $webDirectory `
            -WindowStyle Hidden | Out-Null

        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 800
            if (Test-JarvisWeb) {
                break
            }
        }
        if (Test-JarvisWeb) {
            Write-Host "  -> Interface gráfica ativa!" -ForegroundColor Green
        } else {
            Write-Warning "A interface gráfica ainda está carregando em http://localhost:3000..."
        }
    }

    # 3. Abre na Janela Desktop Nativa do Jarvis
    Write-Host "[3/3] Abrindo a Janela Desktop do Jarvis..." -ForegroundColor Cyan
    Get-Process -Name 'electron' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Push-Location $webDirectory
    try {
        & node desktop/run.cjs
    } finally {
        Pop-Location
    }
    Write-Host "Jarvis pronto para uso!" -ForegroundColor Green
    Start-Sleep -Seconds 2
}
catch {
    Write-Host ""
    Write-Host "[ERRO AO INICIAR]" -ForegroundColor Red
    Write-Error $_
    Start-Sleep -Seconds 5
}

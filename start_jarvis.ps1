$ErrorActionPreference = 'Stop'

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
            $_.CommandLine -match '\bdev\b'
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

function Test-JarvisWebConfig {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:3000/api/connection-details" -Method Post -ContentType "application/json" -Body "{}" -TimeoutSec 3
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

    # Limpa duplicatas reais do Python se houver mais de uma instância raiz rodando
    $workers = @(Get-JarvisWorkers)
    if ($workers.Count -gt 1) {
        Write-Host "Detectadas múltiplas instâncias do Jarvis (Python). Limpando fantasmas..." -ForegroundColor Yellow
        $workers | Select-Object -Skip 1 | ForEach-Object {
            & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null
        }
        Start-Sleep -Milliseconds 500
        $workers = @(Get-JarvisWorkers)
    }

    # 1. Inicia o Worker do LiveKit (Cérebro Python) em janela CMD visível
    if ($workers.Count -eq 1) {
        Write-Host "[1/3] Cérebro do Jarvis (Python) já está em execução (PID: $($workers[0].ProcessId))." -ForegroundColor Green
    }
    else {
        Write-Host "[1/3] Ligando o Cérebro do Jarvis em Python..." -ForegroundColor Yellow
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList @('/k', 'title [JARVIS 1/2] BACKEND (Python) && .\.venv\Scripts\python.exe backend\agent.py dev') `
            -WorkingDirectory $agentDirectory `
            -WindowStyle Normal | Out-Null
        Start-Sleep -Seconds 3

        if (-not (Get-JarvisWorkers)) {
            Write-Warning "O worker do Jarvis está iniciando..."
        } else {
            Write-Host "  -> Cérebro conectado com sucesso!" -ForegroundColor Green
        }
    }

    # Se o frontend estiver rodando com configuração antiga (LiveKit Cloud), reinicia o node
    if ((Test-JarvisWeb) -and (-not (Test-JarvisWebConfig))) {
        Write-Host "Atualizando configuração do servidor web para o LiveKit Local..." -ForegroundColor Yellow
        Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'next' -or $_.CommandLine -match 'pnpm' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }

    # 2. Inicia o Servidor Web (Frontend Next.js) em janela CMD visível
    if (Test-JarvisWeb) {
        Write-Host "[2/3] Interface web do Jarvis já está ativa e respondendo." -ForegroundColor Green
    }
    else {
        Write-Host "[2/3] Ligando a Interface Gráfica Next.js..." -ForegroundColor Yellow
        $pkgCommand = if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { 'pnpm dev' } else { 'npm run dev' }
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList @('/k', "title [JARVIS 2/2] FRONTEND (Next.js) && $pkgCommand") `
            -WorkingDirectory $webDirectory `
            -WindowStyle Normal | Out-Null

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

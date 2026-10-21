$ErrorActionPreference = 'Stop'

# Trava para evitar que múltiplos cliques no atalho executem ao mesmo tempo
$lockPath = Join-Path $env:TEMP "jarvis_startup.lock"
try {
    if (Test-Path $lockPath) {
        $lockAge = (Get-Date) - (Get-Item $lockPath).LastWriteTime
        if ($lockAge.TotalSeconds -lt 6) {
            Write-Host "Inicialização do Jarvis já em andamento. Aguarde..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
            exit 0
        }
    }
    Set-Content -Path $lockPath -Value (Get-Date).ToString() -Force
} catch {}

$agentDirectory = $PSScriptRoot
$backendDirectory = Join-Path $agentDirectory 'backend'
$agentFile = Join-Path $backendDirectory 'agent.py'
$pythonExe = Join-Path $agentDirectory '.venv\Scripts\python.exe'
$webDirectory = Join-Path $agentDirectory 'frontend'
$webEnvFile = Join-Path $webDirectory '.env.local'
$webUrl = 'http://localhost:3000'

function Get-JarvisWorker {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'agent\.py' -and
            $_.CommandLine -match '\bdev\b'
        } |
        Select-Object -First 1
}

function Test-JarvisPort {
    param([int]$Port = 3000)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect("127.0.0.1", $Port, $null, $null)
        $wait = $iar.AsyncWaitHandle.WaitOne(800, $false)
        if ($wait) {
            $tcp.EndConnect($iar)
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
    if (Test-JarvisPort -Port 3000) {
        return $true
    }
    return $false
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

    $workerJaEstavaAtivo = $false
    $webJaEstavaAtiva = $false

    # 1. Inicia o Worker do LiveKit (Cérebro Python) em janela CMD visível
    $worker = Get-JarvisWorker
    if ($worker) {
        $workerJaEstavaAtivo = $true
        Write-Host "[1/3] Cérebro do Jarvis (Python) já está em execução (PID: $($worker.ProcessId))." -ForegroundColor Green
    }
    else {
        Write-Host "[1/3] Abrindo Cérebro do Jarvis em Python..." -ForegroundColor Yellow
        $backendArgs = "/k title [JARVIS 1/2] BACKEND (Python) && cd /d `"$agentDirectory`" && `"$pythonExe`" backend\agent.py dev"
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList $backendArgs `
            -WorkingDirectory $agentDirectory `
            -WindowStyle Normal | Out-Null
        Start-Sleep -Seconds 3

        if (-not (Get-JarvisWorker)) {
            Write-Warning "O worker do Jarvis está iniciando..."
        } else {
            Write-Host "  -> Cérebro conectado com sucesso!" -ForegroundColor Green
        }
    }

    # 2. Inicia o Servidor Web (Frontend Next.js) em janela CMD visível
    if (Test-JarvisWeb) {
        $webJaEstavaAtiva = $true
        Write-Host "[2/3] Interface web do Jarvis já está ativa." -ForegroundColor Green
    }
    else {
        Write-Host "[2/3] Abrindo Interface Gráfica Next.js..." -ForegroundColor Yellow
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

    # 3. Abre o Navegador (somente se não estava tudo ativo previamente)
    if ($workerJaEstavaAtivo -and $webJaEstavaAtiva) {
        Write-Host ""
        Write-Host "Jarvis já está 100% ativo e pronto para uso no seu navegador!" -ForegroundColor Green
        Start-Sleep -Seconds 2
    }
    else {
        Write-Host "[3/3] Abrindo o Jarvis no navegador..." -ForegroundColor Cyan
        Start-Sleep -Seconds 1
        Start-Process $webUrl
        Write-Host "Jarvis pronto para uso!" -ForegroundColor Green
        Start-Sleep -Seconds 2
    }
}
catch {
    Write-Host ""
    Write-Host "[ERRO AO INICIAR]" -ForegroundColor Red
    Write-Error $_
    Start-Sleep -Seconds 5
}
finally {
    try { Remove-Item -Path $lockPath -Force -ErrorAction SilentlyContinue } catch {}
}

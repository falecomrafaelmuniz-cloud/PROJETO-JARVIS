$ErrorActionPreference = 'Stop'

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

    # 1. Inicia o Worker do LiveKit (Cérebro Python)
    $worker = Get-JarvisWorker
    if ($worker) {
        Write-Host "[1/3] Cérebro do Jarvis (Python) já está em execução." -ForegroundColor Green
    }
    else {
        Write-Host "[1/3] Ligando o Cérebro do Jarvis em Python..." -ForegroundColor Yellow
        Start-Process -FilePath $pythonExe `
            -ArgumentList @('"backend\agent.py"', 'dev') `
            -WorkingDirectory $agentDirectory `
            -WindowStyle Minimized | Out-Null
        Start-Sleep -Seconds 3

        if (-not (Get-JarvisWorker)) {
            Write-Warning "O worker do Jarvis está iniciando em segundo plano..."
        } else {
            Write-Host "  -> Cérebro conectado com sucesso!" -ForegroundColor Green
        }
    }

    # 2. Inicia o Servidor Web (Frontend Next.js)
    if (Test-JarvisWeb) {
        Write-Host "[2/3] Interface web do Jarvis já está ativa." -ForegroundColor Green
    }
    else {
        Write-Host "[2/3] Ligando a Interface Gráfica Next.js..." -ForegroundColor Yellow
        $pkgCommand = if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { 'pnpm dev' } else { 'npm run dev' }
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList @('/k', $pkgCommand) `
            -WorkingDirectory $webDirectory `
            -WindowStyle Minimized | Out-Null

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

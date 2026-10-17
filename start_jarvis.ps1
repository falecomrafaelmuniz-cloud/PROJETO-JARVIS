$ErrorActionPreference = 'Stop'

$agentDirectory = $PSScriptRoot
$backendDirectory = Join-Path $agentDirectory 'backend'
$agentFile = Join-Path $backendDirectory 'agent.py'
$pythonExe = Join-Path $agentDirectory '.venv\Scripts\python.exe'
$webDirectory = Join-Path $agentDirectory 'frontend'
$webEnvFile = Join-Path $webDirectory '.env.local'
$webUrl = 'http://localhost:3000'

function Get-JarvisWorker {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object {
            $_.ExecutablePath -eq $pythonExe -and
            $_.CommandLine -match 'agent\.py' -and
            $_.CommandLine -match '\bdev\b'
        } |
        Select-Object -First 1
}

function Test-JarvisWeb {
    try {
        $response = Invoke-WebRequest -Uri $webUrl -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200 -and $response.Content.Contains('Jarvis')
    }
    catch {
        return $false
    }
}

try {
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw "Python environment not found: $pythonExe"
    }
    if (-not (Test-Path -LiteralPath $agentFile -PathType Leaf)) {
        throw "Assistant entry point not found: $agentFile"
    }
    if (-not (Test-Path -LiteralPath $webDirectory -PathType Container)) {
        throw "Web interface folder not found: $webDirectory"
    }
    if (-not (Test-Path -LiteralPath $webEnvFile -PathType Leaf)) {
        throw "Web interface configuration not found: $webEnvFile"
    }
    if (-not (Get-Command pnpm.cmd -ErrorAction SilentlyContinue)) {
        throw 'pnpm.cmd is not installed or is not available in PATH.'
    }

    $worker = Get-JarvisWorker
    if ($worker) {
        Write-Host 'Jarvis voice worker is already running.'
    }
    else {
        Write-Host 'Starting Jarvis voice worker...'
        Start-Process -FilePath $pythonExe `
            -ArgumentList @('"backend\agent.py"', 'dev') `
            -WorkingDirectory $agentDirectory `
            -WindowStyle Minimized | Out-Null
        Start-Sleep -Seconds 4

        if (-not (Get-JarvisWorker)) {
            throw 'The voice worker did not stay running. Check its minimized command window for an error.'
        }
    }

    if (Test-JarvisWeb) {
        Write-Host 'Jarvis web interface is already running.'
    }
    else {
        Write-Host 'Starting Jarvis web interface...'
        Start-Process -FilePath $env:ComSpec `
            -ArgumentList @('/k', 'pnpm dev') `
            -WorkingDirectory $webDirectory `
            -WindowStyle Minimized | Out-Null

        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            if (Test-JarvisWeb) {
                break
            }
        }

        if (-not (Test-JarvisWeb)) {
            throw "The web interface did not become ready at $webUrl. Check its minimized command window."
        }
    }

    Write-Host 'Opening Jarvis...'
    Start-Process $webUrl
}
catch {
    Write-Error $_
    Read-Host 'Press Enter to close this window'
    exit 1
}

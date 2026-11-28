param(
    [switch]$RelaunchDesktop
)

$ErrorActionPreference = 'SilentlyContinue'
$env:ELECTRON_RUN_AS_NODE = $null

$repoRoot = $PSScriptRoot
$webDir = Join-Path $repoRoot 'frontend'
$roamingDir = Join-Path $env:APPDATA 'JARVIS'

# Aguarda a resposta HTTP do /api/update chegar na interface antes de reiniciar os servidores
Start-Sleep -Milliseconds 1500

# 1. Encerra Electron (se aberto), Next.js/pnpm e Python agent (árvore completa)
Get-Process -Name 'electron' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'agent\.py' } |
    ForEach-Object { & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null }

Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'next|pnpm' } |
    ForEach-Object { & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null }

Get-CimInstance Win32_Process -Filter "Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'JARVIS 2/2|pnpm dev|next dev' } |
    ForEach-Object { & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null }

$conn3000 = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($conn3000) {
    foreach ($c in $conn3000) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 2

# 2. Limpa caches antigos do Next.js e do Electron para garantir carregamento 100% limpo
$nextBuildDir = Join-Path $webDir '.next'
if (Test-Path -LiteralPath $nextBuildDir) {
    Remove-Item -LiteralPath $nextBuildDir -Recurse -Force -ErrorAction SilentlyContinue
}

foreach ($cacheFolder in @('Cache', 'Code Cache', 'GPUCache')) {
    $targetCache = Join-Path $roamingDir $cacheFolder
    if (Test-Path -LiteralPath $targetCache) {
        Remove-Item -LiteralPath $targetCache -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# 3. Garante dependências do frontend sincronizadas (sem bloqueio de arquivos abertos)
Push-Location $webDir
try {
    if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) {
        & pnpm.cmd install --prefer-offline 2>$null | Out-Null
    } elseif (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
        & npm.cmd install --prefer-offline 2>$null | Out-Null
    }
} finally {
    Pop-Location
}

# 4. Reinicia todos os serviços do Jarvis (e reabre a janela Desktop se estava em uso)
$startScript = Join-Path $repoRoot 'start_jarvis.ps1'
if ($RelaunchDesktop) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $startScript -SkipAutoUpdate
} else {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $startScript -SkipAutoUpdate -SkipElectron
}

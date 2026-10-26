import { NextResponse } from 'next/server';
import { spawn } from 'child_process';

export const revalidate = 0;

export async function POST() {
  try {
    const psCommand = [
      'Start-Sleep -Milliseconds 700;',
      "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'agent\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };",
      "Get-Process -Name 'livekit-server' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue;",
      "Get-CimInstance Win32_Process -Filter \"Name = 'cmd.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'JARVIS 1/2|JARVIS 2/2' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };",
      "Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'next' -or $_.CommandLine -match 'pnpm' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };",
    ].join(' ');

    const child = spawn(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', psCommand],
      {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
      }
    );
    child.unref();

    return NextResponse.json({ status: 'shutting_down' });
  } catch (error) {
    return new NextResponse(
      error instanceof Error ? error.message : 'Erro ao desligar servidores',
      { status: 500 }
    );
  }
}

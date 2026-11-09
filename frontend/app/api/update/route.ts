import { NextResponse } from 'next/server';
import { execFile, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

export const revalidate = 0;

function resolveGitExecutable(): string {
  const candidates = [
    process.env.LOCALAPPDATA
      ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Git', 'cmd', 'git.exe')
      : '',
    'C:\\Program Files\\Git\\cmd\\git.exe',
    'C:\\Program Files (x86)\\Git\\cmd\\git.exe',
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return 'git';
}

export async function GET() {
  try {
    const repoRoot = path.resolve(process.cwd(), '..');
    const gitExe = resolveGitExecutable();

    await execFileAsync(gitExe, ['fetch', 'origin', 'main'], {
      cwd: repoRoot,
      timeout: 15_000,
    });

    const { stdout: localHead } = await execFileAsync(gitExe, ['rev-parse', 'HEAD'], {
      cwd: repoRoot,
    });
    const { stdout: remoteHead } = await execFileAsync(gitExe, ['rev-parse', 'origin/main'], {
      cwd: repoRoot,
    });

    const localSha = localHead.trim();
    const remoteSha = remoteHead.trim();

    return NextResponse.json({
      status: 'ok',
      updateAvailable: localSha !== remoteSha,
      localCommit: localSha.slice(0, 7),
      remoteCommit: remoteSha.slice(0, 7),
    });
  } catch {
    return NextResponse.json({
      status: 'ok',
      updateAvailable: false,
    });
  }
}

export async function POST() {
  try {
    const repoRoot = path.resolve(process.cwd(), '..');
    const gitExe = resolveGitExecutable();

    // 1. Busca atualizações do GitHub (origin/main)
    await execFileAsync(gitExe, ['fetch', 'origin', 'main'], {
      cwd: repoRoot,
      timeout: 30_000,
    });

    const { stdout: localHead } = await execFileAsync(gitExe, ['rev-parse', 'HEAD'], {
      cwd: repoRoot,
    });
    const { stdout: remoteHead } = await execFileAsync(gitExe, ['rev-parse', 'origin/main'], {
      cwd: repoRoot,
    });

    const localSha = localHead.trim();
    const remoteSha = remoteHead.trim();

    let changedFiles: string[] = [];
    let commitSummary = '';

    if (localSha !== remoteSha) {
      const { stdout: diffOut } = await execFileAsync(
        gitExe,
        ['diff', '--name-only', `${localSha}..${remoteSha}`],
        { cwd: repoRoot }
      );
      changedFiles = diffOut
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);

      const { stdout: logOut } = await execFileAsync(
        gitExe,
        ['log', '--oneline', '-n', '5', `${localSha}..${remoteSha}`],
        { cwd: repoRoot }
      );
      commitSummary = logOut.trim();

      // Tenta pull com --autostash; se houver qualquer conflito local em código, sincroniza exatamente com origin/main
      try {
        await execFileAsync(gitExe, ['pull', '--rebase', '--autostash', 'origin', 'main'], {
          cwd: repoRoot,
          timeout: 45_000,
        });
      } catch {
        await execFileAsync(gitExe, ['rebase', '--abort'], { cwd: repoRoot }).catch(() => {});
        try {
          await execFileAsync(gitExe, ['pull', '--no-rebase', '--autostash', 'origin', 'main'], {
            cwd: repoRoot,
            timeout: 45_000,
          });
        } catch {
          await execFileAsync(gitExe, ['merge', '--abort'], { cwd: repoRoot }).catch(() => {});
          await execFileAsync(gitExe, ['reset', '--hard', 'origin/main'], {
            cwd: repoRoot,
            timeout: 30_000,
          });
        }
      }
    }

    // 2. Se backend/requirements.txt mudou, atualiza pacotes Python do .venv
    const pythonExe = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
    const requirementsPath = path.join(repoRoot, 'backend', 'requirements.txt');
    if (
      changedFiles.some((f) => f.replace(/\\/g, '/') === 'backend/requirements.txt') &&
      fs.existsSync(pythonExe) &&
      fs.existsSync(requirementsPath)
    ) {
      await execFileAsync(pythonExe, ['-m', 'pip', 'install', '-r', requirementsPath], {
        cwd: repoRoot,
        timeout: 120_000,
      });
    }

    // 3. Se frontend/package.json ou pnpm-lock.yaml mudou, atualiza pacotes do frontend
    if (
      changedFiles.some((f) => {
        const norm = f.replace(/\\/g, '/');
        return norm === 'frontend/package.json' || norm === 'frontend/pnpm-lock.yaml';
      })
    ) {
      await execFileAsync('cmd.exe', ['/c', 'pnpm', 'install'], {
        cwd: path.join(repoRoot, 'frontend'),
        timeout: 120_000,
      });
    }

    // 4. Reinicia o worker Python em segundo plano para carregar o código novo do backend imediatamente
    const restartBackendPs = [
      'Start-Sleep -Milliseconds 400;',
      `$repoRoot = '${repoRoot.replace(/'/g, "''")}';`,
      "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'agent\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };",
      "Get-CimInstance Win32_Process -Filter \"Name = 'cmd.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'JARVIS 1/2' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };",
      'Start-Sleep -Milliseconds 500;',
      "Start-Process -FilePath $env:ComSpec -ArgumentList @('/k', 'title [JARVIS 1/2] BACKEND (Python) && .\\.venv\\Scripts\\python.exe backend\\agent.py dev') -WorkingDirectory $repoRoot -WindowStyle Minimized | Out-Null;",
    ].join(' ');

    const child = spawn(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', restartBackendPs],
      {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
      }
    );
    child.unref();

    const updated = localSha !== remoteSha;

    return NextResponse.json({
      status: updated ? 'updated' : 'up_to_date',
      updated,
      previousCommit: localSha.slice(0, 7),
      currentCommit: remoteSha.slice(0, 7),
      changedFilesCount: changedFiles.length,
      commitSummary,
      message: updated
        ? `Atualizado com sucesso (${changedFiles.length} arquivo(s) atualizado(s))! Cérebro reiniciado.`
        : 'Jarvis já está na versão mais recente! Cérebro recarregado.',
    });
  } catch (error) {
    return NextResponse.json(
      {
        status: 'error',
        message:
          error instanceof Error ? error.message : 'Não foi possível atualizar o Jarvis agora.',
      },
      { status: 500 }
    );
  }
}


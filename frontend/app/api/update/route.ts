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

    // 1. Obter branch atual e número de versão progressivo local (contagem de commits)
    let currentBranch = 'main';
    try {
      const { stdout: branchOut } = await execFileAsync(gitExe, ['rev-parse', '--abbrev-ref', 'HEAD'], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      currentBranch = branchOut.trim() || 'main';
    } catch {
      // fallback
    }

    let buildNumber = 90;
    try {
      const { stdout: countOut } = await execFileAsync(gitExe, ['rev-list', '--count', 'HEAD'], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      const parsed = parseInt(countOut.trim(), 10);
      if (!isNaN(parsed) && parsed > 0) buildNumber = parsed;
    } catch {
      // fallback
    }

    const localVersion = `v3.0.${buildNumber}`;

    let localSha = '';
    try {
      const { stdout: headOut } = await execFileAsync(gitExe, ['rev-parse', 'HEAD'], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      localSha = headOut.trim();
    } catch {
      // fallback
    }

    // 2. Tenta checar o GitHub silenciosamente em segundo plano com timeout curto (10s)
    let updateAvailable = false;
    let remoteSha = '';
    let remoteVersion = localVersion;

    try {
      await execFileAsync(gitExe, ['fetch', 'origin', currentBranch], {
        cwd: repoRoot,
        timeout: 10_000,
      });

      const { stdout: rHeadOut } = await execFileAsync(gitExe, ['rev-parse', `origin/${currentBranch}`], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      remoteSha = rHeadOut.trim();

      if (localSha && remoteSha && localSha !== remoteSha) {
        // Verifica se o remote está à frente
        const { stdout: aheadOut } = await execFileAsync(
          gitExe,
          ['rev-list', '--count', `${localSha}..${remoteSha}`],
          { cwd: repoRoot, timeout: 5_000 }
        );
        const aheadCount = parseInt(aheadOut.trim(), 10) || 0;
        if (aheadCount > 0) {
          updateAvailable = true;
          remoteVersion = `v3.0.${buildNumber + aheadCount}`;
        }
      }
    } catch {
      // Falha de rede ou sem conexão com GitHub: não interrompe a inicialização
    }

    return NextResponse.json({
      status: 'ok',
      version: localVersion,
      buildNumber,
      currentBranch,
      updateAvailable,
      remoteVersion,
      localCommit: localSha ? localSha.slice(0, 7) : '',
      remoteCommit: remoteSha ? remoteSha.slice(0, 7) : '',
    });
  } catch {
    return NextResponse.json({
      status: 'ok',
      version: 'v3.0.90',
      buildNumber: 90,
      currentBranch: 'main',
      updateAvailable: false,
      remoteVersion: 'v3.0.90',
      localCommit: '',
      remoteCommit: '',
    });
  }
}

export async function POST() {
  try {
    const repoRoot = path.resolve(process.cwd(), '..');
    const gitExe = resolveGitExecutable();

    let currentBranch = 'main';
    try {
      const { stdout: branchOut } = await execFileAsync(gitExe, ['rev-parse', '--abbrev-ref', 'HEAD'], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      currentBranch = branchOut.trim() || 'main';
    } catch {
      // fallback
    }

    // 1. Busca atualizações do GitHub na branch atual
    await execFileAsync(gitExe, ['fetch', 'origin', currentBranch], {
      cwd: repoRoot,
      timeout: 30_000,
    });

    const { stdout: localHead } = await execFileAsync(gitExe, ['rev-parse', 'HEAD'], {
      cwd: repoRoot,
    });
    const { stdout: remoteHead } = await execFileAsync(gitExe, ['rev-parse', `origin/${currentBranch}`], {
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

      // Tenta pull com --autostash na branch ativa
      try {
        await execFileAsync(gitExe, ['pull', '--rebase', '--autostash', 'origin', currentBranch], {
          cwd: repoRoot,
          timeout: 45_000,
        });
      } catch {
        await execFileAsync(gitExe, ['rebase', '--abort'], { cwd: repoRoot }).catch(() => {});
        try {
          await execFileAsync(gitExe, ['pull', '--no-rebase', '--autostash', 'origin', currentBranch], {
            cwd: repoRoot,
            timeout: 45_000,
          });
        } catch {
          await execFileAsync(gitExe, ['merge', '--abort'], { cwd: repoRoot }).catch(() => {});
          await execFileAsync(gitExe, ['reset', '--hard', `origin/${currentBranch}`], {
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

    // 4. Reinicia o worker Python em segundo plano para carregar o código novo
    const restartBackendPs = [
      'Start-Sleep -Milliseconds 400;',
      `$repoRoot = '${repoRoot.replace(/'/g, "''")}';`,
      "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'agent\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };",
      'Start-Sleep -Milliseconds 500;',
      "Start-Process -FilePath \"$repoRoot\\.venv\\Scripts\\python.exe\" -ArgumentList @('backend\\agent.py', 'dev') -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null;",
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

    // Recalcula build pós-atualização
    let newBuildNumber = 90;
    try {
      const { stdout: newCountOut } = await execFileAsync(gitExe, ['rev-list', '--count', 'HEAD'], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      const parsed = parseInt(newCountOut.trim(), 10);
      if (!isNaN(parsed) && parsed > 0) newBuildNumber = parsed;
    } catch {
      // fallback
    }

    return NextResponse.json({
      status: updated ? 'updated' : 'up_to_date',
      updated,
      version: `v3.0.${newBuildNumber}`,
      buildNumber: newBuildNumber,
      previousCommit: localSha.slice(0, 7),
      currentCommit: remoteSha.slice(0, 7),
      changedFilesCount: changedFiles.length,
      commitSummary,
      message: updated
        ? `Jarvis atualizado para v3.0.${newBuildNumber}! Cérebro reiniciado.`
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

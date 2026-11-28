import { NextResponse } from 'next/server';
import { execFile, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

export const revalidate = 0;

const REPO_REMOTE_URL = 'https://github.com/falecomrafaelmuniz-cloud/PROJETO-JARVIS.git';
const TARGET_BRANCH = 'main';

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

async function ensureGitRepositoryAndRemote(gitExe: string, repoRoot: string): Promise<void> {
  const gitDir = path.join(repoRoot, '.git');
  if (!fs.existsSync(gitDir)) {
    await execFileAsync(gitExe, ['init', '-b', TARGET_BRANCH], {
      cwd: repoRoot,
      timeout: 10_000,
    });
  }

  try {
    await execFileAsync(gitExe, ['remote', 'get-url', 'origin'], {
      cwd: repoRoot,
      timeout: 5_000,
    });
  } catch {
    await execFileAsync(gitExe, ['remote', 'add', 'origin', REPO_REMOTE_URL], {
      cwd: repoRoot,
      timeout: 5_000,
    });
  }
}

async function isElectronProcessRunning(): Promise<boolean> {
  try {
    const { stdout } = await execFileAsync(
      'tasklist.exe',
      ['/FI', 'IMAGENAME eq electron.exe', '/NH'],
      { timeout: 5_000 }
    );
    return stdout.toLowerCase().includes('electron.exe');
  } catch {
    return false;
  }
}

async function stopPythonAgentProcesses(): Promise<void> {
  const stopCmd =
    "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'agent\\.py' } | ForEach-Object { & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null }";
  try {
    await execFileAsync(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', stopCmd],
      { timeout: 10_000 }
    );
  } catch {
    // Ignora se nenhum worker estiver ativo
  }
}

export async function GET() {
  try {
    const repoRoot = path.resolve(process.cwd(), '..');
    const gitExe = resolveGitExecutable();

    let currentBranch = TARGET_BRANCH;
    try {
      const { stdout: branchOut } = await execFileAsync(
        gitExe,
        ['rev-parse', '--abbrev-ref', 'HEAD'],
        { cwd: repoRoot, timeout: 5_000 }
      );
      currentBranch = branchOut.trim() || TARGET_BRANCH;
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

    // Sempre consulta origin/main (independentemente da branch local atual)
    let updateAvailable = false;
    let remoteSha = '';
    let remoteVersion = localVersion;

    try {
      await ensureGitRepositoryAndRemote(gitExe, repoRoot);

      await execFileAsync(gitExe, ['fetch', 'origin', TARGET_BRANCH], {
        cwd: repoRoot,
        timeout: 12_000,
      });

      const { stdout: rHeadOut } = await execFileAsync(
        gitExe,
        ['rev-parse', `origin/${TARGET_BRANCH}`],
        { cwd: repoRoot, timeout: 5_000 }
      );
      remoteSha = rHeadOut.trim();

      let remoteBuildNumber = buildNumber;
      try {
        const { stdout: rCountOut } = await execFileAsync(
          gitExe,
          ['rev-list', '--count', `origin/${TARGET_BRANCH}`],
          { cwd: repoRoot, timeout: 5_000 }
        );
        const parsedRemote = parseInt(rCountOut.trim(), 10);
        if (!isNaN(parsedRemote) && parsedRemote > 0) {
          remoteBuildNumber = parsedRemote;
        }
      } catch {
        // fallback
      }

      if (remoteSha && (!localSha || localSha !== remoteSha || currentBranch !== TARGET_BRANCH)) {
        if (!localSha) {
          updateAvailable = true;
          remoteVersion = `v3.0.${remoteBuildNumber}`;
        } else {
          const { stdout: aheadOut } = await execFileAsync(
            gitExe,
            ['rev-list', '--count', `${localSha}..${remoteSha}`],
            { cwd: repoRoot, timeout: 5_000 }
          );
          const aheadCount = parseInt(aheadOut.trim(), 10) || 0;
          if (aheadCount > 0 || currentBranch !== TARGET_BRANCH) {
            updateAvailable = true;
            remoteVersion = `v3.0.${remoteBuildNumber}`;
          }
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
      currentBranch: TARGET_BRANCH,
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

    // Backup de segurança em memória do .env local (já ignorado pelo .gitignore, mas protegido em dobro)
    const envPath = path.join(repoRoot, '.env');
    const envBackup = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf8') : null;

    // 1. Garante repositório Git conectado ao repositório central oficial
    await ensureGitRepositoryAndRemote(gitExe, repoRoot);

    // 2. Busca absolutamente tudo da origin/main no GitHub
    await execFileAsync(gitExe, ['fetch', 'origin', TARGET_BRANCH, '--prune'], {
      cwd: repoRoot,
      timeout: 45_000,
    });

    let localSha = '';
    try {
      const { stdout: localHead } = await execFileAsync(gitExe, ['rev-parse', 'HEAD'], {
        cwd: repoRoot,
        timeout: 5_000,
      });
      localSha = localHead.trim();
    } catch {
      localSha = '';
    }

    const { stdout: remoteHead } = await execFileAsync(
      gitExe,
      ['rev-parse', `origin/${TARGET_BRANCH}`],
      { cwd: repoRoot, timeout: 5_000 }
    );
    const remoteSha = remoteHead.trim();

    let changedFiles: string[] = [];
    let commitSummary = '';

    if (localSha && localSha !== remoteSha) {
      try {
        const { stdout: diffOut } = await execFileAsync(
          gitExe,
          ['diff', '--name-only', `${localSha}..${remoteSha}`],
          { cwd: repoRoot, timeout: 10_000 }
        );
        changedFiles = diffOut
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean);

        const { stdout: logOut } = await execFileAsync(
          gitExe,
          ['log', '--oneline', '-n', '8', `${localSha}..${remoteSha}`],
          { cwd: repoRoot, timeout: 10_000 }
        );
        commitSummary = logOut.trim();
      } catch {
        // Ignora caso histórico anterior seja raso
      }
    }

    // 3. Encerra o processo Python antes de sincronizar arquivos e instalar pacotes (evita locks no Windows)
    await stopPythonAgentProcesses();

    // 4. Sincroniza com origin/main sem nunca regredir commits locais ainda não enviados ao GitHub
    let behindCount = 0;
    let aheadOfRemoteCount = 0;
    if (localSha && remoteSha && localSha !== remoteSha) {
      try {
        const { stdout: behindOut } = await execFileAsync(
          gitExe,
          ['rev-list', '--count', `${localSha}..${remoteSha}`],
          { cwd: repoRoot, timeout: 5_000 }
        );
        behindCount = parseInt(behindOut.trim(), 10) || 0;

        const { stdout: aheadOut } = await execFileAsync(
          gitExe,
          ['rev-list', '--count', `${remoteSha}..${localSha}`],
          { cwd: repoRoot, timeout: 5_000 }
        );
        aheadOfRemoteCount = parseInt(aheadOut.trim(), 10) || 0;
      } catch {
        behindCount = 1;
      }
    }

    if (!localSha || behindCount > 0) {
      if (aheadOfRemoteCount > 0 && localSha) {
        // Caso raro: este PC tem commits locais novos E o GitHub também tem commits novos
        await execFileAsync(
          gitExe,
          ['checkout', TARGET_BRANCH],
          { cwd: repoRoot, timeout: 15_000 }
        ).catch(() => {});
        await execFileAsync(
          gitExe,
          ['pull', '--rebase', '--autostash', 'origin', TARGET_BRANCH],
          { cwd: repoRoot, timeout: 45_000 }
        );
      } else {
        // Caso padrão: alinha 100% dos arquivos rastreados com origin/main (guardando edições soltas em stash)
        try {
          const { stdout: dirtyOut } = await execFileAsync(
            gitExe,
            ['status', '--porcelain', '--untracked-files=no'],
            { cwd: repoRoot, timeout: 10_000 }
          );
          if (dirtyOut.trim().length > 0) {
            await execFileAsync(
              gitExe,
              ['stash', 'push', '-m', `jarvis-pre-update-${Date.now()}`],
              { cwd: repoRoot, timeout: 15_000 }
            ).catch(() => {});
          }
        } catch {
          // Prossegue para alinhamento direto
        }

        await execFileAsync(
          gitExe,
          ['checkout', '-f', '-B', TARGET_BRANCH, `origin/${TARGET_BRANCH}`],
          { cwd: repoRoot, timeout: 30_000 }
        );
        await execFileAsync(gitExe, ['reset', '--hard', `origin/${TARGET_BRANCH}`], {
          cwd: repoRoot,
          timeout: 30_000,
        });
      }
    } else if (aheadOfRemoteCount === 0) {
      // Mesmo commit da origin/main: apenas garante que esteja na branch main oficial
      await execFileAsync(gitExe, ['checkout', '-B', TARGET_BRANCH], {
        cwd: repoRoot,
        timeout: 15_000,
      }).catch(() => {});
    }

    await execFileAsync(
      gitExe,
      ['branch', `--set-upstream-to=origin/${TARGET_BRANCH}`, TARGET_BRANCH],
      { cwd: repoRoot, timeout: 5_000 }
    ).catch(() => {});

    if (envBackup !== null && !fs.existsSync(envPath)) {
      fs.writeFileSync(envPath, envBackup, 'utf8');
    }

    // 5. SEMPRE garante que todas as dependências Python do backend/requirements.txt estejam instaladas
    const pythonExe = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
    const requirementsPath = path.join(repoRoot, 'backend', 'requirements.txt');
    if (fs.existsSync(pythonExe) && fs.existsSync(requirementsPath)) {
      await execFileAsync(
        pythonExe,
        ['-m', 'pip', 'install', '-r', requirementsPath, '--quiet'],
        {
          cwd: repoRoot,
          timeout: 180_000,
        }
      );
    }

    // 6. Calcula versão atualizada pós-sincronização
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

    // 7. Dispara script desacoplado que limpa caches (.next e Electron), sincroniza pnpm e reinicia tudo do zero
    const wasElectronRunning = await isElectronProcessRunning();
    const restartScriptPath = path.join(repoRoot, 'restart_jarvis_update.ps1');
    const psArgs = [
      '-NoProfile',
      '-ExecutionPolicy',
      'Bypass',
      '-WindowStyle',
      'Hidden',
      '-File',
      restartScriptPath,
    ];
    if (wasElectronRunning) {
      psArgs.push('-RelaunchDesktop');
    }

    const child = spawn('powershell.exe', psArgs, {
      cwd: repoRoot,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.unref();

    const updated = !localSha || behindCount > 0;

    return NextResponse.json({
      status: updated ? 'updated' : 'up_to_date',
      updated,
      fullRestart: true,
      isElectron: wasElectronRunning,
      version: `v3.0.${newBuildNumber}`,
      buildNumber: newBuildNumber,
      previousCommit: localSha ? localSha.slice(0, 7) : '',
      currentCommit: remoteSha.slice(0, 7),
      changedFilesCount: changedFiles.length,
      commitSummary,
      message: updated
        ? `Jarvis atualizado para v3.0.${newBuildNumber}! Reiniciando todos os sistemas...`
        : `Jarvis sincronizado com o GitHub (v3.0.${newBuildNumber})! Reiniciando sistemas limpos...`,
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

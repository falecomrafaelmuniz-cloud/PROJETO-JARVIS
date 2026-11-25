const { app, BrowserWindow, shell, ipcMain } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const http = require('http');

app.setAppUserModelId('com.jarvis.system');
app.name = 'JARVIS';

let mainWindow = null;
let pythonProcess = null;
let livekitProcess = null;
let nextProcess = null;

const projectRoot = path.resolve(__dirname, '..', '..');
const pythonExe = path.join(projectRoot, '.venv', 'Scripts', 'python.exe');
const agentPy = path.join(projectRoot, 'backend', 'agent.py');
const livekitExe = path.join(projectRoot, 'bin', 'livekit-server.exe');
const livekitConfig = path.join(projectRoot, 'bin', 'livekit.yaml');
const iconPath = path.join(projectRoot, 'jarvis_constellation.ico');

// Função auxiliar para testar se uma URL HTTP está pronta
function checkUrlReady(url, maxRetries = 40, intervalMs = 600) {
  return new Promise((resolve) => {
    let retries = 0;
    const check = () => {
      const req = http.get(url, (res) => {
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
          resolve(true);
        } else {
          retry();
        }
      });
      req.on('error', () => retry());
      req.setTimeout(500, () => {
        req.destroy();
        retry();
      });
    };

    const retry = () => {
      retries++;
      if (retries >= maxRetries) {
        resolve(false);
      } else {
        setTimeout(check, intervalMs);
      }
    };

    check();
  });
}

// Inicia processos silenciosos em segundo plano (sem janela de CMD)
function startBackgroundServices() {
  // 1. LiveKit Server (silencioso)
  try {
    livekitProcess = spawn(livekitExe, ['--config', livekitConfig], {
      cwd: projectRoot,
      windowsHide: true,
      stdio: 'ignore',
    });
    console.log('[Desktop] LiveKit iniciado em segundo plano.');
  } catch (err) {
    console.error('[Desktop] Erro ao iniciar LiveKit:', err);
  }

  // 2. Python Agent (silencioso)
  try {
    pythonProcess = spawn(pythonExe, [agentPy, 'dev'], {
      cwd: projectRoot,
      windowsHide: true,
      stdio: 'ignore',
    });
    console.log('[Desktop] Python Agent iniciado em segundo plano.');
  } catch (err) {
    console.error('[Desktop] Erro ao iniciar Python Agent:', err);
  }

  // 3. Frontend Next.js (silencioso)
  try {
    const frontendDir = path.resolve(__dirname, '..');
    nextProcess = spawn('pnpm.cmd', ['start'], {
      cwd: frontendDir,
      windowsHide: true,
      stdio: 'ignore',
      shell: true,
    });
    console.log('[Desktop] Frontend Next.js iniciado.');
  } catch (err) {
    console.error('[Desktop] Erro ao iniciar Next.js:', err);
  }
}

// Encerra todos os processos filhos ao sair
function cleanupProcesses() {
  console.log('[Desktop] Encerrando processos...');
  try {
    if (pythonProcess) {
      exec(`taskkill /F /T /PID ${pythonProcess.pid}`, () => {});
    }
    if (livekitProcess) {
      exec(`taskkill /F /T /PID ${livekitProcess.pid}`, () => {});
    }
    if (nextProcess) {
      exec(`taskkill /F /T /PID ${nextProcess.pid}`, () => {});
    }
  } catch {}
}

let savedBounds = null;

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 840,
    minWidth: 100,
    minHeight: 100,
    title: 'JARVIS SYSTEM V3.0',
    transparent: true,
    frame: false,
    icon: iconPath,
    autoHideMenuBar: true,
    hasShadow: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs'),
      devTools: false,
    },
  });

  // Listener para alternar entre Modo Orb (200x200 flutuante) e Modo Completo
  ipcMain.removeAllListeners('jarvis:set-orb-mode');
  ipcMain.on('jarvis:set-orb-mode', (event, isOrb) => {
    if (!mainWindow) return;
    if (isOrb) {
      if (mainWindow.getSize()[0] > 300) {
        savedBounds = mainWindow.getBounds();
      }
      mainWindow.setAlwaysOnTop(true, 'screen-saver');
      mainWindow.setMinimumSize(100, 100);
      mainWindow.setSize(200, 200, true);
      mainWindow.setIgnoreMouseEvents(true, { forward: true });
    } else {
      mainWindow.setAlwaysOnTop(false);
      mainWindow.setIgnoreMouseEvents(false);
      mainWindow.setMinimumSize(960, 640);
      if (savedBounds && savedBounds.width && savedBounds.width > 300) {
        mainWindow.setBounds(savedBounds, true);
      } else {
        mainWindow.setSize(1320, 840, true);
        mainWindow.center();
      }
    }
  });

  ipcMain.removeAllListeners('jarvis:set-ignore-mouse');
  ipcMain.on('jarvis:set-ignore-mouse', (event, ignore) => {
    if (!mainWindow) return;
    mainWindow.setIgnoreMouseEvents(ignore, { forward: true });
  });

  ipcMain.removeAllListeners('jarvis:close-window');
  ipcMain.on('jarvis:close-window', () => {
    if (mainWindow) mainWindow.close();
  });

  // Abre links externos no navegador padrão do usuário
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Aguarda o servidor web estar pronto
  const isReady = await checkUrlReady('http://localhost:3000');
  if (isReady && mainWindow) {
    mainWindow.loadURL('http://localhost:3000');
  } else if (mainWindow) {
    mainWindow.loadURL('http://localhost:3000');
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// Previne múltiplas instâncias
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    // Verifica se os serviços já estão rodando
    http.get('http://localhost:3000', () => {
      createWindow();
    }).on('error', () => {
      startBackgroundServices();
      createWindow();
    });

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on('before-quit', () => {
    cleanupProcesses();
  });

  app.on('window-all-closed', () => {
    cleanupProcesses();
    if (process.platform !== 'darwin') {
      app.quit();
    }
  });
}

const { spawn } = require('child_process');
const electronPath = require('electron');
const path = require('path');

const mainScript = path.join(__dirname, 'main.cjs');

// Garante que o Electron execute no modo desktop gráfico e não como processo node CLI
const cleanEnv = { ...process.env };
delete cleanEnv.ELECTRON_RUN_AS_NODE;

const child = spawn(electronPath, [mainScript], {
  detached: true,
  stdio: 'ignore',
  env: cleanEnv,
});

child.unref();
process.exit(0);

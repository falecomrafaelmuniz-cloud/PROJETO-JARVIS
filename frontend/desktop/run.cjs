const { spawn } = require('child_process');
const electronPath = require('electron');
const path = require('path');

const mainScript = path.join(__dirname, 'main.cjs');
const child = spawn(electronPath, [mainScript], {
  detached: true,
  stdio: 'ignore',
});

child.unref();
process.exit(0);

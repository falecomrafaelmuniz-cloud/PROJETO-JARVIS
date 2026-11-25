const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisDesktop', {
  setOrbMode: (isOrb) => ipcRenderer.send('jarvis:set-orb-mode', isOrb),
  closeWindow: () => ipcRenderer.send('jarvis:close-window'),
});

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisDesktop', {
  setOrbMode: (isOrb) => ipcRenderer.send('jarvis:set-orb-mode', isOrb),
  setIgnoreMouse: (ignore) => ipcRenderer.send('jarvis:set-ignore-mouse', ignore),
  closeWindow: () => ipcRenderer.send('jarvis:close-window'),
  onToggleOrbMode: (callback) => {
    const handler = () => callback();
    ipcRenderer.on('jarvis:toggle-orb-mode', handler);
    return () => ipcRenderer.removeListener('jarvis:toggle-orb-mode', handler);
  },
});

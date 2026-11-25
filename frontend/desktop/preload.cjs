const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisDesktop', {
  setOrbMode: (isOrb) => ipcRenderer.send('jarvis:set-orb-mode', isOrb),
  setIgnoreMouse: (ignore) => ipcRenderer.send('jarvis:set-ignore-mouse', ignore),
  closeWindow: () => ipcRenderer.send('jarvis:close-window'),
  getWindowPos: () => ipcRenderer.invoke('jarvis:get-window-pos'),
  moveWindow: (x, y) => ipcRenderer.send('jarvis:move-window', { x, y }),
  onToggleOrbMode: (callback) => {
    const handler = () => callback();
    ipcRenderer.on('jarvis:toggle-orb-mode', handler);
    return () => ipcRenderer.removeListener('jarvis:toggle-orb-mode', handler);
  },
});

const FIRST_PORT = 47865;
const LAST_PORT = 47884;
const JARVIS_TAB_KEY = "jarvisTabId";
const JARVIS_WINDOW_KEY = "jarvisWindowId";
let bridgePort = null;
let polling = false;

chrome.alarms.create("jarvis-bridge-poll", { periodInMinutes: 0.5 });

chrome.runtime.onInstalled.addListener(() => {
  void pollBridge();
});

chrome.runtime.onStartup.addListener(() => {
  void pollBridge();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "jarvis-bridge-poll") {
    void pollBridge();
  }
});

async function findBridgePort() {
  for (let port = FIRST_PORT; port <= LAST_PORT; port += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 700);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        continue;
      }
      const health = await response.json();
      if (health.service === "jarvis-browser-bridge" && health.version === 1) {
        return port;
      }
    } catch {
      // Keep searching the local port range while the Jarvis bridge starts.
    } finally {
      clearTimeout(timeout);
    }
  }
  return null;
}

async function pollBridge() {
  if (polling) {
    return;
  }
  polling = true;
  try {
    while (true) {
      if (bridgePort === null) {
        bridgePort = await findBridgePort();
        if (bridgePort === null) {
          await pause(2000);
          continue;
        }
      }

      let response;
      try {
        response = await fetch(`http://127.0.0.1:${bridgePort}/next`, {
          cache: "no-store",
        });
      } catch {
        bridgePort = null;
        await pause(1000);
        continue;
      }
      if (!response.ok) {
        bridgePort = null;
        await pause(1000);
        continue;
      }

      const command = await response.json();
      if (!command.request_id || !command.url) {
        continue;
      }
      await handleCommand(command);
    }
  } catch (error) {
    console.error("Falha na conexão local com a Jarvis:", error);
  } finally {
    polling = false;
  }
}

async function handleCommand(command) {
  let result;
  try {
    await openInJarvisTab(command.url);
    result = { request_id: command.request_id, ok: true };
  } catch (error) {
    result = {
      request_id: command.request_id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }

  await fetch(`http://127.0.0.1:${bridgePort}/result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(result),
  });
}

async function openInJarvisTab(url) {
  const stored = await chrome.storage.local.get([
    JARVIS_TAB_KEY,
    JARVIS_WINDOW_KEY,
  ]);
  const tabId = stored[JARVIS_TAB_KEY];

  if (Number.isInteger(tabId)) {
    try {
      const tab = await chrome.tabs.get(tabId);
      await chrome.tabs.update(tab.id, { url, active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      return;
    } catch {
      await chrome.storage.local.remove(JARVIS_TAB_KEY);
    }
  }

  const activeTabs = await chrome.tabs.query({
    active: true,
    lastFocusedWindow: true,
  });
  const activeTab = activeTabs[0];
  let windowId = stored[JARVIS_WINDOW_KEY];

  if (Number.isInteger(windowId)) {
    try {
      await chrome.windows.get(windowId);
    } catch {
      windowId = undefined;
    }
  } else if (activeTab) {
    windowId = activeTab.windowId;
  }

  const options = { url, active: true };
  if (Number.isInteger(windowId)) {
    options.windowId = windowId;
  }
  if (activeTab && activeTab.windowId === windowId) {
    options.index = activeTab.index + 1;
  }

  const tab = await chrome.tabs.create(options);
  await chrome.storage.local.set({
    [JARVIS_TAB_KEY]: tab.id,
    [JARVIS_WINDOW_KEY]: tab.windowId,
  });
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
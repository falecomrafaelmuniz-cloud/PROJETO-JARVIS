const FIRST_PORT = 47865;
const LAST_PORT = 47884;
const JARVIS_TAB_KEY = "jarvisTabId";
const JARVIS_WINDOW_KEY = "jarvisWindowId";
let bridgePort = null;
let polling = false;

chrome.alarms.create("jarvis-bridge-poll", { periodInMinutes: 0.5 });

chrome.runtime.onInstalled.addListener(() => {
  void injectAutoSkipIntoOpenYouTubeTabs();
  void pollBridge();
});

chrome.runtime.onStartup.addListener(() => {
  void injectAutoSkipIntoOpenYouTubeTabs();
  void pollBridge();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "jarvis-bridge-poll") {
    void pollBridge();
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (
    message &&
    message.type === "JARVIS_SKIP_CLICK" &&
    typeof message.x === "number" &&
    typeof message.y === "number"
  ) {
    void sendSkipClickToBridge(message.x, message.y);
  }
});

async function sendSkipClickToBridge(x, y) {
  if (bridgePort === null) {
    bridgePort = await findBridgePort();
  }
  if (bridgePort === null) return;
  try {
    await fetch(`http://127.0.0.1:${bridgePort}/click-skip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y }),
    });
  } catch {
    // ignore
  }
}

async function injectAutoSkipIntoOpenYouTubeTabs() {
  if (!chrome.scripting) return;
  try {
    const tabs = await chrome.tabs.query({ url: ["*://*.youtube.com/*"] });
    for (const tab of tabs) {
      if (tab.id) {
        await chrome.scripting
          .executeScript({
            target: { tabId: tab.id },
            files: ["youtube_autoskip.js"],
          })
          .catch(() => {});
      }
    }
  } catch {
    // ignore
  }
}

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
        let query = "";
        try {
          const allTabs = await chrome.tabs.query({});
          const activeTab =
            allTabs.find((t) => t.active) || allTabs[0] || null;

          const gmailTab =
            allTabs.find(
              (t) =>
                t.url &&
                t.url.includes("mail.google.com") &&
                t.active
            ) ||
            allTabs.find(
              (t) => t.url && t.url.includes("mail.google.com")
            ) ||
            null;

          const params = new URLSearchParams();
          params.set("v", "2");
          if (activeTab && activeTab.url) {
            params.set("active_url", activeTab.url);
            params.set("active_title", activeTab.title || "");
          }
          if (gmailTab && gmailTab.url) {
            params.set("gmail_url", gmailTab.url);
            params.set("gmail_title", gmailTab.title || "");
          }
          const qs = params.toString();
          if (qs) {
            query = `?${qs}`;
          }
        } catch {
          // ignore tab query errors
        }
        response = await fetch(`http://127.0.0.1:${bridgePort}/next${query}`, {
          cache: "no-store",
        });
      } catch {
        bridgePort = null;
        await pause(600);
        continue;
      }
      if (!response.ok) {
        bridgePort = null;
        await pause(600);
        continue;
      }

      const command = await response.json();
      if (!command.request_id || (!command.url && !command.action)) {
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
    if (command.action === "close_tab") {
      const count = await closeJarvisTab(command.target);
      result = { request_id: command.request_id, ok: true, closed_count: count };
    } else if (command.action === "minimize_tab") {
      const mode = await minimizeJarvisTab(command.target);
      result = { request_id: command.request_id, ok: Boolean(mode), mode };
    } else if (command.action === "focus_tab") {
      const mode = await focusOrMaximizeJarvisTab(command.target);
      result = { request_id: command.request_id, ok: Boolean(mode), mode };
    } else {
      await openInJarvisTab(command.url, Boolean(command.nova_aba));
      result = { request_id: command.request_id, ok: true };
    }
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

async function closeJarvisTab(target) {
  const allTabs = await chrome.tabs.query({});
  const rawTarget = String(target || "atual").trim().toLowerCase();

  // NUNCA FECHA O JARVIS LOCALHOST (PROTEÇÃO TOTAL)
  const isProtected = (tab) => {
    const url = (tab.url || "").toLowerCase();
    return url.includes("localhost:3000") || url.includes("127.0.0.1:3000");
  };

  // Se o alvo for "atual", "ativa", etc.
  if (["atual", "ativa", "ativo", "este", "esta", "esse", "essa", "selecionada", ""].includes(rawTarget)) {
    const activeTabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const tabToClose = activeTabs.find((t) => !isProtected(t)) || allTabs.find((t) => t.active && !isProtected(t));
    if (tabToClose && tabToClose.id) {
      await chrome.tabs.remove(tabToClose.id);
      return 1;
    }
    return 0;
  }

  // Mapeia termos comuns
  let searchTerms = [rawTarget];
  if (rawTarget.includes("drive")) {
    searchTerms.push("drive.google.com", "google drive", "meu drive");
  }
  if (rawTarget.includes("gmail") || rawTarget.includes("email") || rawTarget.includes("e-mail")) {
    searchTerms.push("mail.google.com", "gmail");
  }
  if (rawTarget.includes("youtube")) {
    searchTerms.push("youtube.com", "youtube");
  }
  if (rawTarget.includes("whatsapp")) {
    searchTerms.push("web.whatsapp.com", "whatsapp");
  }

  const matchingTabs = allTabs.filter((tab) => {
    if (isProtected(tab)) return false;
    const url = (tab.url || "").toLowerCase();
    const title = (tab.title || "").toLowerCase();
    return searchTerms.some((term) => url.includes(term) || title.includes(term));
  });

  if (matchingTabs.length === 0) {
    return 0;
  }

  for (const tab of matchingTabs) {
    if (tab.id) {
      try {
        await chrome.tabs.remove(tab.id);
      } catch {
        // ignore
      }
    }
  }
  return matchingTabs.length;
}

async function minimizeJarvisTab(target) {
  const allTabs = await chrome.tabs.query({});
  const rawTarget = String(target || "atual").trim().toLowerCase();

  const isJarvisTab = (tab) => {
    const url = (tab.url || "").toLowerCase();
    const title = (tab.title || "").toLowerCase();
    return (
      url.includes("localhost:3000") ||
      url.includes("127.0.0.1:3000") ||
      title.includes("jarvis - assistente")
    );
  };

  const jarvisTab = allTabs.find((t) => isJarvisTab(t));

  // Se pediu para minimizar a janela inteira do navegador
  if (["navegador", "chrome", "google chrome", "janela"].includes(rawTarget)) {
    const win = await chrome.windows.getLastFocused();
    if (win && win.id) {
      await chrome.windows.update(win.id, { state: "minimized" });
      return "window_minimized";
    }
  }

  let searchTerms = [rawTarget];
  if (rawTarget.includes("drive")) {
    searchTerms.push("drive.google.com", "google drive", "meu drive");
  }
  if (rawTarget.includes("gmail") || rawTarget.includes("email") || rawTarget.includes("e-mail")) {
    searchTerms.push("mail.google.com", "gmail");
  }
  if (rawTarget.includes("youtube")) {
    searchTerms.push("youtube.com", "youtube");
  }
  if (rawTarget.includes("whatsapp")) {
    searchTerms.push("web.whatsapp.com", "whatsapp");
  }

  let targetTab = null;
  if (["atual", "ativa", "ativo", "este", "esta", "esse", "essa", "aba", "guia", "site", ""].includes(rawTarget)) {
    const activeTabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    targetTab = activeTabs[0] || allTabs.find((t) => t.active);
  } else {
    targetTab = allTabs.find((tab) => {
      if (isJarvisTab(tab)) return false;
      const url = (tab.url || "").toLowerCase();
      const title = (tab.title || "").toLowerCase();
      return searchTerms.some((term) => url.includes(term) || title.includes(term));
    });
  }

  if (!targetTab) {
    return "";
  }

  // Se a aba alvo está na MESMA janela de outras abas, destaca apenas essa aba para uma janela própria e minimiza SÓ ela (preservando todas as outras abas abertas na tela!)
  const tabsInSameWindow = allTabs.filter((t) => t.windowId === targetTab.windowId);
  if (tabsInSameWindow.length > 1 && targetTab.id) {
    const newWin = await chrome.windows.create({ tabId: targetTab.id, focused: false });
    if (newWin && newWin.id) {
      await chrome.windows.update(newWin.id, { state: "minimized" });
      return "detached_and_minimized";
    }
  }

  // Se a aba já está sozinha em uma janela separada (e não é a janela do Jarvis), minimiza aquela janela!
  if (targetTab.windowId && (!jarvisTab || targetTab.windowId !== jarvisTab.windowId)) {
    await chrome.windows.update(targetTab.windowId, { state: "minimized" });
    return "window_minimized";
  }

  return "";
}

async function focusOrMaximizeJarvisTab(target) {
  const allTabs = await chrome.tabs.query({});
  const rawTarget = String(target || "atual").trim().toLowerCase();

  if (["navegador", "chrome", "google chrome", "janela", "atual", "ativa", ""].includes(rawTarget)) {
    const wins = await chrome.windows.getAll({ populate: false });
    const targetWin = wins.find((w) => w.state === "minimized") || wins[0];
    if (targetWin && targetWin.id) {
      await chrome.windows.update(targetWin.id, { state: "maximized", focused: true });
      return "window_maximized";
    }
  }

  let searchTerms = [rawTarget];
  if (rawTarget.includes("drive")) {
    searchTerms.push("drive.google.com", "google drive", "meu drive");
  }
  if (rawTarget.includes("gmail") || rawTarget.includes("email") || rawTarget.includes("e-mail")) {
    searchTerms.push("mail.google.com", "gmail");
  }
  if (rawTarget.includes("youtube")) {
    searchTerms.push("youtube.com", "youtube");
  }
  if (rawTarget.includes("whatsapp")) {
    searchTerms.push("web.whatsapp.com", "whatsapp");
  }
  if (rawTarget.includes("jarvis")) {
    searchTerms.push("localhost:3000", "127.0.0.1:3000", "jarvis - assistente");
  }

  const targetTab = allTabs.find((tab) => {
    const url = (tab.url || "").toLowerCase();
    const title = (tab.title || "").toLowerCase();
    return searchTerms.some((term) => url.includes(term) || title.includes(term));
  });

  if (!targetTab || !targetTab.id) {
    return "";
  }

  if (targetTab.windowId) {
    await chrome.windows.update(targetTab.windowId, { state: "maximized", focused: true });
  }
  await chrome.tabs.update(targetTab.id, { active: true });
  return "tab_focused_and_maximized";
}

async function openInJarvisTab(url, novaAba = false) {
  // Se novaAba for true, abre diretamente em uma nova aba para permitir múltiplos recursos simultâneos
  if (novaAba) {
    try {
      const activeTabs = await chrome.tabs.query({
        active: true,
        lastFocusedWindow: true,
      });
      const activeTab = activeTabs[0];
      let windowId = activeTab ? activeTab.windowId : undefined;
      const options = { url, active: true };
      if (Number.isInteger(windowId)) {
        options.windowId = windowId;
      }
      if (activeTab && activeTab.windowId === windowId) {
        options.index = activeTab.index + 1;
      }
      const newTab = await chrome.tabs.create(options);
      if (newTab && newTab.windowId) {
        await chrome.windows.update(newTab.windowId, { focused: true });
      }
      return;
    } catch {
      // fallback
    }
  }

  // Se for uma ação dentro do Google Mail, reutiliza SEMPRE a mesma aba do Gmail
  if (typeof url === "string" && url.includes("mail.google.com")) {
    try {
      const allTabs = await chrome.tabs.query({});
      const gmailTabs = allTabs.filter(
        (t) => t.url && t.url.includes("mail.google.com")
      );
      if (gmailTabs.length > 0) {
        const targetGmailTab =
          gmailTabs.find((t) => t.active) || gmailTabs[0];

        // Fecha abas extras duplicadas do Gmail para manter rigorosamente apenas UMA
        for (const extra of gmailTabs) {
          if (extra.id && extra.id !== targetGmailTab.id) {
            try {
              await chrome.tabs.remove(extra.id);
            } catch {
              // ignore
            }
          }
        }

        await chrome.storage.local.set({
          [JARVIS_TAB_KEY]: targetGmailTab.id,
          [JARVIS_WINDOW_KEY]: targetGmailTab.windowId,
        });

        if (targetGmailTab.url !== url) {
          await chrome.tabs.update(targetGmailTab.id, { url, active: true });
        } else {
          await chrome.tabs.update(targetGmailTab.id, { active: true });
        }
        await chrome.windows.update(targetGmailTab.windowId, { focused: true });
        return;
      }
    } catch {
      // fallback to standard behavior
    }
  }

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
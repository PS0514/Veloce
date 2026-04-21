const MANUAL_MENU_ID = "veloce-extract-task";
const DEFAULT_TIMEOUT_MS = 15000;
const NOTIFICATION_ICON_PATH = "Veloce_Logo.png";

const DEFAULT_SETTINGS = {
  auto_detect_mode: true,
  auto_read_permissions: true,
  manual_trigger_mode: true,
  user_id: "anonymous",
  webhook_url: "",
  ai_prompt: ""
};

function createManualContextMenu() {
  chrome.contextMenus.create(
    {
      id: MANUAL_MENU_ID,
      title: "Add to Calendar",
      contexts: ["selection"]
    },
    () => {
      // Ignore duplicate creation attempts when service worker restarts.
      if (chrome.runtime.lastError) {
        const message = chrome.runtime.lastError.message || "";
        if (!message.includes("duplicate id")) {
          console.debug("[Veloce] Context menu create warning:", message);
        }
      }
    }
  );
}

function removeManualContextMenu() {
  chrome.contextMenus.remove(MANUAL_MENU_ID, () => {
    // Ignore not-found removal attempts when menu is already absent.
    if (chrome.runtime.lastError) {
      const message = chrome.runtime.lastError.message || "";
      if (!message.includes("Cannot find menu item")) {
        console.debug("[Veloce] Context menu remove warning:", message);
      }
    }
  });
}

function syncManualContextMenu(isEnabled) {
  if (isEnabled === false) {
    removeManualContextMenu();
    return;
  }

  createManualContextMenu();
}

chrome.runtime.onInstalled.addListener(() => {
  createManualContextMenu();

  chrome.storage.local.set({
    ...DEFAULT_SETTINGS,
    workflowState: {
      isLoading: false,
      status: "idle",
      statusMessage: "",
      error: "",
      lastUpdated: new Date().toISOString()
    }
  });
});

chrome.storage.local.get(["manual_trigger_mode"], (state) => {
  syncManualContextMenu(state.manual_trigger_mode !== false);
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.manual_trigger_mode) {
    return;
  }

  syncManualContextMenu(changes.manual_trigger_mode.newValue !== false);
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MANUAL_MENU_ID || !tab?.id) {
    return;
  }

  const { manual_trigger_mode: manualTriggerMode } = await chrome.storage.local.get([
    "manual_trigger_mode"
  ]);
  if (manualTriggerMode === false) {
    notifyStatus("manual trigger disabled", "Enable Manual Trigger Mode in settings.");
    return;
  }

  chrome.tabs.sendMessage(tab.id, { type: "trigger-manual-extract" }, () => {
    if (chrome.runtime.lastError) {
      console.debug("[Veloce] Manual trigger message skipped:", chrome.runtime.lastError.message);
    }
  });
});

function persistPayload(payload) {
  chrome.storage.local.set({ lastPayload: payload });
}

async function persistTaskHistory(payload) {
  const { taskHistory } = await chrome.storage.local.get(["taskHistory"]);
  const nextHistory = [payload, ...(Array.isArray(taskHistory) ? taskHistory : [])].slice(0, 20);
  chrome.storage.local.set({ taskHistory: nextHistory });
}

function setWorkflowState(nextState) {
  chrome.storage.local.set({
    workflowState: {
      isLoading: Boolean(nextState.isLoading),
      status: nextState.status || "idle",
      statusMessage: nextState.statusMessage || "",
      error: nextState.error || "",
      lastUpdated: new Date().toISOString()
    }
  });
}

function notifyStatus(status, message) {
  const normalizedStatus = (status || "").toLowerCase();
  let title = "Veloce Update";

  if (normalizedStatus.includes("scheduled")) {
    title = "Event Scheduled";
  } else if (normalizedStatus.includes("conflict")) {
    title = "Conflict Detected";
  } else if (normalizedStatus.includes("error")) {
    title = "Processing Error";
  }

  chrome.notifications.create({
    type: "basic",
    iconUrl: chrome.runtime.getURL(NOTIFICATION_ICON_PATH),
    title,
    message: message || status || "Task processing update received."
  });
}

async function callWebhook(payload) {
  const { webhook_url: webhookUrl, ai_prompt: aiPrompt } = await chrome.storage.local.get([
    "webhook_url",
    "ai_prompt"
  ]);

  if (!webhookUrl) {
    setWorkflowState({
      isLoading: false,
      status: "error",
      error: "Webhook URL is not configured in settings."
    });
    return;
  }

  setWorkflowState({ isLoading: true, status: "processing", statusMessage: "", error: "" });

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetch(webhookUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        ...payload,
        prompt: aiPrompt || ""
      }),
      signal: controller.signal
    });

    if (!response.ok) {
      throw new Error(`Webhook failed with HTTP ${response.status}`);
    }

    const data = await response.json();
    chrome.storage.local.set({ aiResponse: data });
    setWorkflowState({
      isLoading: false,
      status: "success",
      statusMessage: data?.message || "Event created successfully.",
      error: ""
    });
    notifyStatus(data.status, data.message);
  } catch (error) {
    const friendlyError =
      error?.name === "AbortError"
        ? "System busy, please try again."
        : "System busy, please try again.";

    setWorkflowState({
      isLoading: false,
      status: "error",
      error: friendlyError
    });
    notifyStatus("error", friendlyError);
  } finally {
    clearTimeout(timeoutId);
  }
}

function processPayload(payload, sender, sendResponse) {
  persistPayload(payload);
  persistTaskHistory(payload);
  console.log("[Veloce] Payload received", {
    from: sender.tab?.url,
    payload
  });

  callWebhook(payload)
    .then(() => sendResponse({ ok: true }))
    .catch(() => sendResponse({ ok: false }));
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "extracted-payload" && message.payload) {
    processPayload(message.payload, sender, sendResponse);
    return true;
  }

  if (message?.type === "manual-selection" && message.payload) {
    processPayload(message.payload, sender, sendResponse);
    return true;
  }

  return false;
});

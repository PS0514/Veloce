const MANUAL_MENU_ID = "veloce-extract-task";
const DEFAULT_TIMEOUT_MS = 15000;
const NOTIFICATION_ICON_DATA_URI =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnR6JwAAAAASUVORK5CYII=";

const DEFAULT_SETTINGS = {
  auto_detect_mode: true,
  user_id: "anonymous",
  webhook_url: "",
  ai_prompt: ""
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MANUAL_MENU_ID,
    title: "Extract Task",
    contexts: ["selection"]
  });

  chrome.storage.local.set({
    ...DEFAULT_SETTINGS,
    workflowState: {
      isLoading: false,
      error: "",
      lastUpdated: new Date().toISOString()
    }
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MANUAL_MENU_ID || !tab?.id) {
    return;
  }

  chrome.tabs.sendMessage(tab.id, { type: "trigger-manual-extract" });
});

function persistPayload(payload) {
  chrome.storage.local.set({ lastPayload: payload });
}

function setWorkflowState(nextState) {
  chrome.storage.local.set({
    workflowState: {
      isLoading: Boolean(nextState.isLoading),
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
    iconUrl: NOTIFICATION_ICON_DATA_URI,
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
      error: "Webhook URL is not configured in settings."
    });
    return;
  }

  setWorkflowState({ isLoading: true, error: "" });

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
    setWorkflowState({ isLoading: false, error: "" });
    notifyStatus(data.status, data.message);
  } catch (error) {
    const friendlyError =
      error?.name === "AbortError"
        ? "System busy, please try again."
        : "System busy, please try again.";

    setWorkflowState({
      isLoading: false,
      error: friendlyError
    });
    notifyStatus("error", friendlyError);
  } finally {
    clearTimeout(timeoutId);
  }
}

function processPayload(payload, sender, sendResponse) {
  persistPayload(payload);
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

const MANUAL_MENU_ID = "veloce-extract-task";
const DEFAULT_TIMEOUT_MS = 1200000;
const NOTIFICATION_ICON_PATH = "Veloce_Logo.png";
const API_BASE_URL = "http://127.0.0.1:8000";
const TASK_INGEST_PATH = "/veloce-task-scheduler";
const MANUAL_CALENDAR_ADD_PATH = "/veloce-manual-calendar-add";
const ACCOUNT_PORTAL_URL = "http://127.0.0.1:8765/";
const ACCOUNT_STATUS_URL = "http://127.0.0.1:8765/auth/status";

const DEFAULT_SETTINGS = {
  manual_trigger_mode: true
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

async function callServerApi(payload, { directCalendarAdd = false } = {}) {
  const endpoint = `${API_BASE_URL}${directCalendarAdd ? MANUAL_CALENDAR_ADD_PATH : TASK_INGEST_PATH}`;
  const payloadText = typeof payload?.text === "string" ? payload.text : "";

  const body = directCalendarAdd
    ? {
        source: payload?.source || "manual_selection",
        message: payloadText,
        raw_text: payloadText,
        date: payload?.timestamp || new Date().toISOString()
      }
    : {
        ...payload,
        message: payloadText,
        raw_text: payloadText
      };

  setWorkflowState({ isLoading: true, status: "processing", statusMessage: "", error: "" });

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      credentials: "include",
      body: JSON.stringify(body),
      signal: controller.signal
    });

    if (!response.ok) {
      const errorBody = await response.text().catch(() => "");
      throw new Error(
        errorBody
          ? `API request failed with HTTP ${response.status}: ${errorBody}`
          : `API request failed with HTTP ${response.status}`
      );
    }

    const data = await response.json().catch(() => ({}));
    chrome.storage.local.set({ aiResponse: data });
    const statusMessage = data?.message || (directCalendarAdd ? "Added directly to calendar." : "Event created successfully.");
    const status = data?.status || (data?.scheduled ? "scheduled" : "success");

    setWorkflowState({
      isLoading: false,
      status: "success",
      statusMessage,
      error: ""
    });
    notifyStatus(status, statusMessage);
  } catch (error) {
    const friendlyError =
      error?.name === "AbortError"
        ? "Backend request timed out. Please try again."
        : error?.message || "System busy, please try again.";

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

async function getAccountStatus() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetch(ACCOUNT_STATUS_URL, {
      method: "GET",
      credentials: "include",
      signal: controller.signal
    });

    if (!response.ok) {
      return { ok: false, error: `Status check failed with HTTP ${response.status}` };
    }

    const status = await response.json();
    return { ok: true, status };
  } catch (error) {
    return {
      ok: false,
      error: error?.name === "AbortError" ? "Status check timed out." : "Unable to reach account server."
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function processPayload(payload, sender, sendResponse) {
  persistPayload(payload);
  console.log("[Veloce] Payload received", {
    from: sender.tab?.url,
    payload
  });

  const statusResult = await getAccountStatus();
  if (!statusResult.ok || !statusResult.status?.account_ready) {
    const setupError = statusResult.ok
      ? "Finish account setup (Google + Telegram) in Connect Account before using extraction."
      : statusResult.error || "Unable to verify account setup status.";

    setWorkflowState({
      isLoading: false,
      status: "error",
      error: setupError
    });
    notifyStatus("error", setupError);
    sendResponse({ ok: false });
    return;
  }

  const { manual_trigger_mode: manualTriggerMode } = await chrome.storage.local.get([
    "manual_trigger_mode"
  ]);
  const directCalendarAdd = manualTriggerMode !== false && (
    (payload && typeof payload.source === 'string' && payload.source.includes("-manual")) ||
    payload?.source === 'manual_selection' ||
    sender?.id === chrome.runtime.id
  );

  callServerApi(payload, { directCalendarAdd })
    .then(() => sendResponse({ ok: true }))
    .catch(() => sendResponse({ ok: false }));
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "get-account-status") {
    getAccountStatus().then(sendResponse);
    return true;
  }

  if (message?.type === "open-account-portal") {
    chrome.tabs.create({ url: ACCOUNT_PORTAL_URL }, () => {
      sendResponse({ ok: !chrome.runtime.lastError });
    });
    return true;
  }

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

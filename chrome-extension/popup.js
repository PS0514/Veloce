const els = {
  setupGate: document.getElementById("setupGate"),
  tabsContainer: document.getElementById("tabsContainer"),
  googleStatusChip: document.getElementById("googleStatusChip"),
  telegramStatusChip: document.getElementById("telegramStatusChip"),
  setupGateMessage: document.getElementById("setupGateMessage"),
  refreshStatusBtn: document.getElementById("refreshStatusBtn"),
  gateConnectAccountBtn: document.getElementById("gateConnectAccountBtn"),
  resultTabBtn: document.getElementById("resultTabBtn"),
  settingsTabBtn: document.getElementById("settingsTabBtn"),
  resultPanel: document.getElementById("resultPanel"),
  settingsPanel: document.getElementById("settingsPanel"),
  statusPill: document.getElementById("statusPill"),
  loadingState: document.getElementById("loadingState"),
  successState: document.getElementById("successState"),
  errorState: document.getElementById("errorState"),
  fieldTitle: document.getElementById("fieldTitle"),
  fieldDate: document.getElementById("fieldDate"),
  fieldTime: document.getElementById("fieldTime"),
  lastPayload: document.getElementById("lastPayload"),
  manualModeToggle: document.getElementById("manualModeToggle"),
  connectAccountBtn: document.getElementById("connectAccountBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  saveStatus: document.getElementById("saveStatus")
};

const DEFAULT_SETTINGS = {
  manual_trigger_mode: true
};

const DEFAULT_AUTH_STATUS = {
  google_connected: false,
  telegram_connected: false,
  account_ready: false,
  google_status: "Not connected",
  telegram_status: "Not connected"
};

let accountReady = false;

function truncate(text, max = 120) {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function setTab(name) {
  if (!accountReady) {
    return;
  }

  const isResult = name === "result";
  els.resultTabBtn.classList.toggle("active", isResult);
  els.settingsTabBtn.classList.toggle("active", !isResult);
  els.resultPanel.classList.toggle("active", isResult);
  els.settingsPanel.classList.toggle("active", !isResult);
}

function renderAuthChip(element, isConnected) {
  element.classList.toggle("status-chip-on", Boolean(isConnected));
  element.classList.toggle("status-chip-off", !isConnected);
  element.textContent = isConnected ? "Connected" : "Not Connected";
}

function applyGateState(status) {
  accountReady = Boolean(status?.account_ready);

  renderAuthChip(els.googleStatusChip, status?.google_connected);
  renderAuthChip(els.telegramStatusChip, status?.telegram_connected);

  const message = status?.account_ready
    ? "Account setup complete."
    : `${status?.google_status || "Google not connected."} ${status?.telegram_status || "Telegram not connected."}`;
  els.setupGateMessage.textContent = message;

  els.setupGate.style.display = accountReady ? "none" : "block";
  els.tabsContainer.style.display = accountReady ? "flex" : "none";
  els.connectAccountBtn.style.display = accountReady ? "none" : "block";
  if (!accountReady) {
    els.resultPanel.classList.remove("active");
    els.settingsPanel.classList.remove("active");
  } else {
    setTab("result");
  }
}

function requestAccountStatus() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "get-account-status" }, (response) => {
      if (chrome.runtime.lastError || !response) {
        resolve({
          ok: false,
          error: "Unable to contact account status service.",
          status: DEFAULT_AUTH_STATUS
        });
        return;
      }

      resolve(response);
    });
  });
}

async function refreshAccountStatus() {
  els.setupGateMessage.textContent = "Checking account status...";
  const response = await requestAccountStatus();

  if (!response.ok) {
    applyGateState({ ...DEFAULT_AUTH_STATUS, account_ready: false });
    els.setupGateMessage.textContent = response.error || "Unable to verify account status.";
    return;
  }

  applyGateState(response.status || DEFAULT_AUTH_STATUS);
}

function renderAiResponse(aiResponse) {
  const title = aiResponse?.title || "-";
  const date = aiResponse?.date || "-";
  const time = aiResponse?.time || "-";

  els.fieldTitle.textContent = title;
  els.fieldDate.textContent = date;
  els.fieldTime.textContent = time;
}

function renderPayload(payload) {
  if (!payload) {
    els.lastPayload.textContent = "No payload captured yet.";
    return;
  }

  const summary = `${payload.source} | ${new Date(payload.timestamp).toLocaleString()}`;
  els.lastPayload.textContent = `${summary} | ${truncate(payload.text)}`;
}

function renderWorkflowState(workflowState) {
  const isLoading = Boolean(workflowState?.isLoading);
  const hasError = Boolean(workflowState?.error);
  const isSuccess = workflowState?.status === "success";

  els.loadingState.style.display = isLoading ? "flex" : "none";
  els.errorState.style.display = hasError ? "block" : "none";
  els.errorState.textContent = workflowState?.error || "";
  els.successState.style.display = isSuccess && !isLoading ? "block" : "none";
  els.successState.textContent = workflowState?.statusMessage || "Event created.";

  if (isLoading) {
    els.statusPill.textContent = "Processing";
  } else if (hasError) {
    els.statusPill.textContent = "Error";
  } else if (isSuccess) {
    els.statusPill.textContent = "Success";
  } else {
    els.statusPill.textContent = "Ready";
  }
}

function renderSettings(state) {
  els.manualModeToggle.checked = state.manual_trigger_mode !== false;
}

function saveSettings() {
  const next = {
    manual_trigger_mode: els.manualModeToggle.checked
  };

  chrome.storage.local.set(next, () => {
    els.saveStatus.textContent = "Settings saved.";
  });
}

function resetAllSettings() {
  chrome.storage.local.set(DEFAULT_SETTINGS, () => {
    renderSettings(DEFAULT_SETTINGS);
    els.saveStatus.textContent = "Defaults restored.";
  });
}

function refresh() {
  chrome.storage.local.get(
    [
      "aiResponse",
      "lastPayload",
      "workflowState",
      "manual_trigger_mode"
    ],
    (state) => {
      renderAiResponse(state.aiResponse || {});
      renderPayload(state.lastPayload);
      renderWorkflowState(state.workflowState || {});
      renderSettings(state);
    }
  );
}

function connectAccount() {
  chrome.runtime.sendMessage({ type: "open-account-portal" }, (response) => {
    if (chrome.runtime.lastError || !response?.ok) {
      els.saveStatus.textContent = "Failed to open account portal.";
      return;
    }

    els.saveStatus.textContent = "Account portal opened in a new tab.";
    els.setupGateMessage.textContent = "Account portal opened. Complete setup, then return and click Refresh Status.";
  });
}

els.resultTabBtn.addEventListener("click", () => setTab("result"));
els.settingsTabBtn.addEventListener("click", () => setTab("settings"));
els.refreshStatusBtn.addEventListener("click", refreshAccountStatus);
els.gateConnectAccountBtn.addEventListener("click", connectAccount);
els.connectAccountBtn.addEventListener("click", connectAccount);
els.saveSettingsBtn.addEventListener("click", saveSettings);

window.addEventListener("focus", refreshAccountStatus);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshAccountStatus();
  }
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") {
    return;
  }

  if (
    changes.aiResponse ||
    changes.lastPayload ||
    changes.taskHistory ||
    changes.workflowState ||
    changes.manual_trigger_mode
  ) {
    refresh();
  }
});

refresh();
refreshAccountStatus();

const els = {
  resultTabBtn: document.getElementById("resultTabBtn"),
  settingsTabBtn: document.getElementById("settingsTabBtn"),
  resultPanel: document.getElementById("resultPanel"),
  settingsPanel: document.getElementById("settingsPanel"),
  statusPill: document.getElementById("statusPill"),
  loadingState: document.getElementById("loadingState"),
  errorState: document.getElementById("errorState"),
  fieldTitle: document.getElementById("fieldTitle"),
  fieldDate: document.getElementById("fieldDate"),
  fieldTime: document.getElementById("fieldTime"),
  fieldConfidence: document.getElementById("fieldConfidence"),
  fieldMessage: document.getElementById("fieldMessage"),
  clarificationWarning: document.getElementById("clarificationWarning"),
  lastPayload: document.getElementById("lastPayload"),
  autoDetectToggle: document.getElementById("autoDetectToggle"),
  userIdInput: document.getElementById("userIdInput"),
  webhookUrlInput: document.getElementById("webhookUrlInput"),
  promptInput: document.getElementById("promptInput"),
  resetSettingsBtn: document.getElementById("resetSettingsBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  saveStatus: document.getElementById("saveStatus")
};

const DEFAULT_SETTINGS = {
  auto_detect_mode: true,
  user_id: "anonymous",
  webhook_url: "",
  ai_prompt: ""
};

function truncate(text, max = 120) {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function setTab(name) {
  const isResult = name === "result";
  els.resultTabBtn.classList.toggle("active", isResult);
  els.settingsTabBtn.classList.toggle("active", !isResult);
  els.resultPanel.classList.toggle("active", isResult);
  els.settingsPanel.classList.toggle("active", !isResult);
}

function formatConfidence(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return { text: "Unknown", className: "confidence-mid", showWarning: false };
  }

  if (num > 0.9) {
    return { text: `${num.toFixed(2)} High`, className: "confidence-high", showWarning: false };
  }

  if (num < 0.6) {
    return { text: `${num.toFixed(2)} Low`, className: "confidence-low", showWarning: true };
  }

  if (num < 0.8) {
    return { text: `${num.toFixed(2)} Medium`, className: "confidence-mid", showWarning: false };
  }

  return { text: `${num.toFixed(2)} Medium+`, className: "confidence-mid", showWarning: false };
}

function renderAiResponse(aiResponse) {
  const title = aiResponse?.title || "-";
  const date = aiResponse?.date || "-";
  const time = aiResponse?.time || "-";
  const message = aiResponse?.message || "-";
  const confidence = formatConfidence(aiResponse?.confidence);

  els.fieldTitle.textContent = title;
  els.fieldDate.textContent = date;
  els.fieldTime.textContent = time;
  els.fieldMessage.textContent = message;

  els.fieldConfidence.className = `confidence-badge ${confidence.className}`;
  els.fieldConfidence.textContent = confidence.text;
  els.clarificationWarning.style.display = confidence.showWarning ? "block" : "none";
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

  els.loadingState.style.display = isLoading ? "flex" : "none";
  els.errorState.style.display = hasError ? "block" : "none";
  els.errorState.textContent = workflowState?.error || "";

  if (isLoading) {
    els.statusPill.textContent = "Processing";
  } else if (hasError) {
    els.statusPill.textContent = "Error";
  } else {
    els.statusPill.textContent = "Ready";
  }
}

function renderSettings(state) {
  els.autoDetectToggle.checked = state.auto_detect_mode !== false;
  els.userIdInput.value = state.user_id || "anonymous";
  els.webhookUrlInput.value = state.webhook_url || "";
  els.promptInput.value = state.ai_prompt || "";
}

function saveSettings() {
  const next = {
    auto_detect_mode: els.autoDetectToggle.checked,
    user_id: (els.userIdInput.value || "anonymous").trim(),
    webhook_url: (els.webhookUrlInput.value || "").trim(),
    ai_prompt: (els.promptInput.value || "").trim()
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
    ["aiResponse", "lastPayload", "workflowState", "auto_detect_mode", "user_id", "webhook_url", "ai_prompt"],
    (state) => {
      renderAiResponse(state.aiResponse || {});
      renderPayload(state.lastPayload);
      renderWorkflowState(state.workflowState || {});
      renderSettings(state);
    }
  );
}

els.resultTabBtn.addEventListener("click", () => setTab("result"));
els.settingsTabBtn.addEventListener("click", () => setTab("settings"));
els.resetSettingsBtn.addEventListener("click", resetAllSettings);
els.saveSettingsBtn.addEventListener("click", saveSettings);

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") {
    return;
  }

  if (changes.aiResponse || changes.lastPayload || changes.workflowState) {
    refresh();
  }
});

refresh();

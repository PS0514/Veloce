const els = {
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
  fieldConfidence: document.getElementById("fieldConfidence"),
  fieldMessage: document.getElementById("fieldMessage"),
  taskList: document.getElementById("taskList"),
  glmJson: document.getElementById("glmJson"),
  clarificationWarning: document.getElementById("clarificationWarning"),
  lastPayload: document.getElementById("lastPayload"),
  autoDetectToggle: document.getElementById("autoDetectToggle"),
  autoReadToggle: document.getElementById("autoReadToggle"),
  manualModeToggle: document.getElementById("manualModeToggle"),
  userIdInput: document.getElementById("userIdInput"),
  webhookUrlInput: document.getElementById("webhookUrlInput"),
  promptInput: document.getElementById("promptInput"),
  resetSettingsBtn: document.getElementById("resetSettingsBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  saveStatus: document.getElementById("saveStatus")
};

const DEFAULT_SETTINGS = {
  auto_detect_mode: true,
  auto_read_permissions: true,
  manual_trigger_mode: true,
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

function renderTaskHistory(history) {
  if (!Array.isArray(history) || history.length === 0) {
    els.taskList.textContent = "No extracted tasks yet.";
    return;
  }

  const lines = history.slice(0, 8).map((item) => {
    const when = item?.timestamp ? new Date(item.timestamp).toLocaleString() : "Unknown time";
    const source = item?.source || "unknown";
    const text = truncate(item?.text || "", 180) || "(empty)";
    return `- [${when}] ${source}: ${text}`;
  });

  els.taskList.textContent = lines.join("\n");
}

function renderGlmJson(aiResponse) {
  if (!aiResponse || typeof aiResponse !== "object" || Object.keys(aiResponse).length === 0) {
    els.glmJson.textContent = "No GLM response yet.";
    return;
  }

  els.glmJson.textContent = JSON.stringify(aiResponse, null, 2);
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
  els.autoDetectToggle.checked = state.auto_detect_mode !== false;
  els.autoReadToggle.checked = state.auto_read_permissions !== false;
  els.manualModeToggle.checked = state.manual_trigger_mode !== false;
  els.userIdInput.value = state.user_id || "anonymous";
  els.webhookUrlInput.value = state.webhook_url || "";
  els.promptInput.value = state.ai_prompt || "";
}

function saveSettings() {
  const next = {
    auto_detect_mode: els.autoDetectToggle.checked,
    auto_read_permissions: els.autoReadToggle.checked,
    manual_trigger_mode: els.manualModeToggle.checked,
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
    [
      "aiResponse",
      "lastPayload",
      "taskHistory",
      "workflowState",
      "auto_detect_mode",
      "auto_read_permissions",
      "manual_trigger_mode",
      "user_id",
      "webhook_url",
      "ai_prompt"
    ],
    (state) => {
      renderAiResponse(state.aiResponse || {});
      renderGlmJson(state.aiResponse || {});
      renderPayload(state.lastPayload);
      renderTaskHistory(state.taskHistory || []);
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

  if (
    changes.aiResponse ||
    changes.lastPayload ||
    changes.taskHistory ||
    changes.workflowState ||
    changes.auto_detect_mode ||
    changes.auto_read_permissions ||
    changes.manual_trigger_mode
  ) {
    refresh();
  }
});

refresh();

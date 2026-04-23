const seenTelegramMessages = new Set();
const seenAutoPayloads = new Map();
let floatingButton = null;
let gmailAutoTimer = null;
let moodleAutoTimer = null;
let gmailAutoIntervalId = null;
let moodleAutoIntervalId = null;
let gmailObserverStarted = false;
let moodleObserverStarted = false;
let telegramObserverStarted = false;
let autoBootstrapIntervalId = null;
const AUTO_DEDUPE_WINDOW_MS = 30000;

function canSendAutoPayload(signature) {
  const now = Date.now();
  const lastSentAt = seenAutoPayloads.get(signature) || 0;

  if (now - lastSentAt < AUTO_DEDUPE_WINDOW_MS) {
    return false;
  }

  seenAutoPayloads.set(signature, now);

  // Keep map bounded to avoid unbounded growth in long Gmail sessions.
  if (seenAutoPayloads.size > 100) {
    let oldestKey = null;
    let oldestTs = Number.POSITIVE_INFINITY;
    for (const [key, ts] of seenAutoPayloads.entries()) {
      if (ts < oldestTs) {
        oldestTs = ts;
        oldestKey = key;
      }
    }
    if (oldestKey) {
      seenAutoPayloads.delete(oldestKey);
    }
  }

  return true;
}

function isExtensionContextActive() {
  return typeof chrome !== "undefined" && Boolean(chrome.runtime?.id);
}

function getLocalState(keys, fallback = {}) {
  return new Promise((resolve) => {
    if (!isExtensionContextActive()) {
      resolve(fallback);
      return;
    }

    try {
      chrome.storage.local.get(keys, (result) => {
        if (chrome.runtime.lastError) {
          console.debug("[Veloce] Storage read skipped:", chrome.runtime.lastError.message);
          resolve(fallback);
          return;
        }

        resolve(result || fallback);
      });
    } catch (error) {
      console.debug("[Veloce] Storage read failed:", error?.message || error);
      resolve(fallback);
    }
  });
}

function isManualTriggerEnabled() {
  return getLocalState(["manual_trigger_mode"], { manual_trigger_mode: false }).then(
    (result) => result.manual_trigger_mode !== false
  );
}

function detectSource() {
  const host = window.location.hostname;

  if (host.includes("mail.google.com")) {
    return "gmail";
  }

  if (host.includes("telegram.org")) {
    return "telegram";
  }

  if (host.includes("moodle") || host.includes("spectrum.um.edu.my")) {
    return "moodle";
  }

  return "unknown";
}

async function makePayload(source, text) {
  return {
    source,
    text,
    page_url: window.location.href,
    timestamp: new Date().toISOString()
  };
}

function relayPayload(type, payload) {
  if (!isExtensionContextActive()) {
    return;
  }

  try {
    chrome.runtime.sendMessage({ type, payload }, () => {
      if (chrome.runtime.lastError) {
        console.debug("[Veloce] Background relay unavailable", chrome.runtime.lastError.message);
      }
    });
  } catch (error) {
    console.debug("[Veloce] Background relay failed:", error?.message || error);
  }
}

function uniqueNonEmptyText(nodes) {
  const values = nodes
    .map((node) => node?.innerText?.trim() || "")
    .filter(Boolean);

  return [...new Set(values)].join("\n\n");
}

function extractGmailText() {
  const selectors = [
    "div.a3s.aiL",
    "div[data-message-id] div.a3s",
    "div[role='listitem'] div.a3s",
    "div.adn.ads div.a3s",
    "div.ii.gt div.a3s",
    "div[role='main'] div[data-message-id]"
  ];

  const chunks = [];
  for (const selector of selectors) {
    const nodes = Array.from(document.querySelectorAll(selector));
    const text = uniqueNonEmptyText(nodes);
    if (text) {
      chunks.push(text);
    }
  }

  const primary = [...new Set(chunks)].join("\n\n").trim();
  if (primary) {
    return primary;
  }

  // Fallback: capture currently opened mail panel text when Gmail changes internal classes.
  const mainPanel = document.querySelector("div[role='main']");
  const fallback = mainPanel?.innerText?.trim() || "";
  if (!fallback) {
    const inboxRows = Array.from(document.querySelectorAll("tr.zA")).slice(0, 6);
    const inboxLines = inboxRows
      .map((row) => {
        const sender = row.querySelector(".yX.xY span")?.innerText?.trim() || "";
        const subject = row.querySelector(".bog")?.innerText?.trim() || "";
        const snippet = row.querySelector(".y2")?.innerText?.trim() || "";
        return [sender, subject, snippet].filter(Boolean).join(" | ");
      })
      .filter(Boolean);

    return [...new Set(inboxLines)].join("\n").trim();
  }

  // Avoid sending trivial UI noise while still allowing short but valid email content.
  return fallback.length >= 40 ? fallback : "";
}

function extractMoodleText() {
  const mainRoot =
    document.querySelector("#region-main") ||
    document.querySelector("main[role='main']") ||
    document.querySelector("#page-content") ||
    document;

  const lowValuePhrases = new Set([
    "section outline",
    "course index",
    "dashboard",
    "participants",
    "badges",
    "grades"
  ]);

  function isLowValueMoodleText(text) {
    const normalized = (text || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!normalized) {
      return true;
    }

    if (lowValuePhrases.has(normalized)) {
      return true;
    }

    if (normalized.length < 40) {
      return true;
    }

    const lines = (text || "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    return lines.length <= 2 && normalized.length < 80;
  }

  function extractMoodleActivitySummary(root) {
    const activityRows = Array.from(
      root.querySelectorAll("li.activity, .activity-item, .activityinstance, li[id^='module-']")
    );

    if (!activityRows.length) {
      return "";
    }

    const lines = activityRows
      .map((row) => {
        const name =
          row.querySelector(".instancename")?.innerText?.trim() ||
          row.querySelector(".activityname")?.innerText?.trim() ||
          row.querySelector("a")?.innerText?.trim() ||
          "";
        const meta = [
          row.querySelector(".description")?.innerText?.trim() || "",
          row.querySelector(".availabilityinfo")?.innerText?.trim() || "",
          row.querySelector(".due, .date")?.innerText?.trim() || ""
        ]
          .filter(Boolean)
          .join(" | ");

        if (!name) {
          return "";
        }

        return meta ? `${name} | ${meta}` : name;
      })
      .filter(Boolean);

    return [...new Set(lines)].slice(0, 30).join("\n").trim();
  }

  const selectors = [
    ".assignment .intro",
    ".assignintro",
    ".forum-post-container .post-content",
    ".announcement .content",
    ".activity-description",
    ".mod_introbox",
    ".box.generalbox",
    ".description .no-overflow",
    ".quizinfo",
    ".submissionstatustable"
  ];

  const chunks = [];
  const pageHeading =
    mainRoot.querySelector("h1")?.innerText?.trim() ||
    mainRoot.querySelector("h2")?.innerText?.trim() ||
    "";

  if (pageHeading) {
    chunks.push(pageHeading);
  }

  for (const selector of selectors) {
    const nodes = Array.from(mainRoot.querySelectorAll(selector));
    const text = uniqueNonEmptyText(nodes);
    if (text) {
      chunks.push(text);
    }
  }

  const primary = [...new Set(chunks)].join("\n\n").trim();
  if (primary && !isLowValueMoodleText(primary)) {
    return primary.slice(0, 5000);
  }

  const activitySummary = extractMoodleActivitySummary(mainRoot);
  if (activitySummary && !isLowValueMoodleText(activitySummary)) {
    return activitySummary.slice(0, 5000);
  }

  const fallback = (mainRoot.innerText || "").trim();
  if (!fallback || isLowValueMoodleText(fallback)) {
    return "";
  }

  // Keep fallback bounded and avoid extracting very short UI-only fragments.
  return fallback.length >= 80 ? fallback.slice(0, 5000) : "";
}

function getTelegramMessageTexts(root = document) {
  const selectors = [
    ".message .text-content",
    ".Message .text-content",
    ".message-list-item .text-content",
    ".message .translatable-message",
    ".Message .translatable-message",
    "[data-mid] .text-content",
    ".bubbles .text-content"
  ];

  const values = [];
  for (const selector of selectors) {
    const nodes = root.querySelectorAll(selector);
    nodes.forEach((node) => {
      const text = node?.innerText?.trim();
      if (text) {
        values.push(text);
      }
    });
  }

  if (!values.length) {
    // Fallback for Telegram UI variants where message text is nested under generic bubble content blocks.
    const fallbackNodes = root.querySelectorAll("[data-mid], .message, .Message");
    fallbackNodes.forEach((node) => {
      const text = node?.innerText?.trim();
      if (text && text.length > 1) {
        values.push(text);
      }
    });
  }

  return [...new Set(values)];
}

async function sendAutoExtractionIfAvailable() {
  const manualTriggerEnabled = await isManualTriggerEnabled();
  if (manualTriggerEnabled) {
    return;
  }

  const source = detectSource();
  const locationKey = window.location.href.split("#")[0] + window.location.hash;
  if (source === "gmail") {
    const text = extractGmailText();
    if (text) {
      const signature = `gmail:${locationKey}:${text.slice(0, 500)}`;
      if (!canSendAutoPayload(signature)) {
        return;
      }
      const payload = await makePayload("gmail", text);
      relayPayload("extracted-payload", payload);
    }
  }

  if (source === "moodle") {
    const text = extractMoodleText();
    if (text) {
      const signature = `moodle:${locationKey}:${text.slice(0, 500)}`;
      if (!canSendAutoPayload(signature)) {
        return;
      }
      const payload = await makePayload("moodle", text);
      relayPayload("extracted-payload", payload);
    }
  }
}

function debounceAutoScan(source) {
  const delayMs = 800;

  if (source === "gmail") {
    if (gmailAutoTimer) {
      clearTimeout(gmailAutoTimer);
    }
    gmailAutoTimer = setTimeout(() => {
      sendAutoExtractionIfAvailable();
    }, delayMs);
    return;
  }

  if (source === "moodle") {
    if (moodleAutoTimer) {
      clearTimeout(moodleAutoTimer);
    }
    moodleAutoTimer = setTimeout(() => {
      sendAutoExtractionIfAvailable();
    }, delayMs);
  }
}

function startGmailObserver() {
  if (detectSource() !== "gmail" || gmailObserverStarted) {
    return;
  }
  gmailObserverStarted = true;

  debounceAutoScan("gmail");

  const observer = new MutationObserver(() => {
    debounceAutoScan("gmail");
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true
  });

  window.addEventListener("hashchange", () => {
    debounceAutoScan("gmail");
  });

  if (!gmailAutoIntervalId) {
    gmailAutoIntervalId = setInterval(() => {
      debounceAutoScan("gmail");
    }, 5000);
  }
}

function startMoodleObserver() {
  if (detectSource() !== "moodle" || moodleObserverStarted) {
    return;
  }
  moodleObserverStarted = true;

  debounceAutoScan("moodle");

  const observer = new MutationObserver(() => {
    debounceAutoScan("moodle");
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true
  });

  window.addEventListener("hashchange", () => {
    debounceAutoScan("moodle");
  });

  window.addEventListener("popstate", () => {
    debounceAutoScan("moodle");
  });

  if (!moodleAutoIntervalId) {
    moodleAutoIntervalId = setInterval(() => {
      debounceAutoScan("moodle");
    }, 7000);
  }
}

function findTelegramChatContainer() {
  const selectors = [
    "#MiddleColumn",
    "#column-center",
    "#column-middle",
    ".chat",
    ".chat-main",
    ".middle-column",
    ".messages-container",
    ".bubbles"
  ];

  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (el) {
      return el;
    }
  }

  return document.body;
}

async function pushNewTelegramMessages() {
  const manualTriggerEnabled = await isManualTriggerEnabled();
  if (manualTriggerEnabled) {
    return;
  }

  const root = findTelegramChatContainer();
  const messages = getTelegramMessageTexts(root);
  const fresh = messages.filter((text) => !seenTelegramMessages.has(text));

  if (!fresh.length) {
    return;
  }

  fresh.forEach((text) => seenTelegramMessages.add(text));
  const payload = await makePayload("telegram", fresh.join("\n"));
  relayPayload("extracted-payload", payload);
}

function startTelegramObserver() {
  if (detectSource() !== "telegram" || telegramObserverStarted) {
    return;
  }
  telegramObserverStarted = true;

  pushNewTelegramMessages();
  const target = findTelegramChatContainer();

  const observer = new MutationObserver(() => {
    pushNewTelegramMessages();
  });

  observer.observe(target, {
    childList: true,
    subtree: true
  });
}

function getSelectedText() {
  return window.getSelection()?.toString()?.trim() || "";
}

function ensureFloatingButton() {
  if (floatingButton) {
    return floatingButton;
  }

  floatingButton = document.createElement("button");
  floatingButton.type = "button";
  floatingButton.textContent = "Add to Calendar";
  floatingButton.style.position = "fixed";
  floatingButton.style.zIndex = "2147483647";
  floatingButton.style.display = "none";
  floatingButton.style.padding = "6px 10px";
  floatingButton.style.fontSize = "12px";
  floatingButton.style.fontWeight = "700";
  floatingButton.style.border = "none";
  floatingButton.style.borderRadius = "8px";
  floatingButton.style.background = "#111827";
  floatingButton.style.color = "#ffffff";
  floatingButton.style.boxShadow = "0 8px 20px rgba(0,0,0,0.2)";
  floatingButton.style.cursor = "pointer";

  floatingButton.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();

    const manualTriggerEnabled = await isManualTriggerEnabled();
    if (!manualTriggerEnabled) {
      floatingButton.style.display = "none";
      return;
    }

    const selected = getSelectedText();
    if (!selected) {
      floatingButton.style.display = "none";
      return;
    }

    const source = `${detectSource()}-manual`;
    const payload = await makePayload(source, selected);
    relayPayload("manual-selection", payload);
    floatingButton.style.display = "none";
  });

  document.documentElement.appendChild(floatingButton);
  return floatingButton;
}

function positionFloatingButton() {
  isManualTriggerEnabled().then((manualTriggerEnabled) => {
    if (!manualTriggerEnabled) {
      if (floatingButton) {
        floatingButton.style.display = "none";
      }
      return;
    }

  const selected = getSelectedText();
  const button = ensureFloatingButton();

  if (!selected) {
    button.style.display = "none";
    return;
  }

  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    button.style.display = "none";
    return;
  }

  const rect = selection.getRangeAt(0).getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) {
    button.style.display = "none";
    return;
  }

  button.style.top = `${Math.max(8, rect.top - 38)}px`;
  button.style.left = `${Math.max(8, rect.left)}px`;
  button.style.display = "block";
  });
}

async function triggerManualExtraction() {
  const manualTriggerEnabled = await isManualTriggerEnabled();
  if (!manualTriggerEnabled) {
    return;
  }

  const selected = getSelectedText();
  if (!selected) {
    return;
  }

  const source = `${detectSource()}-manual`;
  const payload = await makePayload(source, selected);
  relayPayload("manual-selection", payload);
}

if (isExtensionContextActive()) {
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "trigger-manual-extract") {
      triggerManualExtraction().then(() => sendResponse({ ok: true }));
      return true;
    }

    return false;
  });
}

document.addEventListener("mouseup", () => {
  setTimeout(positionFloatingButton, 0);
});

document.addEventListener("scroll", () => {
  if (floatingButton && floatingButton.style.display === "block") {
    positionFloatingButton();
  }
});

async function startAutoDetection() {
  sendAutoExtractionIfAvailable();
  startGmailObserver();
  startMoodleObserver();
  startTelegramObserver();
}

startAutoDetection();

if (!autoBootstrapIntervalId) {
  autoBootstrapIntervalId = setInterval(() => {
    startAutoDetection();
  }, 4000);
}

window.addEventListener("focus", () => {
  startAutoDetection();
});

if (isExtensionContextActive()) {
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") {
      return;
    }

    if (changes.manual_trigger_mode) {
      startAutoDetection();
    }
  });
}

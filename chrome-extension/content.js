const seenTelegramMessages = new Set();
let floatingButton = null;

function isAutoDetectEnabled() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["auto_detect_mode"], (result) => {
      resolve(result.auto_detect_mode !== false);
    });
  });
}

function detectSource() {
  const host = window.location.hostname;

  if (host.includes("mail.google.com")) {
    return "gmail";
  }

  if (host.includes("telegram.org")) {
    return "telegram";
  }

  if (host.includes("moodle")) {
    return "moodle";
  }

  return "unknown";
}

function getStoredUserId() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["user_id"], (result) => {
      resolve(result.user_id || "anonymous");
    });
  });
}

async function makePayload(source, text) {
  const userId = await getStoredUserId();
  return {
    source,
    text,
    user_id: userId,
    timestamp: new Date().toISOString()
  };
}

function relayPayload(type, payload) {
  chrome.runtime.sendMessage({ type, payload }, () => {
    if (chrome.runtime.lastError) {
      console.debug("[Veloce] Background relay unavailable", chrome.runtime.lastError.message);
    }
  });
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
    "div[role='listitem'] div.a3s"
  ];

  const chunks = [];
  for (const selector of selectors) {
    const nodes = Array.from(document.querySelectorAll(selector));
    const text = uniqueNonEmptyText(nodes);
    if (text) {
      chunks.push(text);
    }
  }

  return [...new Set(chunks)].join("\n\n").trim();
}

function extractMoodleText() {
  const selectors = [
    ".assignment .intro",
    ".assignintro",
    ".forum-post-container .post-content",
    ".announcement .content",
    ".activity-description"
  ];

  const chunks = [];
  for (const selector of selectors) {
    const nodes = Array.from(document.querySelectorAll(selector));
    const text = uniqueNonEmptyText(nodes);
    if (text) {
      chunks.push(text);
    }
  }

  return [...new Set(chunks)].join("\n\n").trim();
}

function getTelegramMessageTexts() {
  const selectors = [
    ".message .text-content",
    ".message .text-content span",
    "div.message .text-content",
    "div.bubbles-group .text-content"
  ];

  const values = [];
  for (const selector of selectors) {
    const nodes = document.querySelectorAll(selector);
    nodes.forEach((node) => {
      const text = node?.innerText?.trim();
      if (text) {
        values.push(text);
      }
    });
  }

  return [...new Set(values)];
}

async function sendAutoExtractionIfAvailable() {
  const source = detectSource();
  if (source === "gmail") {
    const text = extractGmailText();
    if (text) {
      const payload = await makePayload("gmail", text);
      relayPayload("extracted-payload", payload);
    }
  }

  if (source === "moodle") {
    const text = extractMoodleText();
    if (text) {
      const payload = await makePayload("moodle", text);
      relayPayload("extracted-payload", payload);
    }
  }
}

function findTelegramChatContainer() {
  const selectors = [
    "#MiddleColumn",
    ".chat",
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
  const messages = getTelegramMessageTexts();
  const fresh = messages.filter((text) => !seenTelegramMessages.has(text));

  if (!fresh.length) {
    return;
  }

  fresh.forEach((text) => seenTelegramMessages.add(text));
  const payload = await makePayload("telegram", fresh.join("\n"));
  relayPayload("extracted-payload", payload);
}

function startTelegramObserver() {
  if (detectSource() !== "telegram") {
    return;
  }

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
  floatingButton.textContent = "Extract Task";
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
}

async function triggerManualExtraction() {
  const selected = getSelectedText();
  if (!selected) {
    return;
  }

  const source = `${detectSource()}-manual`;
  const payload = await makePayload(source, selected);
  relayPayload("manual-selection", payload);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "trigger-manual-extract") {
    triggerManualExtraction().then(() => sendResponse({ ok: true }));
    return true;
  }

  return false;
});

document.addEventListener("mouseup", () => {
  setTimeout(positionFloatingButton, 0);
});

document.addEventListener("scroll", () => {
  if (floatingButton && floatingButton.style.display === "block") {
    positionFloatingButton();
  }
});

async function startAutoDetection() {
  const autoDetectEnabled = await isAutoDetectEnabled();
  if (!autoDetectEnabled) {
    return;
  }

  sendAutoExtractionIfAvailable();
  startTelegramObserver();
}

startAutoDetection();

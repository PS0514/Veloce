# Veloce Chrome Extension (Manifest V3)

## Included Files
- manifest.json (MV3)
- popup.html
- popup.css
- popup.js
- background.js (service worker)
- content.js

## Permissions
- activeTab
- scripting
- storage
- contextMenus
- notifications

## Host Permissions
- https://mail.google.com/*
- https://web.telegram.org/*
- https://*.moodlecloud.com/*
- https://moodle.your-university.edu/*

Update the Moodle host pattern to your institution domain if needed.

## Load in Chrome
1. Open chrome://extensions
2. Enable Developer mode
3. Click Load unpacked
4. Select this folder (`chrome-extension`)

## Day 2 + Day 3 Behaviors
- Gmail: tries common Gmail body selectors and sends extracted text payloads.
- Telegram Web: uses MutationObserver to detect new messages in active chat.
- Moodle: extracts assignment/announcement description blocks.
- Manual trigger (Add to Calendar flow):
  - Right-click selected text -> Add to Calendar
  - Floating "Add to Calendar" button appears near highlighted text
  - Selected text is sent to the GLM webhook pipeline

## Day 4 Behaviors
- Popup includes a schema-mapped AI card with fields:
  - title
  - date
  - time
  - confidence
  - message
- Popup also shows:
  - extracted task history (latest captured payloads)
  - raw GLM output JSON view
- Confidence badge states:
  - Green when confidence > 0.90
  - Yellow when confidence < 0.80
  - Red with "Needs Clarification" when confidence is very low (< 0.60)

## Day 5 Settings
- Settings tab in popup with:
  - Auto-Detect Mode toggle (master switch)
  - Auto-Read Permissions toggle
  - Manual Trigger Mode toggle
  - User ID
  - n8n Webhook URL
- Settings are persisted in chrome.storage.local.
- content.js checks Auto-Detect Mode and Auto-Read Permissions before any auto scraping starts.
- Manual trigger actions respect Manual Trigger Mode.

## Day 6 Integration
- background.js POSTs extracted payloads to the configured n8n webhook URL.
- Response JSON is stored as aiResponse and rendered by popup.
- Native notifications are shown based on response status.

## Day 7 UX
- Popup shows animated loading spinner while webhook processing is in progress.
- Popup shows explicit success state when event creation succeeds.
- Timeout/network failures show friendly error: "System busy, please try again."

# Veloce Chrome Extension

The Veloce Chrome Extension is a companion tool that allows you to capture tasks and deadlines directly from your browser.

## Features

- **Context Menu Extraction:** Highlight any text on a webpage, right-click, and select "Add to Calendar" to send it to Veloce.
- **Auto-Extraction:** Automatically detects potential tasks on supported platforms (Gmail, Telegram Web, Moodle).
- **Status Notifications:** Provides real-time feedback on whether an event was successfully scheduled or if a conflict was detected.
- **Account Integration:** Quickly access the Veloce Setup Wizard to manage your Google and Telegram connections.

## Supported Sites

The extension is pre-configured to work seamlessly with:
- **Gmail:** `https://mail.google.com/*`
- **Telegram Web:** `https://web.telegram.org/*`
- **Moodle / Learning Management Systems:**
  - `https://spectrum.um.edu.my/*`
  - `https://*.moodlecloud.com/*`
  - Custom Moodle domains.

## How it Works

1. **Manual Selection:** When you highlight text and use the context menu, the extension sends the text to the Orchestrator's `/veloce-manual-calendar-add` endpoint. This bypasses the complex multi-agent pipeline and attempts to directly schedule the event after a quick AI extraction of the time and name.
2. **Auto-Extraction:** On supported sites, the extension scans the DOM for task-like patterns and sends them to the `/veloce-task-scheduler` for full pipeline processing.

## Setup

1. **Installation:**
   - Open Chrome and navigate to `chrome://extensions/`.
   - Enable "Developer mode" in the top right.
   - Click "Load unpacked" and select the `chrome-extension/` folder in the Veloce project directory.
2. **Configuration:**
   - Click the Veloce icon in your extensions bar.
   - Ensure "Manual Trigger Mode" is enabled.
   - Use the "Connect Account" button to ensure your backend is configured and ready.

## Connection Settings

The extension expects the Veloce Orchestrator to be running locally:
- **Orchestrator API:** `http://127.0.0.1:8000`
- **Setup Wizard:** `http://127.0.0.1:8765`

import requests
import json
import sys
from datetime import datetime, timezone

def test_glm_service():
    print("Testing GLM Service...")
    try:
        resp = requests.get("http://localhost:8001/health")
        print(f"Health check: {resp.status_code} - {resp.json()}")
    except Exception as e:
        print(f"GLM Service health check failed: {e}")

def test_calendar_service():
    print("\nTesting Calendar Service...")
    try:
        resp = requests.get("http://localhost:8002/health")
        print(f"Health check: {resp.status_code} - {resp.json()}")
    except Exception as e:
        print(f"Calendar Service health check failed: {e}")

def test_telegram_service():
    print("\nTesting Telegram Service...")
    try:
        resp = requests.get("http://localhost:8003/health")
        print(f"Health check: {resp.status_code} - {resp.json()}")
        
        # Test sending a sample notification if requested
        if "--notify" in sys.argv:
            print("Sending sample notification (Userbot)...")
            payload = {"text": "🚀 Veloce Userbot Test Notification", "use_bot": False}
            resp = requests.post("http://localhost:8003/send-notification", json=payload)
            print(f"Notification result: {resp.status_code} - {resp.json()}")

            print("\nSending sample notification (Telegram Bot)...")
            payload = {"text": "🤖 Veloce BotFather Test Notification", "use_bot": True}
            resp = requests.post("http://localhost:8003/send-notification", json=payload)
            print(f"Notification result: {resp.status_code} - {resp.json()}")
            
    except Exception as e:
        print(f"Telegram Service health check failed: {e}")

if __name__ == "__main__":
    print("=== Veloce Microservices Integration Test ===\n")
    print("Note: Services must be running for these tests to pass.")
    print("GLM: port 8001, Calendar: port 8002, Telegram: port 8003\n")
    
    test_glm_service()
    test_calendar_service()
    test_telegram_service()

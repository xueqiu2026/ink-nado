
import requests
import time
import json

def trigger_bot():
    url = "http://127.0.0.1:8000/start"
    payload = {
        "ticker": "ETH-PERP",
        "quantity": 0.05,
        "spread": 0.0005,
        "interval": 2,
        "boost_mode": False
    }
    try:
        print(f"Sending start request to {url}...")
        resp = requests.post(url, json=payload, timeout=10)
        print(f"Response ({resp.status_code}): {resp.json()}")
    except Exception as e:
        print(f"Failed to connect to API: {e}")

if __name__ == "__main__":
    trigger_bot()

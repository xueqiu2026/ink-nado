import requests
import json

url = "http://127.0.0.1:8000/start"
payload = {
    "ticker": "ETH-PERP",
    "quantity": 0.01,
    "interval": 2,
    "spread": 0.0005,
    "boost_mode": True,
    "max_exposure": 200.0
}

try:
    response = requests.post(url, json=payload)
    print(f"Status: {response.status_code}")
    print(response.json())
except Exception as e:
    print(f"Error: {e}")

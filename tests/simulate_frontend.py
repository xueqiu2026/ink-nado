import requests
import time
import sys

BASE_URL = "http://127.0.0.1:8000"

def log(msg, status="INFO"):
    print(f"[{status}] {msg}")

def fail(msg):
    log(msg, "FAIL")
    sys.exit(1)

def audit_products():
    log("Auditing /products Endpoint...")
    try:
        res = requests.get(f"{BASE_URL}/products")
        data = res.json()
        
        if "products" not in data:
            fail("Missing 'products' key in response")
            
        products = data["products"]
        log(f"Found {len(products)} products.")
        
        if len(products) == 0:
            fail("Product list is empty")
            
        # Check Interface: symbol (str), id (int), min_size (str)
        p = products[0]
        if not isinstance(p.get("symbol"), str): fail(f"Invalid symbol type: {type(p.get('symbol'))}")
        if not isinstance(p.get("id"), int): fail(f"Invalid id type: {type(p.get('id'))}")
        # min_size might be missing or optional in backend, let's check
        if "min_size" in p:
             log("Product Interface OK (includes min_size)")
        else:
             log("WARNING: min_size missing from Product interface")
             
        eth = next((x for x in products if "ETH" in x["symbol"]), None)
        if not eth:
             fail("ETH-PERP not found in products!")
        log(f"ETH-PERP confirmed: ID={eth['id']}")
        return eth
        
    except Exception as e:
        fail(f"Product Audit Failed: {e}")

def audit_stats():
    log("Auditing /stats Endpoint...")
    try:
        res = requests.get(f"{BASE_URL}/stats")
        data = res.json()
        
        # Interface: pnl, equity, health, liq_price, active_pos, volume_rate_min
        required = ["pnl", "equity", "health", "liq_price", "active_pos", "volume_rate_min"]
        missing = [k for k in required if k not in data]
        
        if missing:
            fail(f"Missing Keys in Stats: {missing}")
            
        log("Stats Interface OK.")
        log(f"Current Equity: {data['equity']}")
        log(f"Health: {data['health']}")
        
    except Exception as e:
        fail(f"Stats Audit Failed: {e}")

def simulate_user_flow(product):
    log("--- Starting User Journey Simulation ---")
    
    # 1. Start Bot
    cfg = {
        "ticker": product["symbol"],
        "quantity": 0.05,
        "spread": 0.0005,
        "interval": 3
    }
    log(f"User clicks START with config: {cfg}")
    res = requests.post(f"{BASE_URL}/start", json=cfg)
    if res.status_code != 200: fail(f"Start failed: {res.text}")
    
    # 2. Poll Stats
    log("Polling Stats for 10s...")
    
    # Audit Format once
    audit_stats()
    
    for i in range(5):
        time.sleep(2)
        s = requests.get(f"{BASE_URL}/stats").json()
        log(f"T+{i*2}s | Equity: {s.get('equity', 0):.2f} | Health: {s.get('health', 0):.2f} | PnL: {s.get('pnl', 0):.2f}")
        
    # 3. Stop Bot
    log("User clicks STOP")
    requests.post(f"{BASE_URL}/stop")
    log("Bot Stopped.")
    log("--- Simulation Complete: SUCCESS ---")

if __name__ == "__main__":
    try:
        eth_prod = audit_products()
        # audit_stats() -> Moved to inside user flow
        simulate_user_flow(eth_prod)
    except Exception as e:
        fail(f"Unhandled Error: {e}")

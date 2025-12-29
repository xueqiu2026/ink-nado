
import asyncio
import os
import sys
import json
# Add root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.nado import NadoClient
from trading_bot import TradingConfig
from decimal import Decimal
from dotenv import load_dotenv

async def main():
    load_dotenv()
    
    # Init Client
    cfg = TradingConfig(
        exchange="nado",
        ticker="ETH",
        contract_id="4", 
        quantity=Decimal("0.01"),
        take_profit=Decimal("100"),
        tick_size=Decimal("0.01"),
        direction="buy",
        max_orders=1,
        wait_time=5,
        grid_step=Decimal("0.01"),
        stop_price=Decimal("0"),
        pause_price=Decimal("0"),
        boost_mode=False
    )
    
    client = NadoClient(cfg)
    print(f"\n=== Nado Client Diagnostics ===")
    print(f"Network: {client.network}")
    print(f"Gateway: {client.gateway_url}")
    print(f"Checking Wallet: {client.wallet_address}")
    
    print(f"\n--- 1. Raw All Products Query ---")
    try:
        # Try raw products query
        print("Fetching /query {type: all_products}...")
        res = await client._post("/query", {"type": "all_products"})
        print(f"Response Status: {res.get('status')}")
        
        if 'data' in res:
            data = res['data']
            spot = data.get('spot_products', [])
            perp = data.get('perp_products', [])
            print(f"Found {len(spot)} Spot, {len(perp)} Perps")
            
            print("\n>> PERP PRODUCTS (First 5):")
            for p in perp[:5]:
                print(f"  ID: {p.get('id')} | Symbol: {p.get('symbol')} | Addr: {p.get('address')}")
                # Dump full details for the first one to check structure
                if p == perp[0]:
                    print(f"  [DEBUG] First Perp Full: {p}")
        else:
            print(f"WARN: No 'data' field. Full Res keys: {res.keys()}")
            
    except Exception as e:
        print(f"ERROR: Product Query Failed: {e}")

    print(f"\n--- 2. Raw REST Price Check ---")
    try:
        # Try a direct GET /products if Query fails?
        # Or check price from the earlier dump?
        # Let's try to get price for ID 4 just in case it exists
        price = await client._get_execution_price("buy")
        print(f"Resolved Price for ID {client.product_id}: {price}")
    except Exception as e:
        print(f"Price Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())

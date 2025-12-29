
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
    # We know ETH is ID 4 now
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
    # Force ID 4
    client.product_id = 4
    
    print(f"\n=== Account State (ID 4) ===")
    print(f"Address: {client.wallet_address}")
    
    print(f"\n--- Open Orders ---")
    try:
        # Use low-level post to debug if NadoClient fails
        payload = {
            "type": "open_orders",
            "product_ids": [4],
            "sender": client.wallet_address # Verify if sender is needed?
             # Based on doc, usually sender is inferred or passed. 
             # NadoClient._post sends signed request? No, query is usually public unless specific?
             # Wait, open_orders is Authenticated?
             # NadoClient handles auth in _post if specific headers needed (usually X-Nado... or just sender in body?)
             # Let's check NadoClient.get_open_orders logic
        }
        # In NadoClient._get_open_orders (not implemented? No, we use raw post in HFTBot)
        # Wait, HFTBot cancels orders by digest from placement response?
        # HFTBot tracks `active_order_digests`.
        
        # Let's use the Query endpoint for open orders
        # According to doc map: Query > OpenOrders
        # Requires: sender, product_ids
        
        full_payload = {
            "type": "open_orders",
            "sender": client.wallet_address
            # Try without product_ids
        }
        
        res = await client._post("/query", full_payload)
        orders = res.get('data', {}).get('orders', [])
        print(f"Count: {len(orders)}")
        if not orders:
            print("  [INFO] No Open Orders found via API.")
        for o in orders:
            print(f"  ID: {o.get('digest')} | Product: {o.get('product_id')} | PriceX18: {o.get('price_x18')} | Amt: {o.get('amount_x18')}")
            
    except Exception as e:
        print(f"Order Fetch Failed: {e}")

    print(f"\n--- Positions/Account ---")
    try:
        # Check balances/positions if possible
        # Query: type="spot_balances" or similar?
        # Nado doc: 'spot_balances', 'perp_balances' or 'account_state'?
        payload = {
            "type": "spot_balances",
            "sender": client.wallet_address
        }
        res = await client._post("/query", payload)
        balances = res.get('data', {}).get('balances', [])
        print(f"Spot Balances: {len(balances)} items")
        for b in balances:
            print(f"  ID: {b.get('product_id')} | Balance: {b.get('balance_amount_x18')}")

    except Exception as e:
        print(f"Balance Fetch Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())

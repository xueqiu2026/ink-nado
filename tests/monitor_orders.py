
import asyncio
import os
import sys
import json
from decimal import Decimal
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exchanges.nado import NadoClient
from trading_bot import TradingConfig

async def main():
    load_dotenv()
    cfg = TradingConfig(exchange="nado", ticker="ETH", contract_id="4", quantity=Decimal("0.01"), 
                        take_profit=Decimal("1"), tick_size=Decimal("0.01"), direction="buy", 
                        max_orders=1, wait_time=5, grid_step=Decimal("0.01"), stop_price=Decimal(0), 
                        pause_price=Decimal(0), boost_mode=False)
    
    client = NadoClient(cfg)
    client.product_id = 4
    
    print(f"\n--- CHECKING ACCOUNT STATE ---")
    try:
        # Based on nado_doc_map, query type for account info
        payload = {
            "type": "subaccount_info", # or 'account_state'
            "subaccount": client._subaccount_to_bytes32(client.wallet_address, client.subaccount_name)
        }
        res = await client._post("/query", payload)
        print(f"Account Info: {json.dumps(res, indent=2)}")
    except Exception as e:
        print(f"Account State Load Failed: {e}")

    print(f"\n--- MONITORING ACTIVE ORDERS ---")
    try:
        # CORRECTED: Use "subaccount_orders" instead of "open_orders"
        payload = {
            "type": "subaccount_orders",
            "sender": client._subaccount_to_bytes32(client.wallet_address, client.subaccount_name),
            "product_id": 4
        }
        res = await client._post("/query", payload)
        # Handle response structure
        data = res.get('data', {})
        orders = data.get('orders', [])
            
        print(f"Current Order Count: {len(orders)}")
        for o in orders:
            print(f"  - Digest: {o.get('digest')} | PriceX18: {o.get('price_x18')} | QtyX18: {o.get('amount_x18')}")
        if len(orders) > 2:
            print("⚠️ WARNING: Order accumulation detected!")
        elif len(orders) == 2:
            print("✅ SUCCESS: Dual-sided quotes active.")
            
        for o in orders:
            print(f"  - Digest: {o.get('digest')} | PriceX18: {o.get('price_x18')} | QtyX18: {o.get('amount_x18')}")
            
    except Exception as e:
        print(f"Monitor Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())

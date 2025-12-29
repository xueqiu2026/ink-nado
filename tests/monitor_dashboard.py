
import asyncio
import os
import sys
import json
from decimal import Decimal
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exchanges.nado import NadoClient
from trading_bot import TradingConfig

async def main():
    load_dotenv()
    # Dummy config just to init client
    cfg = TradingConfig(exchange="nado", ticker="ETH", contract_id="4", quantity=Decimal("0.05"), 
                        take_profit=Decimal("1"), tick_size=Decimal("0.01"), direction="buy", 
                        max_orders=1, wait_time=5, grid_step=Decimal("0.01"), stop_price=Decimal(0), 
                        pause_price=Decimal(0), boost_mode=False)
    
    client = NadoClient(cfg)
    client.product_id = 4
    
    print("\n" + "="*50)
    print("      NADO HFT STRATEGY MONITOR (V2.0)      ")
    print("="*50)
    
    try:
        # 1. Fetch Position
        sender = client._subaccount_to_bytes32(client.wallet_address, client.subaccount_name)
        info_payload = {"type": "subaccount_info", "subaccount": sender}
        res_info = await client._post("/query", info_payload)
        
        position = Decimal("0")
        health = Decimal("0")
        
        data_info = res_info.get('data', {})
        
        # Parse Health
        if 'healths' in data_info:
            health = Decimal(data_info['healths'][0].get('health', 0)) / Decimal(10**18)
            
        # Parse Position (Product 4)
        for p in data_info.get('perp_balances', []):
            if int(p.get('product_id')) == 4:
                position = Decimal(p['balance']['amount']) / Decimal(10**18)
                
        # 2. Fetch Orders
        ord_payload = {"type": "subaccount_orders", "sender": sender, "product_id": 4}
        res_ord = await client._post("/query", ord_payload)
        orders = res_ord.get('data', {}).get('orders', [])
        
        # 3. Determine Mode
        THRESHOLD = Decimal("0.02")
        mode = "🟢 NEUTRAL (Farming)"
        if position > THRESHOLD:
            mode = "🔴 DUMP MODE (Long Heavy)"
        elif position < -THRESHOLD:
            mode = "🔵 COVER MODE (Short Heavy)"
            
        # 4. Display Dashboard
        print(f"\n[ACCOUNT STATE]")
        print(f"  Health    : {health:.2f} USDT")
        print(f"  Position  : {position:.4f} ETH")
        print(f"  Mode      : {mode}")
        
        print(f"\n[ACTIVE ORDERS] ({len(orders)})")
        if not orders:
            print("  (No active orders)")
        else:
            for o in orders:
                amt = Decimal(o.get('amount')) / Decimal(10**18) if isinstance(o.get('amount'), str) else 0
                price = Decimal(o.get('price_x18')) / Decimal(10**18) if isinstance(o.get('price_x18'), str) else 0
                side = "✅ BUY " if amt > 0 else "❌ SELL" # Wait, amount is just quantity? 
                # Actually AMOUNT in order is usually positive unsigned?
                # Need to check `amount` field in order. 
                # Actually, amount is quantity remaining. Direction is implied?
                # No, standard is Buy/Sell? 
                # Looking at probe output: 
                # 'amount': '50000000000000000' (0.05).
                # But looking at `price_x18`: '3004400...'
                # Nado orders usually have 'amount' > 0.
                # How to distinguish Buy/Sell?
                # Usually bid/ask is inferred or there is a sign?
                # In Vertex/Nado, amount is signed for Position but Orders?
                # Let's rely on Price vs Oracle or just print params.
                # Actually let's just print Price and Amount.
                print(f"  - {amt:.3f} ETH @ {price:.2f}")

        print("\n" + "="*50 + "\n")
        
    except Exception as e:
        print(f"Monitor Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())

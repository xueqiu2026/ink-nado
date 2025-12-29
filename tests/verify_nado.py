# tests/verify_nado.py
# [Antigravity] Feature Add: Nado Verification (Custom Implementation)

import asyncio
import os
import sys
import aiohttp # Added missing import
from decimal import Decimal
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.nado import NadoClient
from trading_bot import TradingConfig

async def main():
    load_dotenv()
    
    # Check config
    pk = os.getenv('NADO_PRIVATE_KEY')
    if not pk:
        print("Error: NADO_PRIVATE_KEY not set in .env")
        return

    print(f"Initializing Custom Nado Client...")
    
    # Create valid config mockup
    config = TradingConfig(
        ticker="ETH", 
        contract_id="",
        tick_size=Decimal("0"),
        quantity=Decimal("0.001"),
        take_profit=Decimal("0.01"),
        direction="buy",
        max_orders=1,
        wait_time=10,
        exchange="nado",
        grid_step=Decimal("-100"),
        stop_price=Decimal("-1"),
        pause_price=Decimal("-1"),
        boost_mode=False
    )
    
    try:
        client = NadoClient(config)
        
        # 1. Test Initialization & Account Loading
        print("\n[1/3] Testing Account Initialization...")
        print(f"  [OK] Wallet Address: {client.wallet_address}")
        print(f"  [OK] Chain ID: {client.chain_id}")
        
        # 2.5 Check Balance
        print("\n[2.5/3] Checking Account Balance...")
        
        # Correctly formatted subaccount address (sender bytes32 as hex string is not quite right for query params usually)
        # Query typically expects the address or the subaccount identifier.
        # Let's try sending the 'sender' we generated.
        sender_str = client._subaccount_to_bytes32(client.wallet_address, "default")
        
        # Query subaccount info
        balance_res = await client._post("/query", {
            "type": "subaccount_info", 
            "subaccount": sender_str
        })
        
        # Dump for debug
        with open("balance_debug.json", "w") as f:
            import json
            json.dump(balance_res, f, indent=2)
            
        health = balance_res.get('data', {}).get('health_x18', '0')
        print(f"   Account Health: {float(health)/10**18}")
        
        # 5. Live Order Test (The Real Test)
        print("\n[5/5] Testing LIVE Order Placement (Limit IOC)...")
        # User wants leverage. 0.04 failed (132 USDT).
        # We try the BARE MINIMUM to barely cross the 100 USDT line.
        # 0.031 ETH * 3300 = 102.3 USDT.
        # 13 USDT collateral -> ~7.9x leverage.
        quantity = Decimal("0.031") 
        direction = "buy"
        
        print(f"   Quantity: {quantity} ETH (Approx Value: {quantity * 3300} USDT)")
        
        # Ensure contract attributes are fresh
        cid, tick = await client.get_contract_attributes()
        
        res = await client.place_open_order(cid, quantity, direction)
        
        if res.success:
            print(f"  [SUCCESS] Order Placed. ID: {res.order_id}")
            with open("verify_result.txt", "w") as f: f.write("SUCCESS")
        else:
            print(f"  [FAILED] Error: {res.error_message}")
            with open("verify_result.txt", "w") as f: f.write(f"FAILURE: {res.error_message}")
                
    except Exception as e:
        print(f"   -> Connection Failed: {e}")
        import traceback
        traceback.print_exc()
        with open("verify_result.txt", "w") as f: f.write(f"FAILURE: Exception {e}")

        print("\nVerification Complete (Custom Mode).")
        
    except Exception as e:
        print(f"\n[FAILED] Verification Failed: {e}")
        import traceback
        traceback.print_exc()
        with open("verify_result.txt", "w") as f: f.write(f"FAILURE: Exception {e}")

if __name__ == "__main__":
    asyncio.run(main())

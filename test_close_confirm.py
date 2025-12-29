import asyncio
import os
import json
from decimal import Decimal
from dotenv import load_dotenv
from exchanges.nado import NadoClient

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(DecimalEncoder, self).default(obj)

class MockConfig:
    def __init__(self):
        self.network = os.getenv("NADO_NETWORK", "mainnet")
        self.url = "https://gateway.prod.nado.xyz/v1"
        self.product_id = 4
        self.tick_size = 0.1
        self.ticker = "ETH-PERP"

async def test_confirm():
    load_dotenv()
    config = MockConfig()
    client = NadoClient(config)
    
    # CRITICAL: Resolve product info (like product_id) from ticker
    await client.get_contract_attributes()
    
    # Get current position
    pos = await client.get_account_positions()
    
    print(f"Current Position: {pos}")
    if pos == 0:
        print("No position to close. Placing a small test market order (0.1 ETH).")
        amount = 0.1
    else:
        amount = -pos
    
    # Place market order (appendix 513)
    side = "buy" if amount > 0 else "sell"
    res = await client.place_market_order(
        "4", 
        abs(Decimal(str(amount))), 
        side
    )
    
    print("\n--- RESPONSE FROM NADO ---")
    if res:
        print(f"Success: {res.success}")
        print(f"ID:      {res.order_id}")
        if res.error_message:
            print(f"Error:   {res.error_message}")
        
        if res.success:
            print("\n✅ SUCCESS! Market order accepted.")
        else:
            print("\n❌ FAILED. Look at the error above.")
    else:
        print("\n❌ FAILED. Response is None.")

if __name__ == "__main__":
    asyncio.run(test_confirm())

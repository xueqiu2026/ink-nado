
import asyncio
import aiohttp
import json
from decimal import Decimal

async def main():
    url = "https://gateway.prod.nado.xyz/v1/query"
    payload = {"type": "all_products"}
    
    print(f"Fetching Product Map (Price Analysis)...")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            perps = data.get('data', {}).get('perp_products', [])
            
            print(f"{'ID':<5} | {'PRICE (Approx)':<15} | {'RAW x18'}")
            print("-" * 50)
            
            for p in perps:
                pid = p.get('product_id')
                # Price is X18 format
                px18 = Decimal(p.get('oracle_price_x18', '0'))
                price = px18 / Decimal(10**18)
                
                print(f"{pid:<5} | {price:<15.2f} | {px18}")

if __name__ == "__main__":
    asyncio.run(main())

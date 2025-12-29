
import asyncio
import aiohttp
import json

async def try_endpoint(session, method, url, json_data=None, params=None, desc=""):
    print(f"\n--- Testing: {desc} ---")
    print(f"URL: {url}")
    if json_data: print(f"Payload: {json_data}")
    
    try:
        if method == "POST":
            async with session.post(url, json=json_data) as resp:
                print(f"Status: {resp.status}")
                text = await resp.text()
                print(f"Response: {text[:200]}...")
                return resp.status == 200
        else:
            async with session.get(url, params=params) as resp:
                print(f"Status: {resp.status}")
                text = await resp.text()
                print(f"Response: {text[:200]}...")
                return resp.status == 200
    except Exception as e:
        print(f"Error: {e}")
        return False

async def main():
    base_url = "https://gateway.prod.nado.xyz/v1"
    pid = 4 # ETH-PERP
    
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        
        # 1. market_liquidity (Documented)
        await try_endpoint(session, "POST", f"{base_url}/query", 
                           json_data={"type": "market_liquidity", "product_id": pid, "depth": 5},
                           desc="Query: market_liquidity")

        # 2. depth (Common)
        await try_endpoint(session, "POST", f"{base_url}/query", 
                           json_data={"type": "depth", "product_id": pid, "depth": 5},
                           desc="Query: depth")
                           
        # 3. market_depth (Common)
        await try_endpoint(session, "POST", f"{base_url}/query", 
                           json_data={"type": "market_depth", "product_id": pid, "depth": 5},
                           desc="Query: market_depth")
        
        # 4. orderbook
        await try_endpoint(session, "POST", f"{base_url}/query", 
                           json_data={"type": "orderbook", "product_id": pid, "depth": 5},
                           desc="Query: orderbook")
                           
        # 5. GET /depth
        await try_endpoint(session, "GET", f"{base_url}/depth", 
                           params={"product_id": pid},
                           desc="GET /depth")
                           
        # 6. GET /products/ID/book
        await try_endpoint(session, "GET", f"{base_url}/products/{pid}/book", 
                           desc="GET /products/{id}/book")

if __name__ == "__main__":
    asyncio.run(main())

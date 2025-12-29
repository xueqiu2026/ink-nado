
import asyncio
import aiohttp
import json

async def main():
    url = "https://gateway.prod.nado.xyz/v1/query"
    payload = {"type": "all_products"}
    
    print(f"POSTing to {url}...")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            print(f"Status: {resp.status}")
            text = await resp.text()
            # Try to parse and pretty print
            try:
                data = json.loads(text)
                print(json.dumps(data, indent=2))
            except:
                print("RAW TEXT:")
                print(text)

if __name__ == "__main__":
    asyncio.run(main())

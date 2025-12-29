
import asyncio
import aiohttp
import json

async def test():
    async with aiohttp.ClientSession() as session:
        # 1. Start
        print("Starting Bot...")
        payload = {"ticker": "ETH", "quantity": 0.05, "spread": 0.0005, "interval": 5}
        async with session.post("http://localhost:8000/start", json=payload) as resp:
            print(f"Start Response: {await resp.text()}")

        # 2. Listen to WS
        print("Connecting to WS...")
        async with session.ws_connect("http://localhost:8000/ws") as ws:
            count = 0
            async for msg in ws:
                print(f"[WS Log] {msg.data}")
                count += 1
                if count >= 3: # Wait for a few logs
                    break
        
        # 3. Stop
        print("Stopping Bot...")
        async with session.post("http://localhost:8000/stop") as resp:
            print(f"Stop Response: {await resp.text()}")

if __name__ == "__main__":
    try:
        asyncio.run(test())
    except Exception as e:
        print(f"Error: {e}")

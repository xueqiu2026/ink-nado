
import asyncio
import os
import sys
import json
from decimal import Decimal
from dotenv import load_dotenv
import aiohttp

async def main():
    load_dotenv()
    pk = os.getenv("NADO_PRIVATE_KEY")
    from eth_account import Account
    acc = Account.from_key(pk)
    addr = acc.address
    
    # Subaccount bytes32
    # 20 bytes address + 12 bytes 'default'
    sub_hex = addr.lower().replace("0x", "") + "64656661756c740000000000"
    sub_bytes32 = "0x" + sub_hex

    url = "https://gateway.prod.nado.xyz/v1/query"
    
    results = {}
    async with aiohttp.ClientSession() as session:
        # Test 1: subaccount_info standard
        p1 = {"type": "subaccount_info", "subaccount": sub_bytes32}
        async with session.post(url, json=p1) as r:
            results["subaccount_info"] = {"status": r.status, "body": await r.json() if r.status == 200 else await r.text()}

        # Test 2: subaccount_orders (Correct)
        p2 = {
            "type": "subaccount_orders", 
            "sender": sub_bytes32, 
            "product_id": 4
        }
        async with session.post(url, json=p2) as r:
             results["open_orders"] = {"status": r.status, "body": await r.json() if r.status == 200 else await r.text()}

        # Test 3: symbols query
        p3 = {"type": "symbols"}
        async with session.post(url, json=p3) as r:
             results["symbols"] = {"status": r.status, "body": await r.json() if r.status == 200 else await r.text()}

    with open("tests/probe_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Done. Results in tests/probe_results.json")

if __name__ == "__main__":
    asyncio.run(main())
